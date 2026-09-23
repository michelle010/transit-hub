# V1 private-trial deployment

This guide deploys the frozen V1 application for a small, private,
non-commercial trial:

```text
browser
  -> Vercel / Next.js
  -> same-origin /api/* rewrite
  -> Render / FastAPI
  -> managed PostgreSQL
  -> build-injected local GTFS artifact
  -> AMap Web Service
```

This is a deployment procedure, not a public-launch approval. The railway
feed remains a development/POC source and the BL-105/T-098 public-launch gate
must remain `BLOCKED` until the required rights and attribution evidence is
provided. A private trial must be limited to the people and use case approved
by the operator.

## A. GitHub preparation

1. Push the repository source, excluding `.env`, `.env.*` files except the
   examples, and everything below `data/external/`.
2. Confirm the feed is not tracked:

   ```bash
   git ls-files data/external .env apps/web/.env.local
   ```

   The command should print no local feed or secret file.
3. Review the public-launch register before inviting any trial users:

   ```bash
   uv run --project apps/api python scripts/check_public_launch_readiness.py
   ```

   An exit code of `1` with a `BLOCKED` decision is expected for the current
   repository. Deployment does not change that decision.

## B. Managed PostgreSQL

Create a managed PostgreSQL database with a private/non-public connection
policy where the provider supports it. Copy its connection string into the
backend environment as `DATABASE_URL`. Use an async SQLAlchemy URL, normally
`postgresql+asyncpg://...`, and do not put credentials in GitHub, Vercel or
frontend variables.

The database is external. Do not run PostgreSQL inside the Render web service.

## C. Backend environment variables

The Render Blueprint in [`render.yaml`](../render.yaml) declares the minimum
variables. Set secret values in the Render dashboard, never in the Blueprint
file.

Required runtime variables:

```text
DATABASE_URL=postgresql+asyncpg://<managed-db-user>:<password>@<host>/<db>
AMAP_API_KEY=<backend-only-web-service-key>
RAIL_PROVIDER=gtfs
RAIL_GTFS_PATH=data/external/private-trial/output_gtfs.zip
```

The build-time GTFS variables are also required by the Blueprint build:

```text
RAIL_GTFS_ARTIFACT_URL=https://<private-artifact-host>/<pinned-gtfs>.zip
RAIL_GTFS_ARTIFACT_SHA256=<64-lowercase-hex-sha256>
RAIL_GTFS_ARTIFACT_TOKEN=<optional-build-only-bearer-token>
```

The artifact URL must be HTTPS. The token is sent as an Authorization header
by the build script and is never printed. Prefer a short-lived, read-only
artifact credential. Do not put a signed URL or token in this document.

Safe defaults already live in `app/core/config.py`: AMap timeout/retry and
operation limits, process-local candidate concurrency, railway search cache
TTL/capacity, freshness threshold, nearby limits, `APP_ENV`, and `LOG_LEVEL`.
Override them only after observing a real private-trial need. The process-local
cache and concurrency values are not shared between instances.

Environment matrix:

