"""Restart-persistence and resumption tests (Phase 3 preparation)."""
from datetime import date

from src.case_store import CaseStore
from src.models import (
    Customer,
    Payment,
    PaymentRecoveryState,
    RecoveryCase,
)
from src.notifications import MockNotifier
from src.rules import can_send_notification
from src.workflows.payment_recovery import payment_recovery_step
from src.workflows.router import run_recovery_step


def _case_with_failed_payment(payday, reason="insufficient_funds", customer_id="C900"):
    customer = Customer(customer_id=customer_id, name="Synthetic", expected_payday=payday)
    payment = Payment(f"P-{customer_id}", customer_id, "failed", reason, 50.0)
    return RecoveryCase(case_id=f"CASE-{customer_id}", customer=customer, payments=[payment])


# ---------- Restart persistence ----------

def test_restart_preserves_notification_count_and_schedule(tmp_path, audit):
    store = CaseStore(state_dir=str(tmp_path / "state"))
    case = _case_with_failed_payment(payday=5)

    run_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=MockNotifier(), state_store=store,
    )

    reloaded = store.load("CASE-C900")
    assert reloaded.notification_count == 1
    assert reloaded.scheduled_dates == [
        date(2026, 1, 5), date(2026, 1, 7), date(2026, 1, 9)
    ]
    assert reloaded.last_sent_date == date(2026, 1, 5)
    assert reloaded.payment_recovery_state == PaymentRecoveryState.WAITING_FOR_PAYMENT
    assert len(reloaded.notifications) == 1


def test_no_duplicate_notifications_after_restart(tmp_path, audit):
    store = CaseStore(state_dir=str(tmp_path / "state"))
    case = _case_with_failed_payment(payday=5)

    # First run: January 5 -> SMS #1.
    run_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=MockNotifier(), state_store=store,
    )
    # Restart: continue from the SAVED state, not a reset one.
    case = store.load("CASE-C900")
    run_recovery_step(
        case, today=date(2026, 1, 7), audit=audit,
        notifier=MockNotifier(), state_store=store,
    )
    case = store.load("CASE-C900")
    run_recovery_step(
        case, today=date(2026, 1, 9), audit=audit,
        notifier=MockNotifier(), state_store=store,
    )

    case = store.load("CASE-C900")
    assert case.notification_count == 3
    assert case.payment_recovery_state == (
        PaymentRecoveryState.CLOSED_MAX_NOTIFICATIONS_REACHED
    )

    # A further restart must not send anything more.
    notifier = MockNotifier()
    run_recovery_step(
        case, today=date(2026, 1, 11), audit=audit,
        notifier=notifier, state_store=store,
    )
    assert notifier.count == 0
    assert case.notification_count == 3


# ---------- Overdue reminders ----------

def test_overdue_reminder_is_parked_not_sent_late(tmp_path, audit):
    store = CaseStore(state_dir=str(tmp_path / "state"))
    case = _case_with_failed_payment(payday=5)
    notifier = MockNotifier()

    # January 5: the first reminder is sent on time.
    run_recovery_step(
        case, today=date(2026, 1, 5), audit=audit,
        notifier=notifier, state_store=store,
    )
    assert notifier.count == 1

    # January 20: the January 7 reminder is overdue. It must NOT
    # be sent late; the workflow parks as NOT_YET_SPECIFIED.
    outcomes = run_recovery_step(
        case, today=date(2026, 1, 20), audit=audit,
        notifier=notifier, state_store=store,
    )
    assert notifier.count == 1
    assert case.payment_recovery_state == PaymentRecoveryState.NOT_YET_SPECIFIED
    assert "NOT_YET_SPECIFIED" in outcomes["payment_recovery"]

    # The park reason is recorded in the audit log.
    park_entries = [
        e
        for e in audit.entries_for_case("CASE-C900")
        if "earlier than today" in e.action_or_decision
    ]
    assert len(park_entries) == 1


def test_overdue_case_does_not_catch_up_in_one_day(tmp_path, audit):
    """A fresh case whose schedule is already past parks immediately."""
    store = CaseStore(state_dir=str(tmp_path / "state"))
    case = _case_with_failed_payment(payday=5)
    notifier = MockNotifier()
    for _ in range(3):
        run_recovery_step(
            case, today=date(2026, 1, 20), audit=audit,
            notifier=notifier, state_store=store,
        )
    assert notifier.count == 0
    assert case.payment_recovery_state == PaymentRecoveryState.NOT_YET_SPECIFIED


# ---------- Actual send spacing ----------

def test_two_calendar_days_between_actual_sends(audit):
    case = _case_with_failed_payment(payday=5)
    notifier = MockNotifier()
    payment_recovery_step(case, today=date(2026, 1, 5), audit=audit, notifier=notifier)
    assert notifier.count == 1
    # January 6 is only one day after the last actual send.
    payment_recovery_step(case, today=date(2026, 1, 6), audit=audit, notifier=notifier)
    assert notifier.count == 1
    # January 7 is two days after -> allowed (and due today).
    payment_recovery_step(case, today=date(2026, 1, 7), audit=audit, notifier=notifier)
    assert notifier.count == 2


def test_spacing_guard_blocks_one_day_gap_when_due():
    customer = Customer(customer_id="C1", name="T", expected_payday=5)
    case = RecoveryCase(case_id="CASE-C1", customer=customer)
    case.max_notifications = 3
    case.scheduled_dates = [date(2026, 1, 5)]
    case.last_sent_date = date(2026, 1, 6)
    # The scheduled date has arrived, but only one day has passed
    # since the last ACTUAL send.
    assert can_send_notification(case, date(2026, 1, 7)) is False
    assert can_send_notification(case, date(2026, 1, 8)) is True


# ---------- Terminal-state audit behavior ----------

def test_stop_condition_is_logged_once_for_terminal_cases(audit):
    case = _case_with_failed_payment(payday=5)
    notifier = MockNotifier()
    for today in (date(2026, 1, 5), date(2026, 1, 7), date(2026, 1, 9)):
        payment_recovery_step(case, today=today, audit=audit, notifier=notifier)
    assert case.payment_recovery_state == (
        PaymentRecoveryState.CLOSED_MAX_NOTIFICATIONS_REACHED
    )

    # Repeated steps on a terminal case return the stop reason
    # without logging the stop condition again.
    for _ in range(3):
        payment_recovery_step(case, today=date(2026, 1, 11), audit=audit, notifier=notifier)
    stop_entries = [
        e
        for e in audit.entries_for_case("CASE-C900")
        if e.event_type == "STOP_CONDITION_MET"
    ]
    assert len(stop_entries) == 1
    assert notifier.count == 3


# ---------- Month boundaries (regression) ----------

def test_month_boundary_pause_survives_restart(tmp_path, audit):
    store = CaseStore(state_dir=str(tmp_path / "state"))
    case = _case_with_failed_payment(payday=30)
    notifier = MockNotifier()
    run_recovery_step(
        case, today=date(2026, 4, 30), audit=audit,
        notifier=notifier, state_store=store,
    )
    assert notifier.count == 1
    assert case.payment_recovery_state == PaymentRecoveryState.PAUSED_MONTH_BOUNDARY

    # Restart in May: the pause must survive and never auto-resume.
    case = store.load("CASE-C900")
    notifier2 = MockNotifier()
    run_recovery_step(
        case, today=date(2026, 5, 2), audit=audit,
        notifier=notifier2, state_store=store,
    )
    assert notifier2.count == 0
    assert case.payment_recovery_state == PaymentRecoveryState.PAUSED_MONTH_BOUNDARY
