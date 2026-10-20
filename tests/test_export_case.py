"""Tests for ekchain export-case command."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from ekchain.audit import AuditLog
from ekchain.cli import cli
from ekchain.db import CaseDB
from ekchain.models import Finding


def test_export_case_command(tmp_path: Path) -> None:
    """Test export-case writes <case_id>.case.json with correct structure and redacted text."""
    case_id = "test-export-case"
    db_dir = tmp_path

    # Create case DB
    case_db = CaseDB.create(case_id, evidence_root=str(tmp_path), db_dir=db_dir)
    case_db.set_narrative(
        "Analysis performed on C:\\path\\to\\sensitive\\data. "
        "Secret: AKIAIOSFODNN7EXAMPLE"
    )

    case_db.register_source(
        source_name="syslog",
        source_path="C:\\logs\\syslog.log",
        source_hash="sha256:111111",
        extractor="parser_syslog",
        line_count=10,
    )

    finding = Finding(
        finding_id="finding-1",
        case_id=case_id,
        title="Unauthorized access in C:\\Users\\admin",
        description="Found access from external IP.",
        severity="high",
        confidence="confirmed",
        evidence_refs=["tc_1"],
        sources=["syslog"],
        mitre_attack_ids=["T1078"],
        submitted_at="2026-10-15T00:00:00Z",
    )
    case_db.insert_finding(finding)
    case_db.close()

    # Create audit log
    audit_path = db_dir / f"{case_id}.audit.jsonl"
    audit = AuditLog(audit_path)
    audit.log_tool_call(
        "tc_1",
        "parser_syslog",
        {"source_path": "C:\\logs\\syslog.log"},
        "sha256:111",
    )

    # Run export-case command
    runner = CliRunner()
    result = runner.invoke(cli, ["export-case", case_id, "--db-dir", str(db_dir)])
    assert result.exit_code == 0, result.output

    case_json_path = db_dir / f"{case_id}.case.json"
    assert case_json_path.exists()

    data = json.loads(case_json_path.read_text(encoding="utf-8"))

    # Verify keys
    expected_keys = {
        "case_id",
        "generated_at",
        "evidence_sources",
        "findings",
        "narrative",
        "gate_results",
        "audit_stats",
    }
    assert set(data.keys()) == expected_keys

    assert data["case_id"] == case_id
    assert len(data["evidence_sources"]) == 1
    assert data["evidence_sources"][0]["name"] == "syslog"
    assert data["evidence_sources"][0]["artifact_type"] == "parser_syslog"

    assert len(data["findings"]) == 1
    assert data["findings"][0]["id"] == "finding-1"
    assert data["findings"][0]["severity"] == "high"
    assert data["findings"][0]["anomaly_ids"] == ["T1078"]
    assert data["findings"][0]["status"] is None

    # Verify redaction and path stripping in narrative and findings
    assert "AKIAIOSFODNN7EXAMPLE" not in data["narrative"]
    assert "[REDACTED]" in data["narrative"] or "C:\\" not in data["narrative"]

    assert data["audit_stats"]["entry_count"] == 1
    assert data["audit_stats"]["head_hash"] is not None
