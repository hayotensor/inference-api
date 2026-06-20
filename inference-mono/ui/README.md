# Inference API UI

Standalone Next.js frontend for the FastAPI backend in `../api` and the TEE inference API in `../inference_api`.

The UI is intentionally isolated from backend implementation details. Account, auth, billing, and admin calls use `NEXT_PUBLIC_API_BASE_URL`; inference playground calls use `NEXT_PUBLIC_INFERENCE_API_BASE_URL`.

## Local setup

```bash
cp .env.example .env.local
corepack enable
pnpm install
pnpm dev
```

By default the UI expects the main API at `http://localhost:8000` and inference API at `http://localhost:8001`.
