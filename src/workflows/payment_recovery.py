"""
payment_recovery.py (Phase 2 + Phase 4)
Payment-recovery workflow for FAILED payments.

Fully independent of subscription retention (retention.py).

Confirmed rules implemented here:
- The agent NEVER retries, charges, debits, or processes a
  payment. The customer always completes payment manually.
- insufficient_funds: at most three SMS notifications,
  approximately two days apart, anchored on the customer's
  expected payday (payday, payday+2, payday+4).
- bank_error / payment_processor_error: exactly one SMS.
- Stop when: payment succeeds, customer opts out, the
  notification limit is reached, or the case is closed.
- Never send a notification before its scheduled date.
- Pause (never roll over) when the next scheduled date
  would cross into the next month.
- Unknown failure reasons and unresolved rules are parked
  as NOT_YET_SPECIFIED.

Phase 4: optional AI drafting. A draft is stored as
PENDING_REVIEW for human review and is NEVER used for
a notification. The notification text is the
human-approved message (if a human approved one) or
the deterministic template.
"""

import uuid
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Optional

from ..ai.facts import extract_approved_facts
from ..models import DraftRecord, Payment, PaymentRecoveryState, RecoveryCase
from ..notifications import MockNotifier
from ..rules import (
    can_send_notification,
    compute_schedule,
    evaluate_stop_conditions,
    max_notifications_for,
    next_scheduled_date,
    outstanding_failed_payment,
)

if TYPE_CHECKING:
    from ..ai.drafter import MessageDrafter


def _set_state(
    case: RecoveryCase,
    new_state: PaymentRecoveryState,
    audit,
    event_type: str,
    action: str,
    details: Optional[dict] = None,
) -> None:
    """Transition the payment-recovery state and record it in the audit log."""
    previous = case.payment_recovery_state
    case.payment_recovery_state = new_state
    audit.log(
        case_id=case.case_id,
        customer_id=case.customer.customer_id,
        event_type=event_type,
        action_or_decision=action,
        details={
            "previous_state": previous.value,
            "new_state": new_state.value,
            **(details or {}),
        },
    )


def _build_message(payment: Payment) -> str:
    """Draft the SMS text. Manual payment only: no links, no discounts, no auto-retry."""
    if payment.failure_reason == "insufficient_funds":
        return (
            f"Payment reminder: your payment of {payment.amount} failed because "
            f"of insufficient funds. Please complete the payment manually."
        )
    return (
        f"Payment notice: your payment of {payment.amount} could not be processed "
        f"because of a payment error ({payment.failure_reason}). "
        f"Please complete the payment manually."
    )


