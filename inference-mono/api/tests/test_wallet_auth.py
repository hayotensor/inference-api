import jwt
from eth_account import Account
from eth_account.messages import encode_defunct

from app.core.config import settings


def _sign(message: str, private_key) -> str:
    signed = Account.sign_message(encode_defunct(text=message), private_key=private_key)
    sig = signed.signature.hex()
    return sig if sig.startswith("0x") else "0x" + sig


async def _login_with_wallet(client, account):
    nonce = await client.post("/auth/wallet/nonce", json={"address": account.address})
    assert nonce.status_code == 200, nonce.text
    message = nonce.json()["message"]
    verify = await client.post(
        "/auth/wallet/verify",
        json={
            "address": account.address,
            "message": message,
            "signature": _sign(message, account.key),
        },
    )
    return verify


async def test_wallet_login_creates_user_and_issues_tokens(client):
    account = Account.create()
    verify = await _login_with_wallet(client, account)
    assert verify.status_code == 200, verify.text
    body = verify.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["auth_method"] == "wallet"
    assert body["user"]["wallet_verified_at"] is not None
    # Synthetic email must not leak the raw address.
    assert account.address.lower()[2:] not in body["user"]["email"].lower()


async def test_wallet_verified_user_can_mint_api_key(client):
    account = Account.create()
    verify = await _login_with_wallet(client, account)
    access_token = verify.json()["access_token"]

    created = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "openwebui"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["key"].startswith("sk_")


async def test_returning_wallet_logs_into_same_account(client):
    account = Account.create()
    first = await _login_with_wallet(client, account)
    second = await _login_with_wallet(client, account)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["user"]["id"] == second.json()["user"]["id"]


async def test_wallet_wrong_signature_rejected(client):
    account = Account.create()
    attacker = Account.create()
    nonce = await client.post("/auth/wallet/nonce", json={"address": account.address})
    message = nonce.json()["message"]
    verify = await client.post(
        "/auth/wallet/verify",
        json={
            "address": account.address,
            "message": message,
            "signature": _sign(message, attacker.key),  # signed by the wrong key
        },
    )
    assert verify.status_code == 400
    assert verify.json()["detail"]["code"] == "invalid_wallet_signature"


async def test_wallet_nonce_single_use(client):
    account = Account.create()
    nonce = await client.post("/auth/wallet/nonce", json={"address": account.address})
    message = nonce.json()["message"]
    signature = _sign(message, account.key)
    payload = {"address": account.address, "message": message, "signature": signature}

    first = await client.post("/auth/wallet/verify", json=payload)
    assert first.status_code == 200
    replay = await client.post("/auth/wallet/verify", json=payload)
    assert replay.status_code == 400
    assert replay.json()["detail"]["code"] == "invalid_wallet_nonce"


async def test_sso_cookie_token_is_valid_jwt(client):
    account = Account.create()
    login = await _login_with_wallet(client, account)
    access_token = login.json()["access_token"]
    user_email = login.json()["user"]["email"]

    resp = await client.post(
        "/auth/sso/cookie",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200, resp.text
    sso_token = resp.json()["sso_token"]

    decoded = jwt.decode(
        sso_token,
        settings.sso_secret.get_secret_value(),
        algorithms=["HS256"],
        audience=settings.sso_audience,
    )
    assert decoded["email"] == user_email
    assert decoded["sub"] == login.json()["user"]["id"]


async def test_sso_cookie_requires_auth(client):
    resp = await client.post("/auth/sso/cookie")
    assert resp.status_code == 401
