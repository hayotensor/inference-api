"""Regression tests for HYP-23, HYP-24, HYP-31 -- the public API surface was
leaking more than intended: a broken CSP blanked out /docs and /redoc
(HYP-23), the public OpenAPI schema listed the /admin and /internal routers
(HYP-24), and the public register schema advertised admin-settable
is_superuser/is_verified/is_active flags (HYP-31).

See app/main.py (CSP), app/admin/routes.py + app/internal/routes.py
(include_in_schema=False), and app/users/schemas.py + app/auth/routes.py
(UserCreatePublic).
"""

from __future__ import annotations

PASSWORD = "correct-horse-123"


# --------------------------------------------------------------------- #
# HYP-23 -- CSP no longer blanks out /docs and /redoc
# --------------------------------------------------------------------- #


async def test_docs_renders_swagger_ui_not_an_empty_shell(client):
    response = await client.get("/docs")
    assert response.status_code == 200
    assert "swagger-ui" in response.text.lower()
    assert "SwaggerUIBundle" in response.text


async def test_redoc_renders_ui(client):
    response = await client.get("/redoc")
    assert response.status_code == 200
    assert "redoc" in response.text.lower()


async def test_docs_csp_does_not_block_its_own_script_source(client):
    response = await client.get("/docs")
    csp = response.headers["content-security-policy"]
    # The old CSP was a blanket `default-src 'none'` with no script-src carve
    # out at all, which silently blocked SwaggerUIBundle from the CDN. It's
    # fine for default-src to stay 'none' as long as script-src explicitly
    # allows wherever the page's own <script src> points.
    assert "default-src 'none'" in csp
    assert "script-src" in csp
    assert "'none'" not in csp.split("script-src", 1)[1].split(";", 1)[0]


async def test_redoc_csp_does_not_block_its_own_script_source(client):
    response = await client.get("/redoc")
    csp = response.headers["content-security-policy"]
    assert "script-src" in csp
    assert "'none'" not in csp.split("script-src", 1)[1].split(";", 1)[0]


async def test_openapi_json_still_served_and_unaffected(client):
    # Keep: this was never broken, must not regress.
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["openapi"]


async def test_ordinary_json_endpoints_keep_the_strict_default_csp(client):
    # The relaxation must be scoped to the docs paths only -- everything else
    # keeps the original hardened policy.
    response = await client.get("/health")
    assert response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"


# --------------------------------------------------------------------- #
# HYP-24 -- /admin and /internal are not in the public OpenAPI schema
# --------------------------------------------------------------------- #


async def test_openapi_schema_excludes_admin_and_internal_routers(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = set(response.json()["paths"])
    assert paths, "sanity check: schema should still list the product API"
    assert not any(p.startswith("/admin") for p in paths)
    assert not any(p.startswith("/internal") for p in paths)
    assert not any("chat-key" in p for p in paths)


async def test_unauth_admin_call_still_401s_despite_hidden_schema(client):
    # HYP-24 is a docs-surface fix only -- auth must be untouched.
    response = await client.get("/admin/me")
    assert response.status_code == 401


async def test_unauth_internal_call_still_rejected_despite_hidden_schema(client):
    response = await client.post(
        "/internal/users/by-email/chat-key", json={"email": "someone@example.com"}
    )
    assert response.status_code in (401, 403)


# --------------------------------------------------------------------- #
# HYP-31 -- public register schema no longer advertises privileged flags
# --------------------------------------------------------------------- #


async def test_register_schema_has_no_privileged_flags(client):
    response = await client.get("/openapi.json")
    schema = response.json()
    # HYP-65 added an optional `referral_code` field to the public register
    # body via `RegisterRequest(UserCreatePublic)` (app/auth/schemas.py) --
    # harmless and public, same category as `full_name`, so it's added to
    # `allowed` rather than weakening what this test actually guards: that
    # is_superuser/is_verified/is_active can never reach this schema.
    user_create = schema["components"]["schemas"]["RegisterRequest"]
    allowed = {"email", "password", "full_name", "referral_code"}
    assert set(user_create["properties"]) <= allowed
    for forbidden in ("is_superuser", "is_verified", "is_active"):
        assert forbidden not in user_create["properties"]


async def test_register_body_schema_is_the_public_one(client):
    response = await client.get("/openapi.json")
    schema = response.json()
    register_op = schema["paths"]["/auth/register"]["post"]
    body_schema_ref = register_op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    # HYP-65: the register body is now `RegisterRequest`, which extends
    # `UserCreatePublic` (see app/auth/schemas.py) -- still never `UserCreate`,
    # which is the admin-capable schema this test guards against.
    assert body_schema_ref.endswith("/RegisterRequest")
    assert not body_schema_ref.endswith("/UserCreate")


async def test_register_ignores_client_supplied_privilege_fields(client):
    email = "hyp31-privilege-probe@example.com"
    response = await client.post(
        "/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "is_superuser": True,
            "is_verified": True,
        },
    )
    # Either the field is now rejected outright, or (current behavior,
    # matching every other extra-forbids-ignored FastAPI-Users route) it's
    # silently dropped and the account is created unprivileged.
    assert response.status_code in (201, 422)
    if response.status_code == 201:
        body = response.json()
        assert body["is_superuser"] is False
        assert body["is_verified"] is False
