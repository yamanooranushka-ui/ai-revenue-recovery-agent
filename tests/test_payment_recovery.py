"""Phase 2 scenario tests: payment recovery for customers C001-C005."""
from datetime import date

from src.models import (
    Customer,
    Payment,
    PaymentRecoveryState,
    RecoveryCase,
    RetentionState,
)
from src.notifications import MockNotifier
from src.rules import close_case, record_manual_payment_success
from src.workflows.payment_recovery import payment_recovery_step
from src.workflows.retention import retention_step
from src.workflows.router import run_recovery_step


def _synthetic_case(payday: int, customer_id: str = "C900") -> RecoveryCase:
    customer = Customer(customer_id=customer_id, name="Synthetic", expected_payday=payday)
    return RecoveryCase(case_id=f"CASE-{customer_id}", customer=customer)


def _with_failed_payment(case, payment_id, reason, amount=50.0):
    payment = Payment(payment_id, case.customer.customer_id, "failed", reason, amount)
    case.payments.append(payment)
    return case


# ---------- The five real customers ----------

def test_c001_schedules_then_sends_three_sms_two_days_apart(cases, audit):
    """Alex: payday 5, failed insufficient_funds -> SMS on days 5, 7, 9."""
    case = cases["C001"]
    notifier = MockNotifier()

    # Before payday: scheduled, but never early.
    payment_recovery_step(case, today=date(2026, 1, 2), audit=audit, notifier=notifier)
    assert case.payment_recovery_state == PaymentRecoveryState.SCHEDULED
    assert notifier.count == 0

    payment_recovery_step(case, today=date(2026, 1, 5), audit=audit, notifier=notifier)
    assert notifier.count == 1
    assert notifier.created[0].channel == "SMS"

    payment_recovery_step(case, today=date(2026, 1, 7), audit=audit, notifier=notifier)
    assert notifier.count == 2

    payment_recovery_step(case, today=date(2026, 1, 9), audit=audit, notifier=notifier)
    assert notifier.count == 3
    assert [n.scheduled_date.day for n in notifier.created] == [5, 7, 9]
    assert case.payment_recovery_state == PaymentRecoveryState.CLOSED_MAX_NOTIFICATIONS_REACHED
    assert case.payment_recovery_is_closed


def test_c002_bank_error_sends_exactly_one_sms(cases, audit):
    case = cases["C002"]  # Sarah: bank_error
    notifier = MockNotifier()
    payment_recovery_step(case, today=date(2026, 1, 15), audit=audit, notifier=notifier)
    assert notifier.count == 1
    assert notifier.created[0].channel == "SMS"
    assert case.payment_recovery_state == PaymentRecoveryState.CLOSED_MAX_NOTIFICATIONS_REACHED
    # A second step must not send anything more.
    payment_recovery_step(case, today=date(2026, 1, 20), audit=audit, notifier=notifier)
    assert notifier.count == 1


def test_c003_successful_payment_resolves_immediately(cases, audit):
    case = cases["C003"]  # John: payment already successful
    notifier = MockNotifier()
    payment_recovery_step(case, today=date(2026, 1, 5), audit=audit, notifier=notifier)
    assert case.payment_recovery_state == PaymentRecoveryState.RESOLVED_PAID
    assert case.payment_recovery_is_closed
    assert notifier.count == 0


def test_c004_low_usage_does_not_block_payment_recovery(cases, audit):
    """Clarification: retention and payment recovery are independent."""
    case = cases["C004"]  # Emma: lapsed Notion, low usage + insufficient_funds
    notifier = MockNotifier()

    # Retention parks as NOT_YET_SPECIFIED ...
    retention_step(case, audit=audit)
    assert case.retention_state == RetentionState.NOT_YET_SPECIFIED

    # ... but payment recovery still sends its three SMS.
    for day in (5, 7, 9):
        payment_recovery_step(case, today=date(2026, 1, day), audit=audit, notifier=notifier)
    assert notifier.count == 3
    assert case.payment_recovery_state == PaymentRecoveryState.CLOSED_MAX_NOTIFICATIONS_REACHED


def test_c005_payment_processor_error_sends_exactly_one_sms(cases, audit):
    case = cases["C005"]  # David: payment_processor_error
    notifier = MockNotifier()
    payment_recovery_step(case, today=date(2026, 1, 26), audit=audit, notifier=notifier)
    assert notifier.count == 1
    assert notifier.created[0].channel == "SMS"
    assert case.payment_recovery_state == PaymentRecoveryState.CLOSED_MAX_NOTIFICATIONS_REACHED


# ---------- Stop conditions ----------

