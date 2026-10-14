"""
state_machine.py
Deterministic State Machine and Business Rule Engine for the AI Revenue Recovery Agent.

Mandatory Rules:
1. NEVER automatically retry, charge, debit, or process a customer's payment.
2. The customer must manually complete the payment.
3. For insufficient_funds:
   - Notify customer.
   - Use customer's expected payday to determine reminder timing.
   - If failure occurs around beginning of month, max 3 notifications ~2 days apart.
   - Stop if payment succeeds.
4. For bank_error or other technical/processor errors:
   - Notify customer via SMS.
   - Do NOT invent a reminder or retry schedule unless defined by business rules (mark as NOT_YET_SPECIFIED).
5. Stop recovery when:
   - Payment succeeds.
   - Customer explicitly says they do not want to continue.
   - Maximum allowed notifications reached.
   - Recovery case otherwise explicitly closed.
6. Every decision and action is logged to the AuditLogger.
7. For lapsed subscriptions:
   - 4-month usage evaluated: consistently high -> ask if they want to continue.
   - If YES -> continue recovery with defined limits.
   - If NO -> stop recovery, remove from queue.
   - Do not invent discounts, pauses, or retention offers.
"""

from typing import Optional, Tuple
from models import RecoveryCase, CaseState
from audit import AuditLogger


def evaluate_stop_conditions(case: RecoveryCase, audit: AuditLogger) -> Tuple[bool, str]:
    """
    Deterministically evaluates whether the recovery case must stop.
    Returns (should_stop, reason).
    """
    # Stop Condition 1: Payment succeeded
    if case.payment and case.payment.status == "successful":
        prev_state = case.state
        case.state = CaseState.RESOLVED_PAID
        case.is_closed = True
        reason = "Payment is marked successful. Recovery stopped."
        audit.log(
            case_id=case.case_id,
            customer_id=case.customer.customer_id,
            event_type="STOP_CONDITION_MET",
            action_or_decision=reason,
            details={"previous_state": prev_state.value, "new_state": case.state.value, "payment_id": case.payment.payment_id},
        )
        return True, reason

    # Stop Condition 2: Customer opted out
    if case.customer_wants_to_continue is False:
        prev_state = case.state
        case.state = CaseState.CLOSED_OPTED_OUT
        case.is_closed = True
        reason = "Customer explicitly opted out. Recovery stopped and removed from queue."
        audit.log(
            case_id=case.case_id,
            customer_id=case.customer.customer_id,
            event_type="STOP_CONDITION_MET",
            action_or_decision=reason,
            details={"previous_state": prev_state.value, "new_state": case.state.value},
        )
        return True, reason

    # Stop Condition 3: Maximum allowed notifications reached
    if case.max_notifications is not None and case.notification_count >= case.max_notifications:
        prev_state = case.state
        case.state = CaseState.CLOSED_MAX_ATTEMPTS_REACHED
        case.is_closed = True
        reason = f"Maximum allowed notifications ({case.max_notifications}) reached. Recovery stopped."
        audit.log(
            case_id=case.case_id,
            customer_id=case.customer.customer_id,
            event_type="STOP_CONDITION_MET",
            action_or_decision=reason,
            details={
                "previous_state": prev_state.value,
                "new_state": case.state.value,
                "notification_count": case.notification_count,
                "max_notifications": case.max_notifications,
            },
        )
        return True, reason

    # Stop Condition 4: Case explicitly closed
    if case.is_closed:
        reason = "Case is marked as closed. Recovery stopped."
        audit.log(
            case_id=case.case_id,
            customer_id=case.customer.customer_id,
            event_type="STOP_CONDITION_MET",
            action_or_decision=reason,
            details={"state": case.state.value},
        )
        return True, reason

    return False, "Active"


