"""
test_dashboard_data.py (Phase 6)

Read-only data layer of the demo dashboard: row builders and the
loaders. Focus: clear errors for corrupt/invalid saved state and
corrupt audit data (never a silent reset or fabricated state), a
visible notice when state is simply missing, and strict read-only
behaviour towards the four original CSVs.

All writes in these tests happen inside pytest's temp directories.
"""

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from dashboard import data
from src.audit import AuditLogger
from src.models import (
    Customer,
    DraftRecord,
    Notification,
    Payment,
    PaymentRecoveryState,
    RecoveryCase,
    Subscription,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_FILES = (
    "customers.csv",
    "payments.csv",
    "subscriptions.csv",
    "usage_history.csv",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def synthetic_case() -> RecoveryCase:
    """One case with a failed payment, a mock notification and a draft."""
    return RecoveryCase(
        case_id="CASE-T001",
        customer=Customer(customer_id="T001", name="Testy", expected_payday=5),
        payments=[
            Payment(
                payment_id="TP001",
                customer_id="T001",
                status="failed",
                failure_reason="insufficient_funds",
                amount=50.0,
            )
        ],
        subscriptions=[
            Subscription(
                subscription_id="TS001",
                customer_id="T001",
                service="Streamly",
                amount=10.0,
                status="active",
            )
        ],
        payment_recovery_state=PaymentRecoveryState.WAITING_FOR_PAYMENT,
        notification_count=1,
        max_notifications=3,
        scheduled_dates=[date(2026, 10, 5), date(2026, 10, 7), date(2026, 10, 9)],
        notifications=[
            Notification(
                notification_id="NT-001",
                case_id="CASE-T001",
                customer_id="T001",
                channel="SMS",
                message="TEMPLATE TEXT",
                scheduled_date=date(2026, 10, 5),
            )
        ],
        last_sent_date=date(2026, 10, 5),
        pending_drafts={
            "payment_reminder": DraftRecord(
                draft_id="DRAFT-T1",
                case_id="CASE-T001",
                kind="payment_reminder",
                text="Draft text under review.",
                status="PENDING_REVIEW",
                model="fake-model",
                created_at="2026-10-05T00:00:00+00:00",
            )
        },
    )


# --- Row builders -----------------------------------------------------


def test_overview_rows_expose_workflow_state(synthetic_case):
    rows = data.overview_rows({"CASE-T001": synthetic_case})
    assert len(rows) == 1
    row = rows[0]
    assert row["Case"] == "CASE-T001"
    assert row["Customer"] == "Testy"
    assert row["Payday"] == 5
    assert row["Payment recovery"] == "WAITING_FOR_PAYMENT"
    assert row["Reminders"] == "1/3"
    # Next UNSENT scheduled date (notification #1 already recorded on 10-05).
    assert row["Next scheduled"] == "2026-10-07"
    assert row["Pending drafts"] == 1
    assert row["Opted out"] == "No"


def test_pending_draft_rows_lists_pending_first(synthetic_case):
    from dataclasses import replace

    approved = replace(
        synthetic_case.pending_drafts["payment_reminder"], status="APPROVED"
    )
    synthetic_case.pending_drafts["retention_question"] = approved
    rows = data.pending_draft_rows({"CASE-T001": synthetic_case})
    assert [row["Status"] for row in rows] == ["PENDING_REVIEW", "APPROVED"]
    assert rows[0]["Text"] == "Draft text under review."


def test_case_detail_rows_cover_payments_schedule_and_notifications(synthetic_case):
    detail = data.case_detail(synthetic_case)
    assert detail["payments"] == [
        {"Payment": "TP001", "Status": "failed", "Reason": "insufficient_funds", "Amount": 50.0}
    ]
    assert detail["subscriptions"][0]["Service"] == "Streamly"
    assert detail["schedule"] == [
        {"#": 1, "Date": "2026-10-05", "Recorded (mock)": "Yes"},
        {"#": 2, "Date": "2026-10-07", "Recorded (mock)": "No"},
        {"#": 3, "Date": "2026-10-09", "Recorded (mock)": "No"},
    ]
    assert detail["notifications"][0]["Message"] == "TEMPLATE TEXT"
    assert detail["approved_messages"] == []


# --- load_cases -------------------------------------------------------


def test_load_cases_without_state_builds_fresh_with_notice(tmp_path):
    before = {name: _sha256(PROJECT_ROOT / name) for name in CSV_FILES}
    cases, notice = data.load_cases(PROJECT_ROOT, tmp_path / "state")
    assert len(cases) == 5
    assert "No saved state" in notice
    assert not (tmp_path / "state").exists()  # nothing was written
    after = {name: _sha256(PROJECT_ROOT / name) for name in CSV_FILES}
    assert before == after  # original CSVs untouched


def test_load_cases_corrupt_state_raises_clear_error(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    corrupt = state_dir / "CASE-C001.json"
    corrupt.write_text("{this is not JSON", encoding="utf-8")
    with pytest.raises(data.DashboardDataError) as excinfo:
        data.load_cases(PROJECT_ROOT, state_dir)
    message = str(excinfo.value)
    assert "Saved state could not be read" in message
    assert "Refusing to reset or fabricate state" in message
    # The corrupt file is preserved exactly - never reset or replaced.
    assert corrupt.read_text(encoding="utf-8") == "{this is not JSON"


def test_load_cases_invalid_schema_raises_clear_error(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    invalid = state_dir / "CASE-C001.json"
    invalid.write_text(
        json.dumps({"schema_version": 999, "case_id": "CASE-C001"}),
        encoding="utf-8",
    )
    with pytest.raises(data.DashboardDataError) as excinfo:
        data.load_cases(PROJECT_ROOT, state_dir)
    assert "Unsupported state schema version" in str(excinfo.value)
    assert json.loads(invalid.read_text(encoding="utf-8"))["schema_version"] == 999


def test_load_cases_partial_state_reports_how_many_loaded(tmp_path):
    from src.case_store import CaseStore

    cases, _ = data.load_cases(PROJECT_ROOT, tmp_path / "empty")
    store = CaseStore(state_dir=str(tmp_path / "state"))
    store.save(cases["CASE-C001"])
    cases, notice = data.load_cases(PROJECT_ROOT, tmp_path / "state")
    assert "1 of 5" in notice


# --- load_audit -------------------------------------------------------


def test_load_audit_missing_file_gives_notice_not_error(tmp_path):
    entries, notice, error = data.load_audit(tmp_path / "nope.jsonl")
    assert entries == []
    assert error == ""
    assert "No audit log found" in notice


def test_load_audit_corrupt_line_gives_clear_error(tmp_path):
    log = tmp_path / "audit_log.jsonl"
    log.write_text("{broken json\n", encoding="utf-8")
    entries, notice, error = data.load_audit(log)
    assert entries == []  # no partial or invented history
    assert notice == ""
    assert "could not be read" in error
    assert log.read_text(encoding="utf-8") == "{broken json\n"  # never reset


def test_load_audit_valid_log_parses_all_entries(tmp_path):
    log = tmp_path / "audit_log.jsonl"
    logger = AuditLogger(log_path=str(log))
    logger.log(
        case_id="CASE-T001",
        customer_id="T001",
        event_type="DECISION",
        action_or_decision="Something happened.",
        details={"n": 1},
    )
    logger.log(
        case_id="CASE-T001",
        customer_id="T001",
        event_type="ACTION",
        action_or_decision="Something else happened.",
    )
    entries, notice, error = data.load_audit(log)
    assert (notice, error) == ("", "")
    rows = data.audit_rows(entries)
    assert [row["Event"] for row in rows] == ["DECISION", "ACTION"]
    assert rows[0]["Details"] == '{"n": 1}'


def test_audit_rows_never_leaks_draft_or_message_text(tmp_path, synthetic_case):
    log = tmp_path / "audit_log.jsonl"
    AuditLogger(log_path=str(log)).log(
        case_id="CASE-T001",
        customer_id="T001",
        event_type="DECISION",
        action_or_decision="Metadata only.",
        details={"characters": 23},
    )
    entries, _, _ = data.load_audit(log)
    rendered = json.dumps(data.audit_rows(entries))
    assert "TEMPLATE TEXT" not in rendered
    assert "Draft text under review." not in rendered
