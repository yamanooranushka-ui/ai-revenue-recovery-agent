"""Explicit human actions for the demo dashboard.

This is the ONLY module under dashboard/ that writes state, and it
does so exclusively through the existing human-approval entry
points in src/ai/approval.py. It never runs recovery workflows,
never schedules or sends notifications, and never performs payment
operations.

Safety properties:
- Every action RELOADS the case from disk first, so a stale page
  can never overwrite newer state with an old copy.
- A draft that is not PENDING_REVIEW (already approved, already
  rejected, or unknown) raises DashboardActionError: duplicate
  submissions and stale clicks change nothing.
- Only the draft/approval fields change; counts, schedules,
  notifications, workflow states and opt-out flags are untouched.
- The audit entry (written by src/ai/approval.py) carries
  metadata only - never the draft text or customer names.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from src.ai.approval import PENDING_REVIEW, approve_pending_draft, reject_pending_draft
from src.audit import AuditLogger
from src.case_store import CaseStore, CaseStoreError
from src.models import RecoveryCase


class DashboardActionError(Exception):
    """A clear, non-silent refusal: nothing was changed."""


@dataclass
class ActionResult:
    """Outcome of one confirmed human action."""

    ok: bool
    message: str


def _load_pending(
    case_id: str, kind: str, state_dir: Path, audit_path: Path
) -> Tuple[RecoveryCase, CaseStore, AuditLogger]:
    """Reload one case from disk and verify a pending draft exists."""
    store = CaseStore(state_dir=str(state_dir))
    if not store.exists(case_id):
        raise DashboardActionError(
            f"No saved state exists for case '{case_id}'. Nothing to do - "
            "no change was made."
        )
    try:
        case = store.load(case_id)
    except CaseStoreError as error:
        raise DashboardActionError(
            f"Saved state for case '{case_id}' could not be read: {error} "
            "No change was made."
        ) from error

    draft = case.pending_drafts.get(kind)
    if draft is None:
        raise DashboardActionError(
            f"No draft of kind '{kind}' exists for case '{case_id}'. "
            "No change was made."
        )
    if draft.status != PENDING_REVIEW:
        raise DashboardActionError(
            f"Draft {draft.draft_id} is {draft.status} - only a "
            f"{PENDING_REVIEW} draft can be reviewed. No change was made."
        )
    audit = AuditLogger(log_path=str(audit_path))
    return case, store, audit


def approve_draft(
    case_id: str, kind: str, *, state_dir: Path, audit_path: Path
) -> ActionResult:
    """Approve one PENDING_REVIEW draft (confirmed human decision)."""
    case, store, audit = _load_pending(case_id, kind, state_dir, audit_path)
    draft = case.pending_drafts[kind]
    if not approve_pending_draft(case, audit, kind):
        raise DashboardActionError(
            f"Draft {draft.draft_id} was not approved (it is no longer "
            f"{PENDING_REVIEW}). No change was made."
        )
    store.save(case)
    return ActionResult(
        True,
        f"Approved draft {draft.draft_id} ({kind}) for {case_id}. "
        "Recorded in the audit log; no notification was sent.",
    )


def reject_draft(
    case_id: str,
    kind: str,
    *,
    state_dir: Path,
    audit_path: Path,
    reason: Optional[str] = None,
) -> ActionResult:
    """Reject one PENDING_REVIEW draft (confirmed human decision)."""
    case, store, audit = _load_pending(case_id, kind, state_dir, audit_path)
    draft = case.pending_drafts[kind]
    if not reject_pending_draft(case, audit, kind, reason=reason):
        raise DashboardActionError(
            f"Draft {draft.draft_id} was not rejected (it is no longer "
            f"{PENDING_REVIEW}). No change was made."
        )
    store.save(case)
    return ActionResult(
        True,
        f"Rejected draft {draft.draft_id} ({kind}) for {case_id}. "
        "The deterministic template remains in use; nothing was sent.",
    )
