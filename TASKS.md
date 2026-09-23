# TASKS.md

This is the recommended execution order for Codex.

Do not attempt the entire backlog in one run.

---

# Phase 0 — Repository bootstrap

## T-001 — Create monorepo structure
**Backlog:** BL-001

Create:

```text
apps/web
apps/api
packages/shared
data/seed
scripts
tests
```

**Done when:**

- repository structure exists
- README setup instructions reflect reality
- no unused framework scaffolding remains

---

## T-002 — Bootstrap backend
**Backlog:** BL-001, BL-002

Create FastAPI app with:

```text
GET /health
```

Add:

- settings module
- Asia/Shanghai timezone constant
- structured error envelope
- Ruff config
- pytest
- SQLAlchemy
- Alembic

**Done when:**

```bash
pytest
ruff check .
```

pass.

---

## T-003 — Bootstrap frontend
**Backlog:** BL-001

Create Next.js TypeScript app.

Add:

- strict TypeScript
- Tailwind
- basic app shell
- API base URL configuration
- ESLint/Prettier
- accessible default form primitives

Do not build final visual polish yet.

---

## T-004 — Local PostgreSQL
**Backlog:** BL-001

Add Docker Compose with PostgreSQL.

Add `.env.example`.

Never add real credentials.

**Done when:**

backend can run migration against local PostgreSQL.

---

# Phase 1 — Canonical data

## T-010 — Create city/hub schema
**Backlog:** BL-010, BL-011

Implement migrations/models for:

- cities
- hubs
- hub_aliases
- hub_provider_refs

Follow `DATABASE.md`.

---

## T-011 — Seed test cities and hubs
**Backlog:** BL-010, BL-011

Seed enough data for:

- 成都
- 乐山
- 南京
- 苏州

Include representative airports and passenger railway stations.

Clearly mark seed/source metadata.

---

## T-012 — City/hub service
**Backlog:** BL-012

Implement normalized search.

Endpoints:

```text
GET /api/cities/search?q=
GET /api/cities/{city_id}/hubs
```

Unit test aliases and passenger filtering.

---

# Phase 2 — Provider contracts

## T-020 — Define provider interfaces
**Backlog:** BL-013, BL-020, BL-030

Create:

```text
HubProvider
RoutingProvider
RailProvider
```

Also create normalized DTO/domain objects.

No raw provider response should escape adapter modules.

---

## T-021 — Fixture providers
**Backlog:** supports Milestone 0

Before credentials exist, create deterministic fixture providers for:

- hub search
- route result
- train result

This enables end-to-end backend work without external APIs.

Fixture data must be obviously fake/test data in development.

---

# Phase 3 — AMap integration

## T-030 — AMap settings and client
**Backlog:** BL-013, BL-020

Server-side only.

Implement:

- base URL
- API key loading
- timeout
- error translation
- request logging without secrets

---

## T-031 — AMap POI adapter
**Backlog:** BL-013

Resolve candidate airport/rail POIs.

Normalize to `HubCandidate`.

Do not automatically persist every POI.

---

## T-032 — AMap routing adapter
**Backlog:** BL-021, BL-022

Implement:

- transit
- driving

Return normalized:

- duration
- distance
- walking
- transfer count
- segments

Add provider fixture parsing tests.

---

## T-033 — Route cache
**Backlog:** BL-023

Add migration/model/repository.

Implement configured TTL.

Test cache hit/miss.

---

# Phase 4 — Railway POC

## T-040 — Railway data ingestion spike
**Backlog:** BL-031
**Status:** DONE

Goal:

Prove a dated train query can return concrete trains.

Do not over-engineer national ingestion before this works.

Document:

- dataset/source
- update frequency
- station identifier mapping
- limitations

---

## T-041 — Rail station reconciliation
**Backlog:** BL-031, BL-033
**Status:** DONE

Map provider/GTFS station IDs to canonical railway hubs.

Support aliases.

Flag unresolved mappings for diagnostics.

---

## T-042 — Rail search service
**Backlog:** BL-032, BL-033
**Status:** DONE

Implement:

