# Public launch evidence register (T-098)

**Review work:** `COMPLETE`  
**Rights evidence status:** incomplete  
**Computed public-launch decision:** `BLOCKED`  
**Last reviewed:** 2026-09-22 (Asia/Shanghai)

This register is the auditable companion to
[`PUBLIC_LAUNCH_REVIEW.md`](PUBLIC_LAUNCH_REVIEW.md). It records repository
evidence and references to authoritative terms, but it does not contain private
contracts, account credentials or secrets. An implementation reference proves
what the software does; it does not prove that a provider has granted the
corresponding right.

## Evidence hierarchy

1. `TIER_1_CONTRACT`: signed agreement, account entitlement, provider console
   authorization or written permission from the rights holder.
2. `TIER_2_AUTHORITATIVE`: current provider terms, official licence/privacy
   documentation or an explicit dataset licence from its publisher.
3. `TIER_3_IMPLEMENTATION`: source code, configuration, tests, schemas and
   architecture documentation. This tier cannot clear a contractual gate.

External evidence must be referenced by a restricted release record, contract
reference or provider-console record supplied by the owner. It must not be
copied into this repository. The absence of a Tier 1/2 rights reference is
represented by `rights_evidence_present: false`.

## Deterministic release rule

The checker treats these as mandatory gates:

`AMAP-01`, `AMAP-CACHE-01`, `RAIL-01`, `RAIL-02`, `ATTR-RAIL-01`.

Any missing mandatory gate, `BLOCKED` gate,
`REQUIRES_PROVIDER_REPLACEMENT` gate, or `CONDITIONAL` gate marked
`release_blocking: true` produces an overall `BLOCKED` result. Remaining
non-blocking `CONDITIONAL` gates produce `CONDITIONAL`. Only when every gate
is `CLEAR`, `CLEAR_WITH_MONITORING` or `NOT_APPLICABLE`, with required rights
evidence recorded, can the result be `CLEAR`. The rule is deterministic and
does not use a score or majority vote.

Run the offline checker with:

```bash
uv run --project apps/api python scripts/check_public_launch_readiness.py
uv run --project apps/api python scripts/check_public_launch_readiness.py --json
```

Exit status is `0` for `CLEAR`, `1` for a valid `CONDITIONAL`/`BLOCKED`
register, and `2` for a missing or malformed evidence document. The checker
validates metadata only; it cannot verify a contract or make a legal decision.

## Gate records

