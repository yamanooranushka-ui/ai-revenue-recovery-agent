"""
models.py (Phase 1 + Phase 2)
Immutable records and workflow states for the AI Revenue Recovery Agent.

Phase 1: data foundation (CSV records).
Phase 2: deterministic workflow states. No AI, no payment processing.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, List, Optional


class CaseState(str, Enum):
    """Legacy top-level case state from Phase 1."""

    NEW = "NEW"


class PaymentRecoveryState(str, Enum):
    """Lifecycle states for the payment-recovery workflow."""

    NEW = "NEW"
    SCHEDULED = "SCHEDULED"  # Waiting for the next scheduled date
    WAITING_FOR_PAYMENT = "WAITING_FOR_PAYMENT"  # At least one SMS created; customer must pay manually
    PAUSED_MONTH_BOUNDARY = "PAUSED_MONTH_BOUNDARY"  # Next date would cross into the next month
    RESOLVED_PAID = "RESOLVED_PAID"  # Terminal: payment succeeded
    CLOSED_OPTED_OUT = "CLOSED_OPTED_OUT"  # Terminal: customer opted out
    CLOSED_MAX_NOTIFICATIONS_REACHED = "CLOSED_MAX_NOTIFICATIONS_REACHED"  # Terminal: limit reached
    CLOSED_MANUAL = "CLOSED_MANUAL"  # Terminal: closed manually
    NOT_YET_SPECIFIED = "NOT_YET_SPECIFIED"  # Parked: rule not defined


class RetentionState(str, Enum):
    """Lifecycle states for the subscription-retention workflow."""

    NEW = "NEW"
    NOT_APPLICABLE = "NOT_APPLICABLE"  # No lapsed subscriptions
    PENDING_CUSTOMER_INTENT = "PENDING_CUSTOMER_INTENT"  # Asked the customer; awaiting an answer
    CONFIRMED = "CONFIRMED"  # Terminal: customer wants to continue
    CLOSED_OPTED_OUT = "CLOSED_OPTED_OUT"  # Terminal: customer declined
    CLOSED_MANUAL = "CLOSED_MANUAL"  # Terminal: closed manually
    NOT_YET_SPECIFIED = "NOT_YET_SPECIFIED"  # Parked: usage not reliably high


# States from which no further workflow action is taken.
TERMINAL_PAYMENT_RECOVERY_STATES = frozenset(
    {
        PaymentRecoveryState.RESOLVED_PAID,
        PaymentRecoveryState.CLOSED_OPTED_OUT,
        PaymentRecoveryState.CLOSED_MAX_NOTIFICATIONS_REACHED,
        PaymentRecoveryState.CLOSED_MANUAL,
        PaymentRecoveryState.NOT_YET_SPECIFIED,
    }
)

TERMINAL_RETENTION_STATES = frozenset(
    {
        RetentionState.CONFIRMED,
        RetentionState.CLOSED_OPTED_OUT,
        RetentionState.CLOSED_MANUAL,
        RetentionState.NOT_YET_SPECIFIED,
    }
)


@dataclass(frozen=True)
class Customer:
    """One row of customers.csv."""

    customer_id: str
    name: str
    expected_payday: int  # Day of the month, 1-31


@dataclass(frozen=True)
class Payment:
    """One row of payments.csv."""

    payment_id: str
    customer_id: str
    status: str  # 'failed' or 'successful'
    failure_reason: Optional[str]  # Set only when status == 'failed'
    amount: float


@dataclass(frozen=True)
class Subscription:
    """One row of subscriptions.csv."""

    subscription_id: str
    customer_id: str
    service: str
    amount: float
    status: str  # 'active' or 'lapsed'


@dataclass(frozen=True)
class UsageRecord:
    """One row of usage_history.csv."""

    customer_id: str
    service: str
    month: str
    usage: str  # 'high' or 'low'


@dataclass(frozen=True)
class Notification:
    """
    One notification the agent wants to deliver.

    Phase 2 is MOCK ONLY: notifications are recorded, never actually
    sent. Real SMS delivery requires the CRM integration; the phone
    number source is NOT_YET_SPECIFIED.
    """

    notification_id: str
    case_id: str
    customer_id: str
    channel: str  # always "SMS" in Phase 2
    message: str
    scheduled_date: Optional[date]  # None = created when the step ran (no schedule)
    status: str = "CREATED"  # CREATED = mock only; real delivery is a later phase


@dataclass(frozen=True)
class AuditEntry:
    """One immutable audit record, written to the append-only audit log."""

    audit_id: str
    timestamp: str
    case_id: str
    customer_id: str
    event_type: str
    action_or_decision: str
    details: Dict = field(default_factory=dict)


@dataclass(frozen=True)
class DraftRecord:
    """
    An AI-generated message draft awaiting human review.

    Phase 4: AI drafting is suggestion-only. A draft is
    stored with status PENDING_REVIEW and is NEVER used
    for a notification until a human approves it through
    the explicit approval step (src/ai/approval.py).
    """

    draft_id: str
    case_id: str
    kind: str  # "payment_reminder" or "retention_question"
    text: str
    status: str  # "PENDING_REVIEW", "APPROVED", "REJECTED"
    model: str
    created_at: str  # ISO-8601 UTC timestamp


@dataclass
class RecoveryCase:
    """
    Everything we know about ONE customer, gathered from all four CSVs,
    plus the independent states of the two Phase 2 workflows.
    """

    case_id: str
    customer: Customer
    payments: List[Payment] = field(default_factory=list)
    subscriptions: List[Subscription] = field(default_factory=list)
    usage_records: List[UsageRecord] = field(default_factory=list)
    state: CaseState = CaseState.NEW

    # Phase 2: independent workflow states
    payment_recovery_state: PaymentRecoveryState = PaymentRecoveryState.NEW
    retention_state: RetentionState = RetentionState.NEW
    retention_subscription_id: Optional[str] = None

    # Phase 2: customer input and manual closure
    opted_out: bool = False
    closed_manually: bool = False
    close_reason: Optional[str] = None

    # Phase 2: notification tracking (mock only - nothing is ever sent)
    notification_count: int = 0
    max_notifications: Optional[int] = None
    scheduled_dates: List[date] = field(default_factory=list)
    schedule_crosses_month_boundary: bool = False
    notifications: List[Notification] = field(default_factory=list)

    # Phase 3 preparation: the last date a notification was actually
    # created. Enforces real-world spacing between actual sends.
    last_sent_date: Optional[date] = None

    # Phase 4: AI drafting (suggestion-only). `pending_drafts`
    # holds AI drafts awaiting human review, keyed by draft
    # kind; they are never used for notifications.
    # `approved_messages` holds wording a human has approved,
    # keyed by draft kind.
    pending_drafts: Dict[str, DraftRecord] = field(default_factory=dict)
    approved_messages: Dict[str, str] = field(default_factory=dict)

    @property
    def payment_recovery_is_closed(self) -> bool:
        """True when the payment-recovery workflow reached a terminal state."""
        return self.payment_recovery_state in TERMINAL_PAYMENT_RECOVERY_STATES

    def usage_for_service(self, service: str) -> List[UsageRecord]:
        """Return this customer's usage records for one service (case-insensitive)."""
        wanted = service.strip().lower()
        return [r for r in self.usage_records if r.service.strip().lower() == wanted]

    def summary(self) -> str:
        """Human-readable one-case summary (used by tests and debugging)."""
        limit = self.max_notifications if self.max_notifications is not None else "n/a"
        lines = [
            f"RecoveryCase[{self.case_id}] state={self.state.value}",
            f"  Customer : {self.customer.name} ({self.customer.customer_id}), "
            f"payday=day {self.customer.expected_payday}",
            f"  Payment recovery : {self.payment_recovery_state.value} "
            f"(notifications {self.notification_count}/{limit})",
            f"  Retention        : {self.retention_state.value}",
            f"  Payments : {len(self.payments)} record(s)",
        ]
        for p in self.payments:
            reason = p.failure_reason if p.failure_reason else "-"
            lines.append(f"      - {p.payment_id}: {p.status}, reason={reason}, amount={p.amount}")
        lines.append(f"  Subscriptions : {len(self.subscriptions)} record(s)")
        for s in self.subscriptions:
            lines.append(f"      - {s.subscription_id}: {s.service}, {s.status}, amount={s.amount}")
        lines.append(f"  Usage    : {len(self.usage_records)} record(s)")
        return "\n".join(lines)
