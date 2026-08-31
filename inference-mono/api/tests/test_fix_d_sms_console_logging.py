"""Regression tests for D1: ConsoleSMSProvider crashed on every call.

`logger.info("sms.console", extra={"phone_number": ..., "message": message})`
used `"message"`, a reserved `LogRecord` attribute. Stdlib logging's
`Logger.makeRecord` explicitly rejects that key:

    if (key in ["message", "asctime"]) or (key in rv.__dict__):
        raise KeyError("Attempt to overwrite %r in LogRecord" % key)

So every call to `ConsoleSMSProvider.send_sms` raised `KeyError`, and since
`get_sms_provider()` falls back to the console provider for any unset/
unrecognized `SMS_PROVIDER`, `POST /auth/phone/request-otp` returned HTTP 500
for every dev/test/misconfigured deployment -- phone login, phone linking,
and phone signup were entirely unusable.

IMPORTANT: pytest's own log-capture plugin leaves the root logger's
effective level at WARNING for `app.sms.providers` by default, which means
`logger.info(...)` short-circuits via `isEnabledFor()` *before* it ever
touches the `extra` dict -- masking the bug entirely if you just call
`client.post("/auth/phone/request-otp", ...)` under the ambient test
config. That is why the pre-existing `test_phone_otp_*` tests in
`test_phone_auth.py` passed even with the bug in place. These tests force
the specific logger to INFO so the real code path (as it runs in the live
server, where `configure_logging` sets the root logger to INFO/DEBUG) is
actually exercised.
"""

import json
import logging

from app.core.logging import JsonFormatter
from app.sms.providers import ConsoleSMSProvider


async def test_console_sms_provider_logs_without_raising(caplog):
    logger = logging.getLogger("app.sms.providers")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        with caplog.at_level(logging.INFO, logger="app.sms.providers"):
            # Must not raise. Before the fix this raised:
            #   KeyError: "Attempt to overwrite 'message' in LogRecord"
            await ConsoleSMSProvider().send_sms(
                "+14155550123", "Your verification code is 654321."
            )
    finally:
        logger.setLevel(previous_level)

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.phone_number == "+14155550123"


async def test_otp_code_is_discoverable_in_the_console_log(caplog):
    """The console provider IS the OTP delivery mechanism for dev/test --
    the code must actually show up in the emitted log record, not just
    avoid crashing.
    """
    logger = logging.getLogger("app.sms.providers")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        with caplog.at_level(logging.INFO, logger="app.sms.providers"):
            await ConsoleSMSProvider().send_sms(
                "+14155559999", "Your verification code is 042817."
            )
    finally:
        logger.setLevel(previous_level)

    record = caplog.records[0]
    # The OTP text lives under the "text" key (matching the established
    # app/email/providers.py pattern), not "message".
    assert "042817" in record.text

    # Also prove it survives the production JSON formatter end-to-end, since
    # that's what actually renders to the console in the running service.
    rendered = json.loads(JsonFormatter().format(record))
    assert rendered["extra"]["text"] == "Your verification code is 042817."
    assert rendered["extra"]["phone_number"] == "+14155559999"


async def test_console_sms_provider_survives_a_future_logging_collision(caplog):
    """Defensive-guard regression: even if a future edit reintroduces a
    reserved-attribute collision (or any other logging misconfiguration),
    ConsoleSMSProvider.send_sms must not raise -- an observability bug must
    never again 500 an unauthenticated auth endpoint. The failure is still
    surfaced (as a WARNING, with a traceback) rather than silently vanishing.
    """
    logger = logging.getLogger("app.sms.providers")
    previous_level = logger.level
    logger.setLevel(logging.INFO)

    calls = {"n": 0}
    real_info = logger.info

    def flaky_info(msg, *args, **kwargs):
        calls["n"] += 1
        raise KeyError("Attempt to overwrite 'asctime' in LogRecord")

    logger.info = flaky_info
    try:
        with caplog.at_level(logging.WARNING, logger="app.sms.providers"):
            await ConsoleSMSProvider().send_sms("+14155550000", "Your verification code is 999999.")
    finally:
        logger.info = real_info
        logger.setLevel(previous_level)

    assert calls["n"] == 1
    assert any(
        record.levelno == logging.WARNING and "999999" in record.getMessage()
        for record in caplog.records
    )
