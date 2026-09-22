# Development scripts

Seed data is applied with `python -m app.db.seed` from `apps/api`. Keep operational
scripts here only when they are shared across applications; application-specific
commands belong with the application that owns them.

## Public launch rights checker

T-098 keeps public-launch evidence in a human-reviewable register and checks it
without network access or provider credentials:

```bash
uv run --project apps/api python scripts/check_public_launch_readiness.py
uv run --project apps/api python scripts/check_public_launch_readiness.py --json
```

The current repository intentionally exits with status `1` because mandatory
AMap and railway rights evidence is still missing. Exit `2` means the evidence
document is missing or malformed; exit `0` is reserved for a fully cleared
register. The checker never prints evidence notes, credentials or contracts.

The Railway POC importer accepts an extracted GTFS directory or zip file:

```bash
uv run --project apps/api python scripts/import_rail_gtfs.py data/fixtures/rail_gtfs
uv run --project apps/api python scripts/rail_gtfs_smoke.py data/fixtures/rail_gtfs --service-date 2026-10-03
uv run --project apps/api python scripts/transfer_chengdu_leshan.py --mode fixture
```

Downloaded feeds belong under `data/external/`, which is ignored by Git.
