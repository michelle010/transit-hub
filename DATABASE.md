# DATABASE.md

## 1. Purpose

This document defines the V1 persistence model for the China-mainland transit hub decision tool.

The database is responsible for:

1. Canonical city and hub identities.
2. Hub aliases and provider identifiers.
3. Railway timetable data when a local/GTFS-backed provider is used.
4. Cached inter-hub route results.
5. Provider sync metadata.
6. Optional anonymous query diagnostics.

It is **not** intended to become a booking, payment, account, or ticket inventory database.

Default database: **PostgreSQL**.

---

## 2. Design principles

### 2.1 Canonical data vs provider data

Canonical product data:

- cities
- hubs
- aliases
- administrative relationships

Provider-derived/ephemeral data:

- public transit routes
- driving durations
- fetched railway timetable snapshots
- raw external payloads

Do not make provider-specific IDs the canonical identity.

### 2.2 Railway stations are hubs

Do not create completely separate airport and station identity systems.

Use one `hubs` table with:

```text
AIRPORT
RAILWAY
```

A future V2 may add:

```text
COACH_TERMINAL
FERRY
```

without changing core relationship logic.

### 2.3 Geographic data

Store canonical longitude/latitude plus explicit coordinate system.

Recommended V1 values:

```text
WGS84
GCJ02
```

PostGIS is optional for MVP.

For early development, numeric longitude/latitude is enough.

If nearby-hub queries become central, add PostGIS in a later migration.

### 2.4 Time

- Store true timestamps as `TIMESTAMPTZ`.
- China service times use `Asia/Shanghai`.
- Train schedules should preserve `service_date` plus local departure/arrival values.
- Cross-midnight trips must not be inferred only from `TIME`; store or derive day offsets.

---

## 3. Core schema

## 3.1 `cities`

Canonical China-mainland city records.

```sql
CREATE TABLE cities (
    id UUID PRIMARY KEY,
    name_zh VARCHAR(64) NOT NULL,
    name_en VARCHAR(128),
    province_name_zh VARCHAR(64) NOT NULL,
    adcode VARCHAR(16),
    longitude NUMERIC(10, 6),
    latitude NUMERIC(10, 6),
    coordinate_system VARCHAR(16) NOT NULL DEFAULT 'GCJ02',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Indexes:

```sql
CREATE UNIQUE INDEX ux_cities_adcode
ON cities(adcode)
WHERE adcode IS NOT NULL;

CREATE INDEX ix_cities_name_zh
ON cities(name_zh);
```

---

## 3.2 `hubs`

Canonical airport and passenger-railway-station table.

```sql
CREATE TABLE hubs (
    id UUID PRIMARY KEY,
    city_id UUID NOT NULL REFERENCES cities(id),
    canonical_name_zh VARCHAR(128) NOT NULL,
    canonical_name_en VARCHAR(256),
    hub_type VARCHAR(32) NOT NULL,
    importance_level SMALLINT NOT NULL DEFAULT 50,
    longitude NUMERIC(10, 6) NOT NULL,
    latitude NUMERIC(10, 6) NOT NULL,
    coordinate_system VARCHAR(16) NOT NULL,
    railway_station_code VARCHAR(32),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    passenger_service BOOLEAN NOT NULL DEFAULT TRUE,
    source VARCHAR(64),
    source_updated_at TIMESTAMPTZ,
    last_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_hubs_type
      CHECK (hub_type IN ('AIRPORT', 'RAILWAY'))
);
```

Indexes:

```sql
CREATE INDEX ix_hubs_city_type
ON hubs(city_id, hub_type);

