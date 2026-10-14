"""
models.py
Typed data models for the AI Revenue Recovery Agent (Phase 1).
"""

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class Customer:
    """Represents a customer profile and cash-flow characteristics."""
    customer_id: str
    name: str
    expected_payday: int  # Day of the month (1-31)


@dataclass
class Payment:
    """Represents a payment transaction event."""
    payment_id: str
    customer_id: str
    status: str  # e.g., 'failed', 'successful'
    failure_reason: Optional[str]  # e.g., 'insufficient_funds', 'bank_error', None
    amount: float


@dataclass
class Subscription:
    """Represents a customer's subscription to a specific service."""
    subscription_id: str
    customer_id: str
    service: str
    amount: float
    status: str  # e.g., 'lapsed', 'active'


@dataclass
class UsageRecord:
    """Represents historical customer activity for a specific service in a given month."""
    customer_id: str
    service: str
    month: str
    usage: str  # e.g., 'high', 'low'


from enum import Enum


class CaseState(str, Enum):
    """Lifecycle states for a RecoveryCase."""
    NEW = "NEW"
    PENDING_CUSTOMER_INTENT = "PENDING_CUSTOMER_INTENT"
    READY_FOR_NOTIFICATION = "READY_FOR_NOTIFICATION"
    WAITING_FOR_PAYMENT = "WAITING_FOR_PAYMENT"
    RESOLVED_PAID = "RESOLVED_PAID"
    CLOSED_OPTED_OUT = "CLOSED_OPTED_OUT"
    CLOSED_MAX_ATTEMPTS_REACHED = "CLOSED_MAX_ATTEMPTS_REACHED"
    CLOSED_MANUAL = "CLOSED_MANUAL"
    NOT_YET_SPECIFIED = "NOT_YET_SPECIFIED"


@dataclass
class AuditEntry:
    """Represents an immutable record of a system decision or action."""
    audit_id: str
    timestamp: str
    case_id: str
    customer_id: str
    event_type: str  # 'DECISION', 'ACTION', 'STOP_CONDITION_MET', 'STATE_TRANSITION'
    action_or_decision: str
    details: dict


@dataclass
class RecoveryCase:
    """
    Unified representation of a recovery case connecting customer,
    payment, subscription, and historical usage.
    """
    case_id: str
    customer: Customer
    subscription: Optional[Subscription] = None
    payment: Optional[Payment] = None
    usage_history: List[UsageRecord] = field(default_factory=list)
    usage_evaluation: str = "NO_HISTORY"  # 'CONSISTENTLY_HIGH' | 'NOT_YET_SPECIFIED' | 'NO_HISTORY'

    # Deterministic state and tracking fields
    state: CaseState = CaseState.NEW
    notification_count: int = 0
    max_notifications: Optional[int] = None
    preferred_channel: Optional[str] = None  # e.g., 'SMS', 'STANDARD_NOTIFICATION'
    next_schedule_info: Optional[str] = None
    customer_wants_to_continue: Optional[bool] = None
    is_closed: bool = False

    def summary(self) -> str:
        """Helper to print a readable summary of the recovery case."""
        sub_desc = f"{self.subscription.service} (${self.subscription.amount}, status={self.subscription.status})" if self.subscription else "None"
        pmt_desc = f"{self.payment.payment_id} (${self.payment.amount}, status={self.payment.status}, reason={self.payment.failure_reason or 'N/A'})" if self.payment else "None"
        return (
            f"RecoveryCase[{self.case_id}] (State: {self.state.value}):\n"
            f"  Customer: {self.customer.name} (ID: {self.customer.customer_id}, Expected Payday: {self.customer.expected_payday})\n"
            f"  Subscription: {sub_desc}\n"
            f"  Payment: {pmt_desc}\n"
            f"  Usage Records: {len(self.usage_history)} months recorded\n"
            f"  4-Month Usage Evaluation: {self.usage_evaluation}\n"
            f"  Notifications: {self.notification_count}/{self.max_notifications if self.max_notifications is not None else 'N/A'}\n"
            f"  Channel: {self.preferred_channel or 'N/A'}\n"
            f"  Next Schedule: {self.next_schedule_info or 'N/A'}\n"
            f"  Is Closed: {self.is_closed}"
        )
