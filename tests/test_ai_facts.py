"""Phase 4 tests: ApprovedFacts extraction."""
from datetime import date

from src.ai.facts import (
    ApprovedFacts,
    extract_approved_facts,
    extract_retention_facts,
)


def _payment(case, amount=649.0, reason="insufficient_funds"):
    from src.models import Payment

    payment = Payment(
        "P001", case.customer.customer_id, "failed", reason, amount
    )
    case.payments.append(payment)
    return payment


def test_payment_facts_contain_only_approved_fields(cases):
    case = cases["C001"]
    payment = _payment(case, amount=649.0)
    # The workflow sets the limit before drafting runs.
    case.max_notifications = 3
    facts = extract_approved_facts(case, payment, date(2026, 1, 5))

    assert isinstance(facts, ApprovedFacts)
    assert facts.kind == "payment_reminder"
    assert facts.customer_name == "Alex"
    assert facts.amount == "649"
    assert facts.failure_reason == "insufficient_funds"
    assert facts.scheduled_date == "2026-01-05"
    assert facts.notification_number == 1
    assert facts.max_notifications == 3
    assert facts.service is None


def test_payment_facts_format_amounts_without_trailing_zero(cases):
    case = cases["C001"]
    payment = _payment(case, amount=649.5)
    facts = extract_approved_facts(case, payment, None)
    assert facts.amount == "649.5"
    assert facts.scheduled_date == "not scheduled"


def test_payment_facts_track_notification_number(cases):
    case = cases["C001"]
    payment = _payment(case)
    case.notification_count = 2
    case.max_notifications = 3
    facts = extract_approved_facts(case, payment, date(2026, 1, 7))
    assert facts.notification_number == 3
    assert facts.max_notifications == 3


def test_retention_facts_contain_service_name(cases):
    case = cases["C001"]
    subscription = case.subscriptions[0]  # Netflix, lapsed
    facts = extract_retention_facts(case, subscription)

    assert facts.kind == "retention_question"
    assert facts.customer_name == "Alex"
    assert facts.service == "Netflix"
    assert facts.amount == "649"
    assert facts.failure_reason == ""
    assert facts.scheduled_date == "not scheduled"
    assert facts.notification_number == 0
    assert facts.max_notifications == 0


def test_facts_are_immutable(cases):
    case = cases["C001"]
    payment = _payment(case)
    facts = extract_approved_facts(case, payment, date(2026, 1, 5))
    try:
        facts.amount = "0"
    except Exception as error:
        assert isinstance(error, (AttributeError, TypeError))
    else:
        raise AssertionError("ApprovedFacts must be immutable")
