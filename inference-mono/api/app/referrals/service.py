from __future__ import annotations

import re
import secrets
import uuid
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import bad_request
from app.core.security import as_utc, utcnow
from app.db.models.referral import Referral
from app.db.models.referral_code_alias import ReferralCodeAlias
from app.db.models.referral_earning import ReferralEarning
from app.db.models.user import User
from app.db.models.user_wallet import UserWallet
from app.db.models.wallet_transaction import WalletTransaction

# HYP-65 revenue-share terms, deliberately paid in bonus inference-token
# credits (via the HYP-66 wallet) and never as a cash payout to a third
# party -- a real payout would need Stripe Connect + KYC/tax handling +
# payout infra that is entirely out of scope here. See the HYP-65 report for
# this scope cut spelled out explicitly.
#
# 10% of whatever the referred user actually paid Stripe, converted to
# tokens at that payment's own effective $/token rate (the referred user's
# plan's monthly price / monthly token allowance for a subscription invoice,
# or a HYP-66 wallet top-up bundle's own known rate).
REFERRAL_SHARE_RATE = Decimal("0.10")

# 12 months from the referred user's signup (Referral.created_at), after
# which no further payments of theirs earn the referrer anything. Approximated
# as 365 days -- this codebase has no calendar-month arithmetic dependency
# (see app/usage/service.py's own hand-rolled month math) and 12 calendar
# months vs. 365 days differs by at most a day or two, which does not matter
# at a monthly-billing granularity.
REFERRAL_EARNOUT_WINDOW = timedelta(days=365)

# Visually unambiguous alphabet (no 0/O, 1/I/l) for an 8-char code that gets
# typed/read off a shared link without confusion. ~33^8 (~1.7e12) possible
# codes, so a collision on generation is exceptionally rare; still handled
# below rather than assumed away.
_REFERRAL_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_REFERRAL_CODE_LENGTH = 8
_REFERRAL_CODE_GENERATION_ATTEMPTS = 5


def _generate_referral_code() -> str:
    return "".join(secrets.choice(_REFERRAL_CODE_ALPHABET) for _ in range(_REFERRAL_CODE_LENGTH))


# HYP-93: user-chosen ("custom") referral codes, shared as `{app_url}/r/<code>`.
# Stored lowercase; every lookup and uniqueness check is case-insensitive (see
# the `lower(...)` unique indexes on users.referral_code and
# referral_code_aliases.code), so "MyCode" and "mycode" are the same code.
CUSTOM_REFERRAL_CODE_MIN_LENGTH = 3
CUSTOM_REFERRAL_CODE_MAX_LENGTH = 32
# Letters, digits, '-' and '_'; must start and end with a letter or digit.
_CUSTOM_REFERRAL_CODE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")
# A user's previous codes are kept as aliases (so already-shared links keep
# working); cap how many one account can accumulate so nobody can squat on
# an unbounded number of codes by changing theirs over and over.
MAX_REFERRAL_CODE_ALIASES = 10
# Words that would read as an official/system link (talaris.../r/admin) or
# collide with app routes. Compared against the normalized (lowercase) code.
RESERVED_REFERRAL_CODES = frozenset(
    {
        "about", "account", "accounts", "admin", "administrator", "api", "app", "auth",
        "billing", "blog", "chat", "contact", "dashboard", "docs", "help", "home",
        "info", "login", "logout", "mod", "moderator", "null", "official", "owner",
        "pricing", "privacy", "r", "ref", "referral", "referrals", "register", "root",
        "security", "settings", "signin", "signup", "staff", "status", "support",
        "system", "talaris", "team", "terms", "test", "undefined", "www",
    }
)


def _conflict(code: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail={"code": code, "message": detail}
    )


def normalize_custom_referral_code(raw: str) -> str:
    """Validate a user-chosen referral code and return its canonical
    (lowercase) form; raises a 400 with a specific error code otherwise."""
    code = raw.strip().lower()
    if not (CUSTOM_REFERRAL_CODE_MIN_LENGTH <= len(code) <= CUSTOM_REFERRAL_CODE_MAX_LENGTH):
        raise bad_request(
            "referral_code_invalid_length",
            f"Referral code must be {CUSTOM_REFERRAL_CODE_MIN_LENGTH}-"
            f"{CUSTOM_REFERRAL_CODE_MAX_LENGTH} characters.",
        )
    if not _CUSTOM_REFERRAL_CODE_RE.fullmatch(code):
        raise bad_request(
            "referral_code_invalid_characters",
            "Referral code may only use letters, numbers, '-' and '_', and must start "
            "and end with a letter or number.",
        )
    if code in RESERVED_REFERRAL_CODES:
        raise bad_request("referral_code_reserved", "That referral code is reserved.")
    return code


