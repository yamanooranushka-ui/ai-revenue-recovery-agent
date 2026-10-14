"""Tests for the Phase 1 data models."""
from dataclasses import FrozenInstanceError

import pytest

from src.models import CaseState, Customer, Payment, RecoveryCase, Subscription, UsageRecord


def test_customer_holds_csv_fields():
    customer = Customer(customer_id="C101", name="Alice", expected_payday=5)
    assert customer.customer_id == "C101"
    assert customer.name == "Alice"
    assert customer.expected_payday == 5


def test_core_records_are_immutable():
    customer = Customer(customer_id="C101", name="Alice", expected_payday=5)
    with pytest.raises(FrozenInstanceError):
        customer.name = "Bob"


def test_new_case_starts_in_new_state():
    case = RecoveryCase(case_id="CASE-C101", customer=Customer("C101", "Alice", 5))
    assert case.state == CaseState.NEW


def test_case_keeps_multiple_payments_and_subscriptions():
    customer = Customer("C101", "Alice", 5)
    payments = [
        Payment("P1", "C101", "failed", "insufficient_funds", 649.0),
        Payment("P2", "C101", "failed", "bank_error", 499.0),
    ]
    subscriptions = [
        Subscription("S1", "C101", "Netflix", 649.0, "lapsed"),
        Subscription("S2", "C101", "Spotify", 119.0, "active"),
    ]
    case = RecoveryCase(
        case_id="CASE-C101",
        customer=customer,
        payments=payments,
        subscriptions=subscriptions,
    )
    assert len(case.payments) == 2
    assert len(case.subscriptions) == 2
    assert case.payments[1].failure_reason == "bank_error"


def test_usage_for_service_is_case_insensitive():
    customer = Customer("C101", "Alice", 5)
    usage = [
        UsageRecord("C101", "Netflix", "June", "high"),
        UsageRecord("C101", "Netflix", "July", "high"),
        UsageRecord("C101", "Spotify", "June", "low"),
    ]
    case = RecoveryCase(case_id="CASE-C101", customer=customer, usage_records=usage)
    assert len(case.usage_for_service("netflix")) == 2
    assert len(case.usage_for_service("NETFLIX")) == 2
    assert len(case.usage_for_service("spotify")) == 1


def test_summary_is_readable():
    customer = Customer("C101", "Alice", 5)
    case = RecoveryCase(case_id="CASE-C101", customer=customer)
    assert "CASE-C101" in case.summary()
    assert "Alice" in case.summary()
