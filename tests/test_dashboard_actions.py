"""
test_dashboard_actions.py (Phase 6)

The only dashboard module that writes state. These tests prove:

- Approve/reject work only through the existing human-approval
  entry points, persist atomically, and record metadata-only audit
  entries.
- Duplicate submissions and stale clicks are refused with a clear
  error - never a silent double-approval or lost update.
- Nothing else on the case changes (counts, schedules, mock
  notifications, workflow states, opt-out flags).
- The dashboard sources never run recovery workflows or read
  credential-like environment variables.
- The four original CSVs and the six legacy root files are
  byte-identical afterwards.

All writes happen inside pytest's temp directories.
"""

import hashlib
import json
import re
from datetime import date
from pathlib import Path

import pytest

from dashboard import actions
from src.case_store import CaseStore
from src.models import (
    Customer,
    DraftRecord,
    Notification,
    Payment,
    PaymentRecoveryState,
    RecoveryCase,
    RetentionState,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"

PROTECTED_FILES = (
    "customers.csv",
    "payments.csv",
    "subscriptions.csv",
    "usage_history.csv",
    "models.py",
    "ingestion.py",
    "state_machine.py",
    "audit.py",
    "test_phase1.py",
    "test_phase2.py",
)

DRAFT_TEXT = "Draft wording that a human must review first."
NOTIFICATION_TEXT = "Payment reminder: template wording."


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_hashes() -> dict:
    return {name: _sha256(PROJECT_ROOT / name) for name in PROTECTED_FILES}


def _make_case() -> RecoveryCase:
    """A realistic case: one failed payment, one mock notification, one draft."""
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
        subscriptions=[],
        payment_recovery_state=PaymentRecoveryState.WAITING_FOR_PAYMENT,
        retention_state=RetentionState.NOT_APPLICABLE,
        notification_count=1,
        max_notifications=3,
        scheduled_dates=[date(2026, 10, 5), date(2026, 10, 7), date(2026, 10, 9)],
        notifications=[
            Notification(
                notification_id="NT-001",
                case_id="CASE-T001",
                customer_id="T001",
                channel="SMS",
                message=NOTIFICATION_TEXT,
                scheduled_date=date(2026, 10, 5),
            )
        ],
        last_sent_date=date(2026, 10, 5),
        pending_drafts={
            "payment_reminder": DraftRecord(
                draft_id="DRAFT-T1",
                case_id="CASE-T001",
                kind="payment_reminder",
                text=DRAFT_TEXT,
                status="PENDING_REVIEW",
                model="fake-model",
                created_at="2026-10-05T00:00:00+00:00",
            )
        },
    )


@pytest.fixture
def workspace(tmp_path):
    """A fresh state directory + audit log containing one pending draft."""
    state_dir = tmp_path / "state"
    audit_path = tmp_path / "audit" / "audit_log.jsonl"
    CaseStore(state_dir=str(state_dir)).save(_make_case())
    return {"state_dir": state_dir, "audit_path": audit_path}


def _approve(workspace):
    return actions.approve_draft(
        "CASE-T001",
        "payment_reminder",
        state_dir=workspace["state_dir"],
        audit_path=workspace["audit_path"],
    )


def _reject(workspace, reason=None):
    return actions.reject_draft(
        "CASE-T001",
        "payment_reminder",
        state_dir=workspace["state_dir"],
        audit_path=workspace["audit_path"],
        reason=reason,
    )


def _reload(workspace) -> RecoveryCase:
    return CaseStore(state_dir=str(workspace["state_dir"])).load("CASE-T001")


def _audit_text(workspace) -> str:
    path = workspace["audit_path"]
    return path.read_text(encoding="utf-8") if path.is_file() else ""


# --- Approve ----------------------------------------------------------


def test_approve_persists_and_logs_metadata_only(workspace):
    before = _reload(workspace)
    result = _approve(workspace)
    assert result.ok is True
    assert "Approved draft DRAFT-T1" in result.message

    after = _reload(workspace)
    assert after.approved_messages == {"payment_reminder": DRAFT_TEXT}
    assert after.pending_drafts["payment_reminder"].status == "APPROVED"

    audit = _audit_text(workspace)
    assert audit.count('"event_type": "HUMAN_APPROVAL"') == 1
    assert "HUMAN_APPROVAL" in audit
    # Metadata only: no draft text, no notification text, no customer name.
    assert DRAFT_TEXT not in audit
    assert NOTIFICATION_TEXT not in audit
    assert "Testy" not in audit
    # No other case state changed.
    assert after.notification_count == before.notification_count == 1
    assert after.notifications == before.notifications
    assert after.scheduled_dates == before.scheduled_dates
    assert after.payment_recovery_state == before.payment_recovery_state
    assert after.retention_state == before.retention_state
    assert after.opted_out is False
    assert after.max_notifications == before.max_notifications


def test_duplicate_approval_is_refused_with_clear_error(workspace):
    _approve(workspace)
    with pytest.raises(actions.DashboardActionError) as excinfo:
        _approve(workspace)
    assert "only a PENDING_REVIEW draft" in str(excinfo.value)

    after = _reload(workspace)
    assert after.approved_messages == {"payment_reminder": DRAFT_TEXT}
    assert _audit_text(workspace).count('"event_type": "HUMAN_APPROVAL"') == 1


