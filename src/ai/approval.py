"""
approval.py (Phase 4)
The separate, explicit human-approval step for AI drafts.

AI-generated wording is never used by a workflow. A draft
is stored as PENDING_REVIEW and becomes the wording for
future notifications of its kind ONLY after a human calls
approve_pending_draft() here. Until then - and after a
rejection - the deterministic template remains in use.

This module is the ONLY code that promotes draft wording
into approved wording. It changes no other case state.
"""

from dataclasses import replace
from typing import Optional

from ..models import RecoveryCase

PENDING_REVIEW = "PENDING_REVIEW"
APPROVED = "APPROVED"
REJECTED = "REJECTED"


def approve_pending_draft(case: RecoveryCase, audit, kind: str) -> bool:
    """
    Human approval step: promote a PENDING_REVIEW draft of
    the given kind to the approved wording.

    Returns True when a pending draft was approved. Future
    notifications of that kind may then use the approved
    text. Audit logs metadata only (never the draft text).
    """
    draft = case.pending_drafts.get(kind)
    if draft is None or draft.status != PENDING_REVIEW:
        return False
    case.approved_messages = {**case.approved_messages, kind: draft.text}
    case.pending_drafts = {
        **case.pending_drafts,
        kind: replace(draft, status=APPROVED),
    }
    audit.log(
        case_id=case.case_id,
        customer_id=case.customer.customer_id,
        event_type="HUMAN_APPROVAL",
        action_or_decision=(
            "Human reviewed and approved the pending AI draft. "
            "Future notifications of this kind may use the "
            "approved wording."
        ),
        details={
            "draft_id": draft.draft_id,
            "kind": draft.kind,
            "model": draft.model,
            "characters": len(draft.text),
        },
    )
    return True


def reject_pending_draft(
    case: RecoveryCase,
    audit,
    kind: str,
    reason: Optional[str] = None,
) -> bool:
    """
    Human rejection step: discard a PENDING_REVIEW draft of
    the given kind. The deterministic template remains in
    use for that kind. Returns True when a pending draft
    was rejected.
    """
    draft = case.pending_drafts.get(kind)
    if draft is None or draft.status != PENDING_REVIEW:
        return False
    case.pending_drafts = {
        key: value
        for key, value in case.pending_drafts.items()
        if key != kind
    }
    audit.log(
        case_id=case.case_id,
        customer_id=case.customer.customer_id,
        event_type="HUMAN_APPROVAL",
        action_or_decision=(
            "Human rejected the pending AI draft. The "
            "deterministic template remains in use."
        ),
        details={
            "draft_id": draft.draft_id,
            "kind": draft.kind,
            "model": draft.model,
            "reason": reason,
        },
    )
    return True
