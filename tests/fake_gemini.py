"""
Fake Gemini client for tests.

Mirrors the tiny slice of the official `google-genai`
SDK surface that MessageDrafter uses:

    client.models.generate_content(model=..., contents=...)
    -> response.text

No network, no API key, no real SDK required.
"""


class FakeResponse:
    """Mimics the SDK's GenerateContentResponse."""

    def __init__(self, text):
        self.text = text


class FakeGeminiClient:
    """Minimal stand-in for google.genai.Client.

    `text`: the response text (the "AI draft").
    `error`: an exception to raise (simulates API
    failures such as timeouts or rate limits).
    `calls` records every generate_content invocation.
    """

    def __init__(self, text=None, error=None):
        self.text = text
        self.error = error
        self.calls = []
        # The SDK nests the model methods under client.models.
        self.models = self

    def generate_content(self, model=None, contents=None):
        self.calls.append({"model": model, "contents": contents})
        if self.error is not None:
            raise self.error
        return FakeResponse(self.text)