def test_stale_approval_after_external_rejection_is_refused(workspace):
    """A page that still shows a draft cannot approve it after rejection."""
    from src.ai.approval import reject_pending_draft
    from src.audit import AuditLogger

    # Another reviewer rejects the draft first (outside the dashboard).
    store = CaseStore(state_dir=str(workspace["state_dir"]))
    case = store.load("CASE-T001")
    audit = AuditLogger(log_path=str(workspace["audit_path"]))
    assert reject_pending_draft(case, audit, "payment_reminder", reason="stale") is True
    store.save(case)

    with pytest.raises(actions.DashboardActionError) as excinfo:
        _approve(workspace)
    assert "No draft of kind 'payment_reminder'" in str(excinfo.value)

    after = _reload(workspace)
    assert "payment_reminder" not in after.pending_drafts
    assert after.approved_messages == {}
    assert _audit_text(workspace).count('"event_type": "HUMAN_APPROVAL"') == 1


def test_stale_approval_after_external_approval_is_refused(workspace):
    _approve(workspace)
    with pytest.raises(actions.DashboardActionError):
        _approve(workspace)
    assert _reload(workspace).approved_messages == {"payment_reminder": DRAFT_TEXT}
    assert _audit_text(workspace).count('"event_type": "HUMAN_APPROVAL"') == 1


# --- Reject -----------------------------------------------------------


def test_reject_removes_draft_and_keeps_template_rule(workspace):
    result = _reject(workspace, reason="Too informal for the brand.")
    assert result.ok is True
    after = _reload(workspace)
    assert after.pending_drafts == {}
    assert after.approved_messages == {}  # template stays in use

    audit = _audit_text(workspace)
    assert audit.count('"event_type": "HUMAN_APPROVAL"') == 1
    assert "Too informal for the brand." in audit
    assert DRAFT_TEXT not in audit  # metadata + reason only, no draft text


def test_duplicate_rejection_is_refused_with_clear_error(workspace):
    _reject(workspace)
    with pytest.raises(actions.DashboardActionError) as excinfo:
        _reject(workspace)
    assert "No draft of kind 'payment_reminder'" in str(excinfo.value)
    assert _audit_text(workspace).count('"event_type": "HUMAN_APPROVAL"') == 1


# --- Guards -----------------------------------------------------------


def test_unknown_draft_kind_is_refused(workspace):
    with pytest.raises(actions.DashboardActionError) as excinfo:
        actions.approve_draft(
            "CASE-T001",
            "no_such_kind",
            state_dir=workspace["state_dir"],
            audit_path=workspace["audit_path"],
        )
    assert "No draft of kind 'no_such_kind'" in str(excinfo.value)
    assert workspace["audit_path"].exists() is False  # nothing was logged


def test_missing_state_file_is_refused_not_fabricated(workspace):
    with pytest.raises(actions.DashboardActionError) as excinfo:
        actions.approve_draft(
            "CASE-NOPE",
            "payment_reminder",
            state_dir=workspace["state_dir"],
            audit_path=workspace["audit_path"],
        )
    assert "No saved state exists for case 'CASE-NOPE'" in str(excinfo.value)
    assert not (workspace["state_dir"] / "CASE-NOPE.json").exists()


def test_corrupt_state_file_is_refused_and_untouched(workspace):
    corrupt = workspace["state_dir"] / "CASE-T001.json"
    corrupt.write_text("{broken", encoding="utf-8")
    with pytest.raises(actions.DashboardActionError) as excinfo:
        _approve(workspace)
    assert "could not be read" in str(excinfo.value)
    assert corrupt.read_text(encoding="utf-8") == "{broken"  # never reset


# --- Source scan: the dashboard never runs workflows or reads secrets ---

FORBIDDEN_TOKENS = (
    "payment_recovery_step",
    "retention_step",
    "run_recovery_step",
    "MockNotifier",
    "generate_content",
    "genai",
    "GEMINI",
    "API_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
)

ALLOWED_ENV_VARS = {
    "DASHBOARD_STATE_DIR",
    "DASHBOARD_CSV_DIR",
    "DASHBOARD_AUDIT_PATH",
}


def test_dashboard_sources_never_run_workflows_or_touch_credentials():
    env_reads = set()
    for path in sorted(DASHBOARD_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            assert token not in text, f"{path.name} contains forbidden token {token!r}"
        env_reads.update(
            re.findall(r"os\.environ\.get\(\s*[\"']([A-Z0-9_]+)[\"']", text)
        )
        env_reads.update(
            re.findall(r"os\.environ\[[\"']([A-Z0-9_]+)[\"']\]", text)
        )
    assert env_reads <= ALLOWED_ENV_VARS, f"unexpected env reads: {env_reads}"


def test_protected_files_unchanged_after_all_actions(workspace):
    """Original CSVs and the six legacy root files stay byte-identical."""
    before = _protected_hashes()
    _approve(workspace)
    with pytest.raises(actions.DashboardActionError):
        _reject(workspace, reason="second pass")  # refused: already approved
    after = _protected_hashes()
    assert before == after
