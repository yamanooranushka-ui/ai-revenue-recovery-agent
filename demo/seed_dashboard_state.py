"""
seed_dashboard_state.py (Phase 6)
Create demo state for the local dashboard. DEMO MODE - SYNTHETIC
DATA ONLY: this script only prepares local demo files.

Safety rules:
- NEVER overwrites existing state silently: if the state directory
  already contains *.json files, the script refuses and stops with a
  clear message. Existing state is replaced only when the explicit
  --reset-existing flag is deliberately passed (and only *.json
  files in the state directory are deleted).
- The original CSVs and the six legacy root files are read-only
  (verified by SHA-256 before and after).
- AI drafting runs through the exact Phase 4 path with a FAKE
  client: no network, no SDK, no credentials, no live model calls.
- Notifications are mock only (MockNotifier records, never sends).
- The audit log is append-only: --reset-existing never touches it.
- This script - not the dashboard - runs the workflow steps. The
  dashboard itself only reads files and confirms human decisions.

Run:
  python demo/seed_dashboard_state.py
  python demo/seed_dashboard_state.py --reset-existing
"""

import argparse
import hashlib
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ai.drafter import MessageDrafter
from src.audit import AuditLogger
from src.case_store import CaseStore
from src.ingestion import build_recovery_cases
from src.notifications import MockNotifier
from src.validation import load_dataset
from src.workflows.router import run_recovery_step

# Original CSVs + legacy root files: must be byte-identical afterwards.
PROTECTED_FILES = (
    "customers.csv",
    "payments.csv",
    "subscriptions.csv",
    "usage_history.csv",
    "models.py",
    "ingestion.py",
    "state_machine.py",
    "audit.py",
    "test_phase1.py",
    "test_phase2.py",
)

# Fixed synthetic execution date (NOT today): payday day 5 lands the
# insufficient-funds reminders on their first scheduled date, so the
# seeded demo is deterministic on any day the script is run.
SYNTHETIC_DATE = date(2026, 10, 5)

# Sentinel, never a real key (the fake client needs no key at all).
API_KEY_SENTINEL = "demo-fake-key-not-a-real-secret"

# Draft texts used by the fake client. Both pass validate_draft():
# <= 320 characters, no URLs/offers/promises, and no numbers other
# than day/count numbers, so they are valid for every amount.
PAYMENT_DRAFT_TEXT = (
    "Hello! Your recent payment could not be completed. "
    "Please complete the payment manually. This is reminder 1 of 3."
)
RETENTION_DRAFT_TEXT = (
    "Would you like to continue your subscription? Please reply yes or no."
)


class SeedRefused(Exception):
    """Existing state was found and --reset-existing was not given."""

    def __init__(self, state_dir: Path, files: List[Path]):
        self.state_dir = state_dir
        self.files = files
        names = "\n".join(f"  - {path.name}" for path in files)
        super().__init__(
            f"Refusing to overwrite existing state in {state_dir}:\n"
            f"{names}\n"
            "No file was modified. Re-run with --reset-existing to "
            "delete exactly these *.json files and seed fresh demo "
            "state (the append-only audit log is never reset)."
        )


class SeedError(Exception):
    """Seeding failed for another reason; nothing is left half-done."""


class _FakeGeminiClient:
    """Minimal stand-in for the official SDK client.

    No network, no SDK, no credentials: it only answers with local
    draft text, choosing the wording from the prompt's task.
    """

    def __init__(self):
        self.calls = []
        self.models = self  # the SDK nests model methods under client.models

    def generate_content(self, model=None, contents=None):
        self.calls.append({"model": model, "contents": contents})
        text = (
            RETENTION_DRAFT_TEXT
            if "lapsed subscription" in (contents or "")
            else PAYMENT_DRAFT_TEXT
        )
        return SimpleNamespace(text=text)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def existing_state_files(state_dir: Path) -> List[Path]:
    """Every saved state file that would be overwritten."""
    if not state_dir.is_dir():
        return []
    return sorted(state_dir.glob("*.json"))


