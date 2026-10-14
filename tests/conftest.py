"""Shared test setup for the test suite."""
from pathlib import Path

import pytest

from src.audit import AuditLogger
from src.ingestion import build_recovery_cases
from src.validation import load_dataset

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def valid_dir() -> Path:
    return FIXTURES / "valid"


@pytest.fixture
def invalid_dir() -> Path:
    return FIXTURES / "invalid"


@pytest.fixture
def valid_paths(valid_dir) -> dict:
    """Paths of the four valid fixture CSVs."""
    return {
        "customers_path": str(valid_dir / "customers.csv"),
        "payments_path": str(valid_dir / "payments.csv"),
        "subscriptions_path": str(valid_dir / "subscriptions.csv"),
        "usage_path": str(valid_dir / "usage_history.csv"),
    }


@pytest.fixture
def audit(tmp_path):
    """Append-only audit log in a temporary directory (never real audit data)."""
    return AuditLogger(log_path=str(tmp_path / "audit_log.jsonl"))


@pytest.fixture
def cases():
    """One RecoveryCase per customer, built from the real read-only CSVs."""
    dataset = load_dataset(
        customers_path=str(PROJECT_ROOT / "customers.csv"),
        payments_path=str(PROJECT_ROOT / "payments.csv"),
        subscriptions_path=str(PROJECT_ROOT / "subscriptions.csv"),
        usage_path=str(PROJECT_ROOT / "usage_history.csv"),
    )
    all_cases = build_recovery_cases(
        dataset.customers, dataset.payments, dataset.subscriptions, dataset.usage
    )
    return {case.customer.customer_id: case for case in all_cases}