```json
{
  "schema_version": 1,
  "review_status": "COMPLETE",
  "reviewed_at": "2026-09-22",
  "overall_release_status": "BLOCKED",
  "mandatory_gate_ids": [
    "AMAP-01",
    "AMAP-CACHE-01",
    "RAIL-01",
    "RAIL-02",
    "ATTR-RAIL-01"
  ],
  "gates": [
    {
      "gate_id": "AMAP-01",
      "subject": "AMap account, application and production authorization",
      "status": "BLOCKED",
      "evidence_required": [
        "Account owner and developer certification",
        "Application/API scope and production approval",
        "Applicable commercial or written non-commercial authorization",
        "Quota and concurrency entitlement"
      ],
      "evidence_present": [
        {
          "tier": "TIER_2_AUTHORITATIVE",
          "reference": "https://lbs.amap.com/pages/terms/",
          "supports": "Published service terms and account/licence requirements; not this account's entitlement."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/providers/amap/client.py",
          "supports": "Backend-only key loading, timeout, retry and provider request boundary."
        }
      ],
      "evidence_location": [
        "docs/PUBLIC_LAUNCH_REVIEW.md",
        "apps/api/app/providers/amap/client.py"
      ],
      "evidence_owner": "AMap account owner / operator",
      "verified_at": "2026-09-22",
      "verification_notes": "No account-specific certification, entitlement, licence record or quota confirmation is stored in the repository.",
      "remediation": "Record restricted account and authorization evidence, without copying secrets or private contracts into Git.",
      "release_blocking": true,
      "rights_evidence_required": true,
      "rights_evidence_present": false
    },
    {
      "gate_id": "AMAP-02",
      "subject": "AMap route and POI result display requirements",
      "status": "CONDITIONAL",
      "evidence_required": [
        "Confirmation that normalized route/POI results may be shown to end users",
        "Approved attribution and legal-notice placement"
      ],
      "evidence_present": [
        {
          "tier": "TIER_2_AUTHORITATIVE",
          "reference": "https://lbs.amap.com/pages/terms/",
          "supports": "Published notice, proprietary-rights and source-display requirements."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/providers/amap/routing_provider.py",
          "supports": "Normalized route fields and provider-independent segments returned by the adapter."
        }
      ],
      "evidence_location": [
        "docs/PUBLIC_LAUNCH_REVIEW.md",
        "apps/web/components/transfer/RouteDetail.tsx"
      ],
      "evidence_owner": "Product/legal + AMap account owner",
      "verified_at": "2026-09-22",
      "verification_notes": "The exact obligation for this route-result/POI presentation is not confirmed for this application; the current UI has no dedicated AMap attribution element.",
      "remediation": "Obtain the provider's exact wording/placement requirement and implement only the approved copy or metadata.",
      "release_blocking": true,
      "rights_evidence_required": true,
      "rights_evidence_present": false
    },
    {
      "gate_id": "AMAP-CACHE-01",
      "subject": "AMap normalized route storage and reuse",
      "status": "BLOCKED",
      "evidence_required": [
        "Permission for server-side normalized route storage",
        "Permission for TTL cache reuse and public response/display",
        "Approved retention and invalidation scope"
      ],
      "evidence_present": [
        {
          "tier": "TIER_2_AUTHORITATIVE",
          "reference": "https://lbs.amap.com/pages/terms/",
          "supports": "Published restrictions concerning storage, caching, copying, derivatives and display."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/services/route_cache.py",
          "supports": "PostgreSQL cache stores normalized route fields, TTLs and segments; raw_payload is cleared."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/db/migrations/versions/0005_create_route_cache.py",
          "supports": "Route cache persistence schema and uniqueness boundary."
        }
      ],
      "evidence_location": [
        "docs/PUBLIC_LAUNCH_REVIEW.md",
        "apps/api/app/services/route_cache.py",
        "apps/api/app/db/migrations/versions/0005_create_route_cache.py"
      ],
      "evidence_owner": "AMap/vendor + product/legal + engineering",
      "verified_at": "2026-09-22",
      "verification_notes": "Normalization and short TTL do not prove permission; no written cache/storage confirmation is recorded.",
      "remediation": "Obtain written permission/term confirmation or make a separately approved production cache decision.",
      "release_blocking": true,
      "rights_evidence_required": true,
      "rights_evidence_present": false
    },
    {
      "gate_id": "AMAP-MAPS-01",
      "subject": "Separation from third-party map rendering",
      "status": "CLEAR_WITH_MONITORING",
      "evidence_required": [
        "No current third-party map provider or map-tile integration"
      ],
      "evidence_present": [
        {
          "tier": "TIER_2_AUTHORITATIVE",
          "reference": "https://lbs.amap.com/pages/law-web-service/summary",
          "supports": "Published Web Service API restriction concerning other map services."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/web/package.json",
          "supports": "Frontend dependencies contain no map provider SDK."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/web/components/transfer/RouteDetail.tsx",
          "supports": "Route details render normalized segments without map tiles."
        }
      ],
      "evidence_location": [
        "apps/web/package.json",
        "apps/web/components/transfer/RouteDetail.tsx"
      ],
      "evidence_owner": "Engineering",
      "verified_at": "2026-09-22",
      "verification_notes": "No other map service is currently integrated; recheck before adding one.",
      "remediation": "Keep the architecture constraint and re-audit any future map-provider addition.",
      "release_blocking": false,
      "rights_evidence_required": false,
      "rights_evidence_present": false
    },
    {
      "gate_id": "RAIL-01",
      "subject": "Railway timetable source and licence",
      "status": "BLOCKED",
      "evidence_required": [
        "Publisher and upstream data origin",
        "Public and commercial use rights",
        "Redistribution, derivative-data and coordinate rights",
        "Required attribution and update responsibility"
      ],
      "evidence_present": [
        {
          "tier": "TIER_2_AUTHORITATIVE",
          "reference": "https://github.com/wensimehrp/chinese-railway-gtfs",
          "supports": "Upstream README identifies weekly GTFS publication, OpenStreetMap coordinates and an undisclosed rail service source; it does not grant the required rights."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/providers/rail_gtfs/loader.py",
          "supports": "Local GTFS directory/zip loading, source identity and optional explicit timestamp handling."
        }
      ],
      "evidence_location": [
        "README.md",
        "apps/api/app/providers/rail_gtfs/loader.py",
        "data/external/ (ignored local source, not committed)"
      ],
      "evidence_owner": "Railway source maintainer + product/legal",
      "verified_at": "2026-09-22",
      "verification_notes": "No dataset licence, publisher authorization, redistribution permission or commercial-use grant was found.",
      "remediation": "Obtain explicit rights evidence or select an approved replacement provider; do not infer permission from GTFS format or a public repository.",
      "release_blocking": true,
      "rights_evidence_required": true,
      "rights_evidence_present": false
    },
    {
      "gate_id": "RAIL-02",
      "subject": "Railway derived persistence, caching, API and UI display",
      "status": "BLOCKED",
      "evidence_required": [
        "Permission for normalized RailTrip and imported service/stop rows",
        "Permission for RailwaySearchCache and GTFSFeedCache",
        "Permission for API/UI timetable display and alternative/date/backup views"
      ],
      "evidence_present": [
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/db/migrations/versions/0006_create_rail_services_and_stops.py",
          "supports": "Dated railway service and stop persistence."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/services/railway_cache.py",
          "supports": "Bounded normalized railway search cache with TTL/LRU/single-flight."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/web/components/transfer/TrainList.tsx",
          "supports": "Planned train results are displayed in the frontend."
        }
      ],
      "evidence_location": [
        "apps/api/app/db/migrations/versions/0006_create_rail_services_and_stops.py",
        "apps/api/app/services/railway_cache.py",
        "apps/api/app/providers/rail_gtfs/loader.py",
        "apps/web/components/transfer/TrainList.tsx"
      ],
      "evidence_owner": "Railway source maintainer + product/legal",
      "verified_at": "2026-09-22",
      "verification_notes": "Implementation evidence confirms derived use; no source permission covers database persistence, memory cache, API/UI display or updates.",
      "remediation": "Obtain rights covering every listed derived use or mark the current provider development-only and select an approved replacement.",
      "release_blocking": true,
      "rights_evidence_required": true,
      "rights_evidence_present": false
    },
    {
      "gate_id": "RAIL-03",
      "subject": "Railway source timestamp and update responsibility",
      "status": "CONDITIONAL",
      "evidence_required": [
        "Trustworthy publisher timestamp or an approved unknown-timestamp policy",
        "Documented release channel and update owner"
      ],
      "evidence_present": [
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/providers/rail_gtfs/loader.py",
          "supports": "Only explicit feed timestamp fields are accepted; absent values remain UNKNOWN."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/domain/models.py",
          "supports": "UNKNOWN/FRESH/STALE freshness semantics are validated."
        }
      ],
      "evidence_location": [
        "README.md",
        "apps/api/app/providers/rail_gtfs/loader.py",
        "apps/api/app/domain/models.py"
      ],
      "evidence_owner": "Railway source maintainer + operations",
      "verified_at": "2026-09-22",
      "verification_notes": "Current feed has no trustworthy explicit update timestamp; the application correctly reports UNKNOWN.",
      "remediation": "Obtain publisher timestamp/update responsibility or retain and document the explicit UNKNOWN warning.",
      "release_blocking": false,
      "rights_evidence_required": false,
      "rights_evidence_present": false
    },
    {
      "gate_id": "ATTR-01",
      "subject": "AMap attribution and legal notices",
      "status": "CONDITIONAL",
      "evidence_required": [
        "Exact provider-supplied attribution/legal-notice wording",
        "Required placement for normalized route/POI presentation"
      ],
      "evidence_present": [
        {
          "tier": "TIER_2_AUTHORITATIVE",
          "reference": "https://lbs.amap.com/pages/terms/",
          "supports": "Published source and proprietary-notice requirements."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/web/components/transfer/TransferResults.tsx",
          "supports": "Current result presentation; no dedicated AMap attribution copy is present."
        }
      ],
      "evidence_location": [
        "docs/PUBLIC_LAUNCH_REVIEW.md",
        "apps/web/components/transfer/TransferResults.tsx"
      ],
      "evidence_owner": "Product/legal + AMap account owner",
      "verified_at": "2026-09-22",
      "verification_notes": "The exact obligation and wording for this presentation are not confirmed; no speculative copy is added.",
      "remediation": "Record the approved wording and implement a separately reviewed UI/API change if required.",
      "release_blocking": true,
      "rights_evidence_required": true,
      "rights_evidence_present": false
    },
    {
      "gate_id": "ATTR-RAIL-01",
      "subject": "Railway source attribution",
      "status": "BLOCKED",
      "evidence_required": [
        "Required source name/link and attribution wording",
        "Permission to display attribution with normalized timetable results"
      ],
      "evidence_present": [
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/domain/models.py",
          "supports": "Railway source metadata and freshness are modeled."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/web/components/transfer/TransferResults.tsx",
          "supports": "The UI can display railway source/freshness metadata, but no verified legal attribution text exists."
        }
      ],
      "evidence_location": [
        "README.md",
        "apps/web/components/transfer/TransferResults.tsx"
      ],
      "evidence_owner": "Railway source maintainer + product/legal",
      "verified_at": "2026-09-22",
      "verification_notes": "Provider name CHINA_RAILWAY_GTFS is a technical identity, not a confirmed legal attribution grant.",
      "remediation": "Obtain source attribution/permission and add only the approved text/link.",
      "release_blocking": true,
      "rights_evidence_required": true,
      "rights_evidence_present": false
    },
    {
      "gate_id": "PRIV-01",
      "subject": "AMap data-flow privacy disclosure",
      "status": "CONDITIONAL",
      "evidence_required": [
        "Public privacy notice naming AMap products, purpose and data flow",
        "Applicable consent/legal-basis review and policy link"
      ],
      "evidence_present": [
        {
          "tier": "TIER_2_AUTHORITATIVE",
          "reference": "https://lbs.amap.com/api/compliance-center/protocols/privacy_202604",
          "supports": "Published developer disclosure and consent expectations."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/providers/amap/routing_provider.py",
          "supports": "Routing sends canonical coordinates, city context, mode and optional arrival date/time server-side."
        }
      ],
      "evidence_location": [
        "docs/PUBLIC_LAUNCH_REVIEW.md",
        "apps/api/app/providers/amap/routing_provider.py"
      ],
      "evidence_owner": "Product/legal",
      "verified_at": "2026-09-22",
      "verification_notes": "Current code does not access browser/device geolocation or user identity; a public data-flow notice is still not present.",
      "remediation": "Approve and publish the required privacy disclosure before public use; do not invent a legal privacy policy in this repository task.",
      "release_blocking": true,
      "rights_evidence_required": false,
      "rights_evidence_present": false
    },
    {
      "gate_id": "DATA-COORD-01",
      "subject": "Coordinate provenance and public-use rights",
      "status": "CONDITIONAL",
      "evidence_required": [
        "Production source and rights record for canonical coordinates",
        "WGS84/GCJ02 boundary and provider-use record"
      ],
      "evidence_present": [
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/domain/models.py",
          "supports": "Coordinate system is explicit in the domain model."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/providers/amap/routing_provider.py",
          "supports": "AMap requests validate GCJ02 coordinates and do not silently convert WGS84."
        },
        {
          "tier": "TIER_3_IMPLEMENTATION",
          "reference": "apps/api/app/providers/rail_gtfs/loader.py",
          "supports": "GTFS source coordinates remain distinct from AMap coordinates and feed provenance timestamps remain explicit."
        }
      ],
      "evidence_location": [
        "data/seed/cities_and_hubs.json",
        "apps/api/app/domain/models.py",
        "apps/api/app/providers/amap/routing_provider.py",
        "apps/api/app/providers/rail_gtfs/loader.py"
      ],
      "evidence_owner": "Data owner + product/legal",
      "verified_at": "2026-09-22",
      "verification_notes": "Coordinate math is explicit, but production source ownership and public-use rights are not recorded.",
      "remediation": "Create a restricted production coordinate provenance/rights record and retain the WGS84/GCJ02 boundary.",
      "release_blocking": true,
      "rights_evidence_required": true,
      "rights_evidence_present": false
    }
  ]
}
```

