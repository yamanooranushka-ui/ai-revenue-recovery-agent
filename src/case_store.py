"""
case_store.py (Phase 3 preparation)
Persistent case store (JSON).

Saves and reloads RecoveryCase workflow state so that
notification counts, schedules, opt-outs and notification
history survive closing and reopening the program.

Safety rules:
- One JSON file per case, written atomically (temporary
  file + rename), so an interrupted write cannot corrupt
  the previous state.
- A state file that is missing, corrupted, or invalid
  raises CaseStoreError. A notification count is NEVER
  silently reset because a file could not be read.
- Only workflow state is persisted. The original CSVs are
  never touched, and the audit log stays append-only.
"""

import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from .models import (
    CaseState,
    Customer,
    DraftRecord,
    Notification,
    Payment,
    PaymentRecoveryState,
    RecoveryCase,
    RetentionState,
    Subscription,
    UsageRecord,
)

SCHEMA_VERSION = 1


class CaseStoreError(Exception):
    """Raised when a state file is missing, corrupted, or invalid."""


def _draft_to_dict(draft: Optional[DraftRecord]) -> Optional[dict]:
    """Serialize one draft record (or None)."""
    if draft is None:
        return None
    return {
        "draft_id": draft.draft_id,
        "case_id": draft.case_id,
        "kind": draft.kind,
        "text": draft.text,
        "status": draft.status,
        "model": draft.model,
        "created_at": draft.created_at,
    }


def _draft_from_dict(data: dict) -> DraftRecord:
    """Rebuild one draft record. Raises CaseStoreError when invalid."""
    return DraftRecord(
        draft_id=data["draft_id"],
        case_id=data["case_id"],
        kind=data["kind"],
        text=data["text"],
        status=data["status"],
        model=data["model"],
        created_at=data["created_at"],
    )


def case_to_dict(case: RecoveryCase) -> dict:
    """Serialize one case to a JSON-compatible dict."""
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case.case_id,
        "customer": {
            "customer_id": case.customer.customer_id,
            "name": case.customer.name,
            "expected_payday": case.customer.expected_payday,
        },
        "payments": [
            {
                "payment_id": p.payment_id,
                "customer_id": p.customer_id,
                "status": p.status,
                "failure_reason": p.failure_reason,
                "amount": p.amount,
            }
            for p in case.payments
        ],
        "subscriptions": [
            {
                "subscription_id": s.subscription_id,
                "customer_id": s.customer_id,
                "service": s.service,
                "amount": s.amount,
                "status": s.status,
            }
            for s in case.subscriptions
        ],
        "usage_records": [
            {
                "customer_id": u.customer_id,
                "service": u.service,
                "month": u.month,
                "usage": u.usage,
            }
            for u in case.usage_records
        ],
        "state": case.state.value,
        "payment_recovery_state": case.payment_recovery_state.value,
        "retention_state": case.retention_state.value,
        "retention_subscription_id": case.retention_subscription_id,
        "opted_out": case.opted_out,
        "closed_manually": case.closed_manually,
        "close_reason": case.close_reason,
        "notification_count": case.notification_count,
        "max_notifications": case.max_notifications,
        "scheduled_dates": [d.isoformat() for d in case.scheduled_dates],
        "schedule_crosses_month_boundary": case.schedule_crosses_month_boundary,
        "notifications": [
            {
                "notification_id": n.notification_id,
                "case_id": n.case_id,
                "customer_id": n.customer_id,
                "channel": n.channel,
                "message": n.message,
                "scheduled_date": (
                    n.scheduled_date.isoformat() if n.scheduled_date else None
                ),
                "status": n.status,
            }
            for n in case.notifications
        ],
        "last_sent_date": case.last_sent_date.isoformat() if case.last_sent_date else None,
        "pending_drafts": {
            kind: _draft_to_dict(draft)
            for kind, draft in case.pending_drafts.items()
        },
        "approved_messages": dict(case.approved_messages),
    }


