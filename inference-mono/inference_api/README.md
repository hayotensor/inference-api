# TEE Inference API

Standalone FastAPI service for inference endpoints that should run separately from account, login, billing, API-key management, and admin endpoints.

It owns:

- `/v1/models`
- `/v1/inference`
- `/v1/usage`
- `/router/inference/*`

It shares no runtime application modules with `inference-mono/api`; both services only point at the same database schema. API keys and router service tokens are still created by the main API, then validated here with the shared `SECRET_PEPPER`.

## Local

```bash
cp .env.example .env
pip install -e ".[dev]"
uvicorn inference_api.main:app --host 0.0.0.0 --port 8001
```

The main API remains on port `8000`; this service defaults to port `8001` in Docker examples.

## Mesh Routing

Set `MESH_ROUTER_URL` to forward `/v1/inference` payloads to the peer mesh router. Without it, local development uses a deterministic echo response so API-key, quota, and reservation behavior can be tested without a mesh.
