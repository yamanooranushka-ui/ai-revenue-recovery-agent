"""
Phase 4 integration tests.

Prove that unapproved AI drafts can NEVER enter the
notification workflow, that the deterministic template
remains in use until a human approves a draft, and
that drafting only happens for eligible, active cases.
"""
from datetime import date

import pytest

from src.ai.approval import approve_pending_draft, reject_pending_draft
from src.ai.drafter import MessageDrafter
from src.case_store import CaseStore
from src.models import (
    Customer,
    Payment,
    PaymentRecoveryState,
    RecoveryCase,
    RetentionState,
)
from src.notifications import MockNotifier
from src.workflows.payment_recovery import payment_recovery_step
from src.workflows.retention import retention_step
from src.workflows.router import run_recovery_step

from fake_gemini import FakeGeminiClient

UNAPPROVED_DRAFT = (
    "Hi Alex! Great news - we will retry your payment "
    "automatically and give you a 50% discount. Pay at "
    "https://example.com now!"
)

CLEAN_DRAFT = (
    "Hi Alex, your payment of 649.0 failed because of "
    "insufficient funds. Please complete it manually."
)

TEMPLATE = (
    "Payment reminder: your payment of 649.0 failed "
    "because of insufficient funds. Please complete "
    "the payment manually."
)


def _drafter(text=CLEAN_DRAFT, error=None):
    """A drafter that is enabled and configured (fake client)."""
    return MessageDrafter(
        client=FakeGeminiClient(text=text, error=error),
        api_key="test-key",
        enabled=True,
    )


def test_unapproved_draft_never_enters_the_notification(cases, audit):
    """A VALID draft still waits for review: template is used."""
    case = cases["C001"]
    notifier = MockNotifier()
    drafter = _drafter(text=CLEAN_DRAFT)

    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, drafter=drafter,
    )

    assert notifier.count == 1
    # The notification text is the template - never the draft.
    assert notifier.created[0].message == TEMPLATE
    assert CLEAN_DRAFT not in notifier.created[0].message
    # The draft exists only as a PENDING_REVIEW record.
    draft = case.pending_drafts["payment_reminder"]
    assert draft.status == "PENDING_REVIEW"
    assert draft.text == CLEAN_DRAFT
    assert draft.kind == "payment_reminder"
    assert case.approved_messages == {}


def test_dangerous_draft_is_rejected_and_never_stored(cases, audit):
    """A draft with forbidden content is rejected outright."""
    case = cases["C001"]
    notifier = MockNotifier()
    drafter = _drafter(text=UNAPPROVED_DRAFT)

    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, drafter=drafter,
    )

    # The notification uses the template.
    assert notifier.created[0].message == TEMPLATE
    # The dangerous draft was never stored anywhere.
    assert case.pending_drafts == {}
    assert case.approved_messages == {}
    # The audit records the validation failure (metadata only).
    entries = audit.entries_for_case("CASE-C001")
    drafting = [e for e in entries if e.details.get("drafting_status")]
    assert drafting[0].details["drafting_status"] == "validation_failed"


