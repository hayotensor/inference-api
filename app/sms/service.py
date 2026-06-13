from app.sms.providers import get_sms_provider


class SMSService:
    def __init__(self) -> None:
        self.provider = get_sms_provider()

    async def send_otp(self, phone_number: str, code: str) -> None:
        await self.provider.send_sms(phone_number, f"Your verification code is {code}.")
