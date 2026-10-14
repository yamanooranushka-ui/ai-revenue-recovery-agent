"""
rules.py (Phase 2)
Deterministic business rules for payment recovery.

Pure logic only: no I/O, no AI, no network - and above all
NO payment processing. The agent never retries, charges, debits,
or processes a payment. The customer always pays manually.
"""

import calendar
from dataclasses import replace
from datetime import date
from typing import List, NamedTuple, Optional

from .business_rules import (
    INSUFFICIENT_FUNDS_MAX_NOTIFICATIONS,
    INSUFFICIENT_FUNDS_SPACING_DAYS,
    TECHNICAL_ERROR_MAX_NOTIFICATIONS,
)
from .models import (
    Payment,
    PaymentRecoveryState,
    RecoveryCase,
    TERMINAL_PAYMENT_RECOVERY_STATES,
)


class NotificationSchedule(NamedTuple):
    """The in-month notification dates, plus a month-boundary flag."""

    scheduled_dates: List[date]
    crosses_month_boundary: bool


def max_notifications_for(failure_reason: Optional[str]) -> Optional[int]:
    """
    Confirmed notification limits per failure reason.
    Returns None when the reason is unknown (NOT_YET_SPECIFIED).
    """
    if failure_reason == "insufficient_funds":
        return INSUFFICIENT_FUNDS_MAX_NOTIFICATIONS
    if failure_reason in ("bank_error", "payment_processor_error"):
        return TECHNICAL_ERROR_MAX_NOTIFICATIONS
    return None


def compute_schedule(payday: int, year: int, month: int) -> NotificationSchedule:
    """
    Insufficient-funds schedule: payday, payday+2, payday+4.

    Uses real calendar dates. Dates that would cross into the next
    month are NOT scheduled: the case pauses instead, because the
    rollover rule is NOT_YET_SPECIFIED (no automatic rollover).
    """
    _, days_in_month = calendar.monthrange(year, month)
    scheduled: List[date] = []
    crosses_boundary = False
    for offset in (0, INSUFFICIENT_FUNDS_SPACING_DAYS, 2 * INSUFFICIENT_FUNDS_SPACING_DAYS):
        day = payday + offset
        if day > days_in_month:
            crosses_boundary = True
            break
        scheduled.append(date(year, month, day))
    return NotificationSchedule(scheduled_dates=scheduled, crosses_month_boundary=crosses_boundary)


def outstanding_failed_payment(case: RecoveryCase) -> Optional[Payment]:
    """
    The failed payment this workflow recovers: the most recent
    payment, but only if it is still failed. A later successful
    payment means the customer already paid manually.
    """
    if not case.payments:
        return None
    latest = case.payments[-1]
    return latest if latest.status == "failed" else None


def next_scheduled_date(case: RecoveryCase) -> Optional[date]:
    """The next scheduled notification date, or None if the schedule is exhausted."""
    if case.notification_count < len(case.scheduled_dates):
        return case.scheduled_dates[case.notification_count]
    return None


def can_send_notification(case: RecoveryCase, today: date) -> bool:
    """
    Safety gate: a notification may be created only when ALL of
    these hold - not opted out, not closed, not terminal, not
    paused, a confirmed limit exists, the limit is not reached,
    and the scheduled date (if any) has arrived. Never send early.
    """
    if case.opted_out or case.closed_manually:
        return False
    if case.payment_recovery_state in TERMINAL_PAYMENT_RECOVERY_STATES:
        return False
    if case.payment_recovery_state == PaymentRecoveryState.PAUSED_MONTH_BOUNDARY:
        return False
    if case.max_notifications is None:
        return False  # No confirmed limit -> never notify
    if case.notification_count >= case.max_notifications:
        return False
    next_date = next_scheduled_date(case)
    if next_date is not None and today < next_date:
        return False
    # At least two calendar days between ACTUAL notification dates:
    # never send a second reminder sooner, even if the schedule or
    # an overdue case would allow it.
    if case.last_sent_date is not None:
        days_since_last_send = (today - case.last_sent_date).days
        if days_since_last_send < INSUFFICIENT_FUNDS_SPACING_DAYS:
            return False
    return True


