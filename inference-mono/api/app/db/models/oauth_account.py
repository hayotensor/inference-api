import uuid

from fastapi_users.db import SQLAlchemyBaseOAuthAccountTableUUID
from sqlalchemy import ForeignKey, Uuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.db.base import Base


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    __tablename__ = "oauth_accounts"

    @declared_attr
    def user_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            Uuid(as_uuid=True),
            ForeignKey("users.id", ondelete="cascade"),
            nullable=False,
            index=True,
        )
