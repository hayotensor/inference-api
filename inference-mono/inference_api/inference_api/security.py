import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from inference_api.config import settings


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


def generate_uuid() -> uuid.UUID:
    return uuid.uuid4()
