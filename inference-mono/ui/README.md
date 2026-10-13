# Inference API UI

Standalone Next.js frontend for the FastAPI backend in `../api` and the TEE inference API in `../inference_api`.

The UI is intentionally isolated from backend implementation details. Account, auth, billing, and admin calls use `NEXT_PUBLIC_API_BASE_URL`; inference playground calls use `NEXT_PUBLIC_INFERENCE_API_BASE_URL`.

## Local setup

### UI only (no backend services)

```bash
cd inference-mono/ui # from the monorepo root
pnpm install        # only if dependencies are not installed yet
pnpm dev:demo
```

Open `http://localhost:3000/login` and click **Enter local demo**. You can also
sign in with `dev@example.com` / `local-demo`. The demo account has owner access
to `/admin` as well as `/dashboard`.

This command starts only Next.js. It needs no API, database, Redis, Stripe,
OAuth, wallet, or inference service. Dashboard and admin pages use sample data;
pricing and service status also have fixtures. Profile edits, API-key creation /
editing / revocation, subscription changes / cancellation, wallet unlinking, and
admin user edits work in memory. Reload the page or click **Reset demo data** to
restore the fixtures. Demo API keys cannot make real inference requests.

External actions (checkout, customer portal, OAuth, wallet connection, email /
phone verification, and inference) and other unimplemented mutations are
unavailable in this mode. They never fall through to a backend request.
Attestation remains unverified. This mode is for UI work, not integration testing.

Demo sessions use their own browser storage key. `pnpm dev:demo` sets
`NEXT_PUBLIC_UI_DEMO=true` for that process and uses `.next-demo` as its build
directory, with `tsconfig.demo.json` for generated types. Normal typechecks and
Docker builds exclude this demo output. Demo wallet state is not persisted,
and demo server routes cannot set or clear real SSO cookies or call the backend.
Chat controls, blockchain RPC, and Stripe redirects are disabled in demo mode.
The demo build excludes the wallet SDK entirely. In normal mode, wallet code
and styles load on the first wallet-button click; an existing wallet connection
is restored at that point. Dashboard data and navigation do not wait for it.
Desktop and mobile attestation badges share one check and a six-hour refresh
timer, with a manual **Check now** action in the details dialog.
The demo command also disables Next.js telemetry. Stop it and use `pnpm dev`
to return to the normal backend-connected UI. Do not put the demo flag in a production environment: it is development-only,
and the build rejects it when enabled.

Run the demo and normal/production isolation checks without starting Next.js or a browser:

```bash
pnpm test:demo
pnpm typecheck
pnpm lint
```

Browser regression checks attach to an already-running UI and use isolated
browser sessions. With `pnpm dev:demo` running on port 3001:

```bash
PLAYWRIGHT_BASE_URL=http://localhost:3001 pnpm test:browser
```

For a normal development or production server, set `PLAYWRIGHT_UI_MODE=normal`
and point `PLAYWRIGHT_BASE_URL` at its local address. These checks mock the
backend and inject a simulated wallet; they do not use real accounts or sign
with a real wallet. They cover deferred SDK loading, wallet login/linking and
disconnection. Demo checks cover SDK exclusion, runtime errors, storage
isolation, and shared attestation polling/refresh.

### With the backend

```bash
cp .env.example .env.local
corepack enable
pnpm install
pnpm dev
```

By default the UI expects the main API at `http://localhost:8000` and inference API at `http://localhost:8001`.

For production Docker builds, both public URLs are build args:

- `NEXT_PUBLIC_API_BASE_URL`
- `NEXT_PUBLIC_INFERENCE_API_BASE_URL`
