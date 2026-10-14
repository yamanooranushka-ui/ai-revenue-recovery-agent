"""
audit.py
Structured audit logging framework for the AI Revenue Recovery Agent.
Records every decision, action, state change, and rule evaluation.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from models import AuditEntry


class AuditLogger:
    """Centralized, immutable audit logger."""

    def __init__(self):
        self._entries: List[AuditEntry] = []

    def log(
        self,
        case_id: str,
        customer_id: str,
        event_type: str,
        action_or_decision: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """
        Records an audit entry.
        event_type should be one of:
        - 'DECISION'
        - 'ACTION'
        - 'STATE_TRANSITION'
        - 'STOP_CONDITION_MET'
        - 'CUSTOMER_INPUT'
        """
        entry = AuditEntry(
            audit_id=f"AUD-{uuid.uuid4().hex[:8].upper()}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            case_id=case_id,
            customer_id=customer_id,
            event_type=event_type,
            action_or_decision=action_or_decision,
            details=details or {},
        )
        self._entries.append(entry)
        return entry

    def get_entries_for_case(self, case_id: str) -> List[AuditEntry]:
        """Returns all audit entries associated with a specific case ID."""
        return [e for e in self._entries if e.case_id == case_id]

    def all_entries(self) -> List[AuditEntry]:
        """Returns the full list of all recorded audit entries."""
        return list(self._entries)

    def print_case_audit_trail(self, case_id: str) -> None:
        """Prints a human-readable audit trail for a case."""
        case_entries = self.get_entries_for_case(case_id)
        print(f"\n--- Audit Trail for {case_id} ({len(case_entries)} events) ---")
        for e in case_entries:
            print(f"[{e.timestamp}] [{e.event_type}] {e.action_or_decision}")
            if e.details:
                for k, v in e.details.items():
                    print(f"      • {k}: {v}")
