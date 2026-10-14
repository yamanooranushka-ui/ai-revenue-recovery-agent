"""Tests for the append-only audit log (Phase 2)."""
import json
from datetime import date

from src.audit import AuditLogger


def test_log_appends_json_line(tmp_path):
    log_path = tmp_path / "audit" / "audit_log.jsonl"
    audit = AuditLogger(log_path=str(log_path))
    audit.log(
        case_id="CASE-C1",
        customer_id="C1",
        event_type="DECISION",
        action_or_decision="Test decision",
        details={"key": "value"},
    )
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["case_id"] == "CASE-C1"
    assert entry["event_type"] == "DECISION"
    assert entry["action_or_decision"] == "Test decision"
    assert entry["timestamp"]
    assert entry["audit_id"]


def test_log_is_append_only(tmp_path):
    log_path = tmp_path / "audit_log.jsonl"
    audit = AuditLogger(log_path=str(log_path))
    audit.log(case_id="CASE-C1", customer_id="C1", event_type="DECISION", action_or_decision="First")
    audit.log(case_id="CASE-C1", customer_id="C1", event_type="ACTION", action_or_decision="Second")
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["action_or_decision"] == "First"
    assert json.loads(lines[1])["action_or_decision"] == "Second"


def test_entries_for_case_filters(tmp_path):
    audit = AuditLogger(log_path=str(tmp_path / "audit_log.jsonl"))
    audit.log(case_id="CASE-C1", customer_id="C1", event_type="DECISION", action_or_decision="For C1")
    audit.log(case_id="CASE-C2", customer_id="C2", event_type="DECISION", action_or_decision="For C2")
    assert len(audit.entries_for_case("CASE-C1")) == 1
    assert len(audit.all_entries()) == 2


def test_non_serializable_details_are_stringified(tmp_path):
    audit = AuditLogger(log_path=str(tmp_path / "audit_log.jsonl"))
    audit.log(
        case_id="CASE-C1",
        customer_id="C1",
        event_type="ACTION",
        action_or_decision="Scheduled",
        details={"scheduled_date": date(2026, 1, 5)},
    )
    entry = audit.all_entries()[0]
    assert entry.details["scheduled_date"] == "2026-01-05"
