# Public launch licensing review (BL-105)

**Review date:** 2026-09-22 (Asia/Shanghai)  
**Review type:** engineering release-readiness and evidence audit  
**Launch gate:** **BLOCKED pending rights and account evidence**

This document records what the repository currently does and which facts are
still unproven before a public or commercial launch. It is not legal advice and
does not grant permission to use any provider or dataset. The absence of an
evidence item is intentionally treated as unresolved; a public URL or a feed
that is technically downloadable is not, by itself, a redistribution licence.

## Review status versus launch status

- **BL-105 engineering audit:** `COMPLETE`.
- **Rights evidence closure:** tracked in
  [`PUBLIC_LAUNCH_EVIDENCE.md`](PUBLIC_LAUNCH_EVIDENCE.md); external contracts
  and account records are deliberately kept outside Git.
- **Public-launch decision:** `BLOCKED` until the mandatory evidence gates are
  cleared. A completed audit is not a public-use authorization.

The offline checker is intentionally conservative:

```bash
uv run --project apps/api python scripts/check_public_launch_readiness.py
uv run --project apps/api python scripts/check_public_launch_readiness.py --json
```

It validates the recorded gate metadata only. It cannot verify a contract,
provider account entitlement or legal interpretation.

## Scope and current repository usage

| Component | Current usage | Source/vendor | Evidence in repository | Public/commercial status | Caching/storage | Display/attribution | Privacy considerations | Release classification | Required action / owner |
|---|---|---|---|---|---|---|---|---|---|
| AMap Web Service API | Server-side POI and routing requests | AMap Web Service 2.0 | `apps/api/app/providers/amap/client.py`, `hub_provider.py`, `routing_provider.py`; `AMAP_API_KEY` is backend-only | Account type, approved application, quota and public/commercial authorization are not recorded in this repository | See `AMAP-CACHE-01` | Route values and normalized segments reach the API/UI; no map tile page is rendered | Canonical hub coordinates and query parameters are sent for route/POI operations; no browser/device geolocation is used | **BLOCKED** | Obtain account/application/technical-license evidence and approved use scope; owner: operator/vendor contact |
| AMap route results | Normalized duration, distance, walking/transfer metrics and segments | AMap | `RouteOption` and transfer response schemas; raw provider JSON does not escape the adapter | Depends on AMap authorization and display terms | Persisted by `RouteCacheService`; see `AMAP-CACHE-01` | Current UI has no explicit user-facing “高德地图” attribution label | Add provider/data-flow disclosure to the product privacy notice where applicable | **BLOCKED** | Confirm route-result display and attribution terms; owner: product/legal/AMap |
| `route_cache` | PostgreSQL cache keyed by provider/hubs/mode/time bucket | Project database, populated from AMap results | `apps/api/app/services/route_cache.py`, migration `0005_create_route_cache.py` | Storage permission for AMap-derived results is unproven | Transit default 6h; driving default 30m; raw payload is explicitly set to `null` | Cached normalized values can be returned to API/UI | No secrets or user profile data are stored by this cache | **BLOCKED** | Obtain written cache/storage permission or change the release architecture after a separate decision; owner: vendor/legal/engineering |
| Canonical hub coordinates | Seeded and reconciled airport/railway coordinates used as routing inputs | Project seed, GTFS/AMap reconciliation | `data/seed/cities_and_hubs.json`, reconciliation services and provider refs | Coordinate provenance and any public-display rights need a production data record | Stored in canonical `hubs`; not an AMap raw-payload store | Names/coordinates are used in the query/result flow | No device location is collected by current code | **CONDITIONAL** | Record production coordinate provenance and review public use; owner: data owner |
| Railway GTFS feed | Local timetable POC loaded by importer/provider | `wensimehrp/chinese-railway-gtfs` | `scripts/import_rail_gtfs.py`, `apps/api/app/providers/rail_gtfs/`, README; feed is in ignored `data/external/` | Feed license, upstream authorization, commercial/public display and redistribution rights are not stated | See `RAIL-02`; raw feed is not committed | Timetable-derived results are exposed through API/UI; source attribution terms are unknown | No passenger identity is present in the imported timetable model; source obligations remain unresolved | **BLOCKED** | Obtain upstream publisher/source permission and license terms; owner: product/legal/source maintainer |
| `rail_services` / `rail_service_stops` | Persisted normalized dated timetable and station-stop rows | Derived from the local GTFS feed | migration `0006_create_rail_services_and_stops.py`, GTFS importer/provider | Persistence and derived-data rights are unproven | Database persistence is a derived copy; process-local railway caches also exist | `RailTrip` schedule values are public API/UI data | No secrets are stored; provenance and source timestamp are carried when trustworthy | **BLOCKED** | Confirm rights cover import, derived persistence, API/UI display and updates; owner: source maintainer/legal |
| Railway search/feed caches | Normalized `RailTrip` results and parsed-feed reuse | Local GTFS provider | `apps/api/app/services/railway_cache.py`, `GTFSRailProvider` | Cache/display permission follows the unresolved feed rights | Bounded TTL/LRU/single-flight; no GTFS raw row in result cache; source identity invalidates entries | Cached results can be displayed as timetable information | No personal data in cache values | **BLOCKED** | Include cache and feed reload behavior in the source permission review; owner: source maintainer/legal |
| Railway source metadata | Provider/source name, version, coverage, timestamp/freshness | GTFS feed metadata when present | `RailwaySourceMetadata`, API metadata and UI notice | Technical metadata is not a licence or attribution grant; current source timestamp may be `UNKNOWN` | Metadata is retained with normalized results | UI exposes source/freshness notice, but exact legal attribution text is unknown | No personal data | **CONDITIONAL** | Obtain required attribution text/link and trustworthy update provenance; owner: source maintainer/product |
| Frontend/API timetable display | Candidate stations, routes, planned trains and source notice | Project UI/API, backed by AMap and GTFS | `apps/web/components/transfer/`, transfer schemas, README | Public display is effectively redistribution/use of derived provider data; rights are unresolved | API/route/rail caches may feed the display | AMap and railway attribution requirements are not fully implemented/confirmed | Publish a privacy notice describing provider data flows | **BLOCKED** | Complete AMAP/RAIL gates before public launch; owner: product/legal/engineering |

