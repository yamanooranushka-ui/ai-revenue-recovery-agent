"""
policy.py (Phase 4)
Deterministic validation for AI-generated drafts.

Every AI draft must pass these checks before it is
stored as a pending draft. Anything that fails is
rejected and the deterministic template is used
instead. This is a safety backstop: the AI is
suggestion-only and its output is never trusted.
"""

import re
from typing import List

from ..business_rules import AI_DRAFT_MAX_LENGTH
from .facts import ApprovedFacts

# Maximum length of an AI draft (confirmed business rule).
DRAFT_MAX_LENGTH = AI_DRAFT_MAX_LENGTH

# Content an AI draft must never contain: links, invented
# incentives, or promises the agent is forbidden to make
# (no auto-retry, no auto-charge, no debit).
FORBIDDEN_CONTENT = (
    "http://",
    "https://",
    "www.",
    "click here",
    "discount",
    "offer",
    "promo",
    "free",
    "guarantee",
    "limited time",
    "we will retry",
    "automatic payment",
    "we will charge",
    "we will debit",
)

# Numbers at or below this value are treated as day
# numbers or counts (e.g. "reminder 1 of 3"), not
# as amounts. (Known limitation: a wrong amount that
# is itself <= 31 is indistinguishable from a count.)
_NON_AMOUNT_NUMBER_MAX = 31


def validate_draft(draft: str, facts: ApprovedFacts) -> List[str]:
    """
    Validate one draft against the approved facts.

    Returns a list of violations. An empty list means
    the draft is clean. Any non-empty result rejects
    the draft.
    """
    text = draft.strip() if isinstance(draft, str) else ""
    if not text:
        return ["Draft is empty"]

    violations: List[str] = []

    if len(text) > AI_DRAFT_MAX_LENGTH:
        violations.append(
            f"Draft is longer than {AI_DRAFT_MAX_LENGTH} characters"
        )

    lowered = text.lower()
    for forbidden in FORBIDDEN_CONTENT:
        if forbidden in lowered:
            violations.append(f"Unsupported content: '{forbidden}'")

    # Amount consistency: every number in the draft must
    # be the approved amount, or a small day/count number.
    try:
        approved_amount = float(facts.amount)
    except (TypeError, ValueError):
        approved_amount = None
    for number in re.findall(r"\d+(?:\.\d+)?", text):
        try:
            value = float(number)
        except ValueError:
            continue
        if approved_amount is not None and abs(value - approved_amount) < 1e-9:
            continue
        if value <= _NON_AMOUNT_NUMBER_MAX:
            continue
        violations.append(
            f"Invented amount: '{number}' "
            f"(approved amount is {facts.amount})"
        )

    return violations