def test_approved_draft_is_used_for_later_notifications(cases, audit):
    """After explicit human approval, later notifications may use it."""
    case = cases["C001"]
    notifier = MockNotifier()
    drafter = _drafter(text=CLEAN_DRAFT)

    # Notification #1: template (draft not yet approved).
    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    assert notifier.created[0].message == TEMPLATE

    # A human reviews and approves the draft.
    assert approve_pending_draft(case, audit, "payment_reminder")
    assert case.approved_messages["payment_reminder"] == CLEAN_DRAFT
    assert case.pending_drafts["payment_reminder"].status == "APPROVED"

    # Notification #2 uses the approved wording.
    payment_recovery_step(
        case, today=date(2026, 1, 7), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    assert notifier.created[1].message == CLEAN_DRAFT


def test_rejected_draft_is_never_used(cases, audit):
    case = cases["C001"]
    notifier = MockNotifier()
    drafter = _drafter(text=CLEAN_DRAFT)

    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    assert reject_pending_draft(case, audit, "payment_reminder")
    assert "payment_reminder" not in case.pending_drafts
    assert case.approved_messages == {}

    payment_recovery_step(
        case, today=date(2026, 1, 7), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    assert notifier.created[1].message == TEMPLATE


def test_approval_without_pending_draft_is_refused(cases, audit):
    case = cases["C001"]
    assert approve_pending_draft(case, audit, "payment_reminder") is False
    assert reject_pending_draft(case, audit, "payment_reminder") is False


# ---------- Stop conditions: no drafting, ever ----------


def test_opted_out_case_never_drafts(cases, audit):
    case = cases["C001"]
    case.opted_out = True
    notifier = MockNotifier()
    drafter = _drafter()

    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    assert notifier.count == 0
    assert drafter._client.calls == []
    assert case.pending_drafts == {}


def test_terminal_paid_case_never_drafts(cases, audit):
    case = cases["C003"]  # payment already successful
    notifier = MockNotifier()
    drafter = _drafter()

    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    assert case.payment_recovery_state == PaymentRecoveryState.RESOLVED_PAID
    assert notifier.count == 0
    assert drafter._client.calls == []


def test_parked_case_never_drafts(audit):
    customer = Customer("C900", "Synthetic", 5)
    case = RecoveryCase(case_id="CASE-C900", customer=customer)
    case.payments.append(
        Payment("P900", "C900", "failed", "card_declined", 50.0)
    )
    notifier = MockNotifier()
    drafter = _drafter()

    # First step parks the case (unknown failure reason).
    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    assert case.payment_recovery_state == PaymentRecoveryState.NOT_YET_SPECIFIED

    # Every later step stays parked: no drafting, no notification.
    payment_recovery_step(
        case, today=date(2026, 1, 7), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    assert notifier.count == 0
    assert drafter._client.calls == []


def test_paused_month_boundary_case_never_drafts(audit):
    customer = Customer("C901", "Synthetic", 31)
    case = RecoveryCase(case_id="CASE-C901", customer=customer)
    case.payments.append(
        Payment("P901", "C901", "failed", "insufficient_funds", 50.0)
    )
    notifier = MockNotifier()
    drafter = _drafter()

    payment_recovery_step(
        case, today=date(2026, 2, 1), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    assert case.payment_recovery_state == (
        PaymentRecoveryState.PAUSED_MONTH_BOUNDARY
    )
    payment_recovery_step(
        case, today=date(2026, 2, 3), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    assert notifier.count == 0
    assert drafter._client.calls == []


# ---------- Configuration: disabled or missing ----------


def test_disabled_drafter_uses_template(cases, audit):
    case = cases["C001"]
    notifier = MockNotifier()
    drafter = MessageDrafter(
        client=FakeGeminiClient(text=CLEAN_DRAFT),
        api_key="test-key",
        enabled=False,
    )

    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    assert notifier.count == 1
    assert notifier.created[0].message == TEMPLATE
    assert case.pending_drafts == {}
    assert drafter._client.calls == []


def test_drafter_without_key_uses_template(monkeypatch, cases, audit):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    case = cases["C001"]
    notifier = MockNotifier()
    drafter = MessageDrafter(
        client=FakeGeminiClient(text=CLEAN_DRAFT),
        enabled=True,  # flag set, but no API key
    )

    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    assert notifier.created[0].message == TEMPLATE
    assert case.pending_drafts == {}
    assert drafter._client.calls == []


def test_no_drafter_at_all_uses_template(cases, audit):
    case = cases["C001"]
    notifier = MockNotifier()
    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit, notifier=notifier
    )
    assert notifier.created[0].message == TEMPLATE
    assert case.pending_drafts == {}


# ---------- Audit: metadata only, never secrets or draft text ----------


def test_audit_never_logs_draft_text_name_or_key(cases, audit):
    case = cases["C001"]
    notifier = MockNotifier()
    drafter = _drafter(text=CLEAN_DRAFT)

    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    approve_pending_draft(case, audit, "payment_reminder")

    for entry in audit.entries_for_case("CASE-C001"):
        assert CLEAN_DRAFT not in entry.action_or_decision
        assert CLEAN_DRAFT not in str(entry.details)
        assert "Alex" not in entry.action_or_decision
        assert "Alex" not in str(entry.details)
        assert "test-key" not in entry.action_or_decision
        assert "test-key" not in str(entry.details)


def test_audit_records_drafting_metadata(cases, audit):
    case = cases["C001"]
    notifier = MockNotifier()
    drafter = _drafter(text=CLEAN_DRAFT)

    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    entries = audit.entries_for_case("CASE-C001")
    drafting = [e for e in entries if e.details.get("drafting_status")]
    assert len(drafting) == 1
    assert drafting[0].details["drafting_status"] == "created"
    assert drafting[0].details["ai_model"] == drafter.model
    assert drafting[0].details["message_source"] == "approved_template"
    assert "draft_id" in drafting[0].details
    assert "draft_characters" in drafting[0].details


def test_audit_records_human_approval(cases, audit):
    case = cases["C001"]
    notifier = MockNotifier()
    drafter = _drafter(text=CLEAN_DRAFT)
    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, drafter=drafter,
    )
    approve_pending_draft(case, audit, "payment_reminder")

    approvals = [
        e for e in audit.entries_for_case("CASE-C001")
        if e.event_type == "HUMAN_APPROVAL"
    ]
    assert len(approvals) == 1
    assert approvals[0].details["kind"] == "payment_reminder"
    assert approvals[0].details["model"] == drafter.model
    assert "text" not in approvals[0].details


# ---------- Retention drafting: eligible cases only ----------


def test_retention_drafts_only_for_high_usage_lapsed(cases, audit):
    case = cases["C001"]  # Netflix lapsed, consistently high
    drafter = _drafter(text="Do you want to keep Netflix?")

    outcome = retention_step(case, audit=audit, drafter=drafter)

    assert case.retention_state == RetentionState.PENDING_CUSTOMER_INTENT
    draft = case.pending_drafts["retention_question"]
    assert draft.status == "PENDING_REVIEW"
    assert draft.kind == "retention_question"
    assert draft.text == "Do you want to keep Netflix?"
    # The question asked remains the deterministic outcome.
    assert outcome == "Asking customer whether they want to continue Netflix."


def test_retention_never_drafts_for_low_usage(cases, audit):
    case = cases["C004"]  # Notion lapsed, low usage -> parked
    drafter = _drafter()

    retention_step(case, audit=audit, drafter=drafter)

    assert case.retention_state == RetentionState.NOT_YET_SPECIFIED
    assert case.pending_drafts == {}
    assert drafter._client.calls == []


def test_retention_never_drafts_for_active_subscriptions(cases, audit):
    for customer_id in ("C002", "C005"):
        case = cases[customer_id]
        drafter = _drafter()
        retention_step(case, audit=audit, drafter=drafter)
        assert case.retention_state == RetentionState.NOT_APPLICABLE
        assert case.pending_drafts == {}
        assert drafter._client.calls == []


def test_retention_never_drafts_when_opted_out(cases, audit):
    case = cases["C001"]
    case.opted_out = True
    drafter = _drafter()
    retention_step(case, audit=audit, drafter=drafter)
    assert case.retention_state == RetentionState.CLOSED_OPTED_OUT
    assert drafter._client.calls == []


def test_retention_approved_draft_is_stored(cases, audit):
    case = cases["C001"]
    drafter = _drafter(text="Would you like to keep Netflix? Yes or no?")
    retention_step(case, audit=audit, drafter=drafter)
    assert approve_pending_draft(case, audit, "retention_question")
    assert case.approved_messages["retention_question"] == (
        "Would you like to keep Netflix? Yes or no?"
    )


# ---------- Router passes the drafter to both workflows ----------


def test_router_passes_drafter_to_both_workflows(cases, audit):
    case = cases["C001"]
    notifier = MockNotifier()
    drafter = _drafter(text=CLEAN_DRAFT)

    outcomes = run_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, drafter=drafter,
    )

    assert outcomes["payment_recovery"].startswith("Created notification")
    assert "Netflix" in outcomes["retention"]
    # One payment-reminder draft and one retention-question draft.
    assert set(case.pending_drafts) == {
        "payment_reminder", "retention_question"
    }
    # The notification still uses the template.
    assert notifier.created[0].message == TEMPLATE


# ---------- Drafts persist across restarts ----------


def test_drafts_survive_persistence(tmp_path, cases):
    store = CaseStore(state_dir=str(tmp_path / "state"))
    case = cases["C001"]
    case.pending_drafts = {
        "payment_reminder": case.pending_drafts.get(
            "payment_reminder"
        )
    }
    # Build a realistic pending draft + approved message.
    from src.models import DraftRecord

    case.pending_drafts = {
        "payment_reminder": DraftRecord(
            draft_id="DRAFT-ABC12345",
            case_id="C001",
            kind="payment_reminder",
            text=CLEAN_DRAFT,
            status="PENDING_REVIEW",
            model="gemini-3.5-flash-lite",
            created_at="2026-01-05T10:00:00+00:00",
        )
    }
    case.approved_messages = {"retention_question": "Keep Netflix?"}

    store.save(case)
    loaded = store.load(case.case_id)

    draft = loaded.pending_drafts["payment_reminder"]
    assert draft.status == "PENDING_REVIEW"
    assert draft.text == CLEAN_DRAFT
    assert draft.model == "gemini-3.5-flash-lite"
    assert loaded.approved_messages == {"retention_question": "Keep Netflix?"}


def test_persisted_draft_still_cannot_be_used_automatically(
    tmp_path, cases, audit
):
    """A reloaded pending draft is still PENDING_REVIEW: template only."""
    from src.models import DraftRecord

    store = CaseStore(state_dir=str(tmp_path / "state"))
    case = cases["C001"]
    case.pending_drafts = {
        "payment_reminder": DraftRecord(
            draft_id="DRAFT-ABC12345",
            case_id="C001",
            kind="payment_reminder",
            text=UNAPPROVED_DRAFT,
            status="PENDING_REVIEW",
            model="gemini-3.5-flash-lite",
            created_at="2026-01-05T10:00:00+00:00",
        )
    }
    store.save(case)

    loaded = store.load(case.case_id)
    notifier = MockNotifier()
    payment_recovery_step(
        loaded, today=date(2026, 1, 5), audit=audit,
        notifier=notifier,
    )
    assert notifier.created[0].message == TEMPLATE


# ---------- Duplicate human actions (Phase 7 regression) ----------


def _approval_events(audit, case_id="CASE-C001"):
    return [
        entry
        for entry in audit.entries_for_case(case_id)
        if entry.event_type == "HUMAN_APPROVAL"
    ]


def test_duplicate_approval_is_refused_and_audited_once(cases, audit):
    """Approving twice changes nothing and logs exactly one HUMAN_APPROVAL."""
    case = cases["C001"]
    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=MockNotifier(), drafter=_drafter(),
    )
    assert approve_pending_draft(case, audit, "payment_reminder") is True

    # Duplicate submission: refused without side effects.
    assert approve_pending_draft(case, audit, "payment_reminder") is False

    assert case.approved_messages == {"payment_reminder": CLEAN_DRAFT}
    assert case.pending_drafts["payment_reminder"].status == "APPROVED"
    assert len(_approval_events(audit)) == 1  # the duplicate added nothing


def test_rejection_after_approval_is_refused(cases, audit):
    """A late rejection cannot undo or duplicate a recorded approval."""
    case = cases["C001"]
    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=MockNotifier(), drafter=_drafter(),
    )
    assert approve_pending_draft(case, audit, "payment_reminder") is True
    assert reject_pending_draft(case, audit, "payment_reminder") is False

    assert case.approved_messages == {"payment_reminder": CLEAN_DRAFT}
    assert case.pending_drafts["payment_reminder"].status == "APPROVED"
    assert len(_approval_events(audit)) == 1


def test_approval_after_rejection_is_refused(cases, audit):
    """Once rejected the draft is gone: no approval path exists."""
    case = cases["C001"]
    payment_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=MockNotifier(), drafter=_drafter(),
    )
    assert reject_pending_draft(case, audit, "payment_reminder") is True
    assert approve_pending_draft(case, audit, "payment_reminder") is False

    assert case.approved_messages == {}  # deterministic template stays in use
    assert "payment_reminder" not in case.pending_drafts
    assert len(_approval_events(audit)) == 1
