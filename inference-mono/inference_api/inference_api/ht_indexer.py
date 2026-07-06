"""ht-indexer client + subnet-stake eligibility/allowance.

Reads staking history from the Hypertensor **ht-indexer** (a SubSquid indexer
exposing a PostGraphile GraphQL API) to decide whether a wallet has been a
*continuous subnet delegator for >= N days* and, if so, how large a weekly
inference-token quota it earns (proportional to the staked amount).

Design mirrors ``app.blockchain.erc20``'s normalize->floor->allowance pattern on
the balance side, and reconstructs a *continuous-stake streak* from the indexer's
raw ``event`` log on the duration side (the indexer stores no first-staked /
streak field of its own).

Identity: Hypertensor uses ``AccountId20`` (Ethereum-style 0x H160), the same
format the inference API stores in ``evm_wallets.address``. The indexer stores
addresses **lowercase**, so every address is lowercased before use.

The GraphQL transport is deliberately thin; the testable core is the two pure
functions ``compute_streak_start`` and ``subnet_stake_allowance``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal

import httpx

from inference_api.config import settings
from inference_api.security import as_utc

logger = logging.getLogger(__name__)

# Network.* event methods that can move an account's *subnet-delegate*
# (``subnetNode is null``) position for a given subnet. The first three
# serialize ``event.args`` as a positional ARRAY; the two cross-type swaps
# serialize it as a NAMED OBJECT (see the indexer's src/handlers/stake.ts).
SUBNET_DELEGATE_METHODS = (
    "SubnetDelegateStakeAdded",
    "SubnetDelegateStakeRemoved",
    "SubnetDelegateStakeSwapped",
    "DelegateNodeToSubnetDelegateStakeSwapped",
    "SubnetDelegateToNodeDelegateStakeSwapped",
)

# Cap on stake-history events fetched per account. A single delegator emits only
# a handful of stake events; the cap is a safety bound. If an account ever
# exceeds it the oldest events win (ordered ASC) so the streak stays correct.
_MAX_EVENTS = 1000


@dataclass(frozen=True)
class SubnetStakeStatus:
    """Point-in-time view of one account's subnet-delegate stake for one subnet."""

    address: str
    subnet_id: int
    raw_amount: int  # current staked base units (authoritative, from `stakes`)
    streak_start: datetime | None  # start of current unbroken positive run, or None


@dataclass(frozen=True)
class StakeQuotaPolicy:
    """Pure config for the stake -> weekly-allowance conversion."""

    subnet_id: int
    min_days: int
    min_amount: int  # normalized (whole-token) minimum stake to qualify at all
    decimals: int  # native-token decimals used to normalize raw base units
    unit: int  # normalized stake per allowance "unit"
    per_unit: int  # tokens granted per unit
    cap: int  # 0 => uncapped weekly allowance

    @classmethod
    def from_settings(cls) -> "StakeQuotaPolicy":
        if settings.subnet_stake_subnet_id is None:
            raise ValueError("SUBNET_STAKE_SUBNET_ID must be set when the stake quota is enabled")
        return cls(
            subnet_id=settings.subnet_stake_subnet_id,
            min_days=settings.subnet_stake_min_days,
            min_amount=settings.subnet_stake_min_amount,
            decimals=settings.subnet_stake_decimals,
            unit=max(1, settings.subnet_stake_tokens_unit),
            per_unit=settings.subnet_stake_tokens_per_unit,
            cap=settings.subnet_stake_weekly_token_cap,
        )


# --------------------------------------------------------------------------- #
# Pure helpers (fully unit-testable, no I/O)
# --------------------------------------------------------------------------- #


def normalize_stake(raw_amount: int, decimals: int) -> Decimal:
    return Decimal(raw_amount) / (Decimal(10) ** decimals)


