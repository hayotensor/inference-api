"""Subnet chain read client: map a miner hotkey to its on-chain node + class.

Mirrors the lazy-import + dedicated-error pattern of
``api/app/blockchain/substrate_evm.py``: the heavy ``substrate-interface`` /
``async-substrate-interface`` dependency is imported lazily inside the read call
so importing this module never requires it, and any read failure is normalized
to ``ChainReadError`` (config problems to ``ChainConfigError``).

A ``MockChainClient`` is provided for tests (no chain RPC), and registration
gating is controlled by ``chain_required`` / ``chain_min_class`` in config.
"""

from __future__ import annotations

from dataclasses import dataclass

from inference_api.config import settings


class ChainConfigError(RuntimeError):
    """The chain client is misconfigured (e.g. no RPC url)."""


class ChainReadError(RuntimeError):
    """A chain read failed (RPC unreachable, decode error, etc.)."""


# Subnet node classification ordering (ascending privilege). A miner must reach
# at least ``chain_min_class`` to be admitted when ``chain_required`` is set.
CLASS_ORDER = ("Deactivated", "Registered", "Idle", "Included", "Validator")


@dataclass(frozen=True)
class ChainNode:
    subnet_node_id: int | None
    classification: str | None


def class_rank(classification: str | None) -> int:
    """Rank of a classification (higher = more privileged); -1 if unknown."""
    if classification is None:
        return -1
    try:
        return CLASS_ORDER.index(classification)
    except ValueError:
        return -1


def class_meets_minimum(classification: str | None, minimum: str | None) -> bool:
    """True iff ``classification`` is at least ``minimum`` in CLASS_ORDER."""
    if minimum is None:
        return True
    return class_rank(classification) >= class_rank(minimum)


class ChainClient:
    """Reads subnet node state for a hotkey over a Substrate RPC.

    The substrate library is imported lazily so this module imports without it;
    every failure path normalizes to ChainReadError/ChainConfigError.
    """

    def __init__(self, *, rpc_url: str | None = None) -> None:
        self.rpc_url = rpc_url or settings.chain_rpc_url

    def get_node_by_hotkey(self, hotkey: str) -> ChainNode:
        if not self.rpc_url:
            raise ChainConfigError("Subnet chain RPC URL is required")
        try:
            from substrateinterface import SubstrateInterface  # type: ignore

            substrate = SubstrateInterface(url=self.rpc_url)
            try:
                node_id_q = substrate.query("Network", "HotkeySubnetNodeId", [hotkey])
                subnet_node_id = (
                    int(node_id_q.value) if node_id_q is not None and node_id_q.value is not None else None
                )
                classification = None
                if subnet_node_id is not None:
                    class_q = substrate.query(
                        "Network", "SubnetNodeClass", [subnet_node_id]
                    )
                    classification = class_q.value if class_q is not None else None
                return ChainNode(subnet_node_id=subnet_node_id, classification=classification)
            finally:
                close = getattr(substrate, "close", None)
                if callable(close):
                    close()
        except (ChainConfigError, ChainReadError):
            raise
        except Exception as exc:  # noqa: BLE001 - any RPC/decode failure
            raise ChainReadError(str(exc)) from exc


class MockChainClient(ChainClient):
    """Test double: returns a fixed mapping of hotkey -> ChainNode.

    Use ``allow_unknown`` to control whether hotkeys not in the table raise
    ChainReadError (default) or resolve to an empty ChainNode.
    """

    def __init__(
        self,
        nodes: dict[str, ChainNode] | None = None,
        *,
        allow_unknown: bool = False,
        default_classification: str | None = None,
    ) -> None:
        super().__init__(rpc_url="mock://chain")
        self._nodes = dict(nodes or {})
        self._allow_unknown = allow_unknown
        self._default_classification = default_classification

    def set_node(self, hotkey: str, node: ChainNode) -> None:
        self._nodes[hotkey] = node

    def get_node_by_hotkey(self, hotkey: str) -> ChainNode:
        if hotkey in self._nodes:
            return self._nodes[hotkey]
        if self._allow_unknown:
            return ChainNode(subnet_node_id=None, classification=self._default_classification)
        raise ChainReadError(f"hotkey {hotkey[:12]}... not found on chain")
