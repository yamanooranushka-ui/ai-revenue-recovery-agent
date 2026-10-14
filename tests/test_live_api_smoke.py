"""
Optional LIVE API smoke test.

NOT part of the normal test suite:
- Requires a real GEMINI_API_KEY in the environment.
- Requires the google-genai SDK to be installed.
- Makes exactly one real API call.

Without both, the test is SKIPPED - it never
fails and never runs an API call. Run the full
suite freely without any key or network access.

Usage (optional):
    set GEMINI_API_KEY=your-key
    python -m pytest -m live -v tests/test_live_api_smoke.py
"""

import importlib.util
import os

import pytest

_SDK_AVAILABLE = importlib.util.find_spec("google") is not None

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set - live smoke test skipped",
)
@pytest.mark.skipif(
    not _SDK_AVAILABLE,
    reason="google-genai SDK not installed - live smoke test skipped",
)
def test_live_gemini_draft():
    from google import genai

    from src.ai.drafter import MessageDrafter
    from src.ai.facts import ApprovedFacts

    facts = ApprovedFacts(
        kind="payment_reminder",
        customer_name="Test Customer",
        amount="649",
        failure_reason="insufficient_funds",
        scheduled_date="not scheduled",
        notification_number=1,
        max_notifications=3,
    )
    drafter = MessageDrafter(
        client=genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    )
    result = drafter.draft_payment_reminder(facts)

    # A live call must never raise: the result is either a
    # validated draft or a safe fallback to the template.
    assert result.status in (
        "created",
        "validation_failed",
        "api_error",
    )
    if result.text is not None:
        assert len(result.text) <= 320
