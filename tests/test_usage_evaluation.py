"""Tests for the four-month usage evaluation (Phase 2)."""
from src.models import UsageRecord
from src.usage_evaluation import evaluate_usage


def u(month="June", level="high", service="Netflix"):
    return UsageRecord(customer_id="C1", service=service, month=month, usage=level)


def test_four_consecutive_high_months_is_consistently_high():
    records = [u(month=m) for m in ("June", "July", "August", "September")]
    assert evaluate_usage(records) == "CONSISTENTLY_HIGH"


def test_four_most_recent_months_are_used():
    records = [u(month=m, level="low") for m in ("January", "February")]
    records += [u(month=m) for m in ("June", "July", "August", "September")]
    assert evaluate_usage(records) == "CONSISTENTLY_HIGH"


def test_missing_month_prevents_evaluation():
    records = [u(month=m) for m in ("June", "July", "September")]  # August missing
    assert evaluate_usage(records) == "NOT_YET_SPECIFIED"


def test_gap_between_months_prevents_evaluation():
    records = [u(month=m) for m in ("January", "February", "April", "May")]
    assert evaluate_usage(records) == "NOT_YET_SPECIFIED"


def test_insufficient_history_prevents_evaluation():
    records = [u(month=m) for m in ("June", "July", "August")]
    assert evaluate_usage(records) == "NOT_YET_SPECIFIED"


def test_mixed_usage_is_not_consistently_high():
    records = [u(month="June"), u(month="July"), u(month="August"), u(month="September", level="low")]
    assert evaluate_usage(records) == "NOT_YET_SPECIFIED"


def test_conflicting_records_for_same_month_are_ambiguous():
    records = [u(month="June"), u(month="June", level="low")]
    assert evaluate_usage(records) == "NOT_YET_SPECIFIED"


def test_unknown_month_name_prevents_evaluation():
    records = [u(month="June"), u(month="July"), u(month="August"), u(month="Funember")]
    assert evaluate_usage(records) == "NOT_YET_SPECIFIED"


def test_no_history_prevents_evaluation():
    assert evaluate_usage([]) == "NOT_YET_SPECIFIED"
