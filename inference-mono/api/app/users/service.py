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
        if payload.password is not None:
            # `UserUpdate` inherits an optional `password` field from
            # `fastapi_users.schemas.BaseUserUpdate`, and `UserManager.update()`
            # would happily hash and set it -- with no proof the caller knows the
            # *current* password, and without revoking the account's other
            # sessions the way `change_password`/`reset_password` do. A merely
            # still-live (e.g. stolen/XSS'd) access token would be enough to
            # silently lock the real owner out. Route password changes through
            # POST /auth/change-password instead, which checks both.
            raise bad_request(
                "use_change_password_endpoint",
                "Changing your password requires your current password. "
                "Use POST /auth/change-password instead.",
            )
        if (
            payload.email is not None
            and payload.email.lower() != user.email.lower()
            and user.oauth_accounts
        ):
            raise bad_request(
                "email_locked_to_oauth",
                "Your email is managed by your linked sign-in provider "
                f"({', '.join(user.oauth_providers)}) and can't be changed here.",
            )
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