```python
search_trips(
    origin_hubs,
    destination_hubs,
    service_date
)
```

Return concrete trains.

Test multiple destination-city stations.

---

## T-043 — Cross-midnight query
**Backlog:** BL-034
**Status:** DONE

Given an arrival late at night, support trains on the following calendar day as needed.

Test:

```text
arrival: 23:10
candidate departure: next day 06:00
```

---

# Phase 5 — Safe Transfer Time

## T-050 — STT config
**Backlog:** BL-040, BL-041
**Status:** DONE

Create versioned config in backend.

Include baggage modes.

---

## T-051 — STT calculator
**Backlog:** BL-040, BL-041, BL-043
**Status:** DONE

Return:

```text
theoretical_earliest
recommended_earliest
breakdown
rules_version
```

---

## T-052 — Train connection classification
**Backlog:** BL-043, BL-051
**Status:** DONE

For each train:

```text
SPACIOUS
SAFE
TIGHT
INFEASIBLE
```

Write unit tests before integrating into API.

---

# Phase 6 — Recommendation engine

## T-060 — Candidate generation
**Backlog:** BL-050
**Status:** DONE

Given transfer city, generate active passenger railway stations.

---

## T-061 — Candidate evaluation
**Backlog:** BL-051, BL-052
**Status:** DONE

For each station:

1. transit route
2. driving route
3. dated trains
4. STT
5. feasible train list
6. component scores

Parallelize independent external calls where practical.

---

## T-062 — Ranking and explanation
**Backlog:** BL-052, BL-053, BL-054
**Status:** DONE

Implement deterministic scoring.

Return explanation reasons.

Tests must prove ranking changes when:

- transfer duration changes
- safe train count changes
- fallback train availability changes

---

# Phase 7 — Milestone 0 CLI/API proof

## T-070 — Chengdu → Leshan vertical slice
**Status:** DONE

Input:

```text
transfer city: 成都
arrival hub: 成都天府国际机场
arrival datetime: 2026-10-03 14:20 Asia/Shanghai
destination city: 乐山
baggage: checked
```

Output at minimum:

```text
成都东
  transit duration
  driving duration
  recommended_departure_after
  concrete trains

成都南
  transit duration
  driving duration
  recommended_departure_after
  concrete trains

recommendation
  selected hub
  reasons
```

Use real providers when credentials/data permit.

Otherwise fixture mode must demonstrate the complete application flow.

**Hard gate:** Do not begin full result UI until this task works.

---

# Phase 8 — Transfer Evaluation API

## T-080 — `POST /api/transfer/evaluate`
**Backlog:** BL-060
**Status:** DONE

Implement request/response models.

Example request:

```json
{
  "transfer_city": "成都",
  "arrival_hub": "成都天府国际机场",
  "arrival_datetime": "2026-10-03T14:20:00+08:00",
  "destination_city": "乐山",
  "baggage_mode": "CHECKED",
  "include_alternative_hubs": false,
  "routing_preference": "BALANCED"
}
```

---

## T-081 — Partial failure behavior
**Backlog:** BL-055, BL-062
**Status:** DONE

Test:

- transit provider failure but driving available
- one candidate station route failure
- railway provider failure
- no feasible trains

Return stable error/warning codes.

---

# Phase 9 — Web MVP

## T-090 — Query form
**Backlog:** BL-070, BL-071
**Status:** DONE

Build real workflow form.

Do not add marketing content.

---

## T-091 — Result summary
**Backlog:** BL-072
**Status:** DONE

Show:

- recommended hub
- safe transfer time
- earliest recommended train
- reason

---

## T-092 — Candidate station comparison
**Backlog:** BL-073
**Status:** DONE

Prefer a readable list/table architecture over a decorative card grid.

Desktop and mobile must both work.

---

## T-093 — Train list
**Backlog:** BL-074, BL-075
**Status:** DONE

Show concrete train numbers.

Visually differentiate:

- spacious
- safe
- tight
- infeasible

Infeasible hidden by default.

---

## T-094 — Canonical city / hub discovery and autocomplete
**Backlog:** BL-078
**Status:** DONE

No blank screens.

