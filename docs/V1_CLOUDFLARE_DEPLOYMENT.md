# V1 Cloudflare private-trial deployment

This document describes the lowest-risk way to put the frozen V1 behind
Cloudflare for a private, non-commercial trial. It does not change the
Next.js application, FastAPI application, PostgreSQL schema, GTFS semantics,
or provider contracts.

The immediate recommendation is to keep the already validated Node.js
Next.js deployment and put a Cloudflare DNS/proxy layer in front of it:

```text
browser
  -> Cloudflare DNS / proxy / HTTPS
  -> existing Next.js Node.js runtime
  -> same-origin /api/* rewrite
  -> existing FastAPI HTTPS service
  -> PostgreSQL + imported railway data + AMap
```

This is a Cloudflare edge configuration, not a claim that the frontend is
hosted on Cloudflare Pages or Workers. It preserves the current server-side
health check and rewrite behavior without a V1 migration. The existing
[Vercel/Render private-trial guide](./V1_PRIVATE_DEPLOYMENT.md) remains the
reference for building and hosting the application origins.

## Deployment decision

### Option A: Cloudflare Pages static export

This repository was tested with a temporary `output: "export"` configuration.
Next.js generated static files, but emitted the warning that rewrites are not
applied in export mode. That result is not a valid deployment for this app:

1. `apps/web/next.config.ts` rewrites every browser `/api/:path*` request to
   the server-only `API_BASE_URL`. A Pages static export has no Next.js server
   to execute that rewrite.
2. `apps/web/app/page.tsx` calls `GET /health` from an async Server Component
   with `cache: "no-store"`. In an export build the page is prerendered, so the
   API status becomes build-time HTML instead of a request-time status.
3. Making this work would require a new Pages proxy/function and a client-side
   health rewrite. That is a runtime and security change, not a deployment-only
   switch.

For these reasons, do not set `output: "export"` and do not deploy `apps/web/out`
as the V1 frontend.

Cloudflare's static Pages guide documents `npx next build` with `out` as the
output directory, but Next.js documents rewrites as unsupported in static
export mode:

- <https://developers.cloudflare.com/pages/framework-guides/nextjs/deploy-a-static-nextjs-site/>
- <https://nextjs.org/docs/app/guides/static-exports>

### Option B: Cloudflare Workers with the current Next.js path

Cloudflare currently recommends **vinext** for new Next.js applications on
Workers. The official guide says that an existing Next.js 16 application can
be checked with `vinext check` and initialized non-destructively with
`vinext init`. It lists App Router, React Server Components, SSR and rewrites
as supported, but also describes vinext as beta and requires a compatibility
check.

- <https://developers.cloudflare.com/workers/framework-guides/web-apps/nextjs/>
- <https://developers.cloudflare.com/workers/framework-guides/web-apps/opennext/>

This repository does not currently contain vinext, Wrangler, a Worker entry
point, or a Workers build. This task therefore does not run `vinext init`, add
its dependencies, or replace the normal `next dev`/`next build` workflow.
When a Workers deployment is desired, perform the compatibility check in an
isolated branch first:

```bash
cd apps/web
npx vinext check
```

Review the reported compatibility dashboard and run the complete backend,
frontend and Playwright gates before accepting any generated configuration.
Keep the existing Next.js build available until a Workers preview has passed
the live smoke. Do not move FastAPI into that Worker as part of the frontend
experiment.

### Option C: Cloudflare DNS/proxy in front of the existing frontend

This is the selected private-trial option. Use a custom domain in a Cloudflare
zone and proxy it to the already validated Next.js origin. The Next.js server
continues to execute the existing same-origin `/api/*` rewrite, so the browser
never sees `API_BASE_URL` or backend credentials. No Pages export, Worker
runtime, D1, R2, Hyperdrive, or new proxy code is required.

Cloudflare proxying does not make the origin Mainland-China hosted and does
not change the legal status of AMap or the railway source.

## Frontend deployment

Build the existing workspace at the Next.js origin:

```bash
npm ci
npm run build --workspace @transit-hub/web -- --webpack
npm run start --workspace @transit-hub/web
```

The output is the normal `.next` server build. There is no Cloudflare Pages
output directory in this option. The repository's Node requirement remains
the version recorded in `.nvmrc` (`22.12.0`); use that version for the origin
build rather than the developer machine's incidental Node version.

Configure the Next.js server-only variable on the frontend origin:

```text
API_BASE_URL=https://<fastapi-origin.example>
```