| Variable | Classification | Private-trial contract |
|---|---|---|
| `DATABASE_URL` | REQUIRED / SECRET | Managed PostgreSQL async URL |
| `AMAP_API_KEY` | REQUIRED / SECRET | Backend-only AMap Web Service key |
| `AMAP_BASE_URL` | SAFE DEFAULT | `https://restapi.amap.com` |
| `AMAP_TIMEOUT_SECONDS` | OPTIONAL / SAFE DEFAULT | `10` |
| `AMAP_MAX_ATTEMPTS` | OPTIONAL / SAFE DEFAULT | `2` |
| `AMAP_RETRY_BASE_DELAY_MS` | OPTIONAL / SAFE DEFAULT | `100` |
| `AMAP_RETRY_MAX_DELAY_MS` | OPTIONAL / SAFE DEFAULT | `500` |
| `AMAP_MAX_RETRIES_PER_REQUEST` | OPTIONAL / SAFE DEFAULT | `2` |
| `AMAP_MAX_OPERATIONS_PER_REQUEST` | OPTIONAL / SAFE DEFAULT | `16` |
| `AMAP_MAX_CONCURRENT_OPERATIONS` | OPTIONAL / SAFE DEFAULT | `4` |
| `AMAP_TRANSIT_CACHE_TTL_SECONDS` | OPTIONAL / SAFE DEFAULT | `21600` |
| `AMAP_DRIVING_CACHE_TTL_SECONDS` | OPTIONAL / SAFE DEFAULT | `1800` |
| `CANDIDATE_EVALUATION_MAX_CONCURRENCY` | OPTIONAL / SAFE DEFAULT | `3` |
| `RAIL_PROVIDER` | REQUIRED | `gtfs`; never `fixture` in Render |
| `RAIL_GTFS_PATH` | REQUIRED | `data/external/private-trial/output_gtfs.zip` |
| `RAIL_DATA_STALE_AFTER_DAYS` | OPTIONAL / SAFE DEFAULT | `7` |
| `RAIL_SEARCH_CACHE_ENABLED` | OPTIONAL / SAFE DEFAULT | `true` |
| `RAIL_SEARCH_CACHE_TTL_SECONDS` | OPTIONAL / SAFE DEFAULT | `1800` |
| `RAIL_SEARCH_CACHE_MAX_ENTRIES` | OPTIONAL / SAFE DEFAULT | `512` |
| `RAIL_SEARCH_HORIZON_HOURS` | OPTIONAL / SAFE DEFAULT | `12` |
| `FLEXIBLE_DATE_MAX_OFFSET_DAYS` | OPTIONAL / SAFE DEFAULT | `3` |
| `NEARBY_RAILWAY_RADIUS_METERS` / `NEARBY_RAILWAY_MAX_ALTERNATIVES` | OPTIONAL / SAFE DEFAULT | `50000` / `3` |
| `NEARBY_AIRPORT_RADIUS_METERS` / `NEARBY_AIRPORT_MAX_ALTERNATIVES` | OPTIONAL / SAFE DEFAULT | `100000` / `3` |
| `APP_ENV` | OPTIONAL | `private_trial` |
| `LOG_LEVEL` | OPTIONAL | `INFO` |
| `RAIL_GTFS_ARTIFACT_URL` | REQUIRED at build / SECRET-capable | HTTPS URL for the pinned private zip |
| `RAIL_GTFS_ARTIFACT_SHA256` | REQUIRED at build | 64-character integrity digest |
| `RAIL_GTFS_ARTIFACT_TOKEN` | OPTIONAL at build / SECRET | Read-only bearer token, if the artifact host needs it |
| `API_BASE_URL` | REQUIRED on Vercel / SERVER-ONLY | Render HTTPS origin; never `NEXT_PUBLIC_*` |

## D. Database migration

Run migrations against the managed database before the service is expected to
be ready. From the repository root:

```bash
cd apps/api
DATABASE_URL='postgresql+asyncpg://<managed-db-user>:<password>@<host>/<db>' \
  uv run alembic upgrade head
uv run alembic heads
uv run alembic current
cd ../..
```

The repository head is currently:

```text
0007_create_hub_reconciliation_overrides
```

Do not run downgrade commands against a database that contains override audit
history.

## E. Canonical/reference-data bootstrap

Schema migration creates tables only. It does not create canonical product
data. Run the idempotent seed against the same managed database:

```bash
cd apps/api
DATABASE_URL='postgresql+asyncpg://<managed-db-user>:<password>@<host>/<db>' \
  uv run python -m app.db.seed
cd ../..
```

The seed inserts the canonical cities, hubs, aliases and initial provider
references with deterministic IDs. Re-running it is safe.

## F. GTFS provisioning and import

The repository deliberately does not commit the railway feed. The Render build
uses `scripts/prepare_gtfs_artifact.py` to download an operator-supplied,
pinned zip into `data/external/private-trial/output_gtfs.zip` and verifies its
SHA-256 and required GTFS files. This is build-time artifact injection; it does
not silently download a new source and it does not change GTFS semantics.

The script is also usable for a local verification of a private zip:

