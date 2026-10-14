"""Tests for the persistent JSON case store (Phase 3 preparation)."""
import json
from datetime import date

import pytest

from src.case_store import CaseStore, CaseStoreError
from src.models import (
    CaseState,
    Customer,
    Notification,
    Payment,
    PaymentRecoveryState,
    RecoveryCase,
    RetentionState,
    Subscription,
    UsageRecord,
)


def _sample_case() -> RecoveryCase:
    customer = Customer(customer_id="C001", name="Alice", expected_payday=5)
    payments = [Payment("P001", "C001", "failed", "insufficient_funds", 649.0)]
    subscriptions = [Subscription("S001", "C001", "Netflix", 649.0, "lapsed")]
    usage = [UsageRecord("C001", "Netflix", "June", "high")]
    notifications = [
        Notification(
            notification_id="NOT-ABC12345",
            case_id="CASE-C001",
            customer_id="C001",
            channel="SMS",
            message="Payment reminder",
            scheduled_date=date(2026, 1, 5),
        )
    ]
    case = RecoveryCase(
        case_id="CASE-C001",
        customer=customer,
        payments=payments,
        subscriptions=subscriptions,
        usage_records=usage,
        notifications=notifications,
    )
    case.payment_recovery_state = PaymentRecoveryState.WAITING_FOR_PAYMENT
    case.retention_state = RetentionState.PENDING_CUSTOMER_INTENT
    case.retention_subscription_id = "S001"
    case.notification_count = 1
    case.max_notifications = 3
    case.scheduled_dates = [date(2026, 1, 5), date(2026, 1, 7), date(2026, 1, 9)]
    case.schedule_crosses_month_boundary = False
    case.last_sent_date = date(2026, 1, 5)
    return case


def test_save_and_load_round_trip(tmp_path):
    store = CaseStore(state_dir=str(tmp_path / "state"))
    case = _sample_case()
    store.save(case)

    loaded = store.load("CASE-C001")
    assert loaded.case_id == case.case_id
    assert loaded.customer == case.customer
    assert loaded.payments == case.payments
    assert loaded.subscriptions == case.subscriptions
    assert loaded.usage_records == case.usage_records
    assert loaded.notifications == case.notifications
    assert loaded.state == CaseState.NEW
    assert loaded.payment_recovery_state == PaymentRecoveryState.WAITING_FOR_PAYMENT
    assert loaded.retention_state == RetentionState.PENDING_CUSTOMER_INTENT
    assert loaded.retention_subscription_id == "S001"
    assert loaded.notification_count == 1
    assert loaded.max_notifications == 3
    assert loaded.scheduled_dates == case.scheduled_dates
    assert loaded.schedule_crosses_month_boundary is False
    assert loaded.last_sent_date == date(2026, 1, 5)
    assert loaded.opted_out is False


def test_exists_and_load_all(tmp_path):
    store = CaseStore(state_dir=str(tmp_path / "state"))
    assert store.exists("CASE-C001") is False
    store.save(_sample_case())
    assert store.exists("CASE-C001") is True
    assert set(store.load_all()) == {"CASE-C001"}


def test_missing_state_file_raises(tmp_path):
    store = CaseStore(state_dir=str(tmp_path / "state"))
    with pytest.raises(CaseStoreError, match="No saved state"):
        store.load("CASE-C001")


def test_corrupted_json_raises(tmp_path):
    store = CaseStore(state_dir=str(tmp_path / "state"))
    store.save(_sample_case())
    path = tmp_path / "state" / "CASE-C001.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(CaseStoreError, match="not valid JSON"):
        store.load("CASE-C001")


def test_invalid_content_raises_and_never_resets(tmp_path):
    """A state file that cannot be read must never reset the count."""
    store = CaseStore(state_dir=str(tmp_path / "state"))
    case = _sample_case()
    case.notification_count = 2
    store.save(case)
    path = tmp_path / "state" / "CASE-C001.json"
    path.write_text("{}", encoding="utf-8")  # valid JSON, no fields
    with pytest.raises(CaseStoreError, match="schema version"):
        store.load("CASE-C001")


def test_wrong_schema_version_raises(tmp_path):
    store = CaseStore(state_dir=str(tmp_path / "state"))
    store.save(_sample_case())
    path = tmp_path / "state" / "CASE-C001.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = 999
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CaseStoreError, match="schema version"):
        store.load("CASE-C001")


def test_case_id_mismatch_raises(tmp_path):
    store = CaseStore(state_dir=str(tmp_path / "state"))
    store.save(_sample_case())
    # A state file whose name does not match its content is invalid.
    source = tmp_path / "state" / "CASE-C001.json"
    target = tmp_path / "state" / "CASE-C999.json"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(CaseStoreError, match="contains case"):
        store.load("CASE-C999")