Use the canonical city search and city hubs endpoints for editable, debounced
autocomplete. Suggestions are optional; aliases and free text continue through
backend canonical resolution.

---

## T-095 — Playwright E2E and responsive QA
**Backlog:** BL-077
**Status:** DONE

Verify at least:

- desktop laptop viewport
- mobile viewport
- deterministic Playwright core flow with provider API interception
- Asia/Shanghai and America/Los_Angeles browser timezones
- loading, partial, no-recommendation, typed API errors, alias input and train expansion

---

## T-096 — Production readiness and live integration hardening
**Backlog:** BL-079
**Status:** DONE

Add a reproducible live preflight/smoke workflow for the real Chengdu → Leshan
transfer path. Verify database/migration/canonical/GTFS/reconciliation/provider
configuration, FastAPI response invariants, Next.js same-origin rewrite, and
secret-safe diagnostics without changing STT, ranking, railway semantics or
adding persistence.

---

## T-097 — Production observability and operational diagnostics
**Backlog:** Production operations
**Status:** DONE

Add request correlation, safe structured application/provider timing events,
stable failure classification, lightweight liveness/readiness endpoints and
focused live-smoke diagnostics. Preserve the existing transfer API, STT,
ranking, railway and routing semantics; do not add persistence or an external
telemetry platform.

---

## T-098 — Public Launch Rights & Evidence Closure
**Backlog:** BL-105
**Status:** DONE

Create an auditable rights-evidence register and deterministic offline release
gate checker. Keep the engineering review complete while leaving the public
launch BLOCKED when AMap account/cache rights, railway data rights, attribution,
privacy or coordinate evidence is absent. Do not add provider contracts,
secrets, speculative attribution copy, business-logic changes or migrations.

# Phase 10 — P1 enhancements

Only after P0 MVP works.

## T-100 — Route segment detail
**Backlog:** BL-025, BL-076
**Status:** DONE

Expose ordered, provider-independent route segments through the existing route/API
models and show them in an accessible, collapsed-by-default detail panel without
changing routing, STT, ranking, or partial-failure semantics.

## T-101 — Nearby railway alternatives
**Backlog:** BL-080, BL-082
**Status:** DONE

Add destination-side nearby canonical railway alternatives with bounded radius
discovery and additive train destination metadata.  Reuse the existing rail
search, STT and ranking semantics; do not add destination last-mile scoring.

## T-102 — Nearby airport alternatives
**Backlog:** BL-081
**Status:** DONE

Add arrival-side nearby canonical airport alternatives with bounded discovery.
Keep the primary arrival evaluation authoritative; evaluate each alternative as
an isolated same-arrival-time what-if using the existing railway, routing, STT
and ranking services. Expose the results additively in the transfer API and
render them in a separate comparison section without flight search or global
airport ranking.

## T-103 — Flexible date comparison
**Backlog:** BL-090, BL-091
**Status:** DONE

Add bounded, opt-in China-local date comparison over the existing transfer
evaluation pipeline. Expose deterministic convenience metrics and per-date
partial/out-of-range status without changing primary candidate ranking, STT,
GTFS semantics or alternative-hub behavior.

## T-104 — Shareable query URL
**Backlog:** P1 product enhancement
**Status:** DONE

Encode non-sensitive query parameters in URL.

The frontend now uses a typed, allowlisted query codec for the transfer form. It
restores valid query state without automatic evaluation, updates the browser URL
after a successful submission, and provides a last-submitted-query copy action.

---

# Required commands before marking a task complete

Backend:

```bash
ruff check .
pytest
```

Frontend:

```bash
npm run lint
npm run typecheck
npm test
```

When E2E exists:

```bash
npx playwright test
```

If repository scripts differ, use the canonical scripts defined in `README.md`.

---

# First Codex session stopping point

A good first Codex implementation session should normally stop after:

- repository bootstrap
- DB running
- city/hub models
- provider interfaces
- fixture providers
- a basic health/API test

Do **not** ask Codex to implement the entire product in one prompt.

The second session can focus on AMap.

The third can focus on railway POC.

This keeps errors local and architectural drift manageable.
