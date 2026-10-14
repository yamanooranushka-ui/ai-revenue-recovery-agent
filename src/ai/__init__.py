"""
ai/ (Phase 4)
Optional, suggestion-only AI message drafting.

Safety design (all enforced by ordinary code):
- The drafter generates WORDING from ApprovedFacts.
  It never decides eligibility, schedules notifications,
  changes case state, updates counts, handles opt-outs,
  or performs payment operations.
- Drafts are never delivered. Every draft is stored as
  PENDING_REVIEW and is used only after a human approves
  it through the explicit approval step (approval.py).
- Until a draft is approved, the deterministic template
  remains the text used by the workflow.
- AI drafting runs only when BOTH GEMINI_API_KEY and
  AI_DRAFTING_ENABLED=true are present. Otherwise (and
  on every failure) the deterministic template is used.
- The API key and full draft text are never logged.
"""

from .facts import ApprovedFacts, extract_approved_facts, extract_retention_facts
from .policy import DRAFT_MAX_LENGTH, validate_draft

__all__ = [
    "ApprovedFacts",
    "DRAFT_MAX_LENGTH",
    "extract_approved_facts",
    "extract_retention_facts",
    "validate_draft",
]
