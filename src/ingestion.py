"""
ingestion.py (Phase 1)
CSV loading and field-level validation.

Responsibilities:
- Read CSV files safely (read-only, UTF-8, handles BOM and stray spaces).
- Check that required columns exist.
- Convert and validate each field.
- NEVER modifies the CSV files.
"""

import csv
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .business_rules import (
    KNOWN_FAILURE_REASONS,
    PAYMENT_STATUSES,
    SUBSCRIPTION_STATUSES,
    USAGE_LEVELS,
)
from .models import Customer, Payment, RecoveryCase, Subscription, UsageRecord


class IngestionError(Exception):
    """Raised when a CSV file is missing, malformed, or contains invalid data."""


def _clean(value) -> str:
    """Strip surrounding whitespace (and a BOM if present) from a text value."""
    return value.strip().lstrip("﻿") if isinstance(value, str) else value


def _text(row: dict, key: str) -> str:
    """Read a text field from a row; short rows give None, so normalise to ''."""
    value = row.get(key)
    return value if isinstance(value, str) else ""


def read_csv(filepath: str, required_columns: List[str]) -> List[Tuple[int, dict]]:
    """
    Read a CSV file and return a list of (line_number, cleaned_row_dict).
    Raises IngestionError if the file is missing, empty, or missing columns.
    """
    path = Path(filepath)
    if not path.is_file():
        raise IngestionError(f"File not found: '{filepath}'")

    rows: List[Tuple[int, dict]] = []
    # Opened in read-only mode on purpose: Phase 1 never writes to the CSVs.
    with open(path, mode="r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise IngestionError(f"CSV file is empty (no header row): '{filepath}'")

        headers = [_clean(h) for h in reader.fieldnames if h is not None]
        missing = [col for col in required_columns if col not in headers]
        if missing:
            raise IngestionError(
                f"Missing required column(s) in '{filepath}': {missing}. Found: {headers}"
            )

        for line_no, raw_row in enumerate(reader, start=2):  # line 1 is the header
            cleaned = {_clean(k): _clean(v) for k, v in raw_row.items() if k is not None}
            rows.append((line_no, cleaned))
    return rows


def _parse_amount(amount_raw: Optional[str], filepath: str, line_no: int) -> float:
    """Convert an amount to a non-negative finite float, or raise IngestionError."""
    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        raise IngestionError(
            f"Line {line_no} in '{filepath}': amount must be a number. Got '{amount_raw}'."
        )
    if math.isnan(amount) or math.isinf(amount):
        raise IngestionError(
            f"Line {line_no} in '{filepath}': amount must be a normal number. Got '{amount_raw}'."
        )
    if amount < 0:
        raise IngestionError(
            f"Line {line_no} in '{filepath}': amount must not be negative. Got {amount}."
        )
    return amount


def _parse_payday(payday_raw: Optional[str], filepath: str, line_no: int) -> int:
    """Convert expected_payday to a whole number between 1 and 31, or raise."""
    try:
        payday = int(payday_raw)
    except (TypeError, ValueError):
        raise IngestionError(
            f"Line {line_no} in '{filepath}': expected_payday must be a whole number 1-31. "
            f"Got '{payday_raw}'."
        )
    if not 1 <= payday <= 31:
        raise IngestionError(
            f"Line {line_no} in '{filepath}': expected_payday must be between 1 and 31. Got {payday}."
        )
    return payday


def load_customers(filepath: str) -> Dict[str, Customer]:
    """Load customers.csv into a dict: customer_id -> Customer."""
    required = ["customer_id", "name", "expected_payday"]
    customers: Dict[str, Customer] = {}

    for line_no, row in read_csv(filepath, required):
        cid = _text(row, "customer_id")
        name = _text(row, "name")
        if not cid or not name:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': customer_id and name are required."
            )
        if cid in customers:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': duplicate customer_id '{cid}'."
            )
        payday = _parse_payday(_text(row, "expected_payday"), filepath, line_no)
        customers[cid] = Customer(customer_id=cid, name=name, expected_payday=payday)
    return customers


