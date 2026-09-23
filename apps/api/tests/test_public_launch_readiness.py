from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.public_launch_readiness import (
    MANDATORY_GATE_IDS,
    EvidenceDocumentError,
    format_report,
    parse_evidence_document,
    validate_evidence_document,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _evidence(*, authoritative: bool = True) -> list[dict[str, str]]:
    return [
        {
            "tier": "TIER_1_CONTRACT" if authoritative else "TIER_3_IMPLEMENTATION",
            "reference": "tests/fixtures/public-launch-evidence.txt",
            "supports": "Deterministic test evidence.",
        }
    ]


def _gate(
    gate_id: str,
    *,
    status: str = "CLEAR",
    release_blocking: bool = False,
    rights_required: bool = False,
    rights_present: bool = False,
) -> dict[str, object]:
    return {
        "gate_id": gate_id,
        "subject": f"Subject {gate_id}",
        "status": status,
        "evidence_required": ["A recorded evidence reference"],
        "evidence_present": _evidence(authoritative=rights_present or status == "CLEAR"),
        "evidence_location": ["tests/fixtures/public-launch-evidence.txt"],
        "evidence_owner": "Test owner",
        "verified_at": "2026-09-22",
        "verification_notes": "Test record.",
        "remediation": "Test remediation.",
        "release_blocking": release_blocking,
        "rights_evidence_required": rights_required,
        "rights_evidence_present": rights_present,
    }


def _document(
    gates: list[dict[str, object]],
    *,
    overall_status: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "review_status": "COMPLETE",
        "reviewed_at": "2026-09-22",
        "overall_release_status": overall_status,
        "mandatory_gate_ids": list(MANDATORY_GATE_IDS),
        "gates": gates,
    }


def _all_mandatory(*, status: str = "CLEAR") -> list[dict[str, object]]:
    return [
        _gate(
            gate_id,
            status=status,
            rights_required=True,
            rights_present=status == "CLEAR",
        )
        for gate_id in MANDATORY_GATE_IDS
    ]


def test_missing_mandatory_gate_is_blocked() -> None:
    document = _document(
        [_gate("AMAP-01", status="BLOCKED", release_blocking=True)], overall_status="BLOCKED"
    )

    report = validate_evidence_document(document)

    assert report.document_valid is True
    assert report.computed_status == "BLOCKED"
    assert "MANDATORY_GATE_MISSING:AMAP-CACHE-01" in report.readiness_issues


def test_mandatory_blocked_gate_is_blocked() -> None:
    gates = _all_mandatory()
    gates[0] = _gate("AMAP-01", status="BLOCKED", release_blocking=True, rights_required=True)

    report = validate_evidence_document(_document(gates, overall_status="BLOCKED"))

    assert report.computed_status == "BLOCKED"
    assert "AMAP-01" in report.blocking_gate_ids


def test_provider_replacement_is_always_blocking() -> None:
    gates = _all_mandatory()
    gates[0] = _gate(
        "AMAP-01",
        status="REQUIRES_PROVIDER_REPLACEMENT",
        release_blocking=False,
        rights_required=True,
    )

    report = validate_evidence_document(_document(gates, overall_status="BLOCKED"))

    assert report.computed_status == "BLOCKED"
    assert "AMAP-01" in report.blocking_gate_ids


def test_clear_gate_without_evidence_reference_is_invalid() -> None:
    gates = _all_mandatory()
    gates[0]["evidence_present"] = []

    report = validate_evidence_document(_document(gates, overall_status="BLOCKED"))

    assert report.document_valid is False
    assert "CLEAR_WITHOUT_EVIDENCE_REFERENCE" in report.validation_errors


def test_valid_clear_register_requires_rights_evidence() -> None:
    gates = _all_mandatory()
    report = validate_evidence_document(_document(gates, overall_status="CLEAR"))

    assert report.document_valid is True
    assert report.computed_status == "CLEAR"
    assert report.blocking_gate_ids == ()


def test_clear_with_monitoring_does_not_block() -> None:
    gates = _all_mandatory()
    gates.append(
        _gate(
            "AMAP-MAPS-01",
            status="CLEAR_WITH_MONITORING",
            release_blocking=False,
        )
    )

    report = validate_evidence_document(_document(gates, overall_status="CLEAR"))

    assert report.document_valid is True
    assert report.computed_status == "CLEAR"


def test_non_blocking_conditional_register_is_conditional() -> None:
    gates = _all_mandatory()
    gates.append(
        _gate(
            "RAIL-03",
            status="CONDITIONAL",
            release_blocking=False,
        )
    )

    report = validate_evidence_document(_document(gates, overall_status="CONDITIONAL"))

    assert report.document_valid is True
    assert report.computed_status == "CONDITIONAL"
    assert report.conditional_gate_ids == ("RAIL-03",)


def test_blocking_conditional_gate_aggregates_to_blocked() -> None:
    gates = _all_mandatory()
    gates.append(
        _gate(
            "PRIV-01",
            status="CONDITIONAL",
            release_blocking=True,
        )
    )

    report = validate_evidence_document(_document(gates, overall_status="BLOCKED"))

    assert report.computed_status == "BLOCKED"
    assert "PRIV-01" in report.blocking_gate_ids


def test_malformed_evidence_document_is_rejected() -> None:
    with pytest.raises(EvidenceDocumentError) as error:
        parse_evidence_document("```json\n{invalid}\n```")

    assert error.value.code == "EVIDENCE_JSON_INVALID"


def test_actual_register_is_valid_but_blocked() -> None:
    path = REPOSITORY_ROOT / "docs" / "PUBLIC_LAUNCH_EVIDENCE.md"
    document = parse_evidence_document(path.read_text(encoding="utf-8"))

    report = validate_evidence_document(document)

    assert report.document_valid is True
    assert report.declared_status == "BLOCKED"
    assert report.computed_status == "BLOCKED"
    assert len(report.gate_statuses) == 11


def test_secret_values_are_not_echoed_in_human_report() -> None:
    secret = "amap-secret-value-that-must-not-be-printed"
    document = _document(_all_mandatory(), overall_status="CLEAR")
    document["gates"][0]["verification_notes"] = secret  # type: ignore[index]

    output = format_report(validate_evidence_document(document))

    assert secret not in output


def test_json_projection_has_stable_safe_fields() -> None:
    report = validate_evidence_document(_document(_all_mandatory(), overall_status="CLEAR"))

    output = json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True)

    assert "computed_status" in output
    assert "verification_notes" not in output


def test_checker_json_command_has_stable_exit_and_projection() -> None:
    script = REPOSITORY_ROOT / "scripts" / "check_public_launch_readiness.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--json"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["document_valid"] is True
    assert payload["computed_status"] == "BLOCKED"
    assert "verification_notes" not in completed.stdout