def seed(
    csv_dir: Path,
    state_dir: Path,
    audit_path: Path,
    *,
    reset_existing: bool = False,
) -> dict:
    """Seed demo state into state_dir. Returns a summary dict.

    Raises SeedRefused when state files exist and reset_existing is
    not set - existing state is never overwritten silently.
    """
    csv_dir = Path(csv_dir)
    state_dir = Path(state_dir)
    audit_path = Path(audit_path)

    # Prove the protected files are untouched by the end.
    protected = {
        name: _sha256(PROJECT_ROOT / name) for name in PROTECTED_FILES
    }

    existing = existing_state_files(state_dir)
    if existing and not reset_existing:
        raise SeedRefused(state_dir, existing)
    deleted = []
    if existing:
        for path in existing:
            path.unlink()
            deleted.append(path.name)

    try:
        dataset = load_dataset(
            customers_path=str(csv_dir / "customers.csv"),
            payments_path=str(csv_dir / "payments.csv"),
            subscriptions_path=str(csv_dir / "subscriptions.csv"),
            usage_path=str(csv_dir / "usage_history.csv"),
        )
    except Exception as error:
        raise SeedError(f"The source CSVs could not be loaded: {error}") from error
    if dataset.report.errors:
        raise SeedError(
            "The source CSVs failed validation:\n" + dataset.report.summary()
        )

    cases = build_recovery_cases(
        dataset.customers, dataset.payments, dataset.subscriptions, dataset.usage
    )
    store = CaseStore(state_dir=str(state_dir))
    audit = AuditLogger(log_path=str(audit_path))
    notifier = MockNotifier()
    # Exact Phase 4 path, FAKE client: injected client + explicit
    # sentinel key/enabled/model, so no environment variable and no
    # live model call is ever involved.
    drafter = MessageDrafter(
        client=_FakeGeminiClient(),
        model="fake-demo-drafter",
        api_key=API_KEY_SENTINEL,
        enabled=True,
    )

    results = []
    for case in cases:
        outcomes = run_recovery_step(
            case,
            today=SYNTHETIC_DATE,
            audit=audit,
            notifier=notifier,
            state_store=store,
            drafter=drafter,
        )
        results.append(
            {
                "case_id": case.case_id,
                "payment_recovery": outcomes["payment_recovery"],
                "retention": outcomes["retention"],
                "notifications": case.notification_count,
                "pending_drafts": sorted(
                    kind
                    for kind, draft in case.pending_drafts.items()
                    if draft.status == "PENDING_REVIEW"
                ),
            }
        )

    for name, digest in protected.items():
        if _sha256(PROJECT_ROOT / name) != digest:
            raise SeedError(f"Protected file was modified: {name}")

    pending_total = sum(len(row["pending_drafts"]) for row in results)
    notification_total = sum(row["notifications"] for row in results)
    return {
        "state_dir": state_dir,
        "audit_path": audit_path,
        "deleted": deleted,
        "synthetic_date": SYNTHETIC_DATE.isoformat(),
        "cases": results,
        "pending_drafts": pending_total,
        "notifications": notification_total,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seed local demo state for the dashboard "
            "(DEMO MODE - synthetic data only)."
        )
    )
    parser.add_argument(
        "--csv-dir",
        type=Path,
        default=PROJECT_ROOT,
        help="Directory with the four read-only CSVs.",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=PROJECT_ROOT / "state",
        help="Directory for per-case JSON state files.",
    )
    parser.add_argument(
        "--audit-path",
        type=Path,
        default=PROJECT_ROOT / "audit" / "audit_log.jsonl",
        help="Append-only audit log (never reset by this script).",
    )
    parser.add_argument(
        "--reset-existing",
        action="store_true",
        help=(
            "Explicitly delete existing *.json state files in the "
            "state directory before seeding. Without this flag the "
            "script refuses to touch existing state."
        ),
    )
    args = parser.parse_args(argv)

    try:
        summary = seed(
            args.csv_dir,
            args.state_dir,
            args.audit_path,
            reset_existing=args.reset_existing,
        )
    except SeedRefused as error:
        print(str(error), file=sys.stderr)
        return 2
    except SeedError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if summary["deleted"]:
        print(
            "Deleted existing state file(s) (--reset-existing): "
            + ", ".join(summary["deleted"])
        )
    for row in summary["cases"]:
        print(
            f"  {row['case_id']}: recovery={row['payment_recovery']!r} "
            f"retention={row['retention']!r} "
            f"notifications={row['notifications']} "
            f"pending_drafts={row['pending_drafts']}"
        )
    print(
        f"Seeded {len(summary['cases'])} case(s) into {summary['state_dir']} "
        f"at synthetic date {summary['synthetic_date']}."
    )
    print(f"Pending drafts awaiting human review: {summary['pending_drafts']}")
    print(
        f"Audit log (append-only, never reset): {summary['audit_path']}"
    )
    print(
        "DEMO MODE - synthetic data only. No SMS, no payments, "
        "no live AI calls."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
