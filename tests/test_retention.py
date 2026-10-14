"""Retention workflow tests (Phase 2)."""
from src.models import (
    Customer,
    RecoveryCase,
    RetentionState,
    Subscription,
    UsageRecord,
)
from src.workflows.retention import retention_step


def test_c001_lapsed_high_usage_asks_whether_to_continue(cases, audit):
    case = cases["C001"]  # Netflix, lapsed, high usage June-September
    outcome = retention_step(case, audit=audit)
    assert case.retention_state == RetentionState.PENDING_CUSTOMER_INTENT
    assert case.retention_subscription_id == "S001"


def test_c001_customer_says_yes_confirms_retention(cases, audit):
    case = cases["C001"]
    retention_step(case, audit=audit)
    retention_step(case, audit=audit, customer_wants_to_continue=True)
    assert case.retention_state == RetentionState.CONFIRMED


def test_c001_customer_says_no_removes_from_queue(cases, audit):
    case = cases["C001"]
    retention_step(case, audit=audit)
    retention_step(case, audit=audit, customer_wants_to_continue=False)
    assert case.retention_state == RetentionState.CLOSED_OPTED_OUT


def test_c004_low_usage_is_parked(cases, audit):
    case = cases["C004"]  # Notion, lapsed, low usage
    retention_step(case, audit=audit)
    assert case.retention_state == RetentionState.NOT_YET_SPECIFIED


def test_active_subscriptions_are_not_applicable(cases, audit):
    for customer_id in ("C002", "C005"):  # Spotify and Canva are active
        case = cases[customer_id]
        retention_step(case, audit=audit)
        assert case.retention_state == RetentionState.NOT_APPLICABLE


def test_no_usage_history_is_parked(audit):
    customer = Customer(customer_id="C900", name="Synthetic", expected_payday=5)
    subscription = Subscription("S900", "C900", "Netflix", 649.0, "lapsed")
    case = RecoveryCase(case_id="CASE-C900", customer=customer, subscriptions=[subscription])
    retention_step(case, audit=audit)
    assert case.retention_state == RetentionState.NOT_YET_SPECIFIED


def test_multiple_lapsed_subscriptions_asks_about_the_high_usage_one(audit):
    customer = Customer(customer_id="C901", name="Synthetic", expected_payday=5)
    subscriptions = [
        Subscription("S901", "C901", "Netflix", 649.0, "lapsed"),  # low usage
        Subscription("S902", "C901", "Spotify", 119.0, "lapsed"),  # high usage
    ]
    usage = [UsageRecord("C901", "Netflix", m, "low") for m in ("June", "July", "August", "September")]
    usage += [UsageRecord("C901", "Spotify", m, "high") for m in ("June", "July", "August", "September")]
    case = RecoveryCase(
        case_id="CASE-C901", customer=customer,
        subscriptions=subscriptions, usage_records=usage,
    )
    retention_step(case, audit=audit)
    assert case.retention_state == RetentionState.PENDING_CUSTOMER_INTENT
    assert case.retention_subscription_id == "S902"


def test_opt_out_stops_retention(cases, audit):
    case = cases["C001"]
    case.opted_out = True
    retention_step(case, audit=audit)
    assert case.retention_state == RetentionState.CLOSED_OPTED_OUT


def test_manual_close_stops_retention(cases, audit):
    case = cases["C001"]
    case.closed_manually = True
    case.close_reason = "Customer asked us to stop"
    retention_step(case, audit=audit)
    assert case.retention_state == RetentionState.CLOSED_MANUAL


def test_answer_before_asking_is_ignored(cases, audit):
    case = cases["C001"]
    retention_step(case, audit=audit, customer_wants_to_continue=True)  # not asked yet
    assert case.retention_state == RetentionState.PENDING_CUSTOMER_INTENT


def test_confirmed_retention_keeps_payment_recovery_independent(cases, audit):
    """Retention finishing must not change payment-recovery state."""
    case = cases["C001"]
    retention_step(case, audit=audit)
    retention_step(case, audit=audit, customer_wants_to_continue=True)
    assert case.retention_state == RetentionState.CONFIRMED
    assert case.payment_recovery_state.value == "NEW"  # untouched
    assert case.notification_count == 0


# ---------- Mixed and ambiguous usage (Phase 7 regression) ----------


def test_mixed_usage_is_parked(audit):
    """Mixed high/low history is not a defined rule: park, invent nothing."""
    customer = Customer(customer_id="C902", name="Synthetic", expected_payday=5)
    subscription = Subscription("S903", "C902", "Netflix", 649.0, "lapsed")
    usage = [
        UsageRecord("C902", "Netflix", "June", "high"),
        UsageRecord("C902", "Netflix", "July", "low"),
        UsageRecord("C902", "Netflix", "August", "high"),
        UsageRecord("C902", "Netflix", "September", "high"),
    ]
    case = RecoveryCase(
        case_id="CASE-C902", customer=customer,
        subscriptions=[subscription], usage_records=usage,
    )
    retention_step(case, audit=audit)
    assert case.retention_state == RetentionState.NOT_YET_SPECIFIED
    assert case.retention_subscription_id is None  # no question was asked


def test_ambiguous_conflicting_usage_is_parked(audit):
    """Two records for the same month conflict -> no action invented."""
    customer = Customer(customer_id="C903", name="Synthetic", expected_payday=5)
    subscription = Subscription("S904", "C903", "Netflix", 649.0, "lapsed")
    usage = [
        UsageRecord("C903", "Netflix", "June", "high"),
        UsageRecord("C903", "Netflix", "June", "low"),  # conflicts with above
        UsageRecord("C903", "Netflix", "July", "high"),
        UsageRecord("C903", "Netflix", "August", "high"),
        UsageRecord("C903", "Netflix", "September", "high"),
    ]
    case = RecoveryCase(
        case_id="CASE-C903", customer=customer,
        subscriptions=[subscription], usage_records=usage,
    )
    retention_step(case, audit=audit)
    assert case.retention_state == RetentionState.NOT_YET_SPECIFIED
    assert case.retention_subscription_id is None
