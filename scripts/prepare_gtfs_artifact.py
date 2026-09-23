#!/usr/bin/env python3
"""Provision a private GTFS zip into a deployment workspace.

The Render build uses this command to inject an operator-supplied artifact
without committing railway data to Git.  The source URL and optional bearer
token are read only from the build environment and are never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


REQUIRED_GTFS_FILES = {"stops.txt", "routes.txt", "trips.txt", "stop_times.txt"}
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024


class ArtifactProvisionError(RuntimeError):
    """A safe, actionable deployment-artifact error."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_gtfs_zip(path: Path) -> None:
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ArtifactProvisionError("GTFS artifact exceeds the 512 MiB deployment limit")
    if not zipfile.is_zipfile(path):
        raise ArtifactProvisionError("GTFS artifact is not a valid zip file")
    try:
        with zipfile.ZipFile(path) as archive:
            names = {Path(name).name for name in archive.namelist() if not name.endswith("/")}
            missing = sorted(REQUIRED_GTFS_FILES - names)
            if missing:
                raise ArtifactProvisionError(
                    "GTFS artifact is missing required files: " + ", ".join(missing)
                )
            bad_member = archive.testzip()
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArtifactProvisionError("GTFS artifact could not be read") from exc
    if bad_member is not None:
        raise ArtifactProvisionError("GTFS artifact contains a corrupt zip member")


def _copy_local(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise ArtifactProvisionError("local GTFS artifact does not exist")
    try:
        shutil.copyfile(source, destination)
    except OSError as exc:
        raise ArtifactProvisionError("local GTFS artifact could not be copied") from exc


def _download(source_url: str, token: str | None, destination: Path) -> None:
    if not source_url.lower().startswith("https://"):
        raise ArtifactProvisionError("RAIL_GTFS_ARTIFACT_URL must use HTTPS")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = Request(source_url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=120) as response, destination.open("wb") as handle:
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARTIFACT_BYTES:
                    raise ArtifactProvisionError(
                        "GTFS artifact exceeds the 512 MiB deployment limit"
                    )
                handle.write(chunk)
    except ArtifactProvisionError:
        raise
    except (OSError, URLError, TimeoutError) as exc:
        # Do not include the URL, query string, or provider response in build
        # output: signed URLs and provider errors can contain credentials.
        raise ArtifactProvisionError("GTFS artifact download failed") from exc


def provision_artifact(
    *,
    output: Path,
    source_url: str | None = None,
    token: str | None = None,
    expected_sha256: str | None = None,
    local_source: Path | None = None,
) -> Path:
    """Download or copy, verify, and atomically install one GTFS zip."""

    if bool(source_url) == bool(local_source):
        raise ArtifactProvisionError(
            "provide exactly one of RAIL_GTFS_ARTIFACT_URL or --source"
        )
    normalized_hash = (expected_sha256 or "").strip().lower()
    if len(normalized_hash) != 64 or any(character not in "0123456789abcdef" for character in normalized_hash):
        raise ArtifactProvisionError("RAIL_GTFS_ARTIFACT_SHA256 must be a 64-character hex digest")

    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        if source_url:
            _download(source_url, token, temporary_path)
        else:
            assert local_source is not None
            _copy_local(local_source.expanduser(), temporary_path)
        _validate_gtfs_zip(temporary_path)
        if _sha256(temporary_path) != normalized_hash:
            raise ArtifactProvisionError("GTFS artifact SHA-256 does not match the configured digest")
        temporary_path.replace(output)
        temporary_path = None
    except ArtifactProvisionError:
        raise
    except OSError as exc:
        raise ArtifactProvisionError("GTFS artifact could not be installed") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/external/private-trial/output_gtfs.zip"),
        help="deployment-local output path (default: %(default)s)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="local zip source for an operator-controlled build/test; Render uses the URL env",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_url = os.environ.get("RAIL_GTFS_ARTIFACT_URL", "").strip() or None
    expected_sha256 = os.environ.get("RAIL_GTFS_ARTIFACT_SHA256", "").strip() or None
    token = os.environ.get("RAIL_GTFS_ARTIFACT_TOKEN", "").strip() or None
    if args.source is None and source_url is None:
        print(
            "GTFS artifact is not configured: set RAIL_GTFS_ARTIFACT_URL and "
            "RAIL_GTFS_ARTIFACT_SHA256, or pass --source for a local build.",
            file=sys.stderr,
        )
        return 2
    try:
        output = provision_artifact(
            output=args.output,
            source_url=source_url,
            token=token,
            expected_sha256=expected_sha256,
            local_source=args.source,
        )
    except ArtifactProvisionError as exc:
        print(f"GTFS artifact provisioning failed: {exc}", file=sys.stderr)
        return 2
    print(f"GTFS artifact ready: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
