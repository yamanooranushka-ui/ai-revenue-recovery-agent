"""Tests for cross-file relationship validation (Phase 1)."""
from src.validation import load_dataset


def _load_invalid(invalid_dir, folder):
    return load_dataset(
        customers_path=str(invalid_dir / folder / "customers.csv"),
        payments_path=str(invalid_dir / folder / "payments.csv"),
        subscriptions_path=str(invalid_dir / folder / "subscriptions.csv"),
        usage_path=str(invalid_dir / folder / "usage_history.csv"),
    )


def test_unknown_customer_in_payments_is_an_error(invalid_dir):
    dataset = _load_invalid(invalid_dir, "unknown_customer")
    assert not dataset.report.ok
    assert any("C999" in error for error in dataset.report.errors)


def test_usage_for_unknown_service_is_a_warning_only(invalid_dir):
    dataset = _load_invalid(invalid_dir, "usage_unknown_service")
    # Warnings do not block the dataset; errors would.
    assert dataset.report.ok
    assert len(dataset.report.warnings) == 1
    assert "Spotify" in dataset.report.warnings[0]
    # A header-only payments file is valid (zero rows).
    assert dataset.payments == {}
