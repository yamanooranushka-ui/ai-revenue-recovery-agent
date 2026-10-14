"""Tests for deterministic business rules (Phase 2)."""
from datetime import date

from src.models import (
    Customer,
    Payment,
    PaymentRecoveryState,
    RecoveryCase,
)
from src.rules import (
    can_send_notification,
    close_case,
    compute_schedule,
    max_notifications_for,
    next_scheduled_date,
    outstanding_failed_payment,
    record_manual_payment_success,
)


def _case(payday: int = 5, payments=None) -> RecoveryCase:
    customer = Customer(customer_id="C1", name="Test", expected_payday=payday)
    return RecoveryCase(case_id="CASE-C1", customer=customer, payments=payments or [])


# ---------- Notification limits ----------

def test_max_notifications_for_confirmed_limits():
    assert max_notifications_for("insufficient_funds") == 3
    assert max_notifications_for("bank_error") == 1
    assert max_notifications_for("payment_processor_error") == 1
    assert max_notifications_for("card_declined") is None  # NOT_YET_SPECIFIED
    assert max_notifications_for(None) is None


# ---------- Payday scheduling ----------

def test_schedule_three_dates_two_days_apart():
    schedule = compute_schedule(payday=5, year=2026, month=1)  # 31 days
    assert [d.day for d in schedule.scheduled_dates] == [5, 7, 9]
    assert schedule.scheduled_dates[0] == date(2026, 1, 5)
    assert schedule.crosses_month_boundary is False


def test_schedule_pauses_at_30_day_month_boundary():
    schedule = compute_schedule(payday=30, year=2026, month=4)  # April: 30 days
    assert [d.day for d in schedule.scheduled_dates] == [30]
    assert schedule.crosses_month_boundary is True


def test_schedule_pauses_when_payday_is_day_31_in_february():
    schedule = compute_schedule(payday=31, year=2026, month=2)  # 28 days
    assert schedule.scheduled_dates == []
    assert schedule.crosses_month_boundary is True


def test_schedule_fits_exactly_at_end_of_february():
    schedule = compute_schedule(payday=24, year=2026, month=2)  # 24, 26, 28
    assert [d.day for d in schedule.scheduled_dates] == [24, 26, 28]
    assert schedule.crosses_month_boundary is False


# ---------- Outstanding payment selection ----------

def test_outstanding_failed_payment_picks_most_recent_failed():
    failed1 = Payment("P1", "C1", "failed", "insufficient_funds", 10.0)
    failed2 = Payment("P2", "C1", "failed", "bank_error", 20.0)
    case = _case(payments=[failed1, failed2])
    assert outstanding_failed_payment(case).payment_id == "P2"


def test_outstanding_failed_payment_none_when_latest_succeeded():
    failed = Payment("P1", "C1", "failed", "insufficient_funds", 10.0)
    successful = Payment("P2", "C1", "successful", None, 10.0)
    case = _case(payments=[failed, successful])
    assert outstanding_failed_payment(case) is None


# ---------- Scheduled dates ----------

def test_next_scheduled_date_walks_the_schedule():
    case = _case()
    case.scheduled_dates = [date(2026, 1, 5), date(2026, 1, 7), date(2026, 1, 9)]
    assert next_scheduled_date(case) == date(2026, 1, 5)
    case.notification_count = 1
    assert next_scheduled_date(case) == date(2026, 1, 7)
    case.notification_count = 3
    assert next_scheduled_date(case) is None


# ---------- Eligibility safety gate ----------

def test_can_send_notification_blocked_by_opt_out():
    case = _case()
    case.opted_out = True
    assert can_send_notification(case, date(2026, 1, 5)) is False


def test_can_send_notification_blocked_when_manually_closed():
    case = _case()
    close_case(case, "Customer asked us to stop")
    assert can_send_notification(case, date(2026, 1, 5)) is False


def test_can_send_notification_blocked_when_limit_reached():
    case = _case()
    case.max_notifications = 3
    case.notification_count = 3
    assert can_send_notification(case, date(2026, 1, 5)) is False


def test_can_send_notification_blocked_when_paused():
    case = _case()
    case.payment_recovery_state = PaymentRecoveryState.PAUSED_MONTH_BOUNDARY
    assert can_send_notification(case, date(2026, 1, 5)) is False


def test_can_send_notification_blocked_when_terminal():
    case = _case()
    case.payment_recovery_state = PaymentRecoveryState.RESOLVED_PAID
    assert can_send_notification(case, date(2026, 1, 5)) is False


def test_can_send_notification_blocked_without_confirmed_limit():
    case = _case()
    assert can_send_notification(case, date(2026, 1, 5)) is False


def test_can_send_notification_never_sends_early():
    case = _case()
    case.max_notifications = 3
    case.scheduled_dates = [date(2026, 1, 5)]
    assert can_send_notification(case, date(2026, 1, 4)) is False
    assert can_send_notification(case, date(2026, 1, 5)) is True


# ---------- Manual events ----------

def test_close_case_records_reason():
    case = _case()
    close_case(case, "Customer asked us to stop")
    assert case.closed_manually is True
    assert case.close_reason == "Customer asked us to stop"


def test_record_manual_payment_success_updates_payment():
    failed = Payment("P1", "C1", "failed", "insufficient_funds", 10.0)
    case = _case(payments=[failed])
    assert record_manual_payment_success(case, "P1") is True
    assert case.payments[0].status == "successful"
    assert case.payments[0].failure_reason is None
    assert record_manual_payment_success(case, "UNKNOWN") is False
