import uuid
from datetime import datetime

from fastapi_users import schemas
from pydantic import Field

# Keep in sync with the `users.full_name` column, which is `String(255)`
# (see app/db/models/user.py). Without this bound, an oversized value passes
# validation and reaches Postgres, which raises an unhandled
# StringDataRightTruncationError (a 500 with a leaked traceback) instead of a
# clean 422.
FULL_NAME_MAX_LENGTH = 255


class UserRead(schemas.BaseUser[uuid.UUID]):
    full_name: str | None = None
    phone_number: str | None = None
    phone_verified_at: datetime | None = None
    wallet_verified_at: datetime | None = None
    created_at: datetime
    # Non-empty when the account is linked to an OAuth provider (e.g. "google").
    # The UI uses this to lock the email field: it's that provider's verified
    # identity, not something to change independently in Settings.
    oauth_providers: list[str] = Field(default_factory=list)


class UserCreate(schemas.BaseUserCreate):
    full_name: str | None = Field(default=None, max_length=FULL_NAME_MAX_LENGTH)


class UserUpdate(schemas.BaseUserUpdate):
    full_name: str | None = Field(default=None, max_length=FULL_NAME_MAX_LENGTH)
