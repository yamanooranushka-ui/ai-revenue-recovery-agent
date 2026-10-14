"""
audit.py (Phase 2)
Private, append-only audit log.

Every decision, action, state transition, customer input and stop
condition is written as one JSON line. The file is opened in
APPEND mode only - there is no edit or delete API.
"""

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import AuditEntry


class AuditLogger:
    """Append-only audit log backed by a JSON-lines file."""

    def __init__(self, log_path: str = "audit/audit_log.jsonl"):
        self._path = Path(log_path)

    def log(
        self,
        case_id: str,
        customer_id: str,
        event_type: str,
        action_or_decision: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """Create one immutable audit entry and append it to the log."""
        entry = AuditEntry(
            audit_id=f"AUD-{uuid.uuid4().hex[:8].upper()}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            case_id=case_id,
            customer_id=customer_id,
            event_type=event_type,
            action_or_decision=action_or_decision,
            details=details or {},
        )
        # Append-only: the file is never opened for writing or truncation.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, mode="a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), default=str) + "\n")
        return entry

    def all_entries(self) -> List[AuditEntry]:
        """Return every entry in the log."""
        return self._read_entries()

    def entries_for_case(self, case_id: str) -> List[AuditEntry]:
        """Return all entries for one case."""
        return [entry for entry in self._read_entries() if entry.case_id == case_id]

    def _read_entries(self) -> List[AuditEntry]:
        if not self._path.is_file():
            return []
        entries: List[AuditEntry] = []
        with open(self._path, mode="r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(AuditEntry(**json.loads(line)))
        return entries