Do not use `NEXT_PUBLIC_API_BASE_URL` for this rewrite and never put AMap or
database credentials in the frontend project.

## Cloudflare account steps

1. Add a domain that the operator controls to Cloudflare, or use an existing
   Cloudflare zone.
2. Create the DNS record required by the chosen Next.js origin. For a custom
   Vercel domain, follow Vercel's current custom-domain instructions; for
   another Node host, use its documented CNAME/record target. Do not guess a
   target from this document.
3. Enable proxying only after the origin responds correctly over HTTPS.
4. Use `Full (strict)` TLS when the origin has a valid certificate.
5. Keep the origin's host and health-check configuration unchanged.
6. If the trial requires access control, use an explicitly configured private
   access product or an origin allowlist. Do not add authentication code to V1
   as part of this deployment task.

Cloudflare GitHub build integration is not needed for Option C. GitHub
continues to build the existing Next.js origin. If Option B is later adopted,
use Workers Builds only in a separate preview project.

## Backend deployment requirement

Keep FastAPI on a normal Python 3.12+ host, such as the existing Render
private-trial setup. Follow [V1_PRIVATE_DEPLOYMENT.md](./V1_PRIVATE_DEPLOYMENT.md)
for PostgreSQL, migrations, seed data, GTFS artifact provisioning and the
`/api/health/ready` gate.

Required backend values are configured in the backend provider dashboard, not
Cloudflare frontend variables:

```text
DATABASE_URL=postgresql+asyncpg://<managed-db-user>:<password>@<host>/<db>
AMAP_API_KEY=<backend-only-key>
RAIL_PROVIDER=gtfs
RAIL_GTFS_PATH=data/external/private-trial/output_gtfs.zip
```

The GTFS zip is an operator-supplied private build artifact. It stays outside
Git and must not be uploaded to a public Cloudflare bucket. The API still uses
the existing `GTFSFeedCache`; it does not download a feed during a browser
request and never falls back to a fixture provider in `gtfs` mode.

Run migrations and seed data before opening the frontend:

```bash
cd apps/api
uv run alembic upgrade head
uv run python -m app.db.seed
uv run uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
```

The database remains PostgreSQL. Do not migrate it to D1. Do not introduce
Hyperdrive unless a later, separately approved Workers backend prototype
requires it.

## API routing and CORS

The selected path keeps browser requests same-origin:

```text
https://<cloudflare-domain>/api/*
  -> Next.js server rewrite
  -> https://<fastapi-origin>/api/*
```

No public CORS policy is required. Do not add
`Access-Control-Allow-Origin: *`. If a future deployment removes the Next.js
runtime and calls FastAPI directly from the browser, configure an explicit
allowlist containing only the actual HTTPS frontend origins and review
credentials/headers before enabling it.

## Environment and secret handling

Frontend/origin variables:

| Variable | Where | Secret | Purpose |
|---|---|---:|---|
| `API_BASE_URL` | Next.js server only | No | FastAPI origin for the server rewrite |

Backend variables:

| Variable | Where | Secret | Purpose |
|---|---|---:|---|
| `DATABASE_URL` | FastAPI only | Yes | PostgreSQL connection |
| `AMAP_API_KEY` | FastAPI only | Yes | AMap Web Service calls |
| `RAIL_PROVIDER` | FastAPI only | No | `gtfs` in private trial |
| `RAIL_GTFS_PATH` | FastAPI only | No | Local/private feed artifact path |
| `RAIL_GTFS_ARTIFACT_TOKEN` | Build environment only | Yes | Optional private artifact download |

Do not add any of these as `NEXT_PUBLIC_*` variables. Verify after every
deployment that the static JavaScript bundles contain neither the AMap key nor
the database URL. The existing structured logging/redaction layer must remain
enabled.

## HTTPS, deploy, rollback and smoke test

Use HTTPS from the Cloudflare domain to the frontend origin and HTTPS from
Next.js to FastAPI. Do not expose the development `127.0.0.1` API URL in a
trial deployment.

Deployment order:

1. Provision managed PostgreSQL.
2. Run `alembic upgrade head` and the canonical seed.
3. Provision and verify the pinned GTFS artifact.
4. Deploy FastAPI and wait for `/api/health/ready` to return HTTP 200.
5. Deploy/restart the Next.js origin with server-only `API_BASE_URL`.
6. Add or enable the Cloudflare DNS/proxy record.
7. Run the existing live smoke against the public API and web URLs.

