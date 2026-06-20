from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.rate_limit_url,
    default_limits=[],
    headers_enabled=False,
    swallow_errors=settings.rate_limit_fail_open,
    enabled=settings.rate_limit_enabled,
)
