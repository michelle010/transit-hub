# GENTS.md

> Codex / AI coding agent working agreement for this repository.
>
> Note: Codex commonly recognizes `AGENTS.md` as the canonical repository instruction file. This repository keeps both `GENTS.md` and `AGENTS.md` with the same content so the requested filename is preserved while Codex can also auto-discover the conventional file.

## 1. Mission

Build a China-mainland transit hub decision tool.

V1 answers:

> I am transferring in city X. I arrive at airport/station A at a known date and time. Which railway station should I go to for my next trip to city B, how should I get there, how much time should I reserve, and which concrete trains are realistically catchable?

This is **not** an OTA, ticketing platform, hotel app, or general travel super-app.

The core value is:

1. Discover the relevant hubs in a transfer city.
2. Compare intra-city transfer time by public transit and driving/taxi.
3. Find concrete trains on the selected date.
4. Calculate safe transfer time.
5. Remove infeasible trains.
6. Rank candidate railway stations with explainable reasons.
7. Optionally show alternative nearby railway stations and airports near the destination.

Read `PRD.md`, `DATABASE.md`, `BACKLOG.md`, and `TASKS.md` before making product or architecture changes.

---

## 2. Product scope

### V1 must support

- China mainland only.
- Airports and passenger railway stations.
- High-speed, EMU/intercity, and conventional rail.
- User-selected date.
- User-provided arrival time.
- Checked baggage option:
  - no checked baggage
  - checked baggage
  - uncertain
- Public transit route.
- Driving/taxi duration.
- Concrete train numbers and departure/arrival times.
- Safe-transfer-time calculation.
- Candidate hub ranking.
- Destination city containing multiple railway stations.
- Optional nearby alternative railway stations and airports.
- Cross-midnight search behavior.

### V1 explicitly does not support

- Ticket purchase.
- Flight/train price comparison.
- Guaranteed seat availability.
- Hotels, attractions, restaurants.
- Coach/bus terminals.
- International, Hong Kong, Macau, or Taiwan routing.
- Automatic discovery of the best transfer city from A to B.
- Full flight-to-flight route planning.
- Real-time flight disruption handling.
- Claims that a connection is guaranteed.

Do not add out-of-scope functionality unless the backlog explicitly promotes it.

---

## 3. Default technical stack

Use this stack unless the existing repository already differs:

### Frontend

- Next.js
- TypeScript
- React
- App Router
- Server Components where appropriate
- Client Components only when interaction requires them
- Tailwind CSS
- Accessible semantic HTML

### Backend

- Python 3.12+
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- httpx for external HTTP calls

### Database

- PostgreSQL
- SQLite is permitted only for local smoke tests if PostgreSQL is unavailable.

### Testing

Backend:
- pytest
- pytest-asyncio

Frontend:
- Vitest where useful
- React Testing Library
- Playwright for end-to-end flows

### Tooling

- Ruff for Python lint/format
- mypy or pyright for meaningful backend type checking
- ESLint + TypeScript strict mode for frontend
- Prettier for frontend formatting

---

## 4. Repository shape

Prefer a simple monorepo:

```text
/
├─ AGENTS.md
├─ GENTS.md
├─ PRD.md
├─ DATABASE.md
├─ BACKLOG.md
├─ TASKS.md
├─ README.md
├─ apps/
│  ├─ web/
│  └─ api/
├─ packages/
│  └─ shared/
├─ data/
│  └─ seed/
├─ scripts/
├─ tests/
├─ docker-compose.yml
└─ .env.example
```

Do not split into microservices.

The backend should remain a modular monolith.

---

## 5. Architecture rules

### 5.1 Provider isolation is mandatory

Never place direct high-dependency provider calls inside route handlers or business rules.

Use explicit adapters:

```text
HubProvider
RoutingProvider
RailProvider
```

Suggested interfaces:

