# BACKLOG.md

## Conventions

Priority:

- **P0** — required for MVP
- **P1** — high-value after MVP core works
- **P2** — later / V2 candidate

Status values:

- `BACKLOG`
- `READY`
- `IN_PROGRESS`
- `BLOCKED`
- `DONE`

Do not pull P1/P2 work while a blocking P0 vertical slice is incomplete.

---

# Epic E0 — Project foundation

### BL-001 — Monorepo bootstrap
**Priority:** P0  
**Status:** DONE

Create:

- `apps/web`
- `apps/api`
- shared repository docs
- environment templates
- Docker Compose for PostgreSQL
- basic CI/lint/test commands

**Acceptance:**
- web starts locally
- API starts locally
- PostgreSQL starts locally
- one documented command path exists for setup

### BL-002 — Shared domain conventions
**Priority:** P0  
**Status:** DONE

Define:

- hub types
- baggage mode
- route modes
- connection statuses
- Asia/Shanghai time handling
- provider result metadata

### BL-003 — Observability baseline
**Priority:** P1  
**Status:** DONE

Implemented by the request-correlation, structured event, provider timing,
cache outcome, failure classification, and secret-redaction observability
layer covered by T-097 and its offline tests.

Structured logs with:

- request id
- provider
- latency
- cache hit/miss
- error class

No external observability SaaS required for MVP.

---

# Epic E1 — Canonical cities and hubs

### BL-010 — City model and seed data
**Priority:** P0  
**Status:** DONE

Implement canonical China-mainland city entities.

Seed MVP test cities:

- 成都
- 乐山
- 南京
- 苏州

### BL-011 — Hub model and aliases
**Priority:** P0  
**Status:** DONE

Support:

- airport
- railway
- aliases
- railway station code
- provider references
- active/passenger flags

### BL-012 — Hub search API
**Priority:** P0  
**Status:** DONE

Endpoints:

```text
GET /api/cities/search
GET /api/cities/{city_id}/hubs
```

### BL-013 — AMap POI provider
**Priority:** P0  
**Status:** DONE

Use AMap to discover/verify airport and station POIs.

Must normalize provider results before application use.

### BL-014 — Hub reconciliation
**Priority:** P1  
**Status:** DONE

Rules for:

- duplicate station names
- station vs metro-station false positives
- airport aliases
- inactive hubs

### BL-015 — Manual hub correction workflow
**Priority:** P1  
**Status:** DONE

Simple operator CLI override mechanism with transactional ProviderRef sync and audit history.

Implemented through `scripts/manage_hub_overrides.py`; no public admin API is exposed.

No full admin panel required.

---

# Epic E2 — Intra-city routing

### BL-020 — RoutingProvider interface
**Priority:** P0  
**Status:** DONE

Support:

- public transit
- driving
- walking where useful

### BL-021 — AMap transit routing
**Priority:** P0  
**Status:** DONE

Normalize:

- total duration
- walking distance
- transfer count
- segments
- provider timestamp

### BL-022 — AMap driving routing
**Priority:** P0  
**Status:** DONE

Return duration/distance.

No taxi price needed.

### BL-023 — Route cache
**Priority:** P0  
**Status:** DONE

Cache by:

- origin
- destination
- mode
- time bucket

### BL-024 — Public transit unavailable/night mode
**Priority:** P0  
**Status:** DONE

Return an explicit, provider-neutral route availability state rather than
falling back to a daytime route. A deterministic provider no-route response
is normalized as `UNAVAILABLE` / `NO_ROUTE`; provider, timeout, quota and
operation-budget failures remain distinct. Allowed route modes are hard
constraints: a transit-only request never adds driving, while a request that
allows both modes keeps a usable driving result when transit is unavailable.
The route cache, retry policy and AMap request semantics are unchanged.

### BL-025 — Route detail UI
**Priority:** P1  
**Status:** DONE

Show:

- walking
- metro/bus segments
- transfer points
- total time
- driving comparison

---

# Epic E3 — Railway provider

### BL-030 — RailProvider interface
**Priority:** P0  
**Status:** DONE

Normalized search contract for station-to-station trips.

### BL-031 — GTFS/local timetable ingestion POC
**Priority:** P0  
**Status:** DONE

Build an importer that can map railway stops to canonical hubs.

### BL-032 — Concrete dated train search
**Priority:** P0  
**Status:** DONE

Return:

- train number
- origin station
- destination station
- departure
- arrival
- duration
- train type

### BL-033 — City-to-city rail expansion
**Priority:** P0  
**Status:** DONE

Input:

```text
origin station candidate
destination city
```

Search all in-city destination railway stations.

### BL-034 — Cross-midnight rail search
**Priority:** P0  
**Status:** DONE

Support arrival time near midnight and next-day candidate trains.

### BL-035 — Rail provider cache
**Priority:** P0  
**Status:** DONE

