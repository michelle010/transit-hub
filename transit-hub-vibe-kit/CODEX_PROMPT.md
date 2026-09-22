# Codex Prompt — First Implementation Session

You are implementing the first development slice of a China-mainland transit-hub decision tool.

Before editing code, read these repository files completely:

- `PRD.md`
- `AGENTS.md` (or `GENTS.md` if AGENTS.md is absent)
- `DATABASE.md`
- `BACKLOG.md`
- `TASKS.md`
- `README.md`

## Product goal

V1 answers:

> Given a known transfer city, arrival hub, arrival date/time, destination city, and baggage status, which railway station should the traveler use next, how long will the intra-city transfer take, what safe transfer time should be reserved, and which concrete trains are realistically catchable?

Do not turn this into an OTA or general travel app.

## Scope for THIS session

Implement **only the repository foundation and first backend vertical skeleton**.

Complete these tasks from `TASKS.md`:

- T-001 Create monorepo structure
- T-002 Bootstrap backend
- T-003 Bootstrap frontend
- T-004 Local PostgreSQL
- T-010 Create city/hub schema
- T-011 Seed test cities and hubs
- T-012 City/hub service
- T-020 Define provider interfaces
- T-021 Fixture providers

Do **not** implement the real AMap adapter or railway ingestion in this session unless all tasks above are finished, tested, and there is still a clear reason to continue.

## Required architecture

Use:

- `apps/web`: Next.js + TypeScript + Tailwind
- `apps/api`: Python 3.12+ + FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic
- PostgreSQL via Docker Compose
- `pytest` + `pytest-asyncio`
- Ruff
- TypeScript strict mode
- ESLint

The backend is a modular monolith.

Do not create microservices.

## Data model

Implement at least:

- `cities`
- `hubs`
- `hub_aliases`
- `hub_provider_refs`

Follow `DATABASE.md`.

A railway station is a `hub`, not a separate top-level identity system.

Do not add users, auth, payments, bookings, prices, or tickets.

## Provider boundaries

Create typed interfaces/protocols for:

```text
HubProvider
RoutingProvider
RailProvider
```

Create normalized domain DTOs.

Raw provider payloads must not leak into application/domain services.

For this first session, implement deterministic fixture/mock providers so the repository can be developed and tested without external API credentials.

## Seed data

Seed enough deterministic data for:

- 成都
- 乐山
- 南京
- 苏州

Include representative airports and passenger railway stations required for future tests.

Do not claim seed data is live truth.

## API

Implement:

```text
GET /health
GET /api/cities/search?q=
GET /api/cities/{city_id}/hubs
```

Responses should use stable Pydantic models.

Use explicit error handling for missing cities.

## Frontend

Create only a minimal working application shell.

Do not spend this session on final visual design.

A simple page proving the web app can reach the API health endpoint is sufficient.

Do not build a marketing landing page.

## Configuration

Add:

- `.env.example`
- backend settings module
- `DATABASE_URL`
- placeholders for future `AMAP_API_KEY` and `RAIL_PROVIDER`

Never commit real keys.

Never expose server keys through `NEXT_PUBLIC_*`.

## Tests

At minimum add tests for:

- health endpoint
- city search
- city hubs
- hub alias normalization
- fixture provider contracts

Tests must not require external network access.

## Developer experience

Provide accurate documented commands for:

- starting PostgreSQL
- running migrations
- starting API
- starting web
- backend lint
- backend tests
- frontend lint
- frontend typecheck

Update `README.md` so the commands match the implementation exactly.

## Quality bar

Before finishing:

1. run backend lint/tests
2. run frontend lint/typecheck
3. confirm migrations run from empty DB
4. confirm seed operation is idempotent
5. confirm no secrets are committed
6. confirm no out-of-scope feature was added

If a dependency or framework command differs from the repository docs, update the docs.

## Final response format

When finished, report:

1. What you implemented
2. Repository structure created
3. Database migrations created
4. Endpoints added
5. Tests added
6. Commands executed and whether they passed
7. Known blockers
8. Exactly which `TASKS.md` item should be done next

Do not merely describe code that should exist. Make the changes and verify them.