def case_from_dict(data: dict, expected_case_id: str) -> RecoveryCase:
    """
    Rebuild one case from a dict. Raises CaseStoreError if the
    data is missing fields, has the wrong schema version, or
    references a different case.
    """
    try:
        if data.get("schema_version") != SCHEMA_VERSION:
            raise CaseStoreError(
                f"Unsupported state schema version: {data.get('schema_version')}"
            )
        if data.get("case_id") != expected_case_id:
            raise CaseStoreError(
                f"State file for '{expected_case_id}' contains case "
                f"'{data.get('case_id')}'."
            )

        customer_data = data["customer"]
        customer = Customer(
            customer_id=customer_data["customer_id"],
            name=customer_data["name"],
            expected_payday=int(customer_data["expected_payday"]),
        )
        payments = [
            Payment(
                payment_id=p["payment_id"],
                customer_id=p["customer_id"],
                status=p["status"],
                failure_reason=p.get("failure_reason"),
                amount=float(p["amount"]),
            )
            for p in data["payments"]
        ]
        subscriptions = [
            Subscription(
                subscription_id=s["subscription_id"],
                customer_id=s["customer_id"],
                service=s["service"],
                amount=float(s["amount"]),
                status=s["status"],
            )
            for s in data["subscriptions"]
        ]
        usage_records = [
            UsageRecord(
                customer_id=u["customer_id"],
                service=u["service"],
                month=u["month"],
                usage=u["usage"],
            )
            for u in data["usage_records"]
        ]
        notifications = [
            Notification(
                notification_id=n["notification_id"],
                case_id=n["case_id"],
                customer_id=n["customer_id"],
                channel=n["channel"],
                message=n["message"],
                scheduled_date=(
                    date.fromisoformat(n["scheduled_date"])
                    if n.get("scheduled_date")
                    else None
                ),
                status=n.get("status", "CREATED"),
            )
            for n in data["notifications"]
        ]

        return RecoveryCase(
            case_id=data["case_id"],
            customer=customer,
            payments=payments,
            subscriptions=subscriptions,
            usage_records=usage_records,
            state=CaseState(data["state"]),
            payment_recovery_state=PaymentRecoveryState(data["payment_recovery_state"]),
            retention_state=RetentionState(data["retention_state"]),
            retention_subscription_id=data.get("retention_subscription_id"),
            opted_out=bool(data["opted_out"]),
            closed_manually=bool(data["closed_manually"]),
            close_reason=data.get("close_reason"),
            notification_count=int(data["notification_count"]),
            max_notifications=data.get("max_notifications"),
            scheduled_dates=[
                date.fromisoformat(d) for d in data["scheduled_dates"]
            ],
            schedule_crosses_month_boundary=bool(
                data["schedule_crosses_month_boundary"]
            ),
            notifications=notifications,
            last_sent_date=(
                date.fromisoformat(data["last_sent_date"])
                if data.get("last_sent_date")
                else None
            ),
            pending_drafts={
                kind: _draft_from_dict(draft_data)
                for kind, draft_data in data.get(
                    "pending_drafts", {}
                ).items()
            },
            approved_messages=dict(data.get("approved_messages", {})),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CaseStoreError(
            f"Saved state for case '{expected_case_id}' is invalid: {error}"
        )


class CaseStore:
    """Persistent, per-case JSON state store."""

    def __init__(self, state_dir: str = "state"):
        self._dir = Path(state_dir)

    def _path_for(self, case_id: str) -> Path:
        return self._dir / f"{case_id}.json"

    def exists(self, case_id: str) -> bool:
        """True when a saved state exists for the case."""
        return self._path_for(case_id).is_file()

    def save(self, case: RecoveryCase) -> None:
        """Atomically save one case (overwrites previous state)."""
        self._dir.mkdir(parents=True, exist_ok=True)
        final_path = self._path_for(case.case_id)
        temp_path = self._dir / f"{case.case_id}.json.tmp"
        temp_path.write_text(
            json.dumps(case_to_dict(case), indent=2), encoding="utf-8"
        )
        # Atomic replace: readers see either the old or the new
        # file, never a half-written one.
        temp_path.replace(final_path)

    def load(self, case_id: str) -> RecoveryCase:
        """
        Load one case. Raises CaseStoreError when the state is
        missing, corrupted, or invalid - never a silent reset.
        """
        path = self._path_for(case_id)
        if not path.is_file():
            raise CaseStoreError(f"No saved state for case '{case_id}'.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise CaseStoreError(
                f"Saved state for case '{case_id}' is not valid JSON: {error}"
            )
        return case_from_dict(data, expected_case_id=case_id)

    def load_all(self) -> Dict[str, RecoveryCase]:
        """Load every saved case in the state directory."""
        cases: Dict[str, RecoveryCase] = {}
        if not self._dir.is_dir():
            return cases
        for path in sorted(self._dir.glob("*.json")):
            cases[path.stem] = self.load(path.stem)
        return cases
