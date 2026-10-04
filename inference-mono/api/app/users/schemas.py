import uuid
from datetime import datetime

from fastapi_users import schemas
from pydantic import EmailStr, Field

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
    # False for phone-only/wallet-only accounts, whose `email` is a synthetic
    # placeholder with no real inbox. The UI uses this to hide "send
    # verification email" for them instead of firing it into a void.
    has_verifiable_email: bool = True


class UserCreate(schemas.BaseUserCreate):
    full_name: str | None = Field(default=None, max_length=FULL_NAME_MAX_LENGTH)


class UserCreatePublic(schemas.CreateUpdateDictModel):
    """Request-body schema for the public ``POST /auth/register`` route.

    Deliberately omits ``is_active``/``is_superuser``/``is_verified`` --
    unlike ``UserCreate`` (which extends ``schemas.BaseUserCreate`` and
    therefore *has* those fields), this is what actually gets published in
    the public OpenAPI schema. `UserManager.create(..., safe=True)` already
    strips those flags server-side via `CreateUpdateDictModel.create_update_dict`
    (see fastapi_users.schemas) even for `UserCreate`, so a caller sending
    them was never able to make them stick -- but they still showed up as
    documented, settable fields on the public register schema, which is the
    leak this schema closes.
    """

    email: EmailStr
    password: str
    full_name: str | None = Field(default=None, max_length=FULL_NAME_MAX_LENGTH)


class UserUpdate(schemas.BaseUserUpdate):
    full_name: str | None = Field(default=None, max_length=FULL_NAME_MAX_LENGTH)
