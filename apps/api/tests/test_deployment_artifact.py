from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.prepare_gtfs_artifact import (  # noqa: E402, I001
    ArtifactProvisionError,
    provision_artifact,
)


FIXTURE_FEED = REPOSITORY_ROOT / "data" / "fixtures" / "rail_gtfs"


def _zip_fixture(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in FIXTURE_FEED.iterdir():
            if source.is_file():
                archive.write(source, source.name)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_private_gtfs_artifact_is_verified_and_installed(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    output = tmp_path / "release" / "output_gtfs.zip"
    _zip_fixture(source)

    installed = provision_artifact(
        output=output,
        local_source=source,
        expected_sha256=_sha256(source),
    )

    assert installed == output
    assert output.read_bytes() == source.read_bytes()


def test_private_gtfs_artifact_rejects_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    _zip_fixture(source)

    with pytest.raises(ArtifactProvisionError, match="SHA-256"):
        provision_artifact(
            output=tmp_path / "output.zip",
            local_source=source,
            expected_sha256="0" * 64,
        )


def test_private_gtfs_artifact_requires_one_source(tmp_path: Path) -> None:
    with pytest.raises(ArtifactProvisionError, match="exactly one"):
        provision_artifact(
            output=tmp_path / "output.zip",
            expected_sha256="0" * 64,
        )