```bash
uv run --project apps/api python scripts/live_smoke.py \
  --api-url https://<fastapi-origin.example> \
  --web-url https://<cloudflare-domain.example> \
  --arrival-at 2026-09-18T14:20:00+08:00 \
  --gtfs-path /private/path/output_gtfs.zip
```

The smoke must verify canonical resolution, live provider identity, rail date
coverage, health endpoints, transfer response invariants, `X-Request-ID` and
same-origin rewrite behavior. It must not assert a fixed AMap duration, score
or recommended station.

For rollback, first disable the Cloudflare proxy if it is the suspected layer,
then promote the last known-good frontend/backend deployments. Keep PostgreSQL
at the current Alembic head; do not downgrade the database during an app
rollback. Restore the previous pinned GTFS artifact and rerun readiness and
live smoke before reopening the trial.

## Desktop, iPhone and iPad smoke plan

After the automated smoke passes, open the same HTTPS Cloudflare URL on:

- Desktop Chrome or Safari: submit Chengdu → Leshan and inspect the complete
  result, partial-data messages and route details.
- iPhone Safari: use the full form, open a shared query URL, rotate the device
  and verify no horizontal overflow.
- iPad Safari: repeat the query and compare candidate cards, train expansion,
  stale/fresh notices and error states.

Do not treat a browser page loading as evidence that FastAPI, PostgreSQL, GTFS
and AMap are healthy; keep the live smoke and readiness checks in the release
procedure.

## Mainland-China limitations and domain choice

Cloudflare's global edge reachability, a `pages.dev`/`workers.dev` hostname,
and Cloudflare China Network service are different claims. This repository
does not claim Mainland-China hosting or guaranteed Mainland-China reachability
from the existence of a Cloudflare zone. Confirm actual accessibility from the
intended networks before inviting trial users.

For Option C, a custom domain in the operator's Cloudflare zone is the useful
choice because it can proxy the existing Next.js origin. `pages.dev` and
`workers.dev` are not used by this option. Do not infer ICP filing or Mainland
China hosting from DNS delegation alone.

Cloudflare and the origin provider may impose free-plan limits, sleeping/cold
starts, bandwidth/request limits, build limits and quota changes. Check the
current account terms before relying on a free tier. The existing AMap quota,
request operation budget, process-local caches and single-instance GTFS
assumptions remain in force.

## Optional Workers experiment

If Cloudflare-hosted frontend execution is required later, use a separate
preview project and branch:

```bash
cd apps/web
npx vinext check
```

Only after compatibility review should the operator consider `vinext init`,
the generated Wrangler configuration and a Workers preview. Preserve the
normal Next.js development/build scripts until the preview passes all existing
tests and the real smoke. Do not move the FastAPI backend, PostgreSQL, GTFS or
AMap into Workers as part of that frontend experiment.

## FastAPI on Python Workers: why it is not selected

Cloudflare documents FastAPI support in Python Workers, but the current API is
not a drop-in Worker:

- the repository targets Python `>=3.12`, while current Python Workers
  examples require `>=3.13`;
- the app creates an async SQLAlchemy engine at import time, and Cloudflare's
  current Hyperdrive Python documentation says async SQLAlchemy is not yet
  supported because of greenlet requirements;
- Alembic is a process/CLI migration tool, not a request-runtime dependency;
- the API reads a local GTFS zip/directory and relies on a process-local parsed
  feed cache;
- Python Workers have an ephemeral in-memory filesystem and do not provide a
  durable local feed path;
- `GTFSFeedCache` uses `threading.RLock`, while Python Workers document
  threading as non-functional;
- AMap uses `httpx.AsyncClient`, retry, semaphore and process-local operation
  guards that would require a Worker-specific compatibility test;
- process-local RailwaySearchCache, RouteCache sessions and lifespan disposal
  would need a new lifecycle and database design.

The existing FastAPI host is therefore the safer V1 backend. Cloudflare's
Python Workers and Hyperdrive documentation are useful for a future prototype,
but they do not justify a backend migration here:

- <https://developers.cloudflare.com/workers/languages/python/packages/fastapi/>
- <https://developers.cloudflare.com/hyperdrive/examples/python-workers/>
- <https://developers.cloudflare.com/workers/languages/python/stdlib/>

## Licensing and private-trial boundary

This deployment guide does not change BL-105/T-098. A technically successful
Cloudflare deployment does not establish AMap commercial authorization, AMap
cache/display rights, railway data redistribution/commercial rights,
attribution compliance or privacy compliance. Keep the deployment private and
non-commercial until the existing public-launch evidence register is cleared
by the responsible human owners.
