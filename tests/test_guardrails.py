"""Guardrail tests: prove the agent cannot process payments."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
FORBIDDEN_TERMS = (
    "charge",
    "retry",
    "debit",
    "process_payment",
    "auto_pay",
    "execute_payment",
)


def _all_definitions():
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                yield path, node.name


def test_no_payment_processing_functions_or_classes():
    offenders = [
        (str(path), name)
        for path, name in _all_definitions()
        if any(term in name.lower() for term in FORBIDDEN_TERMS)
    ]
    assert offenders == []


def test_no_external_payment_or_sms_libraries():
    requirements = (SRC.parent / "requirements.txt").read_text(encoding="utf-8").lower()
    for library in ("stripe", "twilio", "braintree", "paypal", "plaid"):
        assert library not in requirements


def test_notifier_is_mock_only():
    from src.notifications import MockNotifier

    notifier = MockNotifier()
    # A mock notifier exposes no network-sending methods at all.
    public_members = [m for m in dir(notifier) if not m.startswith("_")]
    assert sorted(public_members) == ["count", "create", "created"]
    for member in public_members:
        for term in ("send", "deliver", "dispatch", "post", "request"):
            assert term not in member.lower()


def test_src_contains_no_network_capable_imports():
    """src/ may never reach the network.

    The ONLY tolerated network-capable import is the documented
    lazy `google import genai` inside src/ai/drafter.py, which is
    itself gated behind both configuration flags and an injected
    client. Everything else (HTTP clients, sockets, mail/FTP) is a
    real-SMS / real-payment integration in disguise.
    """
    banned_modules = {
        "requests", "urllib", "http", "socket", "aiohttp", "httpx",
        "ftplib", "smtplib", "telnetlib", "websocket", "xmlrpc",
        "twilio", "stripe",
    }
    offenders = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                root = module.split(".")[0]
                if root in banned_modules:
                    offenders.append(f"{path.name}: import {module}")
                if root == "google" and path.name != "drafter.py":
                    offenders.append(
                        f"{path.name}: import {module} "
                        "(only drafter.py may import the AI SDK)"
                    )
    assert offenders == []