Add a bounded process-local cache for normalized railway search results and
reuse unchanged local GTFS feed parsing. Cache identity includes provider,
source, station sets, service date and search window; TTL, capacity, eviction
and concurrent single-flight behavior are configurable. Cache hits preserve
railway source metadata and do not alter STT, ranking or availability semantics.

### BL-036 — Data freshness warning
**Priority:** P0  
**Status:** DONE

Expose source update time when the timetable source provides a trustworthy
timestamp, otherwise show an explicit unknown-state warning. Freshness is
diagnostic only and does not affect railway availability or ranking.

### BL-037 — Future official/alternate provider
**Priority:** P1  
**Status:** BACKLOG

Provider abstraction must permit replacement without business-logic rewrite.

---

# Epic E4 — Safe Transfer Time engine

### BL-040 — STT domain model
**Priority:** P0  
**Status:** DONE

Inputs:

- arrival mode
- arrival datetime
- baggage mode
- route duration
- hub category

Outputs:

- theoretical earliest
- recommended earliest
- breakdown
- rules version

### BL-041 — Flight → railway rules
**Priority:** P0  
**Status:** DONE

Initial configurable buffers from PRD.

### BL-042 — Railway → railway cross-station rules
**Priority:** P0  
**Status:** DONE

Explicitly distinguishes airport-to-railway, railway same-station and railway
cross-station STT. Same-station transfers skip self-routing; cross-station
transfers reuse normalized routing/cache/provider failure semantics, with
centralized railway preparation buffers and additive `transfer_kind` output.

### BL-043 — Connection classification
**Priority:** P0  
**Status:** DONE

Classify:

- spacious
- safe
- tight
- infeasible

### BL-044 — STT unit tests
**Priority:** P0  
**Status:** DONE

Cover:

- checked baggage
- no baggage
- uncertain baggage
- route duration differences
- midnight crossing

---

# Epic E5 — Candidate hub evaluation

### BL-050 — Candidate railway station generation
**Priority:** P0  
**Status:** DONE

From transfer city, select relevant passenger railway hubs.

### BL-051 — Train feasibility filter
**Priority:** P0  
**Status:** DONE

Remove trains earlier than theoretical feasible threshold.

Mark trains between theoretical and recommended as tight.

### BL-052 — Ranking formula
**Priority:** P0  
**Status:** DONE

Implement deterministic score from PRD.

### BL-053 — Explainable recommendation
**Priority:** P0  
**Status:** DONE

Return 2–4 plain-language reasons.

### BL-054 — Backup-train robustness
**Priority:** P0  
**Status:** DONE

Expose the earliest later SAFE/SPACIOUS scheduled connection as candidate-local backup metadata.
This is descriptive timetable information only: it does not change STT, candidate ranking, status,
date convenience, nearby-airport isolation or railway provider call counts.

### BL-055 — Partial provider failure
**Priority:** P0  
**Status:** DONE

Return partial candidate results when one route/provider fails.

---

# Epic E6 — Transfer Evaluation API

### BL-060 — `POST /api/transfer/evaluate`
**Priority:** P0  
**Status:** DONE

Input:

- transfer city
- arrival hub
- arrival datetime
- destination city
- baggage mode
- routing preference
- include alternatives

### BL-061 — Parallel candidate evaluation
**Priority:** P0  
**Status:** DONE

Candidate stations are evaluated with a bounded, configurable scheduler. The
production request graph gives each candidate task its own SQLAlchemy
`AsyncSession`; immutable routing/GTFS feed resources and the existing railway
cache remain shared. Results are restored to canonical input order before the
existing ranking/recommendation services run. A value of `1` preserves
sequential execution. This policy is separate from the AMap concurrency guard
and request-level operation/retry budgets.

### BL-062 — Stable error model
**Priority:** P0  
**Status:** DONE

Examples:

```text
CITY_NOT_FOUND
HUB_NOT_FOUND
NO_CANDIDATE_STATIONS
ROUTING_PROVIDER_UNAVAILABLE
RAIL_DATA_UNAVAILABLE
NO_FEASIBLE_TRAINS
```

---

# Epic E7 — Web query experience

### BL-070 — Query form
**Priority:** P0  
**Status:** DONE

Fields:

- transfer city
- arrival hub
- date
- arrival time
- destination city
- baggage mode
- routing preference
- nearby alternatives toggle

### BL-071 — City/hub autocomplete
**Priority:** P0  
**Status:** DONE

### BL-072 — Result summary
**Priority:** P0  
**Status:** DONE

Show:

- recommended station
- recommended transfer mode
- safe departure-after time
- earliest recommended train
- concise reason

### BL-073 — Candidate station list
**Priority:** P0  
**Status:** DONE

Each row/card:

- station
- transit time
- driving time
- safe train count
- earliest safe train
- recommendation level

### BL-074 — Train list
**Priority:** P0  
**Status:** DONE

Concrete trains with safety status.

### BL-075 — Tight/infeasible visibility
**Priority:** P0  
**Status:** DONE