def load_payments(filepath: str) -> Dict[str, List[Payment]]:
    """Load payments.csv into a dict: customer_id -> list of Payments (all of them)."""
    required = ["payment_id", "customer_id", "status", "failure_reason", "amount"]
    payments: Dict[str, List[Payment]] = {}
    seen_payment_ids = set()

    for line_no, row in read_csv(filepath, required):
        pid = _text(row, "payment_id")
        cid = _text(row, "customer_id")
        status = _text(row, "status").lower()
        reason_raw = _text(row, "failure_reason") or None
        amount = _parse_amount(_text(row, "amount"), filepath, line_no)

        if not pid or not cid:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': payment_id and customer_id are required."
            )
        if pid in seen_payment_ids:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': duplicate payment_id '{pid}'."
            )
        seen_payment_ids.add(pid)

        if status not in PAYMENT_STATUSES:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': unknown payment status '{status}'. "
                f"Allowed: {list(PAYMENT_STATUSES)}."
            )

        failure_reason = reason_raw.lower() if reason_raw else None
        if status == "failed" and not failure_reason:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': failed payment '{pid}' must have a failure_reason."
            )
        if status == "successful" and failure_reason:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': successful payment '{pid}' "
                f"must not have a failure_reason."
            )
        if failure_reason and failure_reason not in KNOWN_FAILURE_REASONS:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': unknown failure_reason '{failure_reason}'. "
                f"Known: {list(KNOWN_FAILURE_REASONS)}. New reasons must be approved by the "
                f"business before use."
            )

        payments.setdefault(cid, []).append(
            Payment(
                payment_id=pid,
                customer_id=cid,
                status=status,
                failure_reason=failure_reason,
                amount=amount,
            )
        )
    return payments


def load_subscriptions(filepath: str) -> Dict[str, List[Subscription]]:
    """Load subscriptions.csv into a dict: customer_id -> list of Subscriptions (all of them)."""
    required = ["subscription_id", "customer_id", "service", "amount", "status"]
    subscriptions: Dict[str, List[Subscription]] = {}
    seen_ids = set()

    for line_no, row in read_csv(filepath, required):
        sid = _text(row, "subscription_id")
        cid = _text(row, "customer_id")
        service = _text(row, "service")
        status = _text(row, "status").lower()
        amount = _parse_amount(_text(row, "amount"), filepath, line_no)

        if not sid or not cid or not service:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': subscription_id, customer_id "
                f"and service are required."
            )
        if sid in seen_ids:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': duplicate subscription_id '{sid}'."
            )
        seen_ids.add(sid)
        if status not in SUBSCRIPTION_STATUSES:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': unknown subscription status '{status}'. "
                f"Allowed: {list(SUBSCRIPTION_STATUSES)}."
            )

        subscriptions.setdefault(cid, []).append(
            Subscription(
                subscription_id=sid,
                customer_id=cid,
                service=service,
                amount=amount,
                status=status,
            )
        )
    return subscriptions


def load_usage_history(filepath: str) -> Dict[Tuple[str, str], List[UsageRecord]]:
    """
    Load usage_history.csv into a dict: (customer_id, service_lower) -> list of UsageRecords.

    A customer may have usage for several services. The same month may appear
    only once per (customer, service) - a duplicate is ambiguous, so it is an error.
    """
    required = ["customer_id", "service", "month", "usage"]
    usage: Dict[Tuple[str, str], List[UsageRecord]] = {}
    seen_months = set()

    for line_no, row in read_csv(filepath, required):
        cid = _text(row, "customer_id")
        service = _text(row, "service")
        month = _text(row, "month")
        level = _text(row, "usage").lower()

        if not cid or not service or not month or not level:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': customer_id, service, month "
                f"and usage are required."
            )
        if level not in USAGE_LEVELS:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': unknown usage level '{level}'. "
                f"Allowed: {list(USAGE_LEVELS)}."
            )

        key = (cid, service.strip().lower())
        month_key = (cid, service.strip().lower(), month.strip().lower())
        if month_key in seen_months:
            raise IngestionError(
                f"Line {line_no} in '{filepath}': duplicate usage row for customer "
                f"'{cid}', service '{service}', month '{month}'."
            )
        seen_months.add(month_key)

        usage.setdefault(key, []).append(
            UsageRecord(customer_id=cid, service=service, month=month, usage=level)
        )
    return usage


def build_recovery_cases(
    customers: Dict[str, Customer],
    payments: Dict[str, List[Payment]],
    subscriptions: Dict[str, List[Subscription]],
    usage: Dict[Tuple[str, str], List[UsageRecord]],
) -> List[RecoveryCase]:
    """
    Combine the four loaded datasets into one RecoveryCase per customer.

    ALL payments and ALL subscriptions for each customer are kept.
    Usage records from every service the customer has are included.
    """
    # Group usage records by customer once, so nothing is dropped.
    usage_by_customer: Dict[str, List[UsageRecord]] = {}
    for (usage_customer_id, _service), records in usage.items():
        usage_by_customer.setdefault(usage_customer_id, []).extend(records)

    cases: List[RecoveryCase] = []
    for cid, customer in customers.items():
        cases.append(
            RecoveryCase(
                case_id=f"CASE-{cid}",
                customer=customer,
                payments=payments.get(cid, []),
                subscriptions=subscriptions.get(cid, []),
                usage_records=usage_by_customer.get(cid, []),
            )
        )
    return cases
