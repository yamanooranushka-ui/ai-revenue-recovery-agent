"""
facts.py (Phase 4)
The ONLY facts an AI draft may see.

The drafter receives a plain data object (ApprovedFacts).
It never receives the case, the notifier, the audit log,
or the state store, so it cannot change any workflow state.

Customer-provided fields are untrusted input: they are
passed as DATA in the prompt, never as instructions, and
every draft passes deterministic validation (policy.py)
before it is stored.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ApprovedFacts:
    """Immutable facts approved for AI message drafting.

    `kind` selects the drafting task:
    - "payment_reminder": a manual-payment reminder SMS
    - "retention_question": a yes/no question about a
      lapsed subscription (eligible cases only)
    """

    kind: str
    customer_name: str
    amount: str
    failure_reason: str
    scheduled_date: str
    notification_number: int
    max_notifications: int
    service: Optional[str] = None


def _format_amount(amount: float) -> str:
    """Format an amount as a plain number string (no currency is known)."""
    return f"{amount:g}"


def extract_approved_facts(case, payment, scheduled_date) -> ApprovedFacts:
    """Facts for a payment-reminder draft (from the failed payment)."""
    return ApprovedFacts(
        kind="payment_reminder",
        customer_name=case.customer.name,
        amount=_format_amount(payment.amount),
        failure_reason=payment.failure_reason or "",
        scheduled_date=(
            scheduled_date.isoformat() if scheduled_date else "not scheduled"
        ),
        notification_number=case.notification_count + 1,
        max_notifications=case.max_notifications or 0,
    )


def extract_retention_facts(case, subscription) -> ApprovedFacts:
    """Facts for a retention-question draft (eligible lapsed subscription only)."""
    return ApprovedFacts(
        kind="retention_question",
        customer_name=case.customer.name,
        amount=_format_amount(subscription.amount),
        failure_reason="",
        scheduled_date="not scheduled",
        notification_number=0,
        max_notifications=0,
        service=subscription.service,
    )
