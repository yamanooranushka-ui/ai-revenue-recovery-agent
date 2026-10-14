"""Scheduling tests: payday-anchored dates, boundaries, never-early sending."""
from datetime import date

from src.models import PaymentRecoveryState
from src.notifications import MockNotifier
from src.workflows.payment_recovery import payment_recovery_step


def _case_with_failed_payment(payday, reason="insufficient_funds", customer_id="C900"):
    from src.models import Customer, Payment, RecoveryCase

    customer = Customer(customer_id=customer_id, name="Synthetic", expected_payday=payday)
    payment = Payment(f"P-{customer_id}", customer_id, "failed", reason, 50.0)
    return RecoveryCase(case_id=f"CASE-{customer_id}", customer=customer, payments=[payment])


def test_notifications_are_never_sent_early(audit):
    case = _case_with_failed_payment(payday=15)
    notifier = MockNotifier()
    outcome = payment_recovery_step(case, today=date(2026, 3, 10), audit=audit, notifier=notifier)
    assert notifier.count == 0
    assert case.payment_recovery_state == PaymentRecoveryState.SCHEDULED
    assert outcome.startswith("Waiting until")


def test_three_notifications_on_payday_plus_two_and_four(audit):
    case = _case_with_failed_payment(payday=15)
    notifier = MockNotifier()
    for today in (date(2026, 3, 15), date(2026, 3, 17), date(2026, 3, 19)):
        payment_recovery_step(case, today=today, audit=audit, notifier=notifier)
    assert notifier.count == 3
    assert [n.scheduled_date.day for n in notifier.created] == [15, 17, 19]
    assert case.payment_recovery_state == PaymentRecoveryState.CLOSED_MAX_NOTIFICATIONS_REACHED


def test_sending_twice_on_the_same_day_is_not_allowed(audit):
    case = _case_with_failed_payment(payday=15)
    notifier = MockNotifier()
    payment_recovery_step(case, today=date(2026, 3, 15), audit=audit, notifier=notifier)
    payment_recovery_step(case, today=date(2026, 3, 15), audit=audit, notifier=notifier)
    assert notifier.count == 1


def test_mid_month_insufficient_funds_still_gets_three_sms(audit):
    """Confirmed: the three-notification cap applies mid-month too."""
    case = _case_with_failed_payment(payday=20)
    notifier = MockNotifier()
    for today in (date(2026, 3, 20), date(2026, 3, 22), date(2026, 3, 24)):
        payment_recovery_step(case, today=today, audit=audit, notifier=notifier)
    assert notifier.count == 3


def test_month_boundary_pause_does_not_roll_over(audit):
    case = _case_with_failed_payment(payday=30)
    notifier = MockNotifier()
    payment_recovery_step(case, today=date(2026, 4, 28), audit=audit, notifier=notifier)
    payment_recovery_step(case, today=date(2026, 4, 30), audit=audit, notifier=notifier)
    assert notifier.count == 1
    assert case.payment_recovery_state == PaymentRecoveryState.PAUSED_MONTH_BOUNDARY
    # Running the step in May must NOT roll the schedule forward.
    payment_recovery_step(case, today=date(2026, 5, 2), audit=audit, notifier=notifier)
    payment_recovery_step(case, today=date(2026, 5, 30), audit=audit, notifier=notifier)
    assert notifier.count == 1
    assert case.payment_recovery_state == PaymentRecoveryState.PAUSED_MONTH_BOUNDARY