```bash
export RAIL_GTFS_ARTIFACT_SHA256="$(shasum -a 256 /private/path/output_gtfs.zip | awk '{print $1}')"
python scripts/prepare_gtfs_artifact.py \
  --source /private/path/output_gtfs.zip \
  --output /tmp/verified-output_gtfs.zip
unset RAIL_GTFS_ARTIFACT_SHA256
```

The application still needs normalized railway rows in PostgreSQL. Import the
same pinned feed after migration and canonical seed, preferably before the
Render service is deployed:

```bash
DATABASE_URL='postgresql+asyncpg://<managed-db-user>:<password>@<host>/<db>' \
  uv run --project apps/api python scripts/import_rail_gtfs.py \
  /private/path/output_gtfs.zip \
  --service-date 2026-09-18
```

Use additional `--service-date` arguments for the trial date range. The
importer output is the reconciliation record: inspect `available_from`,
`available_to`, `matched`, `unmatched`, `ambiguous`, and the matched/unresolved
station lists. Confirm the required Chengdu and Leshan stations before launch.

Do not use `--create-missing-hubs` as a shortcut for unresolved production
identity. Resolve canonical data or create an audited manual override first.

If the database is bootstrapped from a Render shell instead, run the same
import command against the artifact path:

```bash
uv run --project apps/api python scripts/import_rail_gtfs.py \
  data/external/private-trial/output_gtfs.zip \
  --service-date 2026-09-18
```

The ordinary transfer request never imports data as a side effect.

## G. Render Web Service

The checked-in [`render.yaml`](../render.yaml) uses Render's native Python
runtime rather than Docker. The existing `pyproject.toml` and `uv.lock` make
native installation reproducible, and the feed is already an explicit build
artifact. No PostgreSQL container, Redis, or persistent disk is required for
this private-trial shape.

Create the service from the Blueprint or create it manually with:

```text
Root directory: repository root (.)
Runtime: Python
Plan: Free for low-volume trial only
```

Build command:

```bash
python -m pip install --upgrade uv && \
uv sync --project apps/api --frozen --no-dev && \
python scripts/prepare_gtfs_artifact.py \
  --output data/external/private-trial/output_gtfs.zip
```

Start command:

```bash
uv run --project apps/api uvicorn app.main:app \
  --app-dir apps/api --host 0.0.0.0 --port $PORT
```

The application reads Render's `PORT`, binds to all interfaces, and disposes
the SQLAlchemy engine on shutdown. Uvicorn preserves the existing request ID
middleware and structured logging.

## H. Render health check

Set Render's health-check path to:

```text
/api/health/ready
```

Readiness is HTTP 200 only when the database, Alembic head, canonical data,
AMap configuration and GTFS artifact are valid. An absent or malformed GTFS
artifact returns `RAIL_DATA_NOT_LOADED` or `RAIL_FEED_FORMAT_ERROR`; it does not
fall back to FixtureRailProvider. `/api/health/live` is dependency-free and
can be used while diagnosing a service that has not yet been bootstrapped.

Readiness does not import railway rows, call AMap, or run a transfer request.
The parsed feed is reused through the existing `GTFSFeedCache`; dated railway
row and station checks remain part of `scripts/live_smoke.py`.

## I. Vercel project

Create a Vercel project from the same repository with:

```text
Root directory: .
Framework preset: Next.js
Node.js version: 22.x (the repository records 22.12.0 in .nvmrc)
Install command: npm ci
Build command: npm run build --workspace @transit-hub/web
Output directory: leave the Next.js default
```

The checked-in [`vercel.json`](../vercel.json) records the install/build
commands. Keeping the root at `.` is intentional: the workspace lockfile and
`packages/shared` must be available to the Next.js build.

## J. Vercel environment variables and same-origin API

Set this **server-only** Vercel project variable for Preview and Production:

```text
API_BASE_URL=https://<render-service>.onrender.com
```

Do not prefix it with `NEXT_PUBLIC_`. The existing `next.config.ts` rewrites
every `/api/:path*` browser request to `${API_BASE_URL}/api/:path*`, so the
browser talks only to the Vercel origin. The rewrite covers discovery,
transfer evaluation, liveness and readiness without adding a second API client
or broad CORS policy. The backend key and database URL never enter the
frontend environment.