## Provider replacement decision

The current outcome is **C** from the T-098 decision rule: Railway rights are
not cleared and no approved replacement source is recorded. `GTFSRailProvider`
remains a development/POC provider and BL-037 remains blocked on source
selection/evidence. A replacement becomes eligible only after it supplies
source identity, public/commercial rights, API/display/storage/cache rights,
attribution, freshness, service-date/cross-midnight semantics, stable IDs,
reconciliation support and operational limits.

## Remediation outcomes

| Surface | Current outcome | Permission-dependent next action |
|---|---|---|
| RouteCache | `UNKNOWN_PENDING_EVIDENCE` | Keep for development; obtain AMap confirmation or make a separate production cache decision. |
| RailwaySearchCache | `UNKNOWN_PENDING_EVIDENCE` | Keep as a development optimization; confirm derived-result cache rights with the source owner. |
| GTFSFeedCache | `UNKNOWN_PENDING_EVIDENCE` | Keep for local development; confirm parsed-feed reuse/retention rights or disable in an approved public mode. |
| AMap attribution | `UNKNOWN_PENDING_EVIDENCE` | Do not add speculative text; implement exact approved wording if required. |
| Railway attribution | `UNKNOWN_PENDING_EVIDENCE` | Obtain source wording and permission before public display. |
| Privacy notice | `DOCUMENTATION_REQUIRED` | Product/legal must approve the AMap data-flow disclosure. |

