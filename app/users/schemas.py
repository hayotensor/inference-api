import uuid
from datetime import datetime

from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    full_name: str | None = None
    phone_number: str | None = None
    phone_verified_at: datetime | None = None
    created_at: datetime


class UserCreate(schemas.BaseUserCreate):
    full_name: str | None = None


class UserUpdate(schemas.BaseUserUpdate):
    full_name: str | None = None
