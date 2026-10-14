"""Tests for CSV loading and field-level validation (Phase 1)."""
import pytest

from src.ingestion import (
    IngestionError,
    build_recovery_cases,
    load_customers,
    load_payments,
    load_subscriptions,
    load_usage_history,
)
from src.validation import load_dataset


# ---------- Happy path ----------

def test_valid_files_load(valid_paths):
    customers = load_customers(valid_paths["customers_path"])
    payments = load_payments(valid_paths["payments_path"])
    subscriptions = load_subscriptions(valid_paths["subscriptions_path"])
    usage = load_usage_history(valid_paths["usage_path"])

    assert set(customers.keys()) == {"C101", "C102", "C103", "C104"}
    assert customers["C101"].name == "Alice"
    assert customers["C101"].expected_payday == 5

    # C101 has TWO payments and TWO subscriptions - none may be dropped.
    assert len(payments["C101"]) == 2
    assert len(subscriptions["C101"]) == 2
    assert {p.payment_id for p in payments["C101"]} == {"PAY101", "PAY102"}

    # A successful payment keeps failure_reason empty.
    assert payments["C102"][0].status == "successful"
    assert payments["C102"][0].failure_reason is None

    # Usage is grouped by (customer_id, service).
    assert len(usage[("C101", "netflix")]) == 4
    assert all(r.usage == "high" for r in usage[("C101", "netflix")])


def test_build_recovery_cases_keeps_everything(valid_paths):
    dataset = load_dataset(**valid_paths)
    cases = build_recovery_cases(
        dataset.customers, dataset.payments, dataset.subscriptions, dataset.usage
    )

    # One case per customer, including customers with no other records.
    assert len(cases) == 4
    by_customer = {case.customer.customer_id: case for case in cases}

    c101 = by_customer["C101"]
    assert len(c101.payments) == 2
    assert len(c101.subscriptions) == 2
    assert len(c101.usage_records) == 4
    assert c101.usage_for_service("Netflix") == c101.usage_records

    # A customer with no payments/subscriptions/usage still gets a case.
    c104 = by_customer["C104"]
    assert c104.payments == []
    assert c104.subscriptions == []
    assert c104.usage_records == []


def test_valid_dataset_has_no_validation_errors(valid_paths):
    dataset = load_dataset(**valid_paths)
    assert dataset.report.ok, dataset.report.summary()


# ---------- File-level errors ----------

def test_missing_file_raises():
    with pytest.raises(IngestionError, match="File not found"):
        load_customers("definitely_not_here.csv")


def test_empty_file_raises(tmp_path):
    empty = tmp_path / "customers.csv"
    empty.write_text("")
    with pytest.raises(IngestionError, match="empty"):
        load_customers(str(empty))


def test_missing_column_raises(invalid_dir):
    path = str(invalid_dir / "missing_column" / "customers.csv")
    with pytest.raises(IngestionError, match="Missing required column"):
        load_customers(path)


def test_bom_and_whitespace_are_cleaned(tmp_path):
    raw = "customer_id,name,expected_payday\r\n C101 , Alice , 5 \r\n"
    path = tmp_path / "customers.csv"
    path.write_bytes(b"\xef\xbb\xbf" + raw.encode("utf-8"))
    customers = load_customers(str(path))
    assert set(customers) == {"C101"}
    assert customers["C101"].name == "Alice"


# ---------- Field-level errors (one table-driven test) ----------

@pytest.mark.parametrize(
    "relative_path,loader",
    [
        ("bad_payday/customers.csv", load_customers),
        ("payday_out_of_range/customers.csv", load_customers),
        ("duplicate_customer/customers.csv", load_customers),
        ("negative_amount/payments.csv", load_payments),
        ("unknown_payment_status/payments.csv", load_payments),
        ("failed_no_reason/payments.csv", load_payments),
        ("success_with_reason/payments.csv", load_payments),
        ("unknown_failure_reason/payments.csv", load_payments),
        ("duplicate_payment/payments.csv", load_payments),
        ("duplicate_subscription/subscriptions.csv", load_subscriptions),
        ("unknown_sub_status/subscriptions.csv", load_subscriptions),
        ("duplicate_usage/usage_history.csv", load_usage_history),
        ("unknown_usage_level/usage_history.csv", load_usage_history),
    ],
)
def test_invalid_data_raises(invalid_dir, relative_path, loader):
    with pytest.raises(IngestionError):
        loader(str(invalid_dir / relative_path))