## Cache rights matrix

| Cache | Source/provider | Stored representation | Persistence and lifetime | Survives process restart | User-visible | Licensing gate | Required remediation |
|---|---|---|---|---|---|---|---|
| `RouteCache` | AMap | Normalized duration/distance/walking/transfer fields and route segments; raw payload cleared | PostgreSQL; transit default 6h, driving default 30m | Yes, subject to expiry and database lifecycle | Yes, through normalized API/UI route details | `AMAP-CACHE-01` | `UNKNOWN_PENDING_EVIDENCE`: obtain written permission or approve a public-mode cache redesign. |
| `RailwaySearchCache` | `CHINA_RAILWAY_GTFS` | Normalized `RailTrip` tuples; no raw GTFS rows or ranking/STT | Process-local bounded LRU; default TTL 1800s, max 512 entries | No | Indirectly, through rail search/API/UI | `RAIL-02` | `UNKNOWN_PENDING_EVIDENCE`: confirm derived-result caching and reuse rights. |
| `GTFSFeedCache` | Local GTFS feed | Parsed `GTFSFeed` object | Process-local bounded cache; max 4 feed identities, invalidated on source identity change; no TTL | No | Indirectly, through provider results | `RAIL-02` | `UNKNOWN_PENDING_EVIDENCE`: confirm parsed-feed reuse/retention or disable in approved public mode. |

## Data-flow boundary

The current route data flow is:

```text
user transfer request
  → canonical city/hub resolution
  → backend AMap routing request
  → normalized RouteOption
  → optional PostgreSQL RouteCache
  → Transfer API response
  → frontend route/result display
```

The backend sends canonical hub coordinates, city context, route mode and (for
transit) the local arrival date/time. The current implementation does not send
browser/device geolocation, cookies, user identity or an end-user profile to
the AMap adapter. This technical statement is not a substitute for the public
privacy notice.
