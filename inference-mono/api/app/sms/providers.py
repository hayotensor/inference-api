import logging
from typing import Protocol

import boto3
import httpx
from starlette.concurrency import run_in_threadpool

from app.core.config import settings

logger = logging.getLogger(__name__)


class SMSProvider(Protocol):
    async def send_sms(self, phone_number: str, message: str) -> None:
        ...


class ConsoleSMSProvider:
    async def send_sms(self, phone_number: str, message: str) -> None:
        # NOTE: the extra key is "text", not "message" -- "message" is a
        # reserved `LogRecord` attribute, and passing it via `extra=` makes
        # stdlib logging raise `KeyError: "Attempt to overwrite 'message' in
        # LogRecord"` on every call. This mirrors the established pattern in
        # app/email/providers.py's ConsoleEmailProvider (extra={"to", ...,
        # "text": message.text}).
        try:
            logger.info(
                "sms.console", extra={"phone_number": phone_number, "text": message}
            )
        except Exception:
            # Defensive guard: this provider's only job is to make the OTP
            # discoverable in the console for dev/test. A future logging
            # mistake (another reserved-key collision, a bad formatter, etc.)
            # must never again turn into an unhandled 500 on an
            # unauthenticated auth endpoint. Fall back to an unstructured
            # log record -- the OTP stays discoverable, and the logging bug
            # itself is still surfaced (at WARNING, with a traceback) instead
            # of silently vanishing.
            logger.warning(
                "sms.console.log_failed phone_number=%s text=%s",
                phone_number,
                message,
                exc_info=True,
            )


class TwilioSMSProvider:
    async def send_sms(self, phone_number: str, message: str) -> None:
        if not (
            settings.twilio_account_sid
            and settings.twilio_auth_token
            and settings.twilio_from_number
        ):
            raise RuntimeError("Twilio SMS provider requires account SID, auth token, and from number")
        url = (
            "https://api.twilio.com/2010-04-01/Accounts/"
            f"{settings.twilio_account_sid}/Messages.json"
        )
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                url,
                data={"To": phone_number, "From": settings.twilio_from_number, "Body": message},
                auth=(settings.twilio_account_sid, settings.twilio_auth_token.get_secret_value()),
            )
            response.raise_for_status()


class AWSSNSSMSProvider:
    async def send_sms(self, phone_number: str, message: str) -> None:
        client = boto3.client("sns", region_name=settings.aws_region)
        await run_in_threadpool(client.publish, PhoneNumber=phone_number, Message=message)


def get_sms_provider() -> SMSProvider:
    match settings.sms_provider:
        case "twilio":
            return TwilioSMSProvider()
        case "aws_sns":
            return AWSSNSSMSProvider()
        case _:
            return ConsoleSMSProvider()