class ReferralService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ensure_referral_code(self, user: User) -> str:
        """Get-or-create the user's shareable referral code. Generated lazily
        -- on first access to GET /referrals/me -- rather than at signup, so
        an account that never opens the Referrals page never needs one."""
        if user.referral_code:
            return user.referral_code
        for _ in range(_REFERRAL_CODE_GENERATION_ATTEMPTS):
            code = _generate_referral_code()
            user.referral_code = code
            self.session.add(user)
            try:
                async with self.session.begin_nested():
                    await self.session.flush()
                return code
            except IntegrityError:
                # Collision -- either an astronomically unlikely clash with
                # another user's code, or a concurrent request for this same
                # user winning the race. begin_nested()'s own rollback to the
                # SAVEPOINT already undid the failed UPDATE; refetch and
                # retry, mirroring the _lock_or_create_wallet pattern used
                # elsewhere in this codebase for the same kind of race.
                await self.session.refresh(user)
                if user.referral_code:
                    return user.referral_code
        raise RuntimeError("Could not allocate a unique referral code")

    def referral_link(self, code: str) -> str:
        # HYP-93: short link. The UI's /r/<code> route forwards to
        # /signup?ref=<code>, so older `/signup?ref=` links keep working too.
        return f"{str(settings.app_url).rstrip('/')}/r/{code}"

    async def _find_code_owner(self, code: str) -> tuple[uuid.UUID, str] | None:
        """Case-insensitive lookup of who owns `code` -- as their current
        referral code or as one of their previous ones (an alias). Returns
        (user_id, code as stored)."""
        needle = code.strip().lower()
        if not needle:
            return None
        row = (
            await self.session.execute(
                select(User.id, User.referral_code).where(
                    func.lower(User.referral_code) == needle
                )
            )
        ).first()
        if row is not None:
            return row[0], row[1]
        row = (
            await self.session.execute(
                select(ReferralCodeAlias.user_id, ReferralCodeAlias.code).where(
                    func.lower(ReferralCodeAlias.code) == needle
                )
            )
        ).first()
        if row is not None:
            return row[0], row[1]
        return None

    async def set_custom_code(self, user: User, raw_code: str) -> str:
        """HYP-93: replace the user's referral code with one they chose.
        400 on an invalid/reserved code, 409 if anyone else already has it
        (current code or a previous one). The user's previous code becomes an
        alias that keeps attributing to them, so links already shared with it
        don't break; picking one of your own old codes again just reclaims
        it."""
        code = normalize_custom_referral_code(raw_code)
        current = user.referral_code
        if current is not None and current.lower() == code:
            if current != code:
                user.referral_code = code
                self.session.add(user)
                await self.session.flush()
            return code

        owner = await self._find_code_owner(code)
        if owner is not None and owner[0] != user.id:
            raise _conflict("referral_code_taken", "That referral code is already taken.")

        if owner is not None:
            # Reclaiming one of this user's own previous codes.
            own_alias = (
                await self.session.execute(
                    select(ReferralCodeAlias).where(
                        ReferralCodeAlias.user_id == user.id,
                        func.lower(ReferralCodeAlias.code) == code,
                    )
                )
            ).scalar_one()
            await self.session.delete(own_alias)
        elif current is not None:
            alias_count = (
                await self.session.execute(
                    select(func.count())
                    .select_from(ReferralCodeAlias)
                    .where(ReferralCodeAlias.user_id == user.id)
                )
            ).scalar_one()
            if alias_count >= MAX_REFERRAL_CODE_ALIASES:
                raise bad_request(
                    "referral_code_change_limit",
                    "You've changed your referral code too many times. Contact support to "
                    "change it again.",
                )

        if current is not None:
            self.session.add(ReferralCodeAlias(user_id=user.id, code=current))
        user.referral_code = code
        self.session.add(user)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            # Lost a race with someone else claiming the same code.
            raise _conflict(
                "referral_code_taken", "That referral code is already taken."
            ) from exc
        return code

    async def attribute_signup(self, referred_user: User, referral_code: str | None) -> None:
        """Record signup-time referral attribution. Called once, right after
        registration. An unknown/invalid code is never an error -- it just
        means no attribution happens, so a typo'd, expired, or fabricated
        `?ref=` value never blocks signup. Fixed permanently once recorded;
        nothing later ever re-attributes or moves a Referral row.

        NOTE on abuse: this alone does not stop someone from referring
        themselves through a *second* account (self-referral is only
        rejected when the two ids are literally identical, which can't
        happen before the referred account exists anyway). See the HYP-65
        report for why that multi-account vector is flagged rather than
        closed here.
        """
        if not referral_code:
            return
        # HYP-93: case-insensitive, and a user's previous (aliased) codes
        # still attribute to them, so a link shared before they picked a
        # custom code keeps working.
        owner = await self._find_code_owner(referral_code)
        if owner is None:
            return
        referrer_id, code = owner
        if referrer_id == referred_user.id:
            return
        existing = (
            await self.session.execute(
                select(Referral).where(Referral.referred_user_id == referred_user.id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        self.session.add(
            Referral(
                referrer_user_id=referrer_id,
                referred_user_id=referred_user.id,
                referral_code_used=code,
            )
        )
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError:
            # Lost a race with a concurrent attribution attempt for the same
            # brand-new user (referred_user_id is unique) -- the other one
            # already recorded it.
            pass

    async def credit_for_payment(
        self,
        *,
        referred_user_id: uuid.UUID,
        amount_cents: int,
        source_id: str,
        rate_cents_per_token: Decimal,
    ) -> None:
        """Credits the REFERRER's HYP-66 wallet 10% of `amount_cents` (a
        Stripe invoice's amount_paid, or a wallet top-up bundle's own price),
        converted to bonus tokens at `rate_cents_per_token`. No-op if the
        payer was never referred, the referral's 12-month earn-out window has
        elapsed, or `source_id` already earned a credit -- idempotent against
        a retried webhook delivery, the same two-layer pattern (outer
        StripeWebhookEvent dedupe + an inner id check) HYP-66 uses for wallet
        top-up purchases.
        """
        if amount_cents <= 0 or rate_cents_per_token <= 0:
            return
        referral = (
            await self.session.execute(
                select(Referral).where(Referral.referred_user_id == referred_user_id)
            )
        ).scalar_one_or_none()
        if referral is None:
            return
        if utcnow() - as_utc(referral.created_at) > REFERRAL_EARNOUT_WINDOW:
            return
        existing = (
            await self.session.execute(
                select(ReferralEarning).where(ReferralEarning.source_id == source_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return

        bonus_cents = Decimal(amount_cents) * REFERRAL_SHARE_RATE
        tokens = int((bonus_cents / rate_cents_per_token).to_integral_value(rounding=ROUND_FLOOR))
        if tokens <= 0:
            return

        wallet = await self._lock_or_create_wallet(referral.referrer_user_id)
        wallet.balance_tokens += tokens
        self.session.add(wallet)
        self.session.add(
            WalletTransaction(
                user_id=referral.referrer_user_id,
                delta_tokens=tokens,
                kind="referral_bonus",
            )
        )
        self.session.add(
            ReferralEarning(
                referral_id=referral.id,
                source_id=source_id,
                referred_user_id=referred_user_id,
                referrer_user_id=referral.referrer_user_id,
                tokens_credited=tokens,
                usd_equivalent_cents=int(bonus_cents.to_integral_value(rounding=ROUND_FLOOR)),
            )
        )
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError:
            # Lost a race with a concurrent delivery of the same payment
            # event; the other one already recorded this source_id.
            pass

    async def summary(self, user: User) -> dict[str, Any]:
        code = await self.ensure_referral_code(user)
        total_referrals = (
            await self.session.execute(
                select(func.count()).select_from(Referral).where(Referral.referrer_user_id == user.id)
            )
        ).scalar_one()
        total_tokens_earned = (
            await self.session.execute(
                select(func.coalesce(func.sum(ReferralEarning.tokens_credited), 0)).where(
                    ReferralEarning.referrer_user_id == user.id
                )
            )
        ).scalar_one()
        recent = (
            (
                await self.session.execute(
                    select(ReferralEarning)
                    .where(ReferralEarning.referrer_user_id == user.id)
                    .order_by(ReferralEarning.created_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        return {
            "referral_code": code,
            "referral_link": self.referral_link(code),
            "total_referrals": int(total_referrals),
            "total_tokens_earned": int(total_tokens_earned),
            "recent_earnings": list(recent),
        }

    async def _lock_or_create_wallet(self, user_id: uuid.UUID) -> UserWallet:
        """Mirrors app/billing/service.py's and app/usage/service.py's copies
        of this same helper -- kept as its own small copy here too rather
        than factored out, consistent with how it's already duplicated
        across those two modules."""
        wallet = (
            await self.session.execute(
                select(UserWallet).where(UserWallet.user_id == user_id).with_for_update()
            )
        ).scalar_one_or_none()
        if wallet is not None:
            return wallet
        wallet = UserWallet(user_id=user_id, balance_tokens=0)
        self.session.add(wallet)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError:
            wallet = (
                await self.session.execute(
                    select(UserWallet).where(UserWallet.user_id == user_id).with_for_update()
                )
            ).scalar_one()
        return wallet
