from app.core.config import settings
from app.email.providers import OutboundEmail, get_email_provider


class EmailService:
    def __init__(self) -> None:
        self.provider = get_email_provider()

    async def send_verification_email(self, email: str, token: str) -> None:
        await self.provider.send_email(
            OutboundEmail(
                to=email,
                subject="Verify your email",
                text=(
                    "Verify your email by sending this token to "
                    f"POST {settings.api_base_url}/auth/verify-email: {token}"
                ),
                html=(
                    "<p>Verify your email by sending this token to "
                    f"<code>POST {settings.api_base_url}/auth/verify-email</code>:</p>"
                    f"<pre>{token}</pre>"
                ),
            )
        )

    async def send_password_reset_email(self, email: str, token: str) -> None:
        await self.provider.send_email(
            OutboundEmail(
                to=email,
                subject="Reset your password",
                text=(
                    "Reset your password by sending this token to "
                    f"POST {settings.api_base_url}/auth/reset-password: {token}"
                ),
                html=(
                    "<p>Reset your password by sending this token to "
                    f"<code>POST {settings.api_base_url}/auth/reset-password</code>:</p>"
                    f"<pre>{token}</pre>"
                ),
            )
        )
