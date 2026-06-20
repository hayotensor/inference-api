import hashlib
import hmac
import secrets
import string
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import phonenumbers
from phonenumbers import NumberParseException

from app.core.config import settings

API_KEY_RANDOM_ALPHABET = string.ascii_letters + string.digits


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_past(value: datetime) -> bool:
    return as_utc(value) <= utcnow()


def expires_in(**kwargs: Any) -> datetime:
    return utcnow() + timedelta(**kwargs)


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def keyed_hash(secret: str) -> str:
    pepper = settings.secret_pepper.get_secret_value().encode("utf-8")
    return hmac.new(pepper, secret.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(phone_number: str, code: str, purpose: str) -> str:
    return keyed_hash(f"{purpose}:{phone_number}:{code}")


def generate_api_key(environment: str) -> tuple[str, str, str]:
    if environment not in {"live", "test"}:
        raise ValueError("environment must be live or test")
    random_part = "".join(secrets.choice(API_KEY_RANDOM_ALPHABET) for _ in range(32))
    full_key = f"sk_{environment}_{random_part}"
    return full_key, full_key[:16], full_key[-4:]


def generate_router_token() -> tuple[str, str, str]:
    random_part = "".join(secrets.choice(API_KEY_RANDOM_ALPHABET) for _ in range(40))
    full_token = f"rk_live_{random_part}"
    return full_token, full_token[:16], full_token[-4:]


def generate_uuid() -> uuid.UUID:
    return uuid.uuid4()


def normalize_phone_number(phone_number: str) -> str:
    try:
        parsed = phonenumbers.parse(phone_number, settings.default_phone_region)
    except NumberParseException as exc:
        raise ValueError("Invalid phone number") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("Invalid phone number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def synthetic_phone_email(phone_number: str) -> str:
    digest = keyed_hash(f"phone-email:{phone_number}")[:32]
    return f"phone_{digest}@{settings.phone_user_email_domain}"


def is_synthetic_phone_email(email: str) -> bool:
    local_part, _, domain = email.lower().partition("@")
    return local_part.startswith("phone_") and domain == settings.phone_user_email_domain.lower()
