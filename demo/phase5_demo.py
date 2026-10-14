"""
phase5_demo.py (Phase 5)
End-to-end demonstration of the AI Revenue Recovery Agent,
using SYNTHETIC data only.

Every act has hard assertions: if any safety property fails,
the script exits non-zero with a traceback.

What this proves:
 1. A synthetic customer with an eligible failed payment is
    handled by the deterministic recovery rules (schedule,
    never early, limits, stop conditions).
 2. Gemini drafting runs through the exact Phase 4 path -
    with a FAKE client, so no live API call is possible.
 3. The generated draft is stored as PENDING_REVIEW.
 4. The unapproved draft never reaches MockNotifier; the
    deterministic template is used instead.
 5. After explicit human approval, later notifications use
    the approved wording (including after a restart/reload).
 6. The audit log carries drafting/approval metadata but NO
    API key, NO draft text, NO notification message text and
    NO customer name.
 7. Original CSVs, legacy root files and the project tree are
    unchanged (SHA-256 before/after); all demo artifacts live
    inside a temporary directory.
 8. No real SMS, no payment processing, no live API calls:
    MockNotifier only records, the fake client only answers.

Run:  python demo/phase5_demo.py
"""

import hashlib
import os
import sys
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ai.approval import approve_pending_draft
from src.ai.drafter import DEFAULT_MODEL, MessageDrafter
from src.audit import AuditLogger
from src.case_store import CaseStore
from src.ingestion import build_recovery_cases
from src.models import PaymentRecoveryState, RetentionState
from src.notifications import MockNotifier
from src.validation import load_dataset
# Phase 2's deterministic template builder: the single source of
# truth for "the template" that must be used before approval.
from src.workflows.payment_recovery import _build_message
from src.workflows.router import load_or_build_cases, run_recovery_step

# Original CSVs + legacy root files: must be byte-identical after the demo.
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

# Synthetic customer - written ONLY into the demo temp directory.
SYNTHETIC_CUSTOMER_NAME = "Jordan Demo"
SYNTHETIC_CSVS = {
    "customers.csv": (
        "customer_id,name,expected_payday\n"
        "DEMO001,Jordan Demo,10\n"
    ),
    "payments.csv": (
        "payment_id,customer_id,status,failure_reason,amount\n"
        "DMP001,DEMO001,failed,insufficient_funds,649.0\n"
    ),
    "subscriptions.csv": (
        "subscription_id,customer_id,service,amount,status\n"
        "DMS001,DEMO001,Streamly,199.0,active\n"
    ),
    "usage_history.csv": "customer_id,service,month,usage\n",
}

# The AI draft used by the fake client. Passes validation:
# <= 320 chars, no URLs/offers/promises, only the approved amount.
DRAFT_TEXT = (
    "Hi Jordan, your payment of 649 could not be completed. "
    "Please complete the payment manually. This is reminder 1 of 3."
)

# Sentinel, never a real key: used to prove no key text reaches the audit log.
API_KEY_SENTINEL = "demo-fake-key-not-a-real-secret"


class _FakeGeminiClient:
    """Minimal stand-in for google.genai.Client (no network, no SDK, no key)."""

    def __init__(self, text: str):
        self.text = text
        self.calls = []
        self.models = self  # the SDK nests model methods under client.models

    def generate_content(self, model=None, contents=None):
        self.calls.append({"model": model, "contents": contents})
        return SimpleNamespace(text=self.text)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _say(title: str) -> None:
    print(f"\n=== {title} ===")


