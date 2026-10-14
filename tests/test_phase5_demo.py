"""
test_phase5_demo.py (Phase 5)

Regression wrapper for the end-to-end demonstration.

`run_demo()` performs every Phase 5 proof with hard assertions
using synthetic data only, a fake Gemini client, and a
temporary directory for all generated CSVs, case state and
audit output. These tests run it once and re-check the
reported outcomes so the demonstration stays green as the
project evolves - without any live API call, SMS, payment
processing, or writes outside pytest's temp area.
"""

import pytest

from demo.phase5_demo import run_demo


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """Run the full demonstration once for this module."""
    root = tmp_path_factory.mktemp("phase5_demo")
    summary = run_demo(root)
    summary["root"] = root
    return summary


def test_deterministic_rules_handle_eligible_failed_payment(demo):
    """Proof 1: schedule, never-early guard, limits, stop conditions."""
    assert demo["dataset_errors"] == 0
    assert demo["scheduled_dates"] == [
        "2026-01-10", "2026-01-12", "2026-01-14",
    ]
    assert demo["early_runs_blocked"] is True
    assert demo["notifications_created"] == 3
    assert demo["final_state"] == "CLOSED_MAX_NOTIFICATIONS_REACHED"
    assert demo["stop_condition_events"] == 1  # logged exactly once
    assert demo["payment_still_failed"] is True  # never auto-marked paid


def test_gemini_drafting_runs_through_phase4_path(demo):
    """Proof 2: drafting happened via the fake client only."""
    assert demo["drafting_calls"] == 3
    assert demo["drafting_audit_entries"] == 3
    assert demo["live_api_calls"] == 0
    assert demo["google_genai_imported_during_demo"] is False


def test_draft_saved_as_pending_review(demo):
    """Proof 3: the generated draft waits for human review."""
    assert demo["pending_status_after_first_draft"] == "PENDING_REVIEW"
    assert demo["human_approval_events"] == 1


def test_unapproved_draft_never_reaches_notifier(demo):
    """Proof 4 + 5: template before approval, approved wording after."""
    assert demo["first_message_used_template"] is True
    assert demo["second_message_used_approved"] is True
    assert demo["third_message_used_approved"] is True
    assert demo["message_sources"] == {
        1: "approved_template",
        2: "approved_message",
        3: "approved_message",
    }


def test_persistence_preserves_state_across_reload(demo):
    """Proof: counts, schedule and approval survive a restart."""
    assert demo["reload_notification_count"] == 1
    assert demo["reload_preserved_approved_wording"] is True


def test_notification_limit_enforced(demo):
    """Proof: the confirmed 3-SMS cap closes the case."""
    assert demo["notifications_created"] == 3
    assert demo["final_state"] == "CLOSED_MAX_NOTIFICATIONS_REACHED"


def test_audit_metadata_without_leaks(demo):
    """Proof 6: metadata present; no key/draft/message/name leaked."""
    assert demo["audit_leak_free"] is True
    assert demo["audit_entries"] >= 8
    assert demo["drafting_audit_entries"] >= 1
    assert demo["human_approval_events"] == 1


def test_no_external_side_effects(demo):
    """Proofs 7 + 8: nothing overwritten, nothing sent, no live calls."""
    assert demo["protected_files_unchanged"] is True
    assert demo["project_root_unchanged"] is True
    assert demo["artifacts_inside_temp"] is True
    assert demo["notification_statuses"] == ["CREATED"]  # never delivered
    assert demo["messages_delivered"] == 0
    assert demo["payment_operations"] == 0
    # The artifacts really exist, inside pytest's temp area only.
    root = demo["root"]
    assert (root / "csvs" / "customers.csv").is_file()
    assert (root / "state" / "CASE-DEMO001.json").is_file()
    assert (root / "audit" / "phase5_audit.jsonl").is_file()