def subnet_stake_allowance(
    status: SubnetStakeStatus, policy: StakeQuotaPolicy, *, now: datetime
) -> int:
    """Proportional weekly token allowance for an eligible staker, else 0.

    Eligible iff the account is currently staked (``raw_amount > 0``), the
    normalized stake meets ``min_amount``, and its current *continuous* stake
    streak is at least ``min_days`` old ("staked >= N days AND still staked").
    """
    if status.raw_amount <= 0:
        return 0
    normalized = normalize_stake(status.raw_amount, policy.decimals)
    if normalized < Decimal(policy.min_amount):
        return 0
    if status.streak_start is None:
        return 0
    if as_utc(now) - as_utc(status.streak_start) < timedelta(days=policy.min_days):
        return 0
    units = int((normalized / Decimal(policy.unit)).to_integral_value(rounding=ROUND_FLOOR))
    allowance = units * policy.per_unit
    if policy.cap > 0:
        allowance = min(allowance, policy.cap)
    return max(0, allowance)


def compute_streak_start(
    events: list[dict], *, subnet_id: int, address: str
) -> datetime | None:
    """Start of the current unbroken positive-balance run, from the event log.

    ``events`` are ht-indexer ``event`` rows (``method``, ``data``, ``timestamp``)
    for the account, ordered by timestamp ASCENDING. Each ``data`` is
    ``JSON.stringify(event.args)``. We replay the signed per-event deltas to the
    account's subnet-delegate balance and return the timestamp of the last
    0 -> positive transition that is never followed by a drop back to <= 0.

    Returns ``None`` if the balance is not currently positive per the replay, or
    if no 0 -> positive transition is observable (e.g. the position predates the
    indexer's start block and has emitted no stake events since — such accounts
    only qualify once they next touch their stake).
    """
    addr = address.lower()
    sid = str(subnet_id)
    balance = 0
    streak_start: datetime | None = None
    for event in events:
        delta = _event_delta(event.get("method", ""), event.get("data", ""), sid, addr)
        if not delta:
            continue
        previous = balance
        balance += delta
        if previous <= 0 < balance:
            streak_start = _parse_ts(event.get("timestamp"))
        elif balance <= 0:
            streak_start = None
    return streak_start


def _event_delta(method: str, data: str, sid: str, addr: str) -> int:
    """Signed change to ``addr``'s subnet-delegate balance in subnet ``sid``.

    ``0`` if the event does not affect this (account, subnet) position.
    ``data`` is a positional ARRAY for SubnetDelegateStake{Added,Removed,Swapped}
    and a NAMED OBJECT for the two node<->subnet cross swaps.
    """
    try:
        args = json.loads(data)
    except (TypeError, ValueError):
        return 0

    if method == "SubnetDelegateStakeAdded":
        # [subnet_id, account_id, amount]
        if _at(args, 0) == sid and _lower(_at(args, 1)) == addr:
            return _to_int(_at(args, 2))
    elif method == "SubnetDelegateStakeRemoved":
        # [subnet_id, account_id, amount]
        if _at(args, 0) == sid and _lower(_at(args, 1)) == addr:
            return -_to_int(_at(args, 2))
    elif method == "SubnetDelegateStakeSwapped":
        # [from_subnet_id, to_subnet_id, account_id, amount]
        if _lower(_at(args, 2)) == addr:
            amount = _to_int(_at(args, 3))
            delta = 0
            if _at(args, 1) == sid:  # into this subnet
                delta += amount
            if _at(args, 0) == sid:  # out of this subnet
                delta -= amount
            return delta
    elif method == "DelegateNodeToSubnetDelegateStakeSwapped":
        # {accountId, fromSubnetId, fromSubnetNodeId, toSubnetId, amount} -> INTO subnet-delegate
        if _named(args, "accountId", "account_id") == addr and _named(
            args, "toSubnetId", "to_subnet_id"
        ) == sid:
            return _to_int(_named_raw(args, "amount"))
    elif method == "SubnetDelegateToNodeDelegateStakeSwapped":
        # {accountId, fromSubnetId, toSubnetId, toSubnetNodeId, amount} -> OUT of subnet-delegate
        if _named(args, "accountId", "account_id") == addr and _named(
            args, "fromSubnetId", "from_subnet_id"
        ) == sid:
            return -_to_int(_named_raw(args, "amount"))
    return 0


def _at(args: object, index: int) -> str | None:
    if isinstance(args, list) and 0 <= index < len(args):
        value = args[index]
        return None if value is None else str(value)
    return None


