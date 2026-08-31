from urllib.parse import quote

from app.core.config import settings
from app.email.providers import OutboundEmail, get_email_provider


def _app_link(path: str, token: str) -> str:
    base = str(settings.app_url).rstrip("/")
    return f"{base}/{path.lstrip('/')}?token={quote(token, safe='')}"


class EmailService:
    def __init__(self) -> None:
        self.provider = get_email_provider()

    async def send_verification_email(self, email: str, token: str) -> None:
        link = _app_link("verify-email", token)
        await self.provider.send_email(
            OutboundEmail(
                to=email,
                subject="Verify your email",
                text=(
                    "Welcome to Talaris. Confirm your email address by opening this link:\n\n"
                    f"{link}\n\n"
                    "If you did not create an account you can ignore this message."
                ),
                html=(
                    "<p>Welcome to Talaris. Confirm your email address:</p>"
                    f'<p><a href="{link}">Verify your email</a></p>'
                    f'<p>Or paste this link into your browser:<br><code>{link}</code></p>'
                    "<p>If you did not create an account you can ignore this message.</p>"
                ),
            )
        )

    async def send_password_reset_email(self, email: str, token: str) -> None:
        link = _app_link("reset-password", token)
        await self.provider.send_email(
            OutboundEmail(
                to=email,
                subject="Reset your password",
                text=(
                    "Reset your Talaris password by opening this link:\n\n"
                    f"{link}\n\n"
                    "If you did not request a password reset you can ignore this message."
                ),
                html=(
                    "<p>Reset your Talaris password:</p>"
                    f'<p><a href="{link}">Choose a new password</a></p>'
                    f'<p>Or paste this link into your browser:<br><code>{link}</code></p>'
                    "<p>If you did not request a password reset you can ignore this message.</p>"
                ),
            )
        )
