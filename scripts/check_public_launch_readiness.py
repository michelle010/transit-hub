#!/usr/bin/env python3
"""Validate the repository's public-launch rights evidence register.

Exit codes:

* ``0``: structurally valid register and computed status ``CLEAR``;
* ``1``: structurally valid register whose computed status is ``CONDITIONAL``
  or ``BLOCKED``;
* ``2``: missing/malformed register or invalid evidence metadata.

The command validates recorded evidence metadata only. It cannot verify a
contract, account entitlement or legal interpretation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "apps" / "api"))

from app.services.public_launch_readiness import (  # noqa: E402
    EvidenceDocumentError,
    format_report,
    invalid_document_report,
    load_evidence_document,
    validate_evidence_document,
)

DEFAULT_EVIDENCE_FILE = REPOSITORY_ROOT / "docs" / "PUBLIC_LAUNCH_EVIDENCE.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-file",
        type=Path,
        default=DEFAULT_EVIDENCE_FILE,
        help="Markdown or JSON evidence register (default: docs/PUBLIC_LAUNCH_EVIDENCE.md)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit stable machine-readable status metadata.",
    )
    return parser.parse_args()


def run(path: Path, *, json_output: bool = False) -> int:
    try:
        report = validate_evidence_document(load_evidence_document(path))
    except EvidenceDocumentError as exc:
        report = invalid_document_report(exc.code)

    if json_output:
        print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(format_report(report))

    if not report.document_valid:
        return 2
    return 0 if report.computed_status == "CLEAR" else 1


def main() -> int:
    args = parse_args()
    return run(args.evidence_file, json_output=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