Hide infeasible by default but allow expansion.

### BL-076 — Route details
**Priority:** P1  
**Status:** DONE

### BL-077 — Mobile workflow
**Priority:** P0  
**Status:** DONE

Full query and results must work on phone.

### BL-078 — Empty/error/partial states
**Priority:** P0  
**Status:** DONE

---

# Epic E8 — Nearby alternative hubs

### BL-080 — Alternative railway stations
**Priority:** P1  
**Status:** DONE

Use destination-city-nearby candidate generation.

### BL-081 — Alternative airports
**Priority:** P1  
**Status:** DONE

Mark clearly as alternatives.

### BL-082 — Radius configuration
**Priority:** P1  
**Status:** DONE

Do not hard-code magic distance inside UI.

### BL-083 — Door-to-door alternative scoring
**Priority:** P2
**Status:** BACKLOG

V2 precursor.

---

# Epic E9 — Flexible dates

### BL-090 — Date range query
**Priority:** P1  
**Status:** DONE

### BL-091 — Date convenience score
**Priority:** P1  
**Status:** DONE
Based on:

- feasible train count
- connection margin
- service distribution

Never call it cheaper or less crowded without data.

---

# Epic E10 — Reliability and release

### BL-100 — Provider timeout/retry policy
**Priority:** P0  
**Status:** DONE

**Implementation contract for the next session:**

- keep timeout ownership at the provider boundary; do not wrap the entire
  transfer evaluation in an unbounded `asyncio.wait_for`;
- reuse the existing AMap per-request timeout and add a bounded attempt budget,
  with a default of one initial attempt plus at most one retry;
- retry only transient connection/read timeout and selected HTTP 5xx failures;
  do not retry authentication, invalid request/coordinate, no-route, response
  parsing, or quota failures by default;
- apply bounded backoff with bounded jitter and a total per-operation budget
  that keeps interactive evaluation responsive;
- retry only after a RouteCache miss; cache hits must make zero provider calls,
  successful retries may write one normal cache entry, and failed attempts must
  not write or invalidate cache entries;
- local GTFS queries must not receive network retry semantics; preserve the
  existing distinction between feed loading, database/provider failure,
  out-of-range dates, reconciliation failure, and `NO_RAIL_SERVICE`;
- preserve partial-failure behavior: one failed route mode remains a partial
  candidate when another mode succeeds, and rail timeout remains provider
  unavailable rather than no service;
- extend existing structured provider events with attempt, retryability,
  retry decision, duration, and final outcome fields without logging secrets or
  raw provider payloads;
- add deterministic offline tests for retry classification, bounded attempts,
  cache interaction, partial failures, cancellation, request correlation, and
  redacted diagnostics;
- no database migration, external telemetry platform, quota/rate limiting,
  circuit breaker, or broad concurrency refactor belongs in BL-100.

### BL-101 — Cache TTL configuration
**Priority:** P0  
**Status:** DONE

### BL-102 — Railway source timestamp
**Priority:** P0  
**Status:** DONE

Normalize railway source/version, explicit update timestamp and service-date
coverage across GTFS and fixture providers.

### BL-103 — User safety disclaimer
**Priority:** P0  
**Status:** DONE

### BL-104 — API quota protection
**Priority:** P1  
**Status:** DONE

### BL-105 — Public launch licensing review
**Priority:** P1  
**Status:** BLOCKED

Review:

- AMap terms
- railway source rights
- cache/display rights
- map display compliance

Engineering review completed on 2026-09-22 in
[`docs/PUBLIC_LAUNCH_REVIEW.md`](docs/PUBLIC_LAUNCH_REVIEW.md). The launch gate
remains blocked because the repository does not contain account-specific AMap
authorization/cache permission or railway dataset provenance, public/commercial
use, derived-storage, redistribution and attribution evidence. This status is
not a claim that either provider has denied permission; it records that release
evidence is missing.

T-098 adds the evidence register at
[`docs/PUBLIC_LAUNCH_EVIDENCE.md`](docs/PUBLIC_LAUNCH_EVIDENCE.md) and the
offline checker `scripts/check_public_launch_readiness.py`. The review workflow
is complete; the public-launch gate remains BLOCKED until external evidence is
recorded by the appropriate owner.

---

# Epic E11 — V2 route graph

All V2 items remain P2 until V1 is validated.

### BL-110 — A→B transfer-city discovery
**Priority:** P2
**Status:** BACKLOG

### BL-111 — flight→rail graph edges
**Priority:** P2
**Status:** BACKLOG

### BL-112 — rail→flight graph edges
**Priority:** P2
**Status:** BACKLOG

### BL-113 — cross-airport flight→flight
**Priority:** P2
**Status:** BACKLOG

### BL-114 — multimodal route score
**Priority:** P2
**Status:** BACKLOG

### BL-115 — schedule robustness score
**Priority:** P2
**Status:** BACKLOG
