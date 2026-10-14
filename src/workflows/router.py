"""
router.py (Phase 2 + Phase 3 preparation)
Runs one step of each independent workflow for a case, and
persists the case state after every step.

Payment recovery and subscription retention do not gate each
other: each workflow makes its own decision from the case's
current state.
"""

from datetime import date
from typing import Dict, Optional

from ..case_store import CaseStore
from ..ingestion import build_recovery_cases
from ..models import RecoveryCase
from ..notifications import MockNotifier
from ..validation import Dataset
from .payment_recovery import payment_recovery_step
from .retention import retention_step


def run_recovery_step(
    case: RecoveryCase,
    today: date,
    audit,
    notifier: MockNotifier,
    customer_opt_out: bool = None,
    customer_wants_to_continue: bool = None,
    state_store: Optional[CaseStore] = None,
    drafter=None,
) -> dict:
    """
    Run one step of BOTH independent workflows.

    If a state_store is given, the case is saved AFTER the
    step completes, so workflow state survives a restart.

    `drafter` is optional (Phase 4): when given (and
    enabled), AI drafts are generated and stored as
    PENDING_REVIEW for human review. Drafts are never
    used for notifications - see ai/drafter.py.

    Returns a dict with the outcome of each workflow:
    {
        "payment_recovery": "...",
        "retention": "...",
    }
    """
    outcomes = {
        "payment_recovery": payment_recovery_step(
            case,
            today=today,
            audit=audit,
            notifier=notifier,
            customer_opt_out=customer_opt_out,
            drafter=drafter,
        ),
        "retention": retention_step(
            case,
            audit=audit,
            customer_wants_to_continue=customer_wants_to_continue,
            drafter=drafter,
        ),
    }
    if state_store is not None:
        state_store.save(case)
    return outcomes


def load_or_build_cases(dataset: Dataset, store: CaseStore) -> Dict[str, RecoveryCase]:
    """
    Build one case per customer from the CSVs, then overlay
    any saved state.

    If a saved state exists for a case, the SAVED state wins:
    notification counts, schedules, opt-outs and notification
    history are preserved - a count is never silently reset.
    If a saved state file is corrupted, CaseStoreError is
    raised so the caller can decide (never a silent reset).
    """
    fresh_cases = build_recovery_cases(
        dataset.customers, dataset.payments, dataset.subscriptions, dataset.usage
    )
    cases: Dict[str, RecoveryCase] = {}
    for case in fresh_cases:
        if store.exists(case.case_id):
            cases[case.case_id] = store.load(case.case_id)
        else:
            cases[case.case_id] = case
    return cases
