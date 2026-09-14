"""HYP-45: API key model scoping (control plane).

An API key can optionally be restricted to a specific allowlist of model ids
(``api_keys.allowed_models``, added by
``alembic/versions/20260908_0012_api_key_allowed_models.py``). ``None``/empty
means unrestricted -- the key reaches every model, exactly as every key
created before this column existed -- matching how an unset ``scopes``
default would behave if that were allowed. Enforcement of the allowlist on
the actual inference request path lives in the serving plane (see
``inference_api/tests/test_hyp45_api_key_model_scoping.py``); these tests
cover the control-plane management surface: create/update validation,
persistence, and the ``GET /api-keys/available-models`` picker endpoint.
"""

from __future__ import annotations

import uuid

from app.db.models.miner import Miner, MinerModel
from app.db.session import async_session_maker

from tests.test_api_keys import create_phone_verified_session


async def _seed_miner_model(model_id: str, *, attestation_status: str = "attested") -> None:
    async with async_session_maker() as session:
        miner = Miner(
            id=uuid.uuid4(),
            hotkey=uuid.uuid4().hex,
            tee_endpoint="http://miner.test",
            attestation_status=attestation_status,
        )
        session.add(miner)
        await session.flush()
        session.add(
            MinerModel(
                id=uuid.uuid4(),
                miner_id=miner.id,
                model_id=model_id,
                attestation_status=attestation_status,
                loaded=True,
            )
        )
        await session.commit()


async def test_key_created_without_allowed_models_is_unrestricted(client, monkeypatch):
    """The default -- no ``allowed_models`` in the payload -- must persist as
    ``None``, the backward-compatible "reaches every model" state."""
    session = await create_phone_verified_session(client, monkeypatch)
    access_token = session["access_token"]

    created = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "unrestricted", "environment": "test"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["allowed_models"] is None


async def test_key_can_be_scoped_to_specific_models_on_create(client, monkeypatch):
    session = await create_phone_verified_session(client, monkeypatch)
    access_token = session["access_token"]

    created = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "name": "scoped",
            "environment": "test",
            "allowed_models": ["small-model", "medium-model"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["allowed_models"] == ["small-model", "medium-model"]

    listed = await client.get("/api-keys", headers={"Authorization": f"Bearer {access_token}"})
    assert listed.status_code == 200
    [key_body] = [k for k in listed.json() if k["name"] == "scoped"]
    assert key_body["allowed_models"] == ["small-model", "medium-model"]


async def test_allowed_models_are_stripped_and_deduplicated(client, monkeypatch):
    session = await create_phone_verified_session(client, monkeypatch)
    access_token = session["access_token"]

    created = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "name": "messy",
            "environment": "test",
            "allowed_models": ["small-model", " small-model ", "medium-model", "medium-model"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["allowed_models"] == ["small-model", "medium-model"]


async def test_empty_allowed_models_list_normalizes_to_unrestricted(client, monkeypatch):
    session = await create_phone_verified_session(client, monkeypatch)
    access_token = session["access_token"]

    created = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "empty-list", "environment": "test", "allowed_models": []},
    )
    assert created.status_code == 201, created.text
    assert created.json()["allowed_models"] is None


async def test_blank_allowed_model_entry_is_rejected(client, monkeypatch):
    session = await create_phone_verified_session(client, monkeypatch)
    access_token = session["access_token"]

    created = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "blank-entry", "environment": "test", "allowed_models": ["small-model", "   "]},
    )
    assert created.status_code == 422


async def test_allowed_models_can_be_updated_after_creation(client, monkeypatch):
    session = await create_phone_verified_session(client, monkeypatch)
    access_token = session["access_token"]

    created = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "will-be-scoped", "environment": "test"},
    )
    assert created.json()["allowed_models"] is None
    key_id = created.json()["id"]

    scoped_down = await client.patch(
        f"/api-keys/{key_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"allowed_models": ["large-model"]},
    )
    assert scoped_down.status_code == 200, scoped_down.text
    assert scoped_down.json()["allowed_models"] == ["large-model"]

    widened_back = await client.patch(
        f"/api-keys/{key_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"allowed_models": None},
    )
    assert widened_back.status_code == 200, widened_back.text
    assert widened_back.json()["allowed_models"] is None


async def test_available_models_lists_distinct_ids_from_non_terminal_miners(client, monkeypatch):
    session = await create_phone_verified_session(client, monkeypatch)
    access_token = session["access_token"]

    await _seed_miner_model("alpha-model")
    await _seed_miner_model("beta-model")
    await _seed_miner_model("beta-model")  # second miner serving the same model: no duplicate
    await _seed_miner_model("revoked-only-model", attestation_status="revoked")

    response = await client.get(
        "/api-keys/available-models", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == ["alpha-model", "beta-model"]