Local development continues to use `apps/web/.env.local`:

```text
API_BASE_URL=http://127.0.0.1:8000
```

## K. First deployment

Use this order:

1. Create the managed PostgreSQL database.
2. Run `alembic upgrade head`.
3. Run the canonical seed.
4. Import the pinned GTFS dates and record reconciliation output.
5. Create the Render service and set its secret/configuration variables.
6. Confirm the Render build downloads and verifies the same GTFS artifact.
7. Confirm Render `/api/health/ready` returns HTTP 200.
8. Create the Vercel project and set the server-only `API_BASE_URL`.
9. Deploy Vercel and open the HTTPS URL.

Do not invite trial users until the post-deploy smoke is complete.

## L. Post-deploy smoke

Use the existing live verifier rather than duplicating transfer logic. It must
be run from a machine that can reach both public service URLs and the managed
database when local preflight is needed:

```bash
uv run --project apps/api python scripts/live_smoke.py \
  --api-url https://<render-service>.onrender.com \
  --web-url https://<vercel-project>.vercel.app \
  --arrival-at 2026-09-18T14:20:00+08:00 \
  --gtfs-path /private/path/output_gtfs.zip
```

The live command checks provider identity, canonical resolution, GTFS range,
dated rail data, reconciliation, GCJ02 coordinates, FastAPI health, Vercel
same-origin transfer evaluation, response invariants and `X-Request-ID`. It
does not assert AMap durations, scores or a permanent recommended station.

Also manually open the Vercel HTTPS URL in desktop Chrome, iPad Safari and
iPhone Safari. Confirm the form remains usable, cards do not overflow
horizontally, and partial/error/stale notices are visible.

## M. Rollback

1. In Vercel, promote the last known-good deployment.
2. In Render, redeploy the previous successful commit/build.
3. Keep the database at the current Alembic head; do not downgrade as part of
   an application rollback.
4. If a feed replacement caused the issue, restore the previous pinned artifact
   URL and SHA-256 and redeploy the backend.
5. Re-run `/api/health/ready` and the live smoke before reopening the trial.

## N. Secret rotation

Rotate `AMAP_API_KEY`, `DATABASE_URL` credentials and any artifact token in the
provider dashboards. Update Render variables, redeploy, and revoke the old
credential at the provider. Never write the old or new value to GitHub,
Vercel client variables, logs, issue comments or this document.

## O. GTFS replacement/update

1. Obtain an operator-approved replacement feed outside Git.
2. Verify its provenance/rights separately from this deployment procedure.
3. Compute its SHA-256 and update the Render build variables.
4. Run the importer for the required service dates against the managed
   database; inspect reconciliation and row counts.
5. Deploy the backend and confirm feed date range/source metadata.
6. Re-run the live smoke. Do not fabricate `source_updated_at`; the provider
   reports `UNKNOWN` when the feed has no trustworthy timestamp.

## P. Free-tier and single-instance limitations

- Render Free services can sleep and have cold starts; this is unsuitable for
  guaranteed availability.
- The filesystem is ephemeral between deploys. The build artifact must be
  re-provisioned on every build; do not rely on a manually copied file.
- `GTFSFeedCache`, `RailwaySearchCache`, `AMapConcurrencyGuard` and candidate
  semaphores are process-local. Use one backend instance for predictable
  private-trial behavior.
- RouteCache and railway rows are in managed PostgreSQL, but cache warmth is
  disposable.
- No Redis, distributed lock, worker queue or multi-region consistency is
  provided.
- AMap quotas and the existing request operation budget still apply.
- The timetable is scheduled POC data, not live railway inventory, ticket
  availability or a connection guarantee.

## Q. Public-launch boundary

This deployment does not change BL-105/T-098. The current public-launch
checker remains intentionally blocked until external AMap, railway source,
cache/display, attribution and privacy evidence is recorded by the proper
owner. A successful private deployment proves connectivity and configuration,
not legal or commercial clearance.