def test_opt_out_stops_payment_recovery(cases, audit):
    case = cases["C001"]
    notifier = MockNotifier()
    payment_recovery_step(case, today=date(2026, 1, 5), audit=audit, notifier=notifier)
    assert notifier.count == 1
    payment_recovery_step(case, today=date(2026, 1, 7), audit=audit, notifier=notifier, customer_opt_out=True)
    assert case.payment_recovery_state == PaymentRecoveryState.CLOSED_OPTED_OUT
    assert case.opted_out
    assert notifier.count == 1  # no further notifications


def test_manual_payment_success_stops_recovery(cases, audit):
    case = cases["C001"]
    notifier = MockNotifier()
    payment_recovery_step(case, today=date(2026, 1, 5), audit=audit, notifier=notifier)
    assert notifier.count == 1
    # The customer completes the payment manually (external event recorded by the agent).
    record_manual_payment_success(case, payment_id="P001")
    payment_recovery_step(case, today=date(2026, 1, 7), audit=audit, notifier=notifier)
    assert case.payment_recovery_state == PaymentRecoveryState.RESOLVED_PAID
    assert notifier.count == 1


def test_manual_close_stops_recovery(cases, audit):
    case = cases["C001"]
    notifier = MockNotifier()
    close_case(case, "Customer asked us to stop")
    payment_recovery_step(case, today=date(2026, 1, 5), audit=audit, notifier=notifier)
    assert case.payment_recovery_state == PaymentRecoveryState.CLOSED_MANUAL
    assert notifier.count == 0


# ---------- Date boundaries ----------

def test_month_boundary_pauses_without_rollover(audit):
    """Payday 30 in a 30-day month: one SMS, then pause (no rollover)."""
    case = _synthetic_case(payday=30)
    _with_failed_payment(case, "P900", "insufficient_funds")
    notifier = MockNotifier()
    payment_recovery_step(case, today=date(2026, 4, 28), audit=audit, notifier=notifier)
    assert notifier.count == 0  # scheduled for April 30, not early
    payment_recovery_step(case, today=date(2026, 4, 30), audit=audit, notifier=notifier)
    assert notifier.count == 1
    assert case.payment_recovery_state == PaymentRecoveryState.PAUSED_MONTH_BOUNDARY
    # May must not roll the schedule forward.
    payment_recovery_step(case, today=date(2026, 5, 2), audit=audit, notifier=notifier)
    assert notifier.count == 1
    assert case.payment_recovery_state == PaymentRecoveryState.PAUSED_MONTH_BOUNDARY


def test_day_31_payday_in_february_pauses_immediately(audit):
    case = _synthetic_case(payday=31)
    _with_failed_payment(case, "P900", "insufficient_funds")
    notifier = MockNotifier()
    payment_recovery_step(case, today=date(2026, 2, 1), audit=audit, notifier=notifier)
    assert notifier.count == 0
    assert case.payment_recovery_state == PaymentRecoveryState.PAUSED_MONTH_BOUNDARY


# ---------- Parked rules ----------

def test_unknown_failure_reason_is_parked(audit):
    case = _synthetic_case(payday=5)
    _with_failed_payment(case, "P900", "card_declined")
    notifier = MockNotifier()
    payment_recovery_step(case, today=date(2026, 1, 5), audit=audit, notifier=notifier)
    assert case.payment_recovery_state == PaymentRecoveryState.NOT_YET_SPECIFIED
    assert notifier.count == 0


# ---------- Multiple payments ----------

def test_multiple_failed_payments_recovers_most_recent(audit):
    case = _synthetic_case(payday=5)
    _with_failed_payment(case, "P901", "insufficient_funds")
    _with_failed_payment(case, "P902", "bank_error")  # most recent
    notifier = MockNotifier()
    payment_recovery_step(case, today=date(2026, 1, 5), audit=audit, notifier=notifier)
    assert notifier.count == 1  # bank_error -> exactly one SMS
    assert case.payment_recovery_state == PaymentRecoveryState.CLOSED_MAX_NOTIFICATIONS_REACHED


# ---------- Independence via the router ----------

def test_router_runs_both_workflows_independently(cases, audit):
    case = cases["C001"]
    notifier = MockNotifier()
    # Step 1: retention asks, payment recovery sends its first SMS.
    outcomes = run_recovery_step(case, today=date(2026, 1, 5), audit=audit, notifier=notifier)
    assert case.retention_state == RetentionState.PENDING_CUSTOMER_INTENT
    assert case.payment_recovery_state == PaymentRecoveryState.WAITING_FOR_PAYMENT
    assert notifier.count == 1
    # Step 2: customer answers the retention question; payment recovery keeps waiting.
    outcomes = run_recovery_step(
        case, today=date(2026, 1, 5), audit=audit, notifier=notifier,
        customer_wants_to_continue=True,
    )
    assert case.retention_state == RetentionState.CONFIRMED
    assert case.payment_recovery_state == PaymentRecoveryState.SCHEDULED
    assert notifier.count == 1  # unchanged: the next SMS is on January 7