def _generate_draft(
    case: RecoveryCase,
    payment: Payment,
    scheduled_date: Optional[date],
    drafter: "MessageDrafter",
) -> dict:
    """
    Generate an AI draft and store it as PENDING_REVIEW.

    The draft is NEVER used by this workflow - it awaits
    human approval (src/ai/approval.py). Returns audit
    details only: no draft text and no customer data.
    """
    facts = extract_approved_facts(case, payment, scheduled_date)
    result = drafter.draft_payment_reminder(facts)
    details = {
        "drafting_status": result.status,
        "ai_model": drafter.model,
    }
    if result.text is not None:
        draft = DraftRecord(
            draft_id=f"DRAFT-{uuid.uuid4().hex[:8].upper()}",
            case_id=case.case_id,
            kind="payment_reminder",
            text=result.text,
            status="PENDING_REVIEW",
            model=drafter.model,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        case.pending_drafts = {
            **case.pending_drafts,
            "payment_reminder": draft,
        }
        details["draft_id"] = draft.draft_id
        details["draft_characters"] = len(draft.text)
    details["message_source"] = (
        "approved_message"
        if case.approved_messages.get("payment_reminder")
        else "approved_template"
    )
    return details


def payment_recovery_step(
    case: RecoveryCase,
    today: date,
    audit,
    notifier: MockNotifier,
    customer_opt_out: Optional[bool] = None,
    drafter: Optional["MessageDrafter"] = None,
) -> str:
    """
    Run one step of the payment-recovery workflow.

    `today` is the explicit execution date: the CSVs contain
    only a payday day-number, so the year and month must be
    supplied by the caller.

    `drafter` is optional. When given (and enabled), an AI
    draft is generated and stored as PENDING_REVIEW for
    human review. It is never used by this workflow: the
    notification text is the human-approved message (if a
    human approved one) or the deterministic template.
    """
    # 0. Record an explicit customer opt-out (sticky).
    if customer_opt_out:
        if not case.opted_out:
            case.opted_out = True
            audit.log(
                case_id=case.case_id,
                customer_id=case.customer.customer_id,
                event_type="CUSTOMER_INPUT",
                action_or_decision="Customer opted out of payment recovery.",
            )

    # 1. Stop conditions always win.
    stop_reason = evaluate_stop_conditions(case, audit)
    if stop_reason:
        return stop_reason

    # 2. Paused at a month boundary: no action until a rollover
    #    rule exists. (The pause itself was logged when it happened.)
    if case.payment_recovery_state == PaymentRecoveryState.PAUSED_MONTH_BOUNDARY:
        return "Paused at month boundary (rollover rule NOT_YET_SPECIFIED)."

    # 2b. Parked as NOT_YET_SPECIFIED: no action until the business
    #     defines the missing rule. (The parking itself was logged
    #     when it happened.)
    if case.payment_recovery_state == PaymentRecoveryState.NOT_YET_SPECIFIED:
        return "Parked as NOT_YET_SPECIFIED (rule undefined)."

    # 3. Find the failed payment this workflow recovers (the most recent one).
    payment = outstanding_failed_payment(case)
    if payment is None:
        return "No failed payment to recover."

    # 4. Confirmed notification limit for this failure reason.
    limit = max_notifications_for(payment.failure_reason)
    if limit is None:
        _set_state(
            case,
            PaymentRecoveryState.NOT_YET_SPECIFIED,
            audit,
            "DECISION",
            f"Unknown failure reason '{payment.failure_reason}'. No notification "
            f"rule exists, so the case is parked as NOT_YET_SPECIFIED.",
            details={"failure_reason": payment.failure_reason},
        )
        return "Unknown failure reason - parked as NOT_YET_SPECIFIED."
    case.max_notifications = limit

    # 5. Scheduling (insufficient funds only: payday, payday+2, payday+4).
    if payment.failure_reason == "insufficient_funds":
        if not case.scheduled_dates:
            schedule = compute_schedule(
                case.customer.expected_payday, today.year, today.month
            )
            case.scheduled_dates = schedule.scheduled_dates
            case.schedule_crosses_month_boundary = schedule.crosses_month_boundary
            if not schedule.scheduled_dates:
                # Even the payday itself falls outside this month
                # (e.g. day 31 in February).
                _set_state(
                    case,
                    PaymentRecoveryState.PAUSED_MONTH_BOUNDARY,
                    audit,
                    "DECISION",
                    "Payday scheduling crosses a month boundary immediately "
                    f"(payday day {case.customer.expected_payday} does not exist "
                    f"in {today.year}-{today.month:02d}). Paused; the rollover "
                    "rule is NOT_YET_SPECIFIED.",
                )
                return "Paused at month boundary (rollover rule NOT_YET_SPECIFIED)."

        next_date = next_scheduled_date(case)
        if next_date is not None:
            if today < next_date:
                _set_state(
                    case,
                    PaymentRecoveryState.SCHEDULED,
                    audit,
                    "DECISION",
                    f"Not eligible yet. The next notification is scheduled for "
                    f"{next_date} and must never be sent early.",
                    details={"next_scheduled_date": next_date},
                )
                return f"Waiting until {next_date}."
            if today > next_date:
                # Stale reminder policy: the scheduled date passed
                # without being processed. Never send it late.
                _set_state(
                    case,
                    PaymentRecoveryState.NOT_YET_SPECIFIED,
                    audit,
                    "DECISION",
                    f"The next notification was scheduled for {next_date}, "
                    f"which is earlier than today ({today}). Sending a "
                    f"reminder late is NOT_YET_SPECIFIED. The workflow is "
                    f"parked: no rescheduling, no rollover, and no "
                    f"notification-count reset is performed.",
                    details={
                        "scheduled_date": next_date,
                        "today": today,
                    },
                )
                return (
                    f"Scheduled date {next_date} passed unprocessed - "
                    f"parked as NOT_YET_SPECIFIED."
                )
            # today == next_date: the reminder is due today. It may
            # proceed only if every other safety rule is satisfied.

    # Technical errors have no reminder schedule: the single SMS is
    # created when this step runs.

    # 6. Safety-net eligibility check (ordinary code enforces the rules).
    if not can_send_notification(case, today):
        return "Not eligible for a notification right now."

    # 7. Create the notification (MOCK ONLY - nothing is actually sent).
    scheduled_date = (
        next_scheduled_date(case)
        if payment.failure_reason == "insufficient_funds"
        else today
    )

    # 7a. Optional AI drafting. The draft is stored as
    #     PENDING_REVIEW for a human and is NEVER used
    #     for this notification. The text used below is
    #     the human-approved message (if one exists) or
    #     the deterministic template.
    drafting_details = {}
    if drafter is not None:
        drafting_details = _generate_draft(
            case, payment, scheduled_date, drafter
        )
        audit.log(
            case_id=case.case_id,
            customer_id=case.customer.customer_id,
            event_type="DECISION",
            action_or_decision=(
                "AI drafting ran for human review. Status: "
                f"{drafting_details['drafting_status']}. The "
                "notification text is the human-approved "
                "message (if one exists) or the "
                "deterministic template - never an "
                "unapproved draft."
            ),
            details=drafting_details,
        )
    message = case.approved_messages.get("payment_reminder") or _build_message(payment)
    notification = notifier.create(
        case_id=case.case_id,
        customer_id=case.customer.customer_id,
        message=message,
        scheduled_date=scheduled_date,
    )
    case.notifications.append(notification)
    case.notification_count += 1
    case.last_sent_date = today

    _set_state(
        case,
        PaymentRecoveryState.WAITING_FOR_PAYMENT,
        audit,
        "ACTION",
        f"Created SMS notification #{case.notification_count} (mock - not "
        f"delivered). Reason: {payment.failure_reason}. The customer must "
        f"complete the payment manually.",
        details={
            "notification_id": notification.notification_id,
            "channel": notification.channel,
            "notification_count": case.notification_count,
            "max_notifications": case.max_notifications,
            "scheduled_date": scheduled_date,
            "auto_retry": "DISABLED (manual payment required)",
            "message_source": drafting_details.get(
                "message_source", "approved_template"
            ),
        },
    )

    # 8. Month-boundary pause: all in-month notifications were sent and
    #    the next date would cross into the next month.
    if (
        payment.failure_reason == "insufficient_funds"
        and case.schedule_crosses_month_boundary
        and case.notification_count >= len(case.scheduled_dates)
        and case.notification_count < case.max_notifications
    ):
        _set_state(
            case,
            PaymentRecoveryState.PAUSED_MONTH_BOUNDARY,
            audit,
            "DECISION",
            "All notifications that fit in this month were sent. The next "
            "scheduled date would cross into the next month, and automatic "
            "rollover is NOT_YET_SPECIFIED. Paused.",
        )
        return "Paused at month boundary (rollover rule NOT_YET_SPECIFIED)."

    # 9. Re-check stop conditions (the notification limit may now be reached).
    stop_reason = evaluate_stop_conditions(case, audit)
    if stop_reason:
        return stop_reason

    return f"Created notification #{case.notification_count}."
