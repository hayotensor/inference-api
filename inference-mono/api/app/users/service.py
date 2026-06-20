from fastapi import Request
from fastapi_users.exceptions import InvalidPasswordException, UserAlreadyExists
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_log
from app.auth.service import UserManager
from app.core.errors import bad_request
from app.db.models.user import User
from app.users.schemas import UserUpdate


class UserService:
    def __init__(self, session: AsyncSession, user_manager: UserManager) -> None:
        self.session = session
        self.user_manager = user_manager

    async def update_me(self, user: User, payload: UserUpdate, request: Request) -> User:
        try:
            updated = await self.user_manager.update(payload, user, safe=True, request=request)
        except UserAlreadyExists as exc:
            raise bad_request("email_exists", "A user with this email already exists") from exc
        except InvalidPasswordException as exc:
            raise bad_request("invalid_password", exc.reason) from exc
        await write_audit_log(self.session, "user.update_me", user_id=user.id, request=request)
        return updated

    async def delete_me(self, user: User, request: Request) -> None:
        await write_audit_log(self.session, "user.delete_me", user_id=user.id, request=request)
        await self.user_manager.delete(user, request=request)
