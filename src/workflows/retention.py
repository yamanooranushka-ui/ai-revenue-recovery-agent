"""
retention.py (Phase 2 + Phase 4)
Subscription-retention workflow for LAPSED subscriptions only.

Fully independent of payment recovery (payment_recovery.py):
this workflow never sends payment notifications and never
changes payment state.

Confirmed rules implemented here:
- Lapsed subscription with consistently high usage over the
  most recent four months -> ask the customer whether they
  want to continue.
- Customer says YES -> retention confirmed; payment recovery
  continues under its own limits.
- Customer says NO -> stop and remove from the retention queue.
- Low, mixed, or missing usage history -> NOT_YET_SPECIFIED
  (no action is invented).

Phase 4: optional AI drafting of the retention question,
eligible cases only (consistently high usage). The draft
is stored as PENDING_REVIEW for human review; the
question asked remains the deterministic one until a
human approves a draft.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

from ..ai.facts import extract_retention_facts
from ..models import DraftRecord, RecoveryCase, RetentionState, TERMINAL_RETENTION_STATES
from ..usage_evaluation import evaluate_usage

if TYPE_CHECKING:
    from ..ai.drafter import MessageDrafter


def _set_state(
    case: RecoveryCase,
    new_state: RetentionState,
    audit,
    event_type: str,
    action: str,
    details: Optional[dict] = None,
) -> None:
    """Transition the retention state and record it in the audit log."""
    previous = case.retention_state
    case.retention_state = new_state
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


def _generate_retention_draft(
    case: RecoveryCase,
    subscription,
    drafter: Optional["MessageDrafter"],
) -> dict:
    """
    Draft an AI retention question (eligible cases only).

    The draft is stored as PENDING_REVIEW for human
    review. The question asked remains the
    deterministic one until a human approves a draft.
    Returns audit details only: no draft text and no
    customer data.
    """
    if drafter is None:
        return {}
    facts = extract_retention_facts(case, subscription)
    result = drafter.draft_retention_question(facts)
    details = {
        "ai_drafting_status": result.status,
        "ai_model": drafter.model,
    }
    if result.text is not None:
        draft = DraftRecord(
            draft_id=f"DRAFT-{uuid4().hex[:8].upper()}",
            case_id=case.case_id,
            kind="retention_question",
            text=result.text,
            status="PENDING_REVIEW",
            model=drafter.model,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        case.pending_drafts = {
            **case.pending_drafts,
            "retention_question": draft,
        }
        details["draft_id"] = draft.draft_id
        details["draft_characters"] = len(draft.text)
    return details


def retention_step(
    case: RecoveryCase,
    audit,
    customer_wants_to_continue: Optional[bool] = None,
    drafter: Optional["MessageDrafter"] = None,
) -> str:
    """
    Run one step of the subscription-retention workflow.

    `drafter` is optional. When given (and enabled) and
    the case is eligible (lapsed subscription with
    consistently high usage), an AI draft of the
    retention question is stored as PENDING_REVIEW for
    human review. The question asked remains the
    deterministic one until a human approves a draft.
    """
    lapsed = [s for s in case.subscriptions if s.status == "lapsed"]

    # No lapsed subscriptions -> retention does not apply.
    if not lapsed:
        if case.retention_state == RetentionState.NEW:
            _set_state(
                case,
                RetentionState.NOT_APPLICABLE,
                audit,
                "STATE_TRANSITION",
                "No lapsed subscriptions. Retention workflow not applicable.",
            )
        return "Retention not applicable (no lapsed subscriptions)."

    # Stop conditions for retention.
    if case.opted_out:
        _set_state(
            case,
            RetentionState.CLOSED_OPTED_OUT,
            audit,
            "STOP_CONDITION_MET",
            "Customer opted out. Retention stopped.",
        )
        return "Customer opted out. Retention stopped."

    if case.closed_manually:
        _set_state(
            case,
            RetentionState.CLOSED_MANUAL,
            audit,
            "STOP_CONDITION_MET",
            f"Case closed manually ({case.close_reason}). Retention stopped.",
        )
        return "Case closed manually. Retention stopped."

    # Already finished?
    if case.retention_state in TERMINAL_RETENTION_STATES:
        return f"Retention workflow already finished ({case.retention_state.value})."

    # Answer to a pending question.
    if case.retention_state == RetentionState.PENDING_CUSTOMER_INTENT:
        if customer_wants_to_continue is None:
            return "Waiting for the customer's answer."
        if customer_wants_to_continue:
            _set_state(
                case,
                RetentionState.CONFIRMED,
                audit,
                "CUSTOMER_INPUT",
                "Customer confirmed they want to continue the lapsed "
                "subscription. Retention confirmed. Payment recovery "
                "continues under its own limits.",
                details={"subscription_id": case.retention_subscription_id},
            )
            return "Customer confirmed continuation."
        _set_state(
            case,
            RetentionState.CLOSED_OPTED_OUT,
            audit,
            "CUSTOMER_INPUT",
            "Customer declined to continue. Removed from the retention queue.",
            details={"subscription_id": case.retention_subscription_id},
        )
        return "Customer declined. Removed from retention queue."

    # Evaluate usage for each lapsed subscription.
    evaluations = {
        subscription.subscription_id: evaluate_usage(
            case.usage_for_service(subscription.service)
        )
        for subscription in lapsed
    }
    for subscription in lapsed:
        if evaluations[subscription.subscription_id] == "CONSISTENTLY_HIGH":
            case.retention_state = RetentionState.PENDING_CUSTOMER_INTENT
            case.retention_subscription_id = subscription.subscription_id
            audit.log(
                case_id=case.case_id,
                customer_id=case.customer.customer_id,
                event_type="DECISION",
                action_or_decision=(
                    f"Lapsed subscription '{subscription.service}' shows "
                    f"consistently high usage over the most recent four "
                    f"months. Asking the customer whether they want to continue."
                ),
                details={
                    "previous_state": "NEW",
                    "new_state": case.retention_state.value,
                    "subscription_id": subscription.subscription_id,
                    "service": subscription.service,
                    "usage_evaluation": "CONSISTENTLY_HIGH",
                    **_generate_retention_draft(case, subscription, drafter),
                },
            )
            return (
                f"Asking customer whether they want to continue "
                f"{subscription.service}."
            )

    # No lapsed subscription has reliably high usage.
    _set_state(
        case,
        RetentionState.NOT_YET_SPECIFIED,
        audit,
        "DECISION",
        "Lapsed subscription(s) without reliably high four-month usage. "
        "Retention action is NOT_YET_SPECIFIED.",
        details={"evaluations": evaluations},
    )
    return "Lapsed subscription usage is not reliably high. NOT_YET_SPECIFIED."
