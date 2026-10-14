"""
ingestion.py
CSV ingestion, data validation, usage evaluation, and RecoveryCase building.
"""

import csv
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from models import Customer, Payment, Subscription, UsageRecord, RecoveryCase


class IngestionError(Exception):
    """Custom exception raised for CSV ingestion or validation errors."""
    pass


def _clean_row(raw_row: dict) -> dict:
    """Strip whitespace and BOM from all dictionary keys and values."""
    cleaned = {}
    for k, v in raw_row.items():
        clean_key = k.strip().lstrip('\ufeff') if isinstance(k, str) else k
        clean_val = v.strip() if isinstance(v, str) else v
        cleaned[clean_key] = clean_val
    return cleaned


def read_and_validate_csv(filepath: str, required_columns: List[str]) -> List[dict]:
    """
    Reads a CSV file, verifies that required columns exist, and returns cleaned rows.
    Raises IngestionError if the file is missing, empty, or missing required columns.
    """
    path = Path(filepath)
    if not path.is_file():
        raise IngestionError(f"File not found: '{filepath}'")

    rows = []
    with open(path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise IngestionError(f"CSV file is empty or missing headers: '{filepath}'")

        cleaned_headers = [h.strip().lstrip('\ufeff') for h in reader.fieldnames if h]
        missing_columns = [col for col in required_columns if col not in cleaned_headers]
        if missing_columns:
            raise IngestionError(
                f"Missing required columns in '{filepath}': {missing_columns}. "
                f"Found: {cleaned_headers}"
            )

        for line_no, raw_row in enumerate(reader, start=2):
            cleaned = _clean_row(raw_row)
            rows.append((line_no, cleaned))

    return rows


def load_customers(filepath: str) -> Dict[str, Customer]:
    """Loads and validates customers.csv. Returns dict mapping customer_id -> Customer."""
    required = ["customer_id", "name", "expected_payday"]
    rows = read_and_validate_csv(filepath, required)
    customers = {}

    for line_no, row in rows:
        cid = row.get("customer_id")
        name = row.get("name")
        payday_raw = row.get("expected_payday")

        if not cid or not name:
            raise IngestionError(f"Line {line_no} in '{filepath}': Missing customer_id or name.")

        try:
            payday = int(payday_raw)
            if not (1 <= payday <= 31):
                raise ValueError()
        except (ValueError, TypeError):
            raise IngestionError(
                f"Line {line_no} in '{filepath}': expected_payday must be an integer between 1 and 31. Got '{payday_raw}'."
            )

        customers[cid] = Customer(customer_id=cid, name=name, expected_payday=payday)

    return customers


def load_payments(filepath: str) -> Dict[str, List[Payment]]:
    """Loads and validates payments.csv. Returns dict mapping customer_id -> list of Payments."""
    required = ["payment_id", "customer_id", "status", "failure_reason", "amount"]
    rows = read_and_validate_csv(filepath, required)
    payments_by_customer: Dict[str, List[Payment]] = {}

    for line_no, row in rows:
        pid = row.get("payment_id")
        cid = row.get("customer_id")
        status = row.get("status")
        failure_reason = row.get("failure_reason") or None
        amount_raw = row.get("amount")

        if not pid or not cid or not status:
            raise IngestionError(f"Line {line_no} in '{filepath}': Missing payment_id, customer_id, or status.")

        try:
            amount = float(amount_raw)
            if amount < 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise IngestionError(
                f"Line {line_no} in '{filepath}': amount must be a positive number. Got '{amount_raw}'."
            )

        payment = Payment(
            payment_id=pid,
            customer_id=cid,
            status=status.lower(),
            failure_reason=failure_reason.lower() if failure_reason else None,
            amount=amount,
        )

        payments_by_customer.setdefault(cid, []).append(payment)

    return payments_by_customer


def load_subscriptions(filepath: str) -> Dict[str, List[Subscription]]:
    """Loads and validates subscriptions.csv. Returns dict mapping customer_id -> list of Subscriptions."""
    required = ["subscription_id", "customer_id", "service", "amount", "status"]
    rows = read_and_validate_csv(filepath, required)
    subscriptions_by_customer: Dict[str, List[Subscription]] = {}

    for line_no, row in rows:
        sid = row.get("subscription_id")
        cid = row.get("customer_id")
        service = row.get("service")
        amount_raw = row.get("amount")
        status = row.get("status")

        if not sid or not cid or not service or not status:
            raise IngestionError(f"Line {line_no} in '{filepath}': Missing subscription_id, customer_id, service, or status.")

        try:
            amount = float(amount_raw)
            if amount < 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise IngestionError(
                f"Line {line_no} in '{filepath}': amount must be a positive number. Got '{amount_raw}'."
            )

        sub = Subscription(
            subscription_id=sid,
            customer_id=cid,
            service=service,
            amount=amount,
            status=status.lower(),
        )

        subscriptions_by_customer.setdefault(cid, []).append(sub)

    return subscriptions_by_customer


def load_usage_history(filepath: str) -> Dict[Tuple[str, str], List[UsageRecord]]:
    """
    Loads and validates usage_history.csv.
    Returns dict mapping (customer_id, service.lower()) -> list of UsageRecords.
    """
    required = ["customer_id", "service", "month", "usage"]
    rows = read_and_validate_csv(filepath, required)
    usage_by_customer_service: Dict[Tuple[str, str], List[UsageRecord]] = {}

    for line_no, row in rows:
        cid = row.get("customer_id")
        service = row.get("service")
        month = row.get("month")
        usage = row.get("usage")

        if not cid or not service or not month or not usage:
            raise IngestionError(f"Line {line_no} in '{filepath}': Missing customer_id, service, month, or usage.")

        record = UsageRecord(
            customer_id=cid,
            service=service,
            month=month,
            usage=usage.lower(),
        )

        key = (cid, service.lower())
        usage_by_customer_service.setdefault(key, []).append(record)

    return usage_by_customer_service


def evaluate_4month_usage(records: List[UsageRecord]) -> str:
    """
    Evaluates customer usage history over the 4 months: June, July, August, September.

    Rules:
    - If consistently high usage across all 4 months: 'CONSISTENTLY_HIGH'.
    - If customer has records for these months but usage is low or mixed:
      mark as 'NOT_YET_SPECIFIED' (per Rule 5: do not make a recovery decision for low-usage yet).
    - If no usage records exist: 'NO_HISTORY'.
    """
    target_months = {"june", "july", "august", "september"}

    # Map month -> usage level for matching records
    month_usage = {}
    for r in records:
        clean_month = r.month.strip().lower()
        if clean_month in target_months:
            month_usage[clean_month] = r.usage.strip().lower()

    if not month_usage:
        return "NO_HISTORY"

    # Check if all 4 months are present and high
    all_four_present = target_months.issubset(month_usage.keys())
    all_high = all(month_usage.get(m) == "high" for m in target_months)

    if all_four_present and all_high:
        return "CONSISTENTLY_HIGH"

    # If records exist but are not consistently high, mark as NOT_YET_SPECIFIED
    return "NOT_YET_SPECIFIED"


def build_recovery_cases(
    customers_path: str,
    payments_path: str,
    subscriptions_path: str,
    usage_path: str,
) -> List[RecoveryCase]:
    """
    Ingests all 4 CSVs, connects records using customer_id, subscription_id,
    and (customer_id, service), evaluates usage, and returns unified RecoveryCases.
    """
    customers = load_customers(customers_path)
    payments_by_cust = load_payments(payments_path)
    subs_by_cust = load_subscriptions(subscriptions_path)
    usage_by_cust_svc = load_usage_history(usage_path)

    cases: List[RecoveryCase] = []

    for cid, customer in customers.items():
        # Get customer's subscription and payment
        customer_subs = subs_by_cust.get(cid, [])
        customer_payments = payments_by_cust.get(cid, [])

        primary_sub = customer_subs[0] if customer_subs else None
        primary_payment = customer_payments[0] if customer_payments else None

        # Fetch usage records for this customer's subscription service
        if primary_sub:
            key = (cid, primary_sub.service.lower())
            usage_records = usage_by_cust_svc.get(key, [])
        else:
            usage_records = []

        # Run 4-month usage evaluation
        usage_eval = evaluate_4month_usage(usage_records)

        case = RecoveryCase(
            case_id=f"CASE-{cid}",
            customer=customer,
            subscription=primary_sub,
            payment=primary_payment,
            usage_history=usage_records,
            usage_evaluation=usage_eval,
        )
        cases.append(case)

    return cases
