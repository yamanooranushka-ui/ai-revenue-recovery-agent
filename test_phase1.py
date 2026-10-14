"""
test_phase1.py
Test script to load CSV files, build unified RecoveryCase objects,
verify 4-month usage evaluation, and test data validation checks.
"""

import os
import tempfile
from ingestion import (
    build_recovery_cases,
    read_and_validate_csv,
    load_customers,
    IngestionError,
)


def test_live_csv_loading():
    print("=" * 70)
    print("TEST 1: Ingesting Live CSV Files & Building RecoveryCase Objects")
    print("=" * 70)

    customers_csv = "customers.csv"
    payments_csv = "payments.csv"
    subscriptions_csv = "subscriptions.csv"
    usage_csv = "usage_history.csv"

    cases = build_recovery_cases(
        customers_path=customers_csv,
        payments_path=payments_csv,
        subscriptions_path=subscriptions_csv,
        usage_path=usage_csv,
    )

    print(f"\nSuccessfully built {len(cases)} RecoveryCase objects:\n")
    for case in cases:
        print(case.summary())
        print("-" * 50)

    # Assertions on expected business data
    evaluations = {c.customer.customer_id: c.usage_evaluation for c in cases}
    assert evaluations["C001"] == "CONSISTENTLY_HIGH", "C001 should be CONSISTENTLY_HIGH"
    assert evaluations["C002"] == "NO_HISTORY", "C002 should have NO_HISTORY"
    assert evaluations["C003"] == "CONSISTENTLY_HIGH", "C003 should be CONSISTENTLY_HIGH"
    assert evaluations["C004"] == "NOT_YET_SPECIFIED", "C004 should be NOT_YET_SPECIFIED"
    assert evaluations["C005"] == "NO_HISTORY", "C005 should have NO_HISTORY"

    print("\n[PASS] All customer evaluations match expected rules exactly:")
    print("  - C001 (Alex - Netflix): CONSISTENTLY_HIGH (High usage June-Sept)")
    print("  - C002 (Sarah - Spotify): NO_HISTORY (Active sub, no churn history)")
    print("  - C003 (John - Adobe): CONSISTENTLY_HIGH (High usage June-Sept)")
    print("  - C004 (Emma - Notion): NOT_YET_SPECIFIED (Low usage June-Sept; rule pending)")
    print("  - C005 (David - Canva): NO_HISTORY (Active sub, no churn history)")


def test_missing_file_validation():
    print("\n" + "=" * 70)
    print("TEST 2: Validation on Missing CSV File")
    print("=" * 70)

    try:
        read_and_validate_csv("non_existent_file.csv", ["customer_id"])
        print("[FAIL] Missing file did not raise IngestionError")
        assert False
    except IngestionError as e:
        print(f"[PASS] Successfully caught missing file error: {e}")


def test_missing_column_validation():
    print("\n" + "=" * 70)
    print("TEST 3: Validation on Missing Required Header Columns")
    print("=" * 70)

    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv") as tmp:
        tmp.write("customer_id,name\nC099,Tester\n")
        tmp_path = tmp.name

    try:
        load_customers(tmp_path)
        print("[FAIL] Missing required column 'expected_payday' did not raise IngestionError")
        assert False
    except IngestionError as e:
        print(f"[PASS] Successfully caught missing column error: {e}")
    finally:
        os.remove(tmp_path)


def test_malformed_numeric_validation():
    print("\n" + "=" * 70)
    print("TEST 4: Validation on Malformed Numeric Data (Payday)")
    print("=" * 70)

    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv") as tmp:
        tmp.write("customer_id,name,expected_payday\nC099,Tester,invalid_number\n")
        tmp_path = tmp.name

    try:
        load_customers(tmp_path)
        print("[FAIL] Malformed payday did not raise IngestionError")
        assert False
    except IngestionError as e:
        print(f"[PASS] Successfully caught malformed numeric error: {e}")
    finally:
        os.remove(tmp_path)


if __name__ == "__main__":
    test_live_csv_loading()
    test_missing_file_validation()
    test_missing_column_validation()
    test_malformed_numeric_validation()
    print("\n" + "=" * 70)
    print("ALL PHASE 1 TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)