The application deliberately does not contain AMap raw responses, polylines,
map tiles, 12306/Ctrip scraping, ticket inventory, prices or booking data.
That reduces the reviewed surface but does not remove the need to confirm the
terms for normalized route/POI results and cached storage.

## Evidence reviewed

### AMap

The current AMap service agreement is the controlling public source for this
engineering review. It states that organizational/commercial use requires a
technical service licence or other approved arrangement, that each application
needs a managed key, and that quotas/concurrency remain the developer's
responsibility. It also restricts storing/caching, copying, derivative use,
public display and API wrapping unless the applicable written permission or
documented product terms allow it. The agreement requires preserving notices
and identifies “高德地图” as the source when the service page is displayed.

Reviewed official pages (accessed 2026-09-22):

- [高德地图开放平台服务协议](https://lbs.amap.com/pages/terms/) (updated 2025-12-03; see sections 3, 4.12 and 7).
- [技术服务使用许可协议](https://lbs.amap.com/pages/authorization/) (certified developer and purchased/issued licence scope).
- [Web 服务 API 使用条款](https://lbs.amap.com/pages/law-web-service/summary) (Web Service API use and third-party map-service restriction).
- [路径规划 Web Service 文档](https://lbs.amap.com/api/webservice/guide/api/direction) (route-result API usage; this does not override the service agreement).
- [开放平台隐私权政策](https://lbs.amap.com/api/compliance-center/protocols/privacy_202604) (2026-07 edition, last updated 2026-09-16; developer disclosure and consent expectations).

Repository evidence confirms `AMAP_API_KEY` is backend-only and is not in the
frontend bundle or logs. It does **not** prove the account's certification,
technical licence, approved scenario, quota, or written exception for the
current normalized-result cache. No account certificate, licence document or
vendor ticket is committed (and none should be committed with secrets).

The repository has no other map-service integration, so the Web Service API
exclusive-map-service condition has no known current code-level conflict. It
must be rechecked if another map provider is introduced.

### Railway source

The local source clone is `wensimehrp/chinese-railway-gtfs` and its README says
the feed is updated weekly, contains China railway timetable/station/line data,
uses OpenStreetMap for station coordinates, and obtains timetable/route data
from an unnamed rail service. The README points to the GTFS specification, but
does not provide a dataset licence, publisher authorization, redistribution
permission, commercial-use permission, derivative-data permission, attribution
text, or a source update contract. The local clone has no `LICENSE`, `COPYING`
or `NOTICE` file, and the ignored feed archive contains no licence notice.

The GTFS format/specification is a file format; it is not a licence for the
underlying timetable, route or coordinate data. `agency.txt` and a 12306 URL in
the feed are content/provenance clues, not permission from the data owner.
Accordingly, the source identity is known enough to request clarification, but
public/commercial rights remain **UNKNOWN**.

## Release gates

Statuses are engineering release gates, not legal opinions.

| Gate | Issue / evidence | Status | Closure evidence required | Owner |
|---|---|---|---|---|
| AMAP-01 | Production account, developer certification, application scope, commercial/non-commercial approval and quota are not recorded | **BLOCKED** | Account/application identity, approved scenario, applicable technical licence or written non-commercial approval, quota/QPS evidence | Operator + AMap/vendor |
| AMAP-02 | Route/POI normalized values are returned to users; current UI has no explicit AMap attribution/notice | **CONDITIONAL** | Written confirmation of required attribution for route-result/POI display, then implement the exact approved notice if required | Product/legal + AMap |
| AMAP-CACHE-01 | PostgreSQL `route_cache` stores normalized AMap route results for configured TTLs; terms restrict storage/cache absent permission | **BLOCKED** | Written permission/term confirmation covering normalized route storage, TTL, reuse and user display, or a separately approved cache redesign | Vendor/legal + engineering |
| AMAP-MAPS-01 | No third-party map provider is integrated; route-result UI has no map tiles | **CLEAR WITH MONITORING** | Keep this architecture constraint and review any future map-provider addition | Engineering |
| RAIL-01 | Feed provenance is partly documented, but dataset publisher and licence are undisclosed | **BLOCKED** | Upstream/source-owner statement identifying data origin and public/commercial rights, including derivative use | Product/legal + source maintainer |
| RAIL-02 | Normalized rail rows are persisted and timetable results are exposed through API/UI and caches | **BLOCKED** | Permission covering import, derived database rows, API/UI display, cache/reuse, updates and withdrawal | Product/legal + source maintainer |
| RAIL-03 | Feed source update timestamp is absent; current system truthfully reports `UNKNOWN` | **CONDITIONAL** | Documented release channel/version and trustworthy source timestamp/update responsibility, or retain an explicit unknown warning | Source maintainer + operations |
| ATTR-01 | Required AMap source/legal notices are not represented as a dedicated user-facing UI element | **CONDITIONAL** | Confirm exact notice/placement and preserve provider notices; add only after confirmation | Product/legal + frontend |
| ATTR-RAIL-01 | UI has source/freshness metadata but no verified railway licence/attribution text | **BLOCKED** | Exact attribution wording/link and permission to show it with timetable results | Source maintainer + product |
| PRIV-01 | Current code sends canonical hub coordinates/query context to AMap; no device geolocation is used | **CONDITIONAL** | Public privacy notice names AMap products/purpose/data flow, links the AMap policy and records applicable consent/legal basis | Product/legal |
| DATA-COORD-01 | Seed/GTFS/AMap coordinate provenance and coordinate-system distinctions are implemented, but production ownership/display rights are not documented | **CONDITIONAL** | Production coordinate inventory with source, licence/permission and GCJ02/WGS84 handling | Data owner |

## Cache and derived-data assessment

### AMap `RouteCacheService`

The cache is technically bounded by route mode/time bucket and TTL and stores
normalized fields rather than raw AMap JSON. That is a security and data
minimization improvement, but it is still storage and reuse of AMap-derived
service data. The current repository cannot infer a permission from the fact
that the cache is normalized, short-lived, server-side, or paid by a quota.
Do not remove or silently bypass the cache as part of this review; resolve the
gate with AMap/vendor evidence or make a separate architecture decision.

### `RailwaySearchCache` and GTFS feed parsing cache

These are process-local, bounded caches of normalized `RailTrip` results and
parsed feed objects. They do not store raw provider payloads in the search
result cache, and source identity/version changes invalidate reuse. They are
still derived-data reuse, so the railway source permission must explicitly
cover persistence, memory caching, API/UI display and feed replacement. The
current `source_updated_at: null` / `freshness_status: UNKNOWN` behavior is
truthful and must not be replaced with file mtime or import time.

## BL-037 interaction

`RailProvider` is already an application boundary and `GTFSRailProvider` is a
replaceable POC implementation. That architecture is sufficient for a future
legal/official source; BL-037 itself does not provide rights for the current
feed and is not a substitute for this review.

Before accepting a replacement provider, require the provider record to state:

1. source identity and publisher;
2. public/commercial use and redistribution rights;
3. API, storage, cache and derived-data rights;
4. required attribution and privacy notices;
5. source version/timestamp and freshness semantics;
6. service-date/cross-midnight semantics and provider IDs;
7. reconciliation behavior and operational limits (quota/retry/availability).

## Attribution and privacy actions

Before launch, product/legal should approve a public privacy notice that names
the AMap route/POI services, explains that canonical hub coordinates and query
context are sent server-side for routing/discovery, links to the applicable
AMap privacy policy, and states the legal basis/consent flow if personal data
is later added. The current application does not access browser geolocation or
send an end-user device coordinate.

The UI should not claim that AMap or the railway source endorses the product.
If the approved terms require a visible AMap source notice or railway
attribution, add the exact text and link in a separate implementation task
after the evidence is obtained. Do not invent attribution language now.

## Exact evidence still required before public launch

1. AMap account/application owner and developer-certification evidence (stored
   in the operator's restricted release record, not in Git).
2. AMap technical-service licence or written approval for this public or
   commercial use case, including quota/QPS and the relevant endpoints.
3. Written AMap confirmation for server-side normalized route/POI storage,
   `route_cache` TTL/reuse and user-facing route-result display, or an approved
   alternative design.
4. AMap attribution/legal-notice wording and placement confirmation.
5. Railway feed publisher/source identity and the acquisition chain for
   timetable, station and coordinate data.
6. Railway licence/permission covering public and commercial use,
   redistribution, derived PostgreSQL rows, process caches, API/UI display,
   weekly updates and withdrawal/takedown handling.
7. Required railway attribution text/link and a trustworthy update timestamp or
   an approved ongoing `UNKNOWN` source-freshness policy.
8. Production coordinate provenance and rights record for canonical and
   provider-derived coordinates.
9. Product privacy notice and approval of the AMap data-flow disclosure.

Until items 1–7 are evidenced, the correct release decision is to keep the
public launch blocked. Fixture mode and internal development can continue under
the existing provider disclaimers.

## Scope confirmation

This review does not change STT, ranking, train classification, GTFS semantics,
AMap request semantics, retry/quota/cache settings, public API contracts,
frontend recommendation logic, database schema or migrations. It adds no
provider, no scraping, no ticket/price/inventory capability and no external
telemetry dependency.
