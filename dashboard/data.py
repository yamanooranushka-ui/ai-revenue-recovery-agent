"""Read-only data access for the demo dashboard.

Every function in this module only READS files and returns plain
Python values. Nothing here writes to the case store or the audit
log: the only writes happen in dashboard/actions.py, behind an
explicit two-step human confirmation.

Failure policy (never silent, never fabricated):
- Corrupt or invalid saved state  -> DashboardDataError with a
  clear message. State is never reset or invented.
- Missing saved state             -> cases are built fresh from
  the CSVs (nothing written) and a visible notice says so.
- Missing audit log               -> empty list + a visible notice.
- Corrupt audit log               -> empty list + a visible error;
  no partial or invented history is shown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from src.audit import AuditLogger
from src.case_store import CaseStore, CaseStoreError
from src.models import RecoveryCase
from src.rules import next_scheduled_date
from src.validation import load_dataset
from src.workflows.router import load_or_build_cases


class DashboardDataError(Exception):
    """A clear, non-silent failure while reading saved data."""


@dataclass
class DashboardData:
    """Everything the dashboard needs for one render pass."""

    cases: Dict[str, RecoveryCase]
    state_notice: str = ""
    audit_entries: List[object] = field(default_factory=list)
    audit_notice: str = ""
    audit_error: str = ""


def load_cases(csv_dir: Path, state_dir: Path) -> Tuple[Dict[str, RecoveryCase], str]:
    """Load all cases: fresh from the CSVs, overlaid with saved state.

    Returns (cases_by_id, notice). Raises DashboardDataError when the
    CSVs or the saved state cannot be read - saved state is never
    silently reset or fabricated.
    """
    try:
        dataset = load_dataset(
            customers_path=str(Path(csv_dir) / "customers.csv"),
            payments_path=str(Path(csv_dir) / "payments.csv"),
            subscriptions_path=str(Path(csv_dir) / "subscriptions.csv"),
            usage_path=str(Path(csv_dir) / "usage_history.csv"),
        )
    except Exception as error:
        raise DashboardDataError(
            f"The source CSVs could not be loaded: {error}"
        ) from error
    if dataset.report.errors:
        raise DashboardDataError(
            "The source CSVs failed validation, so no data is shown:\n"
            + dataset.report.summary()
        )

    store = CaseStore(state_dir=str(state_dir))
    try:
        loaded = load_or_build_cases(dataset, store)
    except CaseStoreError as error:
        raise DashboardDataError(
            f"Saved state could not be read: {error} "
            "Refusing to reset or fabricate state. Fix or remove the "
            "affected state file manually, then reload."
        ) from error

    cases = {case.case_id: case for case in loaded.values()}
    saved = [case_id for case_id in cases if store.exists(case_id)]
    if not saved:
        notice = (
            f"No saved state found in {state_dir}. Showing cases built "
            "fresh from the CSVs (nothing was written). Run "
            "demo/seed_dashboard_state.py to create demo state with "
            "pending drafts."
        )
    elif len(saved) < len(cases):
        notice = (
            f"Saved state loaded for {len(saved)} of {len(cases)} cases; "
            "the rest are shown fresh from the CSVs."
        )
    else:
        notice = ""
    return cases, notice


def load_audit(audit_path: Path) -> Tuple[List[object], str, str]:
    """Read the append-only audit log.

    Returns (entries, notice, error):
    - missing file  -> ([], clear notice, "")
    - corrupt file  -> ([], "", clear error) - no partial data shown
    """
    path = Path(audit_path)
    if not path.is_file():
        return [], f"No audit log found at {path} - nothing to display yet.", ""
    try:
        entries = AuditLogger(log_path=str(path)).all_entries()
    except Exception as error:
        return [], "", (
            f"The audit log at {path} could not be read ({type(error).__name__}: "
            f"{error}). No audit history is shown - nothing is reset or "
            "fabricated. Fix or remove the file manually, then reload."
        )
    return entries, "", ""


# --- Row builders (pure; used by the Streamlit views) -----------------


def overview_rows(cases: Dict[str, RecoveryCase]) -> List[dict]:
    """One flat row per case for the overview table."""
    rows = []
    for case in cases.values():
        limit = case.max_notifications if case.max_notifications is not None else "-"
        next_date = next_scheduled_date(case)
        rows.append(
            {
                "Case": case.case_id,
                "Customer": case.customer.name,
                "Payday": case.customer.expected_payday,
                "Payment recovery": case.payment_recovery_state.value,
                "Retention": case.retention_state.value,
                "Reminders": f"{case.notification_count}/{limit}",
                "Next scheduled": next_date.isoformat() if next_date else "-",
                "Pending drafts": len(case.pending_drafts),
                "Opted out": "Yes" if case.opted_out else "No",
            }
        )
    return rows


def case_detail(case: RecoveryCase) -> dict:
    """Display rows for one case: payments, subscriptions, schedule, notifications."""
    payments = [
        {
            "Payment": p.payment_id,
            "Status": p.status,
            "Reason": p.failure_reason or "-",
            "Amount": p.amount,
        }
        for p in case.payments
    ]
    subscriptions = [
        {
            "Subscription": s.subscription_id,
            "Service": s.service,
            "Status": s.status,
            "Amount": s.amount,
        }
        for s in case.subscriptions
    ]
    sent_dates = {n.scheduled_date for n in case.notifications}
    schedule = [
        {
            "#": index + 1,
            "Date": scheduled.isoformat(),
            "Recorded (mock)": "Yes" if scheduled in sent_dates else "No",
        }
        for index, scheduled in enumerate(case.scheduled_dates)
    ]
    notifications = [
        {
            "#": index + 1,
            "Date": n.scheduled_date.isoformat() if n.scheduled_date else "-",
            "Channel": n.channel,
            "Status": n.status,
            "Message": n.message,
        }
        for index, n in enumerate(case.notifications)
    ]
    approved = [
        {"Kind": kind, "Approved wording": text}
        for kind, text in sorted(case.approved_messages.items())
    ]
    return {
        "payments": payments,
        "subscriptions": subscriptions,
        "schedule": schedule,
        "notifications": notifications,
        "approved_messages": approved,
    }


def pending_draft_rows(cases: Dict[str, RecoveryCase]) -> List[dict]:
    """One row per stored AI draft, pending review first."""
    rows = []
    for case in cases.values():
        for kind, draft in case.pending_drafts.items():
            rows.append(
                {
                    "Case": case.case_id,
                    "Customer": case.customer.name,
                    "Kind": kind,
                    "Draft ID": draft.draft_id,
                    "Status": draft.status,
                    "Model": draft.model,
                    "Created": draft.created_at,
                    "Characters": len(draft.text),
                    "Text": draft.text,
                }
            )
    rows.sort(key=lambda row: (row["Status"] != "PENDING_REVIEW", row["Case"]))
    return rows


def audit_rows(entries: List[object]) -> List[dict]:
    """One flat row per audit entry."""
    return [
        {
            "Timestamp": entry.timestamp,
            "Case": entry.case_id,
            "Event": entry.event_type,
            "Action / decision": entry.action_or_decision,
            "Details": json.dumps(entry.details, default=str),
        }
        for entry in entries
    ]
