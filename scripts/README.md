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

## Private-trial deployment

The Render Blueprint uses a build-time, operator-supplied GTFS artifact. It
never commits the feed to Git and never silently falls back to FixtureRailProvider:

```bash
python scripts/prepare_gtfs_artifact.py --help
export RAIL_GTFS_ARTIFACT_SHA256="$(shasum -a 256 /private/path/output_gtfs.zip | awk '{print $1}')"
python scripts/prepare_gtfs_artifact.py \
  --source /private/path/output_gtfs.zip \
  --output data/external/private-trial/output_gtfs.zip
unset RAIL_GTFS_ARTIFACT_SHA256
```

Render sets `RAIL_GTFS_ARTIFACT_URL` and
`RAIL_GTFS_ARTIFACT_SHA256` as build variables; an optional
`RAIL_GTFS_ARTIFACT_TOKEN` is sent only as a bearer header. See
[`docs/V1_PRIVATE_DEPLOYMENT.md`](../docs/V1_PRIVATE_DEPLOYMENT.md) for the
full Vercel/Render/bootstrap/smoke/rollback procedure.
