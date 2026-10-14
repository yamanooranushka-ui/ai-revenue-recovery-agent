"""
test_phase2.py
Test suite for Phase 2: Deterministic State Machine, Stop Conditions,
Payday Scheduling, and Audit Logging.
"""

from ingestion import build_recovery_cases
from state_machine import process_recovery_step, evaluate_stop_conditions
from models import CaseState, Payment
from audit import AuditLogger


def test_scenario_c001_alex():
    """
    Alex (C001):
    - Subscription: Netflix (lapsed)
    - Usage: High across June-Sept (CONSISTENTLY_HIGH)
    - Payment: Failed (insufficient_funds)
    - Payday: 5
    """
    print("\n" + "=" * 70)
    print("TEST: C001 (Alex) - Lapsed + High Usage + Insufficient Funds")
    print("=" * 70)

    cases = {c.customer.customer_id: c for c in build_recovery_cases("customers.csv", "payments.csv", "subscriptions.csv", "usage_history.csv")}
    case = cases["C001"]
    audit = AuditLogger()

    # Step 1: Initial check without customer response -> should ask to continue
    process_recovery_step(case, audit, current_day_of_month=2)
    assert case.state == CaseState.PENDING_CUSTOMER_INTENT, f"Expected PENDING_CUSTOMER_INTENT, got {case.state}"
    print(f"[PASS] Step 1: Correctly paused in state '{case.state.value}' awaiting customer confirmation.")

    # Branch A: Customer says NO -> should stop recovery and remove from queue
    case_no = cases["C001"]
    case_no.state = CaseState.NEW
    process_recovery_step(case_no, audit, current_day_of_month=2, customer_response_to_continue=False)
    assert case_no.state == CaseState.CLOSED_OPTED_OUT, f"Expected CLOSED_OPTED_OUT, got {case_no.state}"
    assert case_no.is_closed is True
    print(f"[PASS] Branch A (Opt-out): Customer said NO -> transitioned to '{case_no.state.value}'.")

    # Branch B: Customer says YES -> proceed with recovery (insufficient_funds rules)
    case_yes = cases["C001"]
    case_yes.state = CaseState.NEW
    case_yes.is_closed = False
    case_yes.customer_wants_to_continue = None
    case_yes.notification_count = 0

    # Notification 1
    process_recovery_step(case_yes, audit, current_day_of_month=2, customer_response_to_continue=True)
    assert case_yes.state == CaseState.WAITING_FOR_PAYMENT
    assert case_yes.notification_count == 1
    assert case_yes.max_notifications == 3
    assert case_yes.preferred_channel == "STANDARD_NOTIFICATION"
    assert "Day 5" in case_yes.next_schedule_info
    print(f"[PASS] Branch B Notification #1 sent. Channel: {case_yes.preferred_channel}, Schedule: {case_yes.next_schedule_info}")

    # Notification 2
    process_recovery_step(case_yes, audit, current_day_of_month=7)
    assert case_yes.notification_count == 2
    assert case_yes.is_closed is False
    print(f"[PASS] Notification #2 sent. Count: {case_yes.notification_count}/3")

    # Notification 3 (Max cap reached -> must auto-close)
    process_recovery_step(case_yes, audit, current_day_of_month=9)
    assert case_yes.notification_count == 3
    assert case_yes.state == CaseState.CLOSED_MAX_ATTEMPTS_REACHED
    assert case_yes.is_closed is True
    print(f"[PASS] Notification #3 reached limit -> transitioned to '{case_yes.state.value}' and closed.")


def test_scenario_c002_sarah():
    """
    Sarah (C002):
    - Subscription: Spotify (active)
    - Payment: Failed (bank_error)
    - Payday: 15
    """
    print("\n" + "=" * 70)
    print("TEST: C002 (Sarah) - Active + Bank Error (Technical)")
    print("=" * 70)

    cases = {c.customer.customer_id: c for c in build_recovery_cases("customers.csv", "payments.csv", "subscriptions.csv", "usage_history.csv")}
    case = cases["C002"]
    audit = AuditLogger()

    process_recovery_step(case, audit, current_day_of_month=3)
    assert case.preferred_channel == "SMS", f"Expected SMS, got {case.preferred_channel}"
    assert case.next_schedule_info == "NOT_YET_SPECIFIED", f"Expected NOT_YET_SPECIFIED schedule, got {case.next_schedule_info}"
    assert case.notification_count == 1
    assert case.state == CaseState.WAITING_FOR_PAYMENT
    print(f"[PASS] Sent notification via {case.preferred_channel}. Schedule marked '{case.next_schedule_info}' (no invented retries).")