def run_demo(tmp_root: Path) -> dict:
    """Run the full demonstration inside tmp_root. Returns a summary dict."""
    tmp_root = Path(tmp_root)
    csv_dir = tmp_root / "csvs"
    state_dir = tmp_root / "state"
    audit_path = tmp_root / "audit" / "phase5_audit.jsonl"
    csv_dir.mkdir(parents=True, exist_ok=True)

    # Proof 7 baseline: hashes of protected files + project-root listing,
    # plus whether google.genai was already loaded by another test.
    hashes_before = {n: _sha256(PROJECT_ROOT / n) for n in PROTECTED_FILES}
    root_before = set(os.listdir(PROJECT_ROOT))
    genai_loaded_before = "google.genai" in sys.modules

    _say("Act 1: synthetic dataset + deterministic scheduling")
    for name, content in SYNTHETIC_CSVS.items():
        (csv_dir / name).write_text(content, encoding="utf-8")

    dataset = load_dataset(
        customers_path=str(csv_dir / "customers.csv"),
        payments_path=str(csv_dir / "payments.csv"),
        subscriptions_path=str(csv_dir / "subscriptions.csv"),
        usage_path=str(csv_dir / "usage_history.csv"),
    )
    assert dataset.report.ok, dataset.report.summary()
    assert not dataset.report.warnings, dataset.report.summary()

    case = build_recovery_cases(
        dataset.customers, dataset.payments, dataset.subscriptions, dataset.usage
    )[0]
    assert case.case_id == "CASE-DEMO001"
    payment = case.payments[0]
    template = _build_message(payment)

    notifier = MockNotifier()  # records only - never delivers
    audit = AuditLogger(log_path=str(audit_path))
    store = CaseStore(state_dir=str(state_dir))
    fake = _FakeGeminiClient(DRAFT_TEXT)
    drafter = MessageDrafter(
        client=fake,
        model=DEFAULT_MODEL,
        api_key=API_KEY_SENTINEL,
        enabled=True,
    )

    # Never early: nothing may happen before the first scheduled date.
    outcomes = run_recovery_step(
        case, today=date(2026, 1, 8), audit=audit, notifier=notifier,
        state_store=store, drafter=drafter,
    )
    assert outcomes["payment_recovery"] == "Waiting until 2026-01-10."
    assert outcomes["retention"].startswith("Retention not applicable")
    assert case.payment_recovery_state == PaymentRecoveryState.SCHEDULED
    assert case.retention_state == RetentionState.NOT_APPLICABLE
    assert notifier.count == 0
    assert fake.calls == []  # no drafting before the reminder is due
    print(f"01-08: {outcomes['payment_recovery']} (0 notifications, 0 drafts)")

    _say("Act 2: first eligible notification uses the DETERMINISTIC TEMPLATE")
    outcomes = run_recovery_step(
        case, today=date(2026, 1, 10), audit=audit, notifier=notifier,
        state_store=store, drafter=drafter,
    )
    assert outcomes["payment_recovery"] == "Created notification #1."
    assert case.scheduled_dates == [
        date(2026, 1, 10), date(2026, 1, 12), date(2026, 1, 14),
    ]
    assert notifier.count == 1
    first_message = notifier.created[0].message
    assert first_message == template  # proof 4: template, not the draft
    assert DRAFT_TEXT not in first_message
    assert case.payment_recovery_state == PaymentRecoveryState.WAITING_FOR_PAYMENT

    # Proof 2: the drafter ran through the real Phase 4 path.
    assert len(fake.calls) == 1
    assert fake.calls[0]["model"] == DEFAULT_MODEL
    assert f'"customer_name": "{SYNTHETIC_CUSTOMER_NAME}"' in fake.calls[0]["contents"]
    assert '"amount": "649"' in fake.calls[0]["contents"]

    # Proof 3: the draft waits for human review.
    draft = case.pending_drafts["payment_reminder"]
    assert draft.status == "PENDING_REVIEW"
    assert draft.text == DRAFT_TEXT
    assert draft.kind == "payment_reminder"
    assert draft.model == DEFAULT_MODEL
    assert case.approved_messages == {}
    print(f"01-10 notification : {first_message}")
    print(f"01-10 draft saved  : [{draft.status}] {draft.text}")

    _say("Act 3: explicit human approval (the only promotion path)")
    assert approve_pending_draft(case, audit, "payment_reminder") is True
    assert case.pending_drafts["payment_reminder"].status == "APPROVED"
    assert case.approved_messages["payment_reminder"] == DRAFT_TEXT
    # Approval is not idempotent by accident: a second approval
    # of an already-approved draft is refused (no pending draft).
    assert approve_pending_draft(case, audit, "payment_reminder") is False
    print("approved: PENDING_REVIEW -> APPROVED (wording now usable)")

    _say("Act 4: persist + reload (restart safety)")
    store.save(case)
    reloaded_cases = load_or_build_cases(dataset, store)
    case = reloaded_cases["CASE-DEMO001"]
    reloaded_count = case.notification_count
    reload_approved = case.approved_messages.get("payment_reminder") == DRAFT_TEXT
    assert reloaded_count == 1  # saved state won; count not reset
    assert len(case.notifications) == 1
    assert case.notifications[0].message == template  # the recorded message survives
    assert reload_approved
    assert case.pending_drafts["payment_reminder"].status == "APPROVED"
    assert case.scheduled_dates == [
        date(2026, 1, 10), date(2026, 1, 12), date(2026, 1, 14),
    ]
    assert case.last_sent_date == date(2026, 1, 10)
    print(f"reloaded: notifications={case.notification_count}, "
          f"approved wording preserved={reload_approved}")

    _say("Act 5: second notification uses the APPROVED WORDING")
    outcomes = run_recovery_step(
        case, today=date(2026, 1, 12), audit=audit, notifier=notifier,
        state_store=store, drafter=drafter,
    )
    assert outcomes["payment_recovery"] == "Created notification #2."
    assert notifier.count == 2
    assert notifier.created[1].message == DRAFT_TEXT  # proof 5
    # A fresh draft is proposed for review; approval is not revoked by it.
    assert case.pending_drafts["payment_reminder"].status == "PENDING_REVIEW"
    assert case.approved_messages["payment_reminder"] == DRAFT_TEXT
    print(f"01-12 notification : {notifier.created[1].message}")

    _say("Act 6: third notification + notification limit enforced")
    outcomes = run_recovery_step(
        case, today=date(2026, 1, 14), audit=audit, notifier=notifier,
        state_store=store, drafter=drafter,
    )
    assert outcomes["payment_recovery"] == (
        "Notification limit (3) reached. Payment recovery stopped."
    )
    assert notifier.count == 3
    assert case.notification_count == 3
    assert case.payment_recovery_state == (
        PaymentRecoveryState.CLOSED_MAX_NOTIFICATIONS_REACHED
    )
    assert case.payment_recovery_is_closed
    assert notifier.created[2].message == DRAFT_TEXT

    _say("Act 7: terminal case does nothing (idempotent stop)")
    calls_before = len(fake.calls)
    outcomes = run_recovery_step(
        case, today=date(2026, 1, 16), audit=audit, notifier=notifier,
        state_store=store, drafter=drafter,
    )
    assert outcomes["payment_recovery"] == (
        "Notification limit (3) reached. Payment recovery stopped."
    )
    assert notifier.count == 3  # no fourth notification
    assert len(fake.calls) == calls_before  # no drafting on a terminal case
    stop_events = [
        e for e in audit.entries_for_case("CASE-DEMO001")
        if e.event_type == "STOP_CONDITION_MET"
    ]
    assert len(stop_events) == 1  # logged exactly once
    assert case.payments[0].status == "failed"  # agent never marked it paid
    print(f"01-16: stop reason returned, no new work. "
          f"drafting calls total={len(fake.calls)}")

    _say("Act 8: audit safety (metadata present, secrets/contents absent)")
    raw_log = audit_path.read_text(encoding="utf-8")
    entries = audit.entries_for_case("CASE-DEMO001")
    # Leak checks (proof 6):
    assert API_KEY_SENTINEL not in raw_log
    assert DRAFT_TEXT not in raw_log
    assert template not in raw_log  # notification message content
    assert SYNTHETIC_CUSTOMER_NAME not in raw_log  # stricter privacy guarantee
    # Append-only sanity: one JSON line per recorded entry.
    assert len(raw_log.strip().splitlines()) == len(audit.all_entries())
    # Required metadata IS present:
    drafting_entries = [e for e in entries if e.details.get("drafting_status")]
    assert len(drafting_entries) == 3
    assert all(e.details["ai_model"] == DEFAULT_MODEL for e in drafting_entries)
    assert all("draft_characters" in e.details for e in drafting_entries)
    assert DEFAULT_MODEL in raw_log
    action_sources = {
        e.details["notification_count"]: e.details["message_source"]
        for e in entries
        if e.event_type == "ACTION" and "notification_count" in e.details
    }
    assert action_sources == {
        1: "approved_template",
        2: "approved_message",
        3: "approved_message",
    }
    approval_entries = [e for e in entries if e.event_type == "HUMAN_APPROVAL"]
    assert len(approval_entries) == 1
    assert approval_entries[0].details["kind"] == "payment_reminder"
    assert approval_entries[0].details["model"] == DEFAULT_MODEL
    assert "text" not in approval_entries[0].details  # no draft content
    present_types = {e.event_type for e in entries}
    assert {"DECISION", "ACTION", "HUMAN_APPROVAL",
            "STOP_CONDITION_MET", "STATE_TRANSITION"} <= present_types
    print(f"audit: {len(entries)} entries, "
          f"0 key/draft/message/name leaks, message sources {action_sources}")

    _say("Act 9: file safety (nothing in the project was touched)")
    hashes_after = {n: _sha256(PROJECT_ROOT / n) for n in PROTECTED_FILES}
    assert hashes_before == hashes_after  # proof 7
    root_after = set(os.listdir(PROJECT_ROOT))
    assert root_before == root_after  # no new/removed project entries
    artifacts = [csv_dir, state_dir, audit_path]
    assert all(p.is_relative_to(tmp_root) for p in artifacts)
    assert (csv_dir / "customers.csv").is_file()
    assert store.exists("CASE-DEMO001")
    assert audit_path.is_file()
    # Proof 8: the demo never pulled in the real SDK.
    genai_imported_during_demo = (
        "google.genai" in sys.modules and not genai_loaded_before
    )
    assert not genai_imported_during_demo
    assert {n.status for n in notifier.created} == {"CREATED"}  # never "SENT"
    print("protected files unchanged, project root unchanged, "
          "artifacts confined to temp dir, google.genai never imported")

    return {
        "case_id": case.case_id,
        "dataset_errors": 0,
        "scheduled_dates": ["2026-01-10", "2026-01-12", "2026-01-14"],
        "early_runs_blocked": True,
        "notifications_created": notifier.count,
        "first_message_used_template": first_message == template,
        "second_message_used_approved": notifier.created[1].message == DRAFT_TEXT,
        "third_message_used_approved": notifier.created[2].message == DRAFT_TEXT,
        "pending_status_after_first_draft": "PENDING_REVIEW",
        "drafting_calls": len(fake.calls),
        "reload_notification_count": reloaded_count,
        "reload_preserved_approved_wording": reload_approved,
        "final_state": case.payment_recovery_state.value,
        "payment_still_failed": case.payments[0].status == "failed",
        "stop_condition_events": len(stop_events),
        "human_approval_events": len(approval_entries),
        "drafting_audit_entries": len(drafting_entries),
        "message_sources": action_sources,
        "audit_entries": len(entries),
        "audit_leak_free": True,  # all leak assertions above passed
        "protected_files_unchanged": hashes_before == hashes_after,
        "project_root_unchanged": root_before == root_after,
        "artifacts_inside_temp": True,
        "google_genai_imported_during_demo": genai_imported_during_demo,
        "notification_statuses": sorted({n.status for n in notifier.created}),
        "live_api_calls": 0,
        "messages_delivered": 0,
        "payment_operations": 0,
    }


def main() -> int:
    print("Phase 5 end-to-end demonstration - SYNTHETIC data only")
    print("Fake Gemini client + MockNotifier: no live API, no SMS, no payments.")
    with TemporaryDirectory(prefix="phase5_demo_") as tmp:
        summary = run_demo(Path(tmp))
    _say("RESULT: all assertions passed")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print(f"\nAll demo artifacts were created in a temporary directory "
          f"and deleted on exit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
