import logging
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import aiosmtplib
import boto3
import httpx
from starlette.concurrency import run_in_threadpool

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboundEmail:
    to: str
    subject: str
    text: str
    html: str | None = None


class EmailProvider(Protocol):
    async def send_email(self, message: OutboundEmail) -> None:
        ...


class ConsoleEmailProvider:
    async def send_email(self, message: OutboundEmail) -> None:
        logger.info(
            "email.console",
            extra={"to": message.to, "subject": message.subject, "text": message.text},
        )


class SMTPEmailProvider:
    async def send_email(self, message: OutboundEmail) -> None:
        if not settings.smtp_host:
            raise RuntimeError("SMTP_HOST is required for smtp email provider")
        email = EmailMessage()
        email["From"] = settings.email_from
        email["To"] = message.to
        email["Subject"] = message.subject
        email.set_content(message.text)
        if message.html:
            email.add_alternative(message.html, subtype="html")
        await aiosmtplib.send(
            email,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=(
                settings.smtp_password.get_secret_value() if settings.smtp_password else None
            ),
            start_tls=settings.smtp_starttls,
        )


class SendGridEmailProvider:
    async def send_email(self, message: OutboundEmail) -> None:
        if not settings.sendgrid_api_key:
            raise RuntimeError("SENDGRID_API_KEY is required for sendgrid email provider")
        payload = {
            "personalizations": [{"to": [{"email": message.to}]}],
            "from": {"email": settings.email_from},
            "subject": message.subject,
            "content": [
                {"type": "text/plain", "value": message.text},
                *(
                    [{"type": "text/html", "value": message.html}]
                    if message.html
                    else []
                ),
            ],
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.sendgrid_api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()


class ResendEmailProvider:
    async def send_email(self, message: OutboundEmail) -> None:
        if not settings.resend_api_key:
            raise RuntimeError("RESEND_API_KEY is required for resend email provider")
        payload = {
            "from": settings.email_from,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
            "html": message.html,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={"Authorization": f"Bearer {settings.resend_api_key.get_secret_value()}"},
            )
            response.raise_for_status()


class SESEmailProvider:
    async def send_email(self, message: OutboundEmail) -> None:
        client = boto3.client("ses", region_name=settings.aws_region)
        body = {"Text": {"Data": message.text}}
        if message.html:
            body["Html"] = {"Data": message.html}
        await run_in_threadpool(
            client.send_email,
            Source=settings.email_from,
            Destination={"ToAddresses": [message.to]},
            Message={"Subject": {"Data": message.subject}, "Body": body},
        )


def get_email_provider() -> EmailProvider:
    match settings.email_provider:
        case "smtp":
            return SMTPEmailProvider()
        case "sendgrid":
            return SendGridEmailProvider()
        case "resend":
            return ResendEmailProvider()
        case "ses":
            return SESEmailProvider()
        case _:
            return ConsoleEmailProvider()
