"""Phase 4 tests: deterministic draft validation (policy)."""
import pytest

from src.ai.facts import ApprovedFacts
from src.ai.policy import (
    DRAFT_MAX_LENGTH,
    FORBIDDEN_CONTENT,
    validate_draft,
)

FACTS = ApprovedFacts(
    kind="payment_reminder",
    customer_name="Alex",
    amount="649",
    failure_reason="insufficient_funds",
    scheduled_date="2026-01-05",
    notification_number=1,
    max_notifications=3,
)


def test_clean_draft_passes():
    draft = (
        "Hi Alex, your payment of 649 failed due to "
        "insufficient funds. Please complete it manually."
    )
    assert validate_draft(draft, FACTS) == []


def test_empty_or_missing_draft_is_rejected():
    assert validate_draft("", FACTS) == ["Draft is empty"]
    assert validate_draft("   ", FACTS) == ["Draft is empty"]
    assert validate_draft(None, FACTS) == ["Draft is empty"]


def test_draft_over_max_length_is_rejected():
    draft = "x" * (DRAFT_MAX_LENGTH + 1)
    violations = validate_draft(draft, FACTS)
    assert any("longer than 320" in v for v in violations)


def test_draft_at_max_length_passes():
    # Exactly 320 characters, no other numbers.
    draft = ("x" * (DRAFT_MAX_LENGTH - 3)) + "649"
    assert len(draft) == DRAFT_MAX_LENGTH
    assert validate_draft(draft, FACTS) == []


@pytest.mark.parametrize(
    "forbidden",
    FORBIDDEN_CONTENT,
)
def test_forbidden_content_is_rejected(forbidden):
    draft = f"Your payment failed. {forbidden} now!"
    violations = validate_draft(draft, FACTS)
    assert any(f"'{forbidden}'" in v for v in violations)


def test_invented_urls_are_rejected():
    for draft in (
        "Pay at https://example.com/pay now",
        "See http://bit.ly/xyz",
        "Visit www.example.com to pay",
    ):
        assert validate_draft(draft, FACTS)


def test_wrong_amount_is_rejected():
    draft = (
        "Hi Alex, your payment of 999 failed. "
        "Please complete it manually."
    )
    violations = validate_draft(draft, FACTS)
    assert any("Invented amount: '999'" in v for v in violations)


def test_approved_amount_in_any_format_passes():
    for draft in (
        "Your payment of 649 failed.",
        "Your payment of 649.0 failed.",
        "Your payment of 649.00 failed.",
    ):
        assert validate_draft(draft, FACTS) == []


def test_small_day_numbers_and_counts_are_allowed():
    drafts = (
        "Reminder 1 of 3: your payment of 649 failed.",
        "Please pay by day 5. Your payment of 649 failed.",
        "This is notice number 2 about your payment of 649.",
    )
    for draft in drafts:
        assert validate_draft(draft, FACTS) == []


def test_case_insensitive_forbidden_content():
    draft = "Your payment failed. We Will Charge you later."
    assert validate_draft(draft, FACTS)


def test_multiple_violations_are_all_reported():
    draft = "https://x.com discount 999 " + "y" * 400
    violations = validate_draft(draft, FACTS)
    assert len(violations) >= 3