def evaluate_stop_conditions(case: RecoveryCase, audit) -> Optional[str]:
    """
    Check the four confirmed stop conditions for payment recovery.
    Returns the stop reason, or None if the case may continue.

    Idempotent: a case already in a terminal state had its stop
    condition logged when it was closed, so the reason is
    returned WITHOUT logging the same event again.
    """
    # Already terminal: return the stored reason without re-logging.
    if case.payment_recovery_state in TERMINAL_PAYMENT_RECOVERY_STATES:
        if case.payment_recovery_state == PaymentRecoveryState.RESOLVED_PAID:
            return "Payment succeeded. Payment recovery stopped."
        if case.payment_recovery_state == PaymentRecoveryState.CLOSED_OPTED_OUT:
            return "Customer opted out. Payment recovery stopped."
        if case.payment_recovery_state == PaymentRecoveryState.CLOSED_MAX_NOTIFICATIONS_REACHED:
            return (
                f"Notification limit ({case.max_notifications}) reached. "
                f"Payment recovery stopped."
            )
        if case.payment_recovery_state == PaymentRecoveryState.CLOSED_MANUAL:
            return (
                f"Case closed manually ({case.close_reason}). "
                f"Payment recovery stopped."
            )
        # NOT_YET_SPECIFIED is a parked state, not one of the four
        # stop conditions: report "no stop condition met".
        return None

    # 1. Payment succeeded (the customer paid manually)
    if case.payments and case.payments[-1].status == "successful":
        case.payment_recovery_state = PaymentRecoveryState.RESOLVED_PAID
        audit.log(
            case_id=case.case_id,
            customer_id=case.customer.customer_id,
            event_type="STOP_CONDITION_MET",
            action_or_decision="Payment succeeded. Payment recovery stopped.",
            details={
                "previous_state": "n/a",
                "new_state": case.payment_recovery_state.value,
                "payment_id": case.payments[-1].payment_id,
            },
        )
        return "Payment succeeded. Payment recovery stopped."

    # 2. Customer opted out
    if case.opted_out:
        case.payment_recovery_state = PaymentRecoveryState.CLOSED_OPTED_OUT
        audit.log(
            case_id=case.case_id,
            customer_id=case.customer.customer_id,
            event_type="STOP_CONDITION_MET",
            action_or_decision="Customer opted out. Payment recovery stopped.",
            details={"new_state": case.payment_recovery_state.value},
        )
        return "Customer opted out. Payment recovery stopped."

    # 3. Notification limit reached
    if case.max_notifications is not None and case.notification_count >= case.max_notifications:
        case.payment_recovery_state = PaymentRecoveryState.CLOSED_MAX_NOTIFICATIONS_REACHED
        audit.log(
            case_id=case.case_id,
            customer_id=case.customer.customer_id,
            event_type="STOP_CONDITION_MET",
            action_or_decision=(
                f"Notification limit ({case.max_notifications}) reached. "
                f"Payment recovery stopped."
            ),
            details={
                "new_state": case.payment_recovery_state.value,
                "notification_count": case.notification_count,
                "max_notifications": case.max_notifications,
            },
        )
        return f"Notification limit ({case.max_notifications}) reached. Payment recovery stopped."

    # 4. Case closed manually
    if case.closed_manually:
        case.payment_recovery_state = PaymentRecoveryState.CLOSED_MANUAL
        audit.log(
            case_id=case.case_id,
            customer_id=case.customer.customer_id,
            event_type="STOP_CONDITION_MET",
            action_or_decision=f"Case closed manually ({case.close_reason}). "
            f"Payment recovery stopped.",
            details={"new_state": case.payment_recovery_state.value},
        )
        return f"Case closed manually ({case.close_reason}). Payment recovery stopped."

    return None


def record_manual_payment_success(case: RecoveryCase, payment_id: str) -> bool:
    """
    Record that the CUSTOMER manually completed a payment.

    This is an external event the agent merely records - the agent
    itself never processes, retries, charges, or debits anything.
    Returns True if the payment was found and updated.
    """
    for index, payment in enumerate(case.payments):
        if payment.payment_id == payment_id:
            case.payments[index] = replace(payment, status="successful", failure_reason=None)
            return True
    return False


def close_case(case: RecoveryCase, reason: str) -> None:
    """Record an external manual closure of the case."""
    case.closed_manually = True
    case.close_reason = reason
