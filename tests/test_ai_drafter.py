"""Phase 4 tests: MessageDrafter (fake client - no network, no API key)."""
from dataclasses import replace

import pytest

from src.ai.drafter import (
    DEFAULT_MODEL,
    MessageDrafter,
)
from src.ai.facts import ApprovedFacts

from fake_gemini import FakeGeminiClient

FACTS = ApprovedFacts(
    kind="payment_reminder",
    customer_name="Alex",
    amount="649",
    failure_reason="insufficient_funds",
    scheduled_date="2026-01-05",
    notification_number=1,
    max_notifications=3,
)

CLEAN_DRAFT = (
    "Hi Alex, your payment of 649 failed. "
    "Please complete it manually."
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """No AI configuration from the real environment."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AI_DRAFTING_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)


# ---------- Configuration ----------


def test_default_model_is_verified_supported():
    # Verified against the official Gemini API model list
    # (ai.google.dev/gemini-api/docs/models, 2026-10-09):
    # gemini-2.0-flash was shut down 2026-06-01;
    # Google recommends 3.5 Flash-Lite or 3.8 Flash
    # for new projects.
    assert DEFAULT_MODEL == "gemini-3.5-flash-lite"


def test_model_defaults_to_verified_default():
    drafter = MessageDrafter()
    assert drafter.model == DEFAULT_MODEL


def test_model_read_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    drafter = MessageDrafter()
    assert drafter.model == "gemini-3.8-flash"


def test_explicit_model_overrides_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    drafter = MessageDrafter(model="gemini-3.6-flash")
    assert drafter.model == "gemini-3.6-flash"


# ---------- Enabled + configured: draft created ----------


def test_draft_created_when_enabled_and_configured():
    client = FakeGeminiClient(text=CLEAN_DRAFT)
    drafter = MessageDrafter(
        client=client, api_key="test-key", enabled=True
    )
    result = drafter.draft_payment_reminder(FACTS)

    assert result.status == "created"
    assert result.text == CLEAN_DRAFT
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == DEFAULT_MODEL
    # Facts are passed as data in the prompt.
    assert '"customer_name": "Alex"' in client.calls[0]["contents"]
    assert '"amount": "649"' in client.calls[0]["contents"]


def test_enabled_and_key_from_environment(monkeypatch):
    monkeypatch.setenv("AI_DRAFTING_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    client = FakeGeminiClient(text=CLEAN_DRAFT)
    drafter = MessageDrafter(client=client)
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "created"
    assert len(client.calls) == 1


def test_retention_question_drafting():
    client = FakeGeminiClient(text="Do you want to continue Netflix?")
    drafter = MessageDrafter(
        client=client, api_key="test-key", enabled=True
    )
    facts = replace(
        FACTS, kind="retention_question", service="Netflix"
    )
    result = drafter.draft_retention_question(facts)
    assert result.status == "created"
    assert result.text == "Do you want to continue Netflix?"
    assert '"service": "Netflix"' in client.calls[0]["contents"]


# ---------- Missing configuration ----------


def test_disabled_flag_never_calls_api():
    client = FakeGeminiClient(text=CLEAN_DRAFT)
    drafter = MessageDrafter(
        client=client, api_key="test-key", enabled=False
    )
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "disabled"
    assert result.text is None
    assert client.calls == []


def test_missing_flag_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("AI_DRAFTING_ENABLED", raising=False)
    client = FakeGeminiClient(text=CLEAN_DRAFT)
    drafter = MessageDrafter(client=client, api_key="test-key")
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "disabled"
    assert client.calls == []


def test_enabled_without_api_key_is_not_configured(monkeypatch):
    monkeypatch.setenv("AI_DRAFTING_ENABLED", "true")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = FakeGeminiClient(text=CLEAN_DRAFT)
    drafter = MessageDrafter(client=client)
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "not_configured"
    assert result.text is None
    assert client.calls == []


def test_neither_flag_nor_key_is_disabled():
    client = FakeGeminiClient(text=CLEAN_DRAFT)
    drafter = MessageDrafter(client=client)
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "disabled"
    assert client.calls == []


def test_flag_value_must_be_exactly_true(monkeypatch):
    monkeypatch.setenv("AI_DRAFTING_ENABLED", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = FakeGeminiClient(text=CLEAN_DRAFT)
    drafter = MessageDrafter(client=client)
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "disabled"
    assert client.calls == []


# ---------- API failures ----------


def test_api_error_falls_back_safely():
    client = FakeGeminiClient(error=RuntimeError("rate limit"))
    drafter = MessageDrafter(
        client=client, api_key="test-key", enabled=True
    )
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "api_error"
    assert result.text is None


def test_api_timeout_falls_back_safely():
    client = FakeGeminiClient(error=TimeoutError("timed out"))
    drafter = MessageDrafter(
        client=client, api_key="test-key", enabled=True
    )
    result = drafter.draft_retention_question(FACTS)
    assert result.status == "api_error"
    assert result.text is None


# ---------- Malformed output ----------


def test_empty_response_is_invalid_output():
    client = FakeGeminiClient(text="   ")
    drafter = MessageDrafter(
        client=client, api_key="test-key", enabled=True
    )
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "invalid_output"
    assert result.text is None


def test_non_string_response_is_invalid_output():
    class WeirdResponse:
        text = None

    class WeirdClient:
        models = None

        def __init__(self):
            self.models = self
            self.calls = []

        def generate_content(self, model=None, contents=None):
            self.calls.append(model)
            return WeirdResponse()

    drafter = MessageDrafter(
        client=WeirdClient(), api_key="test-key", enabled=True
    )
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "invalid_output"
    assert result.text is None


# ---------- Validation failures ----------


def test_draft_with_url_fails_validation():
    client = FakeGeminiClient(
        text="Please pay at https://example.com now"
    )
    drafter = MessageDrafter(
        client=client, api_key="test-key", enabled=True
    )
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "validation_failed"
    assert result.text is None


def test_draft_with_unsupported_promise_fails_validation():
    client = FakeGeminiClient(
        text="We will retry your payment automatically."
    )
    drafter = MessageDrafter(
        client=client, api_key="test-key", enabled=True
    )
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "validation_failed"
    assert result.text is None


def test_draft_with_wrong_amount_fails_validation():
    client = FakeGeminiClient(
        text="Your payment of 999 failed. Please pay manually."
    )
    drafter = MessageDrafter(
        client=client, api_key="test-key", enabled=True
    )
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "validation_failed"
    assert result.text is None


def test_draft_exceeding_max_length_fails_validation():
    client = FakeGeminiClient(
        text="Your payment of 649 failed. " + "x" * 400
    )
    drafter = MessageDrafter(
        client=client, api_key="test-key", enabled=True
    )
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "validation_failed"
    assert result.text is None


def test_response_containing_customer_data_is_still_data():
    """A name embedded in the draft must not become an instruction."""
    client = FakeGeminiClient(
        text="Hi Alex, your payment of 649 failed. "
        "Please complete it manually."
    )
    drafter = MessageDrafter(
        client=client, api_key="test-key", enabled=True
    )
    result = drafter.draft_payment_reminder(FACTS)
    assert result.status == "created"
    assert "Alex" in result.text


def test_fake_client_drafting_never_imports_the_sdk():
    """Accidental-live-call guard: the fake path never touches the SDK."""
    import sys

    was_imported = "google.genai" in sys.modules
    client = FakeGeminiClient(text=CLEAN_DRAFT)
    drafter = MessageDrafter(client=client, api_key="test-key", enabled=True)
    result = drafter.draft_payment_reminder(FACTS)

    assert result.status == "created"
    # No new SDK import happened, whatever the session state was.
    assert ("google.genai" in sys.modules) == was_imported