```python
class HubProvider(Protocol):
    async def search_hubs(self, city: str) -> list[HubCandidate]: ...

class RoutingProvider(Protocol):
    async def get_transit_route(self, origin: Coordinate, destination: Coordinate, at: datetime | None = None) -> RouteOption: ...
    async def get_driving_route(self, origin: Coordinate, destination: Coordinate, at: datetime | None = None) -> RouteOption: ...

class RailProvider(Protocol):
    async def search_trips(
        self,
        origin_station_codes: list[str],
        destination_station_codes: list[str],
        service_date: date,
    ) -> list[RailTrip]: ...
```

Business logic must consume normalized domain models, not raw provider responses.

### 5.2 Domain normalization

Normalize:

- city names
- hub names
- aliases
- coordinates
- railway station codes
- dates/times
- provider identifiers

Keep provider-specific payloads at the adapter boundary.

### 5.3 Coordinates

Treat coordinate systems explicitly.

Never silently mix:

- WGS84
- GCJ-02

Internal models must carry coordinate-system metadata or pass through a deliberate converter.

### 5.4 Timezone

All China-mainland service times use `Asia/Shanghai`.

Never assume system local timezone.

Store timestamps with timezone when they represent actual instants.

Railway timetable local service times should preserve service date plus local time semantics.

### 5.5 No invented precision

If a provider gives a duration estimate, preserve it as an estimate.

Do not convert rough/static information into fake minute-level certainty.

Expose:

- source
- fetched_at
- confidence/freshness when relevant

---

## 6. Safe Transfer Time rules

Safe Transfer Time is a first-class domain service.

Do not put STT calculations in frontend code.

Initial model:

```text
STT =
arrival_release_buffer
+ baggage_buffer
+ origin_hub_internal_buffer
+ intra_city_route_duration
+ destination_station_entry_buffer
+ risk_buffer
```

Initial configurable defaults may follow the PRD, but must live in one versioned backend config module.

Return at least:

- earliest_theoretical_departure
- recommended_departure_after
- connection_status
- breakdown

Connection statuses:

```text
SPACIOUS
SAFE
TIGHT
INFEASIBLE
```

Never label `TIGHT` as recommended.

Every result must carry a disclaimer that the calculation is planning guidance, not a guarantee.

---

## 7. Ranking rules

Ranking must be deterministic and explainable.

Initial score:

```text
0.35 * TransferConvenience
+ 0.30 * TrainAvailability
+ 0.25 * SafetyMargin
+ 0.10 * TransferSimplicity
```

Before scoring, apply hard filters:

- inactive hub
- no route
- no relevant train
- every train infeasible
- stale/invalid critical data

Never expose only a score.

Return human-readable reasons, e.g.:

- shorter airport transfer
- more safe trains after 17:00
- more fallback departures
- fewer public-transit transfers

Keep ranking constants centrally configured.

---

## 8. External data rules

### High-level policy

- External services are replaceable providers.
- Secrets remain server-side.
- Never commit API keys.
- Add `.env.example`, not real credentials.
- Cache responsibly.
- Respect provider terms and quotas.
- Do not scrape a third-party commercial product unless explicitly approved in the project plan.

### High德 / AMap

Use backend adapters only.

Do not call a private server API key from browser code.

Use POI/routing data as provider data, not as permanent truth.

### Railway data

For MVP/prototyping, a GTFS-compatible railway dataset may be used.

Do not hard-code the entire app around one unofficial source.

Commercial/public launch requires a data licensing review.

---

## 9. Database rules

Follow `DATABASE.md`.

Key principles:

- `cities` and `hubs` are canonical product data.
- Aliases are separate records.
- Provider IDs are not primary keys.
- A railway station is a type of hub.
- Route results are cache records, not canonical infrastructure data.
- Raw provider payloads may be stored only when useful for debugging and with bounded retention.
- Use Alembic migrations.
- No schema mutation outside migrations after initial bootstrap.