def _named_raw(args: object, *keys: str) -> object:
    if isinstance(args, dict):
        for key in keys:
            if key in args and args[key] is not None:
                return args[key]
    return None


def _named(args: object, *keys: str) -> str | None:
    """Lowercased string value for the first present key (address/subnet match)."""
    return _lower(_named_raw(args, *keys))


def _lower(value: object) -> str | None:
    return None if value is None else str(value).lower()


def _to_int(value: object) -> int:
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return 0
    if text.lower().startswith("0x"):
        return int(text, 16)
    return int(text)


def _parse_ts(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return as_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# GraphQL client (thin transport)
# --------------------------------------------------------------------------- #

_CURRENT_STAKE_QUERY = """
query CurrentStake($addr: String!, $subnet: String!) {
  stakes(
    filter: {
      address: {equalTo: $addr},
      subnet: {equalTo: $subnet},
      subnetNode: {isNull: true},
      coldkey: {isNull: true}
    }
  ) {
    nodes { amount }
  }
}
""".strip()

_STAKE_EVENTS_QUERY = """
query StakeEvents($addr: String!, $methods: [String!], $first: Int!) {
  events(
    filter: {
      section: {equalTo: "Network"},
      method: {in: $methods},
      data: {includesInsensitive: $addr}
    },
    orderBy: TIMESTAMP_ASC,
    first: $first
  ) {
    nodes { method data timestamp }
  }
}
""".strip()


class HtIndexerError(RuntimeError):
    pass


class HtIndexerStakeClient:
    """Reads subnet-delegate stake status for a wallet from the ht-indexer."""

    def __init__(
        self,
        *,
        graphql_url: str,
        subnet_id: int,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._graphql_url = graphql_url
        self._subnet_id = subnet_id
        self._timeout = timeout
        self._client = client  # injectable for tests / connection reuse

    @classmethod
    def from_settings(cls, client: httpx.AsyncClient | None = None) -> "HtIndexerStakeClient":
        if not settings.ht_indexer_graphql_url:
            raise ValueError("HT_INDEXER_GRAPHQL_URL must be set when the stake quota is enabled")
        if settings.subnet_stake_subnet_id is None:
            raise ValueError("SUBNET_STAKE_SUBNET_ID must be set when the stake quota is enabled")
        return cls(
            graphql_url=settings.ht_indexer_graphql_url,
            subnet_id=settings.subnet_stake_subnet_id,
            timeout=settings.ht_indexer_timeout_seconds,
            client=client,
        )

    async def get_subnet_stake_status(self, address: str) -> SubnetStakeStatus:
        addr = address.lower()
        raw_amount = await self._current_amount(addr)
        streak_start = compute_streak_start(
            await self._stake_events(addr), subnet_id=self._subnet_id, address=addr
        )
        return SubnetStakeStatus(
            address=addr,
            subnet_id=self._subnet_id,
            raw_amount=raw_amount,
            streak_start=streak_start,
        )

    async def _current_amount(self, addr: str) -> int:
        data = await self._graphql(
            _CURRENT_STAKE_QUERY, {"addr": addr, "subnet": str(self._subnet_id)}
        )
        nodes = ((data.get("stakes") or {}).get("nodes")) or []
        total = sum(_to_int(node.get("amount")) for node in nodes)
        return max(0, total)

    async def _stake_events(self, addr: str) -> list[dict]:
        data = await self._graphql(
            _STAKE_EVENTS_QUERY,
            {"addr": addr, "methods": list(SUBNET_DELEGATE_METHODS), "first": _MAX_EVENTS},
        )
        return ((data.get("events") or {}).get("nodes")) or []

    async def _graphql(self, query: str, variables: dict) -> dict:
        payload = {"query": query, "variables": variables}
        if self._client is not None:
            response = await self._client.post(self._graphql_url, json=payload, timeout=self._timeout)
            return _unwrap(response)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._graphql_url, json=payload)
            return _unwrap(response)


def _unwrap(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        raise HtIndexerError(f"ht-indexer HTTP {response.status_code}")
    body = response.json()
    if body.get("errors"):
        raise HtIndexerError(f"ht-indexer GraphQL errors: {body['errors']}")
    return body.get("data") or {}