def test_scenario_c003_john():
    """
    John (C003):
    - Subscription: Adobe (lapsed)
    - Payment: Successful ($999.0)
    - Payday: 30
    """
    print("\n" + "=" * 70)
    print("TEST: C003 (John) - Payment Succeeded Stop Condition")
    print("=" * 70)

    cases = {c.customer.customer_id: c for c in build_recovery_cases("customers.csv", "payments.csv", "subscriptions.csv", "usage_history.csv")}
    case = cases["C003"]
    audit = AuditLogger()

    process_recovery_step(case, audit, current_day_of_month=5)
    assert case.state == CaseState.RESOLVED_PAID
    assert case.is_closed is True
    print(f"[PASS] Stop condition met: State is '{case.state.value}' and case is closed.")


def test_scenario_c004_emma():
    """
    Emma (C004):
    - Subscription: Notion (lapsed)
    - Usage: Low across June-Sept
    - Payment: Failed (insufficient_funds)
    """
    print("\n" + "=" * 70)
    print("TEST: C004 (Emma) - Lapsed + Low Usage (NOT_YET_SPECIFIED)")
    print("=" * 70)

    cases = {c.customer.customer_id: c for c in build_recovery_cases("customers.csv", "payments.csv", "subscriptions.csv", "usage_history.csv")}
    case = cases["C004"]
    audit = AuditLogger()

    process_recovery_step(case, audit, current_day_of_month=2)
    assert case.state == CaseState.NOT_YET_SPECIFIED
    assert case.is_closed is True
    print(f"[PASS] Low-usage case halted: State is '{case.state.value}' without making an unapproved recovery decision.")


def test_scenario_c005_david():
    """
    David (C005):
    - Subscription: Canva (active)
    - Payment: Failed (payment_processor_error)
    """
    print("\n" + "=" * 70)
    print("TEST: C005 (David) - Active + Payment Processor Error")
    print("=" * 70)

    cases = {c.customer.customer_id: c for c in build_recovery_cases("customers.csv", "payments.csv", "subscriptions.csv", "usage_history.csv")}
    case = cases["C005"]
    audit = AuditLogger()

    process_recovery_step(case, audit, current_day_of_month=26)
    assert case.preferred_channel == "SMS"
    assert case.next_schedule_info == "NOT_YET_SPECIFIED"
    assert case.notification_count == 1
    print(f"[PASS] Sent SMS notification for processor error. Schedule: {case.next_schedule_info}.")


def test_audit_trail_and_guardrails():
    print("\n" + "=" * 70)
    print("TEST: Audit Trail Verification & Zero-Payment Guardrails")
    print("=" * 70)

    # 1. Verify audit log captures events
    cases = {c.customer.customer_id: c for c in build_recovery_cases("customers.csv", "payments.csv", "subscriptions.csv", "usage_history.csv")}
    case = cases["C001"]
    audit = AuditLogger()
    process_recovery_step(case, audit, current_day_of_month=2, customer_response_to_continue=True)

    entries = audit.get_entries_for_case("CASE-C001")
    assert len(entries) > 0, "Audit logger should have captured entries for CASE-C001"
    print(f"[PASS] Audit logger successfully logged {len(entries)} events for CASE-C001:")
    for entry in entries:
        print(f"       • [{entry.event_type}] {entry.action_or_decision}")

    # 2. Guardrail inspection: ensure no retry/charge functions exist in the code
    import state_machine
    forbidden_terms = ["retry_payment", "charge", "debit", "process_payment", "execute_charge"]
    module_members = dir(state_machine)
    for term in forbidden_terms:
        matches = [m for m in module_members if term in m.lower()]
        assert len(matches) == 0, f"Found forbidden payment function: {matches}"
    print("[PASS] Guardrail Verified: No automatic retry, charge, or debit functions exist in code.")


if __name__ == "__main__":
    test_scenario_c001_alex()
    test_scenario_c002_sarah()
    test_scenario_c003_john()
    test_scenario_c004_emma()
    test_scenario_c005_david()
    test_audit_trail_and_guardrails()
    print("\n" + "=" * 70)
    print("ALL PHASE 2 TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)
