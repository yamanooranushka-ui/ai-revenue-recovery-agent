"""
usage_evaluation.py (Phase 2)
Four-month usage evaluation for lapsed subscriptions.

Evaluates the FOUR MOST RECENT months available for one
customer + service. Returns:
- 'CONSISTENTLY_HIGH'  - the four most recent months are consecutive
  and all 'high'.
- 'NOT_YET_SPECIFIED'  - insufficient history, ambiguous data,
  missing months, or low/mixed usage. No reliable evaluation.
"""

from typing import Dict, List, Optional

from .models import UsageRecord

MONTH_NUMBERS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def evaluate_usage(records: List[UsageRecord]) -> str:
    """Evaluate usage over the four most recent months available."""
    # month number -> usage level
    by_month: Dict[int, str] = {}
    for record in records:
        month_number = MONTH_NUMBERS.get(record.month.strip().lower())
        if month_number is None:
            return "NOT_YET_SPECIFIED"  # unrecognized month name -> ambiguous
        level = record.usage.strip().lower()
        if month_number in by_month and by_month[month_number] != level:
            return "NOT_YET_SPECIFIED"  # conflicting records for the same month
        by_month[month_number] = level

    if len(by_month) < 4:
        return "NOT_YET_SPECIFIED"  # insufficient history

    # The four most recent distinct months present in the data
    most_recent = sorted(by_month, reverse=True)[:4]
    if most_recent[0] - most_recent[3] != 3:
        return "NOT_YET_SPECIFIED"  # missing months in between -> not consecutive

    if all(by_month[month] == "high" for month in most_recent):
        return "CONSISTENTLY_HIGH"
    return "NOT_YET_SPECIFIED"  # low or mixed usage
