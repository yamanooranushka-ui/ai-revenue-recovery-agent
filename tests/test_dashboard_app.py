"""
test_dashboard_app.py (Phase 6)

End-to-end tests of the local demo dashboard through Streamlit's
AppTest, plus the seed script's overwrite protection.

Proved here:
- The interface is clearly labelled DEMO MODE - SYNTHETIC DATA ONLY
  and never renders credential-like values from the environment.
- Rerunning the app without clicks (any section, any number of
  times) never modifies state files or the audit log.
- Approve/Reject require an explicit two-step confirmation; the
  first click alone changes nothing on disk.
- Duplicate submissions and stale confirmations are refused with a
  clear error; approvals persist across app restarts.
- Corrupt saved state or audit data produces a clear error and no
  fabricated/emptied files.
- The seed script refuses to overwrite existing state unless
  --reset-existing is deliberately passed, and never touches the
  original CSVs or the six legacy root files.

DEMO MODE: temp directories only, fake drafter only, no network,
no live AI calls, no SMS, no payments.
"""

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("streamlit", reason="streamlit is declared in requirements.txt")
from streamlit.testing.v1 import AppTest  # noqa: E402

from dashboard import actions  # noqa: E402
from demo.seed_dashboard_state import main as seed_main  # noqa: E402
from demo.seed_dashboard_state import seed  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = PROJECT_ROOT / "dashboard" / "app.py"
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_hashes() -> dict:
    return {name: _sha256(PROJECT_ROOT / name) for name in PROTECTED_FILES}


