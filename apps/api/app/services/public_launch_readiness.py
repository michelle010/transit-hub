"""Offline validation for the public-launch rights evidence register.

This module validates evidence *records*; it does not decide whether a
provider has granted a licence.  Tier 1/2 references describe the evidence
available to an operator, while Tier 3 references only describe repository
behaviour.  The register therefore remains BLOCKED until the required rights
evidence is recorded by the owner.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EVIDENCE_SCHEMA_VERSION = 1

ALLOWED_GATE_STATUSES = frozenset(
    {
        "CLEAR",
        "CLEAR_WITH_MONITORING",
        "CONDITIONAL",
        "BLOCKED",
        "REQUIRES_PROVIDER_REPLACEMENT",
        "NOT_APPLICABLE",
    }
)
ALLOWED_RELEASE_STATUSES = frozenset({"CLEAR", "CONDITIONAL", "BLOCKED"})
ALLOWED_REVIEW_STATUSES = frozenset({"COMPLETE", "INCOMPLETE"})
ALLOWED_EVIDENCE_TIERS = frozenset(
    {"TIER_1_CONTRACT", "TIER_2_AUTHORITATIVE", "TIER_3_IMPLEMENTATION"}
)
MANDATORY_GATE_IDS = (
    "AMAP-01",
    "AMAP-CACHE-01",
    "RAIL-01",
    "RAIL-02",
    "ATTR-RAIL-01",
)
_GATE_ID_PATTERN = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
_PLACEHOLDER_REFERENCE_PATTERN = re.compile(r"(?:^|[<\[])(?:TBD|TODO|REPLACE|EXAMPLE)", re.I)


class EvidenceDocumentError(ValueError):
    """Raised when the evidence document cannot be parsed safely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Machine-safe result of validating one evidence register."""

    document_valid: bool
    declared_status: str | None
    computed_status: str
    validation_errors: tuple[str, ...]
    readiness_issues: tuple[str, ...]
    blocking_gate_ids: tuple[str, ...]
    conditional_gate_ids: tuple[str, ...]
    gate_statuses: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_valid": self.document_valid,
            "declared_status": self.declared_status,
            "computed_status": self.computed_status,
            "validation_errors": list(self.validation_errors),
            "readiness_issues": list(self.readiness_issues),
            "blocking_gate_ids": list(self.blocking_gate_ids),
            "conditional_gate_ids": list(self.conditional_gate_ids),
            "gate_statuses": {gate_id: status for gate_id, status in self.gate_statuses},
        }


def parse_evidence_document(text: str) -> dict[str, Any]:
    """Extract the first JSON register from Markdown or parse plain JSON."""

    match = re.search(r"```json\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    raw = match.group(1) if match else text.strip()
    if not raw.startswith("{"):
        raise EvidenceDocumentError("EVIDENCE_JSON_BLOCK_MISSING")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        del exc
        raise EvidenceDocumentError("EVIDENCE_JSON_INVALID") from None
    if not isinstance(document, dict):
        raise EvidenceDocumentError("EVIDENCE_DOCUMENT_NOT_OBJECT")
    return document


def load_evidence_document(path: Path) -> dict[str, Any]:
    """Load a register without including its contents in error output."""

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        del exc
        raise EvidenceDocumentError("EVIDENCE_FILE_UNREADABLE") from None
    return parse_evidence_document(text)


def validate_evidence_document(
    document: dict[str, Any],
    *,
    mandatory_gate_ids: tuple[str, ...] = MANDATORY_GATE_IDS,
) -> ReadinessReport:
    """Validate structure and compute the deterministic release status."""

    validation_errors: list[str] = []
    readiness_issues: list[str] = []
    gate_statuses: list[tuple[str, str]] = []

    schema_version = document.get("schema_version")
    if schema_version != EVIDENCE_SCHEMA_VERSION:
        validation_errors.append("SCHEMA_VERSION_INVALID")

    if document.get("review_status") not in ALLOWED_REVIEW_STATUSES:
        validation_errors.append("REVIEW_STATUS_INVALID")

    declared_mandatory = document.get("mandatory_gate_ids")
    if (
        not isinstance(declared_mandatory, list)
        or not all(isinstance(item, str) for item in declared_mandatory)
        or set(declared_mandatory) != set(mandatory_gate_ids)
    ):
        validation_errors.append("MANDATORY_GATE_LIST_INVALID")

    declared_status = document.get("overall_release_status")
    if declared_status not in ALLOWED_RELEASE_STATUSES:
        validation_errors.append("OVERALL_STATUS_INVALID")
        declared_status = None

    gates = document.get("gates")
    if not isinstance(gates, list):
        validation_errors.append("GATES_LIST_MISSING")
        gates = []

    records: dict[str, dict[str, Any]] = {}
    for record in gates:
        if not isinstance(record, dict):
            validation_errors.append("GATE_RECORD_INVALID")
            continue
        gate_id = record.get("gate_id")
        if not isinstance(gate_id, str) or not _GATE_ID_PATTERN.fullmatch(gate_id):
            validation_errors.append("GATE_ID_INVALID")
            continue
        if gate_id in records:
            validation_errors.append("DUPLICATE_GATE_ID")
            continue
        records[gate_id] = record
        status = record.get("status")
        if status not in ALLOWED_GATE_STATUSES:
            validation_errors.append("GATE_STATUS_INVALID")
            continue
        gate_statuses.append((gate_id, status))
        _validate_gate_record(record, validation_errors)

    missing_mandatory = [gate_id for gate_id in mandatory_gate_ids if gate_id not in records]
    for gate_id in missing_mandatory:
        readiness_issues.append(f"MANDATORY_GATE_MISSING:{gate_id}")

    blocking_gate_ids: list[str] = []
    conditional_gate_ids: list[str] = []
    for gate_id, record in records.items():
        status = record.get("status")
        if status in {"BLOCKED", "REQUIRES_PROVIDER_REPLACEMENT"}:
            blocking_gate_ids.append(gate_id)
            readiness_issues.append(f"UNRESOLVED_GATE:{gate_id}")
        elif status == "CONDITIONAL":
            if record.get("release_blocking") is True:
                blocking_gate_ids.append(gate_id)
                readiness_issues.append(f"BLOCKING_CONDITIONAL_GATE:{gate_id}")
            else:
                conditional_gate_ids.append(gate_id)

    blocking_gate_ids.extend(missing_mandatory)
    blocking_gate_ids = sorted(set(blocking_gate_ids))
    conditional_gate_ids = sorted(set(conditional_gate_ids))

    computed_status = (
        "BLOCKED" if blocking_gate_ids else "CONDITIONAL" if conditional_gate_ids else "CLEAR"
    )
    if declared_status is not None and declared_status != computed_status:
        validation_errors.append("OVERALL_STATUS_MISMATCH")

    return ReadinessReport(
        document_valid=not validation_errors,
        declared_status=declared_status,
        computed_status=computed_status,
        validation_errors=tuple(sorted(set(validation_errors))),
        readiness_issues=tuple(sorted(set(readiness_issues))),
        blocking_gate_ids=tuple(blocking_gate_ids),
        conditional_gate_ids=tuple(conditional_gate_ids),
        gate_statuses=tuple(sorted(gate_statuses)),
    )


def invalid_document_report(code: str) -> ReadinessReport:
    """Return a safe failure result for CLI parse/read errors."""

    return ReadinessReport(
        document_valid=False,
        declared_status=None,
        computed_status="BLOCKED",
        validation_errors=(code,),
        readiness_issues=(),
        blocking_gate_ids=(),
        conditional_gate_ids=(),
        gate_statuses=(),
    )


def format_report(report: ReadinessReport) -> str:
    """Render only stable metadata; never echo evidence notes or references."""

    lines = [
        f"public_launch_status={report.computed_status}",
        f"document_valid={'true' if report.document_valid else 'false'}",
        "blocking_gate_ids=" + ",".join(report.blocking_gate_ids),
        "conditional_gate_ids=" + ",".join(report.conditional_gate_ids),
    ]
    if report.validation_errors:
        lines.append("validation_error_codes=" + ",".join(report.validation_errors))
    if report.readiness_issues:
        lines.append("readiness_issue_codes=" + ",".join(report.readiness_issues))
    return "\n".join(lines)


def _validate_gate_record(record: dict[str, Any], errors: list[str]) -> None:
    required_strings = (
        "subject",
        "evidence_owner",
        "verification_notes",
        "remediation",
    )
    for field_name in required_strings:
        if not isinstance(record.get(field_name), str) or not record[field_name].strip():
            errors.append("GATE_FIELD_MISSING")

    for field_name in ("evidence_required", "evidence_location", "evidence_present"):
        if not isinstance(record.get(field_name), list):
            errors.append("GATE_EVIDENCE_FIELD_INVALID")

    for field_name in ("release_blocking", "rights_evidence_required", "rights_evidence_present"):
        if not isinstance(record.get(field_name), bool):
            errors.append("GATE_BOOLEAN_FIELD_INVALID")

    verified_at = record.get("verified_at")
    if verified_at is not None and not isinstance(verified_at, str):
        errors.append("GATE_VERIFIED_AT_INVALID")

    evidence = record.get("evidence_present")
    if not isinstance(evidence, list):
        return
    for item in evidence:
        if not isinstance(item, dict):
            errors.append("EVIDENCE_REFERENCE_INVALID")
            continue
        tier = item.get("tier")
        reference = item.get("reference")
        supports = item.get("supports")
        if tier not in ALLOWED_EVIDENCE_TIERS:
            errors.append("EVIDENCE_TIER_INVALID")
        if not isinstance(reference, str) or not reference.strip():
            errors.append("EVIDENCE_REFERENCE_MISSING")
        elif _PLACEHOLDER_REFERENCE_PATTERN.search(reference):
            errors.append("EVIDENCE_REFERENCE_PLACEHOLDER")
        if not isinstance(supports, str) or not supports.strip():
            errors.append("EVIDENCE_SUPPORT_MISSING")

    status = record.get("status")
    if status in {"CLEAR", "CLEAR_WITH_MONITORING"} and not evidence:
        errors.append("CLEAR_WITHOUT_EVIDENCE_REFERENCE")
    if status in {"CLEAR", "CLEAR_WITH_MONITORING"}:
        if (
            record.get("rights_evidence_required")
            and record.get("rights_evidence_present") is not True
        ):
            errors.append("CLEAR_WITHOUT_RIGHTS_EVIDENCE")
        if status == "CLEAR" and not any(
            item.get("tier") in {"TIER_1_CONTRACT", "TIER_2_AUTHORITATIVE"}
            for item in evidence
            if isinstance(item, dict)
        ):
            errors.append("CLEAR_WITHOUT_AUTHORITATIVE_EVIDENCE")
    if record.get("rights_evidence_present") is True and not any(
        item.get("tier") in {"TIER_1_CONTRACT", "TIER_2_AUTHORITATIVE"}
        for item in evidence
        if isinstance(item, dict)
    ):
        errors.append("RIGHTS_EVIDENCE_TIER_INVALID")