CREATE UNIQUE INDEX ux_hubs_railway_station_code
ON hubs(railway_station_code)
WHERE railway_station_code IS NOT NULL;
```

Notes:

- `importance_level` helps hide irrelevant freight/small stops by default.
- A station may exist in the database but be filtered if `passenger_service = false`.
- Airport terminals can initially remain one canonical airport hub unless routing quality requires terminal-level sub-hubs later.

---

## 3.3 `hub_aliases`

Handles names such as:

- 北京南
- 北京南站
- Beijing South
- provider spelling variants

```sql
CREATE TABLE hub_aliases (
    id UUID PRIMARY KEY,
    hub_id UUID NOT NULL REFERENCES hubs(id) ON DELETE CASCADE,
    alias VARCHAR(256) NOT NULL,
    normalized_alias VARCHAR(256) NOT NULL,
    source VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Indexes:

```sql
CREATE INDEX ix_hub_aliases_normalized
ON hub_aliases(normalized_alias);

CREATE UNIQUE INDEX ux_hub_aliases_hub_alias
ON hub_aliases(hub_id, normalized_alias);
```

---

## 3.4 `hub_provider_refs`

Maps canonical hubs to provider identifiers without polluting `hubs`.

```sql
CREATE TABLE hub_provider_refs (
    id UUID PRIMARY KEY,
    hub_id UUID NOT NULL REFERENCES hubs(id) ON DELETE CASCADE,
    provider VARCHAR(64) NOT NULL,
    provider_object_type VARCHAR(64),
    provider_id VARCHAR(256) NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Constraint:

```sql
CREATE UNIQUE INDEX ux_hub_provider_refs
ON hub_provider_refs(provider, provider_id);
```

Examples:

```text
AMAP + POI id
GTFS + stop_id
future official rail provider + station id
```

## 3.5 `hub_reconciliation_overrides`

Manual reconciliation is an operational authority layer over the generic
matching rules. It maps an exact provider identity (`provider`,
`provider_object_type`, `provider_hub_id`) to an existing canonical Hub. The
target is never created or renamed by this workflow and must remain an active
passenger Hub of the declared type.

The table keeps the current, replaced, or revoked decision, operator identity,
reason, and the original `HubProviderRef` state needed for a safe revoke. A
partial unique index permits at most one `ACTIVE` override for each provider
identity. `hub_reconciliation_override_events` is append-only and records
APPLY, REPLACE, and REVOKE actions, including the previous target when one
exists. Applying an override and synchronizing `hub_provider_refs` happen in
one transaction.

---

## 4. Railway timetable schema

Two supported modes:

### Mode A — external query provider

Do not persist the full national timetable.

Persist only bounded cache results.

### Mode B — local GTFS/timetable ingestion

Use normalized local tables below.

The application must work against a `RailProvider` interface in either mode.

---

## 4.1 `rail_services`

Represents a dated train/service instance.

```sql
CREATE TABLE rail_services (
    id UUID PRIMARY KEY,
    provider VARCHAR(64) NOT NULL,
    service_date DATE NOT NULL,
    train_no VARCHAR(32) NOT NULL,
    train_type VARCHAR(16),
    origin_hub_id UUID REFERENCES hubs(id),
    destination_hub_id UUID REFERENCES hubs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Indexes:

```sql
CREATE INDEX ix_rail_services_date_train
ON rail_services(service_date, train_no);

CREATE UNIQUE INDEX ux_rail_services_provider_date_train
ON rail_services(provider, service_date, train_no);
```

Do not assume `train_no` is globally unique without date/provider.

---

## 4.2 `rail_service_stops`

Ordered stops for a service.

```sql
CREATE TABLE rail_service_stops (
    id UUID PRIMARY KEY,
    service_id UUID NOT NULL REFERENCES rail_services(id) ON DELETE CASCADE,
    hub_id UUID NOT NULL REFERENCES hubs(id),
    stop_sequence INTEGER NOT NULL,
    arrival_local TIMESTAMP,
    departure_local TIMESTAMP,
    arrival_day_offset SMALLINT NOT NULL DEFAULT 0,
    departure_day_offset SMALLINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Indexes:

```sql
CREATE UNIQUE INDEX ux_rail_service_stop_sequence
ON rail_service_stops(service_id, stop_sequence);

CREATE INDEX ix_rail_service_stops_hub
ON rail_service_stops(hub_id);
```

Why local timestamp plus day offset?

Because cross-midnight service behavior must be explicit and queryable.

The current POC keeps `rail_service_stops.hub_id` required because these rows are
the reconciled, queryable product view of a timetable. A nationwide GTFS feed can
contain many stops that are not in the curated canonical hub registry. The importer
retains the relative `stop_sequence` of matched stops and can therefore import a
passenger service when only the stations relevant to a product query reconcile; it
reports unmatched intermediate stops as diagnostics and never creates canonical hubs
implicitly. A future full-fidelity timetable store may add provider-owned stop rows,
but that is not required for the current station-to-station RailProvider contract.

If the imported source already yields full local datetimes, the ORM model may simplify this representation, but cross-day semantics must remain lossless.

---

## 5. Route cache

## 5.1 `route_cache`

Stores bounded provider route responses between hubs.

```sql
CREATE TABLE route_cache (
    id UUID PRIMARY KEY,
    provider VARCHAR(64) NOT NULL,
    origin_hub_id UUID NOT NULL REFERENCES hubs(id),
    destination_hub_id UUID NOT NULL REFERENCES hubs(id),
    route_mode VARCHAR(32) NOT NULL,
    query_bucket VARCHAR(64) NOT NULL,
    duration_seconds INTEGER,
    distance_meters INTEGER,
    walking_distance_meters INTEGER,
    transfer_count INTEGER,
    normalized_segments JSONB,
    raw_payload JSONB,
    fetched_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Allowed `route_mode` initially:

```text
TRANSIT
DRIVING
WALKING
```

`query_bucket` prevents false cache equivalence.

Examples:

```text
weekday-am-peak
weekday-offpeak
weekend-day
night
exact-2026-10-03T14
```

Early MVP can use a simple bucket scheme.

Indexes:

```sql
CREATE INDEX ix_route_cache_lookup
ON route_cache(
    origin_hub_id,
    destination_hub_id,
    route_mode,
    query_bucket,
    expires_at
);
```

Cache TTL belongs in application config.

Do not enforce provider-dependent TTL through DB constraints.

---

## 6. Rail query cache

If the active provider is query-based rather than locally ingested:

```sql
CREATE TABLE rail_query_cache (
    id UUID PRIMARY KEY,
    provider VARCHAR(64) NOT NULL,
    origin_station_codes JSONB NOT NULL,
    destination_station_codes JSONB NOT NULL,
    service_date DATE NOT NULL,
    normalized_result JSONB NOT NULL,
    raw_payload JSONB,
    fetched_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
```

For MVP, a stable deterministic cache key should be computed in application code:

```text
provider
+ sorted(origin codes)
+ sorted(destination codes)
+ service date
```

Add:

```sql
cache_key VARCHAR(128) UNIQUE NOT NULL
```

in the actual migration.

---

## 7. Provider sync tracking

## 7.1 `provider_sync_runs`

```sql
CREATE TABLE provider_sync_runs (
    id UUID PRIMARY KEY,
    provider VARCHAR(64) NOT NULL,
    dataset VARCHAR(64) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status VARCHAR(32) NOT NULL,
    source_version VARCHAR(128),
    records_seen INTEGER,
    records_created INTEGER,
    records_updated INTEGER,
    error_summary TEXT,
    metadata JSONB
);
```

Statuses:

```text
RUNNING
SUCCESS
PARTIAL
FAILED
```

This matters because user-facing freshness warnings depend on knowing whether a timetable/hub dataset is stale.

---

## 8. Optional anonymous diagnostics

Not required for Milestone 0.

If later enabled:

```sql
CREATE TABLE query_events (
    id UUID PRIMARY KEY,
    session_hash VARCHAR(128),
    transfer_city_id UUID REFERENCES cities(id),
    arrival_hub_id UUID REFERENCES hubs(id),
    destination_city_id UUID REFERENCES cities(id),
    arrival_datetime TIMESTAMPTZ,
    baggage_mode VARCHAR(32),
    include_alternatives BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(32),
    latency_ms INTEGER,
    provider_status JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Never store user identity, ticket order information, or precise long-term personal movement profiles for V1.

---

## 9. Data not stored initially

Do **not** create tables for:

- users
- accounts
- passwords
- payments
- bookings
- train prices
- flight prices
- hotel data
- tickets
- seat inventory

Do not create speculative tables for V2 until a real feature requires them.

---

## 10. Safe transfer rules storage

V1 should keep STT constants in versioned application configuration, not the database.

Example:

```python
SAFE_TRANSFER_RULES_VERSION = "v1"

DEFAULTS = {
    "flight_to_rail": {
        "deplane_minutes": 15,
        "baggage_checked_minutes": 25,
        "baggage_unknown_minutes": 20,
        "airport_internal_minutes": 15,
        "rail_entry_minutes": 30,
        "risk_no_bag_minutes": 20,
        "risk_checked_bag_minutes": 25,
    }
}
```

The response should include:

```text
rules_version
```

so calculations are reproducible.

Move these rules to database/admin configuration only after there is an actual operational need.

---

## 11. Alternative hub support

V1 nearby alternatives can be implemented without a relationship table.

Algorithm:

1. Resolve destination city centroid/boundary.
2. Query nearby hubs within a configured radius.
3. Exclude hubs already belonging to the destination city.
4. Apply hub type and passenger-service filters.
5. Mark them as `alternative = true`.

If PostGIS is later introduced, a geographic index can accelerate this.

Until then, a pre-filtered city adjacency list or application-level Haversine calculation is acceptable for MVP.

---

## 12. Migration plan

Suggested migrations:

```text
0001_create_cities
0002_create_hubs
0003_create_hub_aliases
0004_create_hub_provider_refs
0005_create_route_cache
0006_create_rail_services_and_stops
0007_create_hub_reconciliation_overrides
0008_create_provider_sync_runs (future)
0009_create_rail_query_cache (future)
0010_optional_query_events (future)
```

Do not collapse all changes into one migration once collaborative development begins.

---

## 13. Seed data

Create a small deterministic seed dataset for development:

Cities:

- 成都
- 乐山
- 南京
- 苏州

Hubs should include enough examples to test:

- multi-airport/multi-station city behavior
- multiple destination-city stations
- alternate nearby hubs
- airport → railway routing

Seed data is not production truth.

Mark seed records clearly.

---

## 14. Repository ORM model layout

Suggested:

```text
apps/api/app/db/
├─ base.py
├─ session.py
├─ models/
│  ├─ city.py
│  ├─ hub.py
│  ├─ rail.py
│  ├─ route_cache.py
│  └─ provider_sync.py
└─ migrations/
```

Avoid one giant `models.py`.

---

## 15. Data integrity rules

Enforce:

- hub belongs to one canonical city in V1
- provider references are unique per provider/provider_id
- railway station code is unique when present
- route cache requires `expires_at > fetched_at`
- inactive hubs never become primary recommendations
- passenger-service=false railway hubs are excluded by default
- imported data should be idempotent

Importer re-runs must update existing records, not duplicate them.

---

## 16. Backup / retention

For personal MVP:

- PostgreSQL volume backup is enough.
- Raw provider payload cache may be aggressively expired.
- Canonical hub edits should be retained.
- Sync runs should be retained for debugging.

Before public launch, define formal retention and backup policy.