def _hash_workspace(state_dir: Path, audit_path: Path) -> str:
    """Combined hash of every state file and the audit log."""
    digest = hashlib.sha256()
    for path in sorted(state_dir.glob("*.json")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    if audit_path.is_file():
        digest.update(audit_path.read_bytes())
    return digest.hexdigest()


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """Seed demo state once (fake drafter, synthetic date, temp dirs)."""
    root = tmp_path_factory.mktemp("dashboard_seed")
    state_dir = root / "state"
    audit_path = root / "audit" / "audit_log.jsonl"
    summary = seed(PROJECT_ROOT, state_dir, audit_path)
    return {"state_dir": state_dir, "audit_path": audit_path, "summary": summary}


@pytest.fixture
def workspace(seeded, tmp_path):
    """A fresh copy of the seeded state, so tests never share mutations."""
    state_dir = tmp_path / "state"
    audit_path = tmp_path / "audit_log.jsonl"
    shutil.copytree(seeded["state_dir"], state_dir)
    shutil.copy(seeded["audit_path"], audit_path)
    return SimpleNamespace(state_dir=state_dir, audit_path=audit_path)


def _make_app(workspace, monkeypatch, audit_path=None):
    monkeypatch.setenv("DASHBOARD_STATE_DIR", str(workspace.state_dir))
    monkeypatch.setenv("DASHBOARD_CSV_DIR", str(PROJECT_ROOT))
    monkeypatch.setenv(
        "DASHBOARD_AUDIT_PATH", str(audit_path or workspace.audit_path)
    )
    return AppTest.from_file(str(APP_PATH), default_timeout=15)


def _pending_draft(state_dir: Path):
    """Find one (case_id, kind) pair whose draft awaits review."""
    for path in sorted(state_dir.glob("*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        for kind, draft in (saved.get("pending_drafts") or {}).items():
            if draft and draft.get("status") == "PENDING_REVIEW":
                return path.stem, kind
    raise AssertionError("seeded state contains no pending draft")


def _button_keys(at) -> list:
    return [button.key for button in at.button]


def _rendered_text(at) -> str:
    parts = []
    for name in (
        "title", "markdown", "warning", "info", "error", "success",
        "caption", "subheader", "code", "exception",
    ):
        for element in getattr(at, name, []):
            parts.append(
                str(getattr(element, "value", getattr(element, "body", "")))
            )
    for element in at.metric:
        parts.append(str(element.label) + str(getattr(element, "value", "")))
    for element in at.dataframe:
        parts.append(str(element.value))
    for element in at.button:
        parts.append(str(element.label))
    for element in at.radio:
        parts.append(str(element.label) + str(element.options))
    for element in at.selectbox:
        parts.append(str(element.label) + str(element.options))
    for element in at.text_input:
        parts.append(str(element.label))
    return "\n".join(parts)


def _go_to(at, section: str) -> None:
    at.sidebar.radio[0].set_value(section)
    at.run()


# --- Rendering, labelling, read-only behaviour ------------------------


def test_banner_labels_demo_mode_and_no_secret_renders(workspace, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "SUPERSECRET_VALUE_12345")
    at = _make_app(workspace, monkeypatch)
    at.run()
    assert not at.exception
    assert not at.error
    rendered = _rendered_text(at)
    assert "DEMO MODE" in rendered
    assert "SYNTHETIC DATA ONLY" in rendered
    # The environment key can never appear: the dashboard never reads it.
    assert "SUPERSECRET_VALUE_12345" not in rendered


def test_overview_renders_every_case(workspace, monkeypatch):
    at = _make_app(workspace, monkeypatch)
    at.run()
    assert not at.exception
    assert not at.error
    assert len(at.metric) == 3
    assert len(at.dataframe) == 1
    assert len(at.dataframe[0].value) == 5  # one row per synthetic customer


def test_reruns_without_clicks_never_modify_state_or_audit(workspace, monkeypatch):
    at = _make_app(workspace, monkeypatch)
    at.run()
    before = _hash_workspace(workspace.state_dir, workspace.audit_path)
    for section in ("Cases", "Drafts", "Audit", "Overview"):
        _go_to(at, section)
        assert not at.exception, f"exception while rendering {section}"
        assert not at.error, f"error while rendering {section}"
    after = _hash_workspace(workspace.state_dir, workspace.audit_path)
    assert before == after


# --- Two-step approval -------------------------------------------------


def _approve_through_ui(at, workspace, case_id, kind):
    """Click Approve, then Confirm. Returns hashes between the two clicks."""
    _go_to(at, "Drafts")
    approve_key = f"approve__{case_id}__{kind}"
    assert approve_key in _button_keys(at), "approve button not rendered"
    at.button(approve_key).click()
    at.run()
    assert not at.exception
    between = _hash_workspace(workspace.state_dir, workspace.audit_path)
    assert "confirm_action_btn" in _button_keys(at), "confirmation panel missing"
    at.button("confirm_action_btn").click()
    at.run()
    assert not at.exception
    return between


def test_approve_requires_explicit_confirmation_and_persists(workspace, monkeypatch):
    case_id, kind = _pending_draft(workspace.state_dir)
    at = _make_app(workspace, monkeypatch)
    at.run()
    before = _hash_workspace(workspace.state_dir, workspace.audit_path)

    between = _approve_through_ui(at, workspace, case_id, kind)
    assert between == before, "the first click alone must change nothing"
    assert len(at.success) == 1
    assert "Approved draft" in at.success[0].value

    saved = json.loads(
        (workspace.state_dir / f"{case_id}.json").read_text(encoding="utf-8")
    )
    assert saved["pending_drafts"][kind]["status"] == "APPROVED"
    assert saved["approved_messages"][kind]
    audit_text = workspace.audit_path.read_text(encoding="utf-8")
    assert audit_text.count('"event_type": "HUMAN_APPROVAL"') == 1
    assert "Testy" not in audit_text  # no customer names in the audit log

    # Persistence: a brand-new app instance shows the approved wording.
    fresh = _make_app(workspace, monkeypatch)
    fresh.run()
    _go_to(fresh, "Drafts")
    approved_table = fresh.dataframe[0].value
    assert len(approved_table) == 1
    assert approved_table.iloc[0]["Wording"] == saved["approved_messages"][kind]


def test_cancel_leaves_state_and_audit_untouched(workspace, monkeypatch):
    case_id, kind = _pending_draft(workspace.state_dir)
    at = _make_app(workspace, monkeypatch)
    at.run()
    before = _hash_workspace(workspace.state_dir, workspace.audit_path)

    _go_to(at, "Drafts")
    at.button(f"approve__{case_id}__{kind}").click()
    at.run()
    at.button("cancel_action_btn").click()
    at.run()
    assert not at.exception
    assert any("Cancelled" in element.value for element in at.info)
    assert _hash_workspace(workspace.state_dir, workspace.audit_path) == before

    saved = json.loads(
        (workspace.state_dir / f"{case_id}.json").read_text(encoding="utf-8")
    )
    assert saved["pending_drafts"][kind]["status"] == "PENDING_REVIEW"
    assert saved["approved_messages"] == {}


def test_duplicate_submission_after_approval_is_refused(workspace, monkeypatch):
    case_id, kind = _pending_draft(workspace.state_dir)
    at = _make_app(workspace, monkeypatch)
    at.run()
    _approve_through_ui(at, workspace, case_id, kind)

    # The same confirmation arrives a second time (double click, retry, ...).
    at.session_state["confirm_action"] = ("approve", case_id, kind)
    at.run()
    assert "confirm_action_btn" in _button_keys(at)
    at.button("confirm_action_btn").click()
    at.run()
    assert any(
        "only a PENDING_REVIEW draft" in element.value for element in at.error
    )
    audit_text = workspace.audit_path.read_text(encoding="utf-8")
    assert audit_text.count('"event_type": "HUMAN_APPROVAL"') == 1


def test_stale_confirmation_after_external_rejection_is_refused(
    workspace, monkeypatch
):
    case_id, kind = _pending_draft(workspace.state_dir)
    at = _make_app(workspace, monkeypatch)
    at.run()
    _go_to(at, "Drafts")
    at.button(f"approve__{case_id}__{kind}").click()
    at.run()
    assert "confirm_action_btn" in _button_keys(at)

    # Meanwhile, another reviewer rejects the draft through the API.
    actions.reject_draft(
        case_id,
        kind,
        state_dir=workspace.state_dir,
        audit_path=workspace.audit_path,
        reason="handled elsewhere",
    )

    at.button("confirm_action_btn").click()
    at.run()
    assert any(
        f"No draft of kind '{kind}'" in element.value for element in at.error
    )
    saved = json.loads(
        (workspace.state_dir / f"{case_id}.json").read_text(encoding="utf-8")
    )
    assert kind not in saved["pending_drafts"]
    assert saved["approved_messages"] == {}


def test_reject_flow_with_reason_persists(workspace, monkeypatch):
    case_id, kind = _pending_draft(workspace.state_dir)
    at = _make_app(workspace, monkeypatch)
    at.run()
    before = _hash_workspace(workspace.state_dir, workspace.audit_path)

    _go_to(at, "Drafts")
    at.button(f"reject__{case_id}__{kind}").click()
    at.run()
    reason_key = f"reject_reason__{case_id}__{kind}"
    assert reason_key in [element.key for element in at.text_input]
    at.text_input(reason_key).set_value("Not on brand for the demo.")
    at.button("confirm_action_btn").click()
    at.run()
    assert not at.exception
    assert len(at.success) == 1
    assert "Rejected draft" in at.success[0].value
    assert _hash_workspace(workspace.state_dir, workspace.audit_path) != before

    saved = json.loads(
        (workspace.state_dir / f"{case_id}.json").read_text(encoding="utf-8")
    )
    assert kind not in saved["pending_drafts"]
    assert saved["approved_messages"] == {}
    audit_text = workspace.audit_path.read_text(encoding="utf-8")
    assert audit_text.count('"event_type": "HUMAN_APPROVAL"') == 1
    assert "Not on brand for the demo." in audit_text


# --- Corruption is a clear error, never a silent reset -----------------


def test_corrupt_saved_state_shows_clear_error_and_renders_nothing(
    workspace, monkeypatch
):
    corrupt = sorted(workspace.state_dir.glob("*.json"))[0]
    corrupt.write_text("{broken", encoding="utf-8")
    at = _make_app(workspace, monkeypatch)
    at.run()
    assert not at.exception  # handled, not a crash
    assert len(at.error) == 1
    message = at.error[0].value
    assert "Saved state could not be read" in message
    assert "Refusing to reset or fabricate state" in message
    assert len(at.dataframe) == 0  # nothing is rendered or invented
    assert corrupt.read_text(encoding="utf-8") == "{broken"  # never reset


def test_missing_audit_log_shows_notice(workspace, monkeypatch, tmp_path):
    at = _make_app(workspace, monkeypatch, audit_path=tmp_path / "absent.jsonl")
    at.run()
    _go_to(at, "Audit")
    assert not at.exception
    assert not at.error
    assert any("No audit log found" in element.value for element in at.info)


def test_corrupt_audit_log_shows_clear_error(workspace, monkeypatch, tmp_path):
    bad_audit = tmp_path / "bad_audit.jsonl"
    bad_audit.write_text("{broken", encoding="utf-8")
    at = _make_app(workspace, monkeypatch, audit_path=bad_audit)
    at.run()
    _go_to(at, "Audit")
    assert not at.exception
    assert len(at.error) == 1
    assert "could not be read" in at.error[0].value
    assert len(at.dataframe) == 0  # no partial or invented history
    assert bad_audit.read_text(encoding="utf-8") == "{broken"


def test_audit_section_lists_entries(workspace, monkeypatch):
    at = _make_app(workspace, monkeypatch)
    at.run()
    _go_to(at, "Audit")
    assert not at.error
    assert len(at.metric) == 1
    assert len(at.dataframe) == 1
    assert len(at.dataframe[0].value) > 0


# --- Seed script protection -------------------------------------------


def test_seed_refuses_to_overwrite_existing_state(tmp_path, capsys):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    sentinel = state_dir / "CASE-C001.json"
    sentinel.write_text('{"existing": "sentinel"}', encoding="utf-8")

    code = seed_main(
        [
            "--csv-dir", str(PROJECT_ROOT),
            "--state-dir", str(state_dir),
            "--audit-path", str(tmp_path / "audit" / "audit_log.jsonl"),
        ]
    )
    assert code == 2
    captured = capsys.readouterr()
    assert "Refusing to overwrite existing state" in captured.err
    assert "--reset-existing" in captured.err
    assert sentinel.read_text(encoding="utf-8") == '{"existing": "sentinel"}'
    assert not (tmp_path / "audit").exists()  # nothing at all was written


def test_seed_reset_existing_is_explicit_and_leaves_csvs_untouched(
    tmp_path, capsys
):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # A state file for a case the seeder does NOT own: after the reset it
    # must be gone for good, never silently recreated.
    sentinel = state_dir / "CASE-ZZ99.json"
    sentinel.write_text('{"existing": "sentinel"}', encoding="utf-8")
    before = _protected_hashes()

    code = seed_main(
        [
            "--csv-dir", str(PROJECT_ROOT),
            "--state-dir", str(state_dir),
            "--audit-path", str(tmp_path / "audit" / "audit_log.jsonl"),
            "--reset-existing",
        ]
    )
    assert code == 0
    assert "Deleted existing state file(s)" in capsys.readouterr().out
    assert not sentinel.exists()
    assert len(list(state_dir.glob("*.json"))) == 5  # the five demo cases
    # The old sentinel content was deleted, not reused.
    saved = json.loads(
        (state_dir / "CASE-C001.json").read_text(encoding="utf-8")
    )
    assert saved["schema_version"] == 1
    assert _protected_hashes() == before  # CSVs + legacy files identical


def test_seed_creates_pending_drafts_and_touches_nothing_else(tmp_path):
    before = _protected_hashes()
    state_dir = tmp_path / "state"
    audit_path = tmp_path / "audit" / "audit_log.jsonl"
    summary = seed(PROJECT_ROOT, state_dir, audit_path)
    assert summary["pending_drafts"] >= 4
    assert summary["notifications"] >= 4
    assert audit_path.is_file()
    assert len(list(state_dir.glob("*.json"))) == 5
    assert _protected_hashes() == before