def process_recovery_step(
    case: RecoveryCase,
    audit: AuditLogger,
    current_day_of_month: int = 1,
    customer_response_to_continue: Optional[bool] = None,
) -> None:
    """
    Executes one deterministic step of the recovery process for a given case.
    """
    # 1. First evaluate hard stop conditions
    should_stop, stop_reason = evaluate_stop_conditions(case, audit)
    if should_stop:
        return

    # 2. Handle lapsed subscription workflow (Rule 7)
    if case.subscription and case.subscription.status == "lapsed":
        if case.usage_evaluation == "CONSISTENTLY_HIGH":
            # If customer response is not yet recorded
            if case.customer_wants_to_continue is None:
                if customer_response_to_continue is None:
                    # Mark pending inquiry
                    case.state = CaseState.PENDING_CUSTOMER_INTENT
                    audit.log(
                        case_id=case.case_id,
                        customer_id=case.customer.customer_id,
                        event_type="DECISION",
                        action_or_decision="Lapsed subscription with consistently high usage. Prompting customer if they want to continue.",
                        details={"service": case.subscription.service, "usage_eval": case.usage_evaluation},
                    )
                    return
                elif customer_response_to_continue is False:
                    # Customer says NO
                    case.customer_wants_to_continue = False
                    evaluate_stop_conditions(case, audit)
                    return
                else:
                    # Customer says YES
                    case.customer_wants_to_continue = True
                    audit.log(
                        case_id=case.case_id,
                        customer_id=case.customer.customer_id,
                        event_type="CUSTOMER_INPUT",
                        action_or_decision="Customer confirmed they want to continue lapsed subscription. Proceeding with recovery.",
                        details={"service": case.subscription.service},
                    )
        elif case.usage_evaluation == "NOT_YET_SPECIFIED":
            case.state = CaseState.NOT_YET_SPECIFIED
            case.is_closed = True
            audit.log(
                case_id=case.case_id,
                customer_id=case.customer.customer_id,
                event_type="DECISION",
                action_or_decision="Lapsed subscription with low/mixed usage history. Business rules for low-usage are NOT_YET_SPECIFIED. No recovery action taken.",
                details={"service": case.subscription.service, "usage_eval": case.usage_evaluation},
            )
            return
        else:
            case.state = CaseState.NOT_YET_SPECIFIED
            case.is_closed = True
            audit.log(
                case_id=case.case_id,
                customer_id=case.customer.customer_id,
                event_type="DECISION",
                action_or_decision="Lapsed subscription has no usage history. Action is NOT_YET_SPECIFIED.",
                details={"service": case.subscription.service},
            )
            return

    # 3. Evaluate payment failure reason & communication rules
    if not case.payment or case.payment.status != "failed":
        evaluate_stop_conditions(case, audit)
        return

    reason = (case.payment.failure_reason or "").lower()

    if reason == "insufficient_funds":
        # Rule 3: Insufficient Funds
        case.preferred_channel = "STANDARD_NOTIFICATION"
        case.max_notifications = 3

        # Payday alignment & schedule calculation
        payday = case.customer.expected_payday
        is_beginning_of_month = current_day_of_month <= 10

        if is_beginning_of_month:
            reminders = [f"Day {payday} (Payday)", f"Day {payday + 2}", f"Day {payday + 4}"]
            case.next_schedule_info = f"Max 3 reminders, ~2 days apart, aligned with expected payday: {', '.join(reminders)}"
        else:
            case.next_schedule_info = f"Aligned with expected payday: Day {payday}"

        # Dispatch notification (customer must manually pay)
        case.notification_count += 1
        case.state = CaseState.WAITING_FOR_PAYMENT

        audit.log(
            case_id=case.case_id,
            customer_id=case.customer.customer_id,
            event_type="ACTION",
            action_or_decision=f"Sent notification #{case.notification_count} requesting manual payment (Reason: insufficient_funds).",
            details={
                "channel": case.preferred_channel,
                "notification_count": case.notification_count,
                "max_notifications": case.max_notifications,
                "expected_payday": payday,
                "schedule_info": case.next_schedule_info,
                "auto_retry": "DISABLED (manual payment required)",
            },
        )

        # Check if sending this notification reached the max cap
        evaluate_stop_conditions(case, audit)

    elif reason in ("bank_error", "payment_processor_error"):
        # Rule 4: Technical / Processor Errors
        case.preferred_channel = "SMS"
        # No retry or reminder schedule invented
        case.max_notifications = 1  # Single notification unless rule specified
        case.next_schedule_info = "NOT_YET_SPECIFIED"

        case.notification_count += 1
        case.state = CaseState.WAITING_FOR_PAYMENT

        audit.log(
            case_id=case.case_id,
            customer_id=case.customer.customer_id,
            event_type="ACTION",
            action_or_decision=f"Sent SMS notification #{case.notification_count} alerting customer to technical error ({reason}) and requesting manual payment.",
            details={
                "channel": "SMS",
                "notification_count": case.notification_count,
                "reminder_schedule": "NOT_YET_SPECIFIED",
                "auto_retry": "DISABLED (manual payment required)",
            },
        )
    else:
        # Any other failure reason is marked not yet specified
        case.state = CaseState.NOT_YET_SPECIFIED
        audit.log(
            case_id=case.case_id,
            customer_id=case.customer.customer_id,
            event_type="DECISION",
            action_or_decision=f"Unrecognized failure reason '{reason}'. Action is NOT_YET_SPECIFIED.",
            details={"failure_reason": reason},
        )
