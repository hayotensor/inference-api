import uuid

from fastapi_users.db import SQLAlchemyBaseOAuthAccountTableUUID
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.db.base import Base


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    __tablename__ = "oauth_accounts"

    @declared_attr
    def user_id(cls) -> Mapped[uuid.UUID]:
        # Must use the same GUID type as User.id (not plain sqlalchemy.Uuid):
        # both render as Postgres's native `uuid` column in production, so this
        # is a no-op there, but on SQLite Uuid renders as unhyphenated CHAR(32)
        # while GUID renders as hyphenated CHAR(36) -- a mismatch that silently
        # breaks the `users.id = oauth_accounts.user_id` join (relationship
        # loads, e.g. `user.oauth_accounts`, return empty) since SQLite has no
        # native UUID type to normalize the comparison.
        return mapped_column(
            GUID,
            ForeignKey("users.id", ondelete="cascade"),
            nullable=False,
            index=True,
        )
