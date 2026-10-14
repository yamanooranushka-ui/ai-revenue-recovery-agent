"""
validation.py (Phase 1)
Cross-file validation: do the four CSVs agree with each other?

Also provides load_dataset(), a one-call loader for the whole project.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .ingestion import (
    load_customers,
    load_payments,
    load_subscriptions,
    load_usage_history,
)
from .models import Customer, Payment, Subscription, UsageRecord


@dataclass
class ValidationReport:
    """Collects problems found between the CSV files."""

    errors: List[str] = field(default_factory=list)  # Must be fixed before use
    warnings: List[str] = field(default_factory=list)  # Suspicious, but kept

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        lines = [f"Validation: {len(self.errors)} error(s), {len(self.warnings)} warning(s)"]
        for error in self.errors:
            lines.append(f"  ERROR  : {error}")
        for warning in self.warnings:
            lines.append(f"  WARNING: {warning}")
        return "\n".join(lines)


@dataclass
class Dataset:
    """Everything loaded from the four CSVs, plus the validation report."""

    customers: Dict[str, Customer]
    payments: Dict[str, List[Payment]]
    subscriptions: Dict[str, List[Subscription]]
    usage: Dict[Tuple[str, str], List[UsageRecord]]
    report: ValidationReport


def validate_relations(
    customers: Dict[str, Customer],
    payments: Dict[str, List[Payment]],
    subscriptions: Dict[str, List[Subscription]],
    usage: Dict[Tuple[str, str], List[UsageRecord]],
) -> ValidationReport:
    """
    Check that every record points at a real customer.

    - A payment, subscription or usage row referencing an unknown customer
      is an ERROR (the data is broken).
    - Usage for a service the customer has no subscription for is a WARNING
      (it can legitimately happen for a recently lapsed service).
    """
    report = ValidationReport()

    for cid, records in payments.items():
        if cid not in customers:
            ids = ", ".join(p.payment_id for p in records)
            report.errors.append(
                f"payments.csv references unknown customer_id '{cid}' (payments: {ids})."
            )

    for cid, records in subscriptions.items():
        if cid not in customers:
            ids = ", ".join(s.subscription_id for s in records)
            report.errors.append(
                f"subscriptions.csv references unknown customer_id '{cid}' "
                f"(subscriptions: {ids})."
            )

    for (cid, service), records in usage.items():
        if cid not in customers:
            report.errors.append(
                f"usage_history.csv references unknown customer_id '{cid}' "
                f"(service: {service}, rows: {len(records)})."
            )
        else:
            known_services = {s.service.strip().lower() for s in subscriptions.get(cid, [])}
            if known_services and service not in known_services:
                display_service = records[0].service if records else service
                report.warnings.append(
                    f"usage_history.csv has usage for customer '{cid}' on service "
                    f"'{display_service}', which is not in their subscriptions. "
                    f"Kept, but please check."
                )
    return report


def load_dataset(
    customers_path: str,
    payments_path: str,
    subscriptions_path: str,
    usage_path: str,
) -> Dataset:
    """
    Load all four CSVs and check their relationships.
    Raises IngestionError if any file is missing or contains invalid fields.
    """
    customers = load_customers(customers_path)
    payments = load_payments(payments_path)
    subscriptions = load_subscriptions(subscriptions_path)
    usage = load_usage_history(usage_path)
    report = validate_relations(customers, payments, subscriptions, usage)
    return Dataset(
        customers=customers,
        payments=payments,
        subscriptions=subscriptions,
        usage=usage,
        report=report,
    )
