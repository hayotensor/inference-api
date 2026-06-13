from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.models.phone_verification_code import PhoneVerificationCode
from app.db.session import async_session_maker


async def test_phone_otp_account_creation_link_and_unlink(client, monkeypatch):
    monkeypatch.setattr("app.auth.phone.generate_otp_code", lambda: "123456")

    request_otp = await client.post(
        "/auth/phone/request-otp",
        json={"phone_number": "+14155552671", "purpose": "login"},
    )
    assert request_otp.status_code == 202

    verify = await client.post(
        "/auth/phone/verify-otp",
        json={
            "phone_number": "+14155552671",
            "code": "123456",
            "full_name": "Phone User",
        },
    )
    assert verify.status_code == 200
    body = verify.json()
    assert body["access_token"]
    assert body["user"]["phone_number"] == "+14155552671"

    access_token = body["access_token"]
    link_otp = await client.post(
        "/auth/phone/request-otp",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"phone_number": "+14155552672", "purpose": "link"},
    )
    assert link_otp.status_code == 202

    link = await client.post(
        "/auth/phone/link",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"phone_number": "+14155552672", "code": "123456"},
    )
    assert link.status_code == 200
    assert link.json()["phone_number"] == "+14155552672"

    unlink = await client.post(
        "/auth/phone/unlink",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert unlink.status_code == 403


async def test_phone_otp_wrong_code_rejected(client, monkeypatch):
    monkeypatch.setattr("app.auth.phone.generate_otp_code", lambda: "123456")
    await client.post(
        "/auth/phone/request-otp",
        json={"phone_number": "+14155552673", "purpose": "login"},
    )
    response = await client.post(
        "/auth/phone/verify-otp",
        json={"phone_number": "+14155552673", "code": "000000"},
    )
    assert response.status_code == 400


async def test_phone_otp_attempts_persist_and_lock_out(client, monkeypatch):
    monkeypatch.setattr("app.auth.phone.generate_otp_code", lambda: "123456")
    monkeypatch.setattr(settings, "otp_max_attempts", 2)
    await client.post(
        "/auth/phone/request-otp",
        json={"phone_number": "+14155552674", "purpose": "login"},
    )

    first = await client.post(
        "/auth/phone/verify-otp",
        json={"phone_number": "+14155552674", "code": "000000"},
    )
    assert first.status_code == 400
    async with async_session_maker() as session:
        record = (
            await session.execute(PhoneVerificationCode.__table__.select())
        ).first()
        assert record.attempts == 1
        assert record.consumed_at is None

    second = await client.post(
        "/auth/phone/verify-otp",
        json={"phone_number": "+14155552674", "code": "111111"},
    )
    assert second.status_code == 400
    async with async_session_maker() as session:
        record = (
            await session.execute(PhoneVerificationCode.__table__.select())
        ).first()
        assert record.attempts == 2
        assert record.consumed_at is not None

    correct_after_lockout = await client.post(
        "/auth/phone/verify-otp",
        json={"phone_number": "+14155552674", "code": "123456"},
    )
    assert correct_after_lockout.status_code == 400


async def test_phone_invalid_number_returns_400(client):
    response = await client.post(
        "/auth/phone/request-otp",
        json={"phone_number": "not-a-phone-number", "purpose": "login"},
    )
    assert response.status_code == 400


async def test_phone_account_ignores_user_supplied_email_and_hides_digits(client, monkeypatch):
    monkeypatch.setattr("app.auth.phone.generate_otp_code", lambda: "123456")
    await client.post(
        "/auth/phone/request-otp",
        json={"phone_number": "+14155552675", "purpose": "login"},
    )
    response = await client.post(
        "/auth/phone/verify-otp",
        json={
            "phone_number": "+14155552675",
            "code": "123456",
            "email": "victim@example.com",
        },
    )
    assert response.status_code == 200
    email = response.json()["user"]["email"]
    assert email != "victim@example.com"
    assert "14155552675" not in email


async def test_phone_otp_request_rate_limit_can_be_enabled(client, monkeypatch):
    monkeypatch.setattr("app.auth.phone.generate_otp_code", lambda: "123456")
    previous_enabled = limiter.enabled
    limiter.enabled = True
    try:
        statuses = []
        for _ in range(4):
            response = await client.post(
                "/auth/phone/request-otp",
                json={"phone_number": "+14155552676", "purpose": "login"},
            )
            statuses.append(response.status_code)
        assert statuses[:3] == [202, 202, 202]
        assert statuses[3] == 429
    finally:
        limiter.enabled = previous_enabled