---

## 10. API rules

Prefer REST for V1.

Route handlers must:

1. validate input
2. call application/domain services
3. serialize normalized DTOs

They must not contain provider parsing or ranking formulas.

Suggested endpoints:

```text
GET  /health
GET  /api/cities/search
GET  /api/cities/{city_id}/hubs
GET  /api/routes
GET  /api/trains
POST /api/transfer/evaluate
```

All date/time inputs must be explicit and documented.

Error responses should use stable error codes.

---

## 11. Frontend rules

The primary screen is a decision tool, not a marketing page.

Prioritize:

- one clear query form
- ranked candidate hubs
- concrete train options
- route details
- visible safety status
- transparent recommendation reasons

Avoid:

- excessive cards
- fake metrics
- decorative dashboards
- giant hero sections
- travel-blog content
- price UI
- ticket-purchase CTA

Mobile must support the full core workflow.

Never hide critical safety warnings only in tooltips.

---

## 12. UI state requirements

Every data panel needs:

- loading
- empty
- partial data
- provider failure
- stale data warning
- success

A failure in one provider should not necessarily blank the whole result page.

Example:

- railway data available
- driving route available
- public transit provider failed

The UI should still present useful partial results.

---

## 13. Development sequence

Do not start by building the full polished product.

Follow `TASKS.md`.

First prove Milestone 0:

1. Resolve Chengdu hubs.
2. Get Tianfu Airport → Chengdu East route duration.
3. Get a dated Chengdu East → Leshan train list.
4. Run safe-transfer calculation.
5. Rank at least two candidate stations.

Only after that chain works should the full result UI be built.

---

## 14. Testing requirements

### Unit tests

Must cover:

- STT rules
- baggage variants
- cross-midnight cases
- ranking
- train filtering
- alias normalization
- coordinate conversion helpers
- date/time handling

### Contract/adapter tests

Use saved fixtures for provider parsing.

Do not require paid API calls in ordinary unit tests.

### E2E

At minimum cover:

```text
query form
→ candidate stations
→ selected candidate
→ concrete trains
→ route detail
```

Include one cross-midnight scenario.

---

## 15. Definition of done

A task is not done merely because code compiles.

A feature is done when:

- implementation is complete
- types pass
- lint passes
- relevant tests pass
- errors are handled
- loading/empty states exist where needed
- docs are updated if behavior/schema changes
- no secret is committed
- no new out-of-scope feature has leaked in

For visual work, verify desktop and mobile browser output.

---

## 16. Agent behavior

When working with Codex:

1. Read relevant docs before editing.
2. Inspect existing code before proposing new abstractions.
3. Prefer small, reviewable changes.
4. Complete one vertical slice at a time.
5. Do not rewrite working architecture without evidence.
6. Do not add dependencies without a clear need.
7. Do not create placeholder “TODO architecture” in place of working code.
8. When external credentials are missing, implement:
   - interface
   - fixture/mock provider
   - real adapter skeleton
   - clear setup documentation
   rather than blocking unrelated work.
9. Preserve user-facing Chinese copy as UTF-8.
10. Use English for identifiers and technical code comments unless a Chinese explanation is materially clearer.
11. Record material decisions in README or a lightweight ADR if they affect future work.
12. Before finishing a task, summarize:
   - files changed
   - behavior added
   - tests run
   - remaining blockers

---

## 17. Never do these without explicit approval

- Add payment.
- Add account/authentication.
- Add ticket purchasing.
- Add an LLM dependency for deterministic routing/ranking.
- Use machine learning for V1 ranking.
- Add Redis before actual cache pressure justifies it.
- Introduce Kubernetes.
- Split the backend into microservices.
- Store API secrets in frontend code.
- Claim connections are guaranteed.
- Treat unofficial railway data as commercially cleared.
- Scrape 12306/Ctrip in a way that violates their terms.
