"""Helpers to run the REAL TEE producer (tee_wrapper) in-process as a dev miner.

We drive the producer's FastAPI lifespan manually (so app.state — identity,
attester, authorizer, etc. — is initialized) and expose an httpx ASGITransport
so the inference-api provisioner/forwarder can talk to it via an injected
transport. This genuinely exercises the shared get_attestation / verify /
build_provision_request / POST /provision path against the real producer.

``validator_static_keys`` is set to the platform provisioner's verify key hex so
the enclave accepts a provision signed by our platform key.
"""

from __future__ import annotations

import contextlib
import sys
import tempfile
from pathlib import Path

import httpx

# Make the producer importable (it is NOT pip-installed into the venv).
_MINER_SRC = "/home/rizzo/talaris-inference/inference-subnet-miner/src"
if _MINER_SRC not in sys.path:
    sys.path.insert(0, _MINER_SRC)

from nacl.signing import SigningKey  # noqa: E402

from tee_wrapper.app import create_app as create_tee_app  # noqa: E402
from tee_wrapper.config import Settings as TeeSettings  # noqa: E402

# Same fixed platform provisioner seed as conftest (kept independent so this
# module imports without depending on the conftest import path).
PLATFORM_PROVISIONER_SEED = bytes(range(32))
PLATFORM_PROVISIONER_VERIFY_KEY_HEX = bytes(
    SigningKey(PLATFORM_PROVISIONER_SEED).verify_key
).hex()


async def bind_dev_tee(transport, base_url, signing_key: SigningKey) -> str:
    """Bind ``signing_key``'s hotkey into the dev enclave so doc.miner is populated.

    Returns the bound hotkey hex (== the enclave's miner pubkey). After this,
    ``doc.miner == sha256(bytes.fromhex(hotkey))`` so the provisioner's
    expected_miner_hash check matches.
    """
    from talaris_contracts.bind import BindRequest, bind_message

    hotkey_hex = bytes(signing_key.verify_key).hex()
    async with httpx.AsyncClient(base_url=base_url, transport=transport) as ac:
        att = await ac.get("/attestation", params={"nonce": "00" * 32})
        att.raise_for_status()
        doc = att.json()
        message = bind_message(doc["boot_nonce"], doc["fingerprint"])
        signature = signing_key.sign(message).signature.hex()
        req = BindRequest(miner_pubkey=hotkey_hex, signature=signature)
        resp = await ac.post("/bind", json=req.model_dump())
        resp.raise_for_status()
    return hotkey_hex


@contextlib.asynccontextmanager
async def dev_tee_app(*, validator_verify_key_hex: str | None = None):
    """Yield (asgi_transport, base_url) for a running in-process dev TEE.

    The producer lifespan is entered/exited around the yield so app.state is
    populated. A temp dir holds the generated TLS cert/key + usage db.
    """
    verify_key = validator_verify_key_hex or PLATFORM_PROVISIONER_VERIFY_KEY_HEX
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        settings = TeeSettings(
            dev_mode=True,
            attestation_mode="dev",
            allow_dev_mode=True,
            usage_db_path=str(tmp_path / "usage.db"),
            tls_cert_path=str(tmp_path / "cert.pem"),
            tls_key_path=str(tmp_path / "key.pem"),
            validator_static_keys=verify_key,
            miner_key_type="ed25519",
            require_gpu=False,
            verify_model_integrity=False,
        )
        app = create_tee_app(settings)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            yield transport, "http://tee.local"


class StubChatTransport(httpx.AsyncBaseTransport):
    """Wrap the real dev-TEE ASGI transport but answer /v1/chat/completions locally.

    The dev TEE has NO engine, so its /v1/chat/completions returns 503. To
    exercise the real reserve -> forward -> settle path with REAL token counts
    while still using the genuine attestation + provision path, we intercept ONLY
    the chat-completions route and return a deterministic OpenAI-shaped completion
    (with a usage block); every other request flows to the real producer.
    """

    def __init__(
        self,
        inner: httpx.ASGITransport,
        *,
        content: str = "Hello from the stubbed enclave engine.",
        prompt_tokens: int = 11,
        completion_tokens: int = 6,
    ) -> None:
        self._inner = inner
        self._content = content
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens

    @property
    def usage(self) -> dict:
        return {
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "total_tokens": self._prompt_tokens + self._completion_tokens,
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1/chat/completions":
            return await self._inner.handle_async_request(request)

        # Enforce the enclave's bearer-auth contract minimally: require *some*
        # Authorization header (the forwarder always supplies the decrypted token).
        if not request.headers.get("authorization", "").lower().startswith("bearer "):
            return httpx.Response(401, json={"error": "inference credential required"})

        import json as _json

        body = _json.loads(request.content or b"{}")
        if body.get("stream"):
            return self._stream_response(body)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-stub",
                "object": "chat.completion",
                "model": body.get("model", "stub-model"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": self._content},
                    }
                ],
                "usage": self.usage,
            },
        )

    def _stream_response(self, body: dict) -> httpx.Response:
        import json as _json

        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        model = body.get("model", "stub-model")
        chunks = [
            {
                "id": "chatcmpl-stub",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [
                    {"index": 0, "delta": {"content": self._content}, "finish_reason": None}
                ],
            },
            {
                "id": "chatcmpl-stub",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        ]
        lines = [f"data: {_json.dumps(c)}\n\n" for c in chunks]
        if include_usage:
            lines.append(
                "data: "
                + _json.dumps(
                    {
                        "id": "chatcmpl-stub",
                        "object": "chat.completion.chunk",
                        "model": model,
                        "choices": [],
                        "usage": self.usage,
                    }
                )
                + "\n\n"
            )
        lines.append("data: [DONE]\n\n")
        return httpx.Response(
            200,
            content="".join(lines).encode(),
            headers={"content-type": "text/event-stream"},
        )
