"""DEMO MODE - SYNTHETIC DATA ONLY.

Local, read-only dashboard for the AI Revenue Recovery Agent.

Safety properties enforced by this file (and verified by tests):
- Rendering NEVER runs a recovery workflow; it only reads files.
- The only state changes are the two-step confirmed Approve /
  Reject actions on the Drafts page, which call dashboard/actions.py.
- No secrets are read or displayed; no network calls are made.
- Local only: no deployment, no real customer data, no
  authentication, no live AI calls, no SMS, no payments.

Run with:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from dashboard import actions, config, data

DEMO_BANNER = "DEMO MODE — SYNTHETIC DATA ONLY"
SECTIONS = ["Overview", "Cases", "Drafts", "Audit"]


def _table(rows, empty_message: str) -> None:
    """Render a list of dicts as a table (or a clear empty note)."""
    if rows:
        st.dataframe(rows, hide_index=True)
    else:
        st.info(empty_message)


def render_overview(cases, notice: str) -> None:
    """High-level counts and one row per case."""
    st.subheader("Overview")
    pending = sum(
        1
        for case in cases.values()
        for draft in case.pending_drafts.values()
        if draft.status == "PENDING_REVIEW"
    )
    recorded = sum(len(case.notifications) for case in cases.values())
    metric_cols = st.columns(3)
    metric_cols[0].metric("Cases", len(cases))
    metric_cols[1].metric("Drafts pending review", pending)
    metric_cols[2].metric("Reminders recorded (mock)", recorded)
    _table(data.overview_rows(cases), "No cases to show.")


def render_cases(cases) -> None:
    """One case at a time: payments, subscriptions, schedule, notifications."""
    st.subheader("Customer cases")
    case_id = st.selectbox("Case", sorted(cases.keys()))
    case = cases[case_id]
    detail = data.case_detail(case)
    limit = case.max_notifications if case.max_notifications is not None else "-"
    st.markdown(
        f"**{case.case_id}** - {case.customer.name} "
        f"(payday: day {case.customer.expected_payday})  \n"
        f"**Payment recovery:** {case.payment_recovery_state.value}  |  "
        f"**Retention:** {case.retention_state.value}  |  "
        f"**Reminders:** {case.notification_count}/{limit}  |  "
        f"**Opted out:** {'Yes' if case.opted_out else 'No'}"
    )
    st.markdown("##### Payments (read-only)")
    _table(detail["payments"], "No payments for this case.")
    st.markdown("##### Subscriptions (read-only)")
    _table(detail["subscriptions"], "No subscriptions for this case.")
    st.markdown("##### Reminder schedule (mock)")
    _table(
        detail["schedule"],
        "No schedule for this case (technical errors have no reminder schedule).",
    )
    st.markdown("##### Notifications recorded (never sent)")
    _table(detail["notifications"], "No notifications recorded for this case.")
    st.markdown("##### Human-approved wording in use")
    _table(detail["approved_messages"], "No human-approved wording for this case.")


def render_drafts(cases, csv_dir, state_dir, audit_path) -> None:
    """Review AI drafts and approve/reject them behind a two-step confirmation."""
    st.subheader("AI drafts (suggestion-only, human approval required)")
    rows = data.pending_draft_rows(cases)

    # Step 1: one Approve/Reject button pair per pending draft.
    # Clicking a button ONLY records what to confirm; nothing changes yet.
    pending = [row for row in rows if row["Status"] == "PENDING_REVIEW"]
    for row in pending:
        st.markdown(
            f"**{row['Case']}** - {row['Kind']} - "
            f"{row['Characters']} characters - model `{row['Model']}`"
        )
        st.code(row["Text"], language=None)
        approve_col, reject_col = st.columns(2)
        if approve_col.button("Approve draft", key=f"approve__{row['Case']}__{row['Kind']}"):
            st.session_state["confirm_action"] = ("approve", row["Case"], row["Kind"])
        if reject_col.button("Reject draft", key=f"reject__{row['Case']}__{row['Kind']}"):
            st.session_state["confirm_action"] = ("reject", row["Case"], row["Kind"])

    if not pending:
        st.info("No drafts are pending review.")

    # Step 2: confirmation panel. The state change happens ONLY here,
    # after an explicit Confirm click, through dashboard/actions.py.
    action = st.session_state.get("confirm_action")
    if action is not None:
        operation, case_id, kind = action
        case = cases.get(case_id)
        draft = case.pending_drafts.get(kind) if case is not None else None
        reason_key = f"reject_reason__{case_id}__{kind}"
        if operation == "approve":
            st.warning(
                f"Confirm: APPROVE the `{kind}` draft for {case_id}? "
                "Approval records human-reviewed wording in the audit log. "
                "It sends nothing by itself."
            )
        else:
            st.warning(
                f"Confirm: REJECT the `{kind}` draft for {case_id}? "
                "The deterministic template stays in use."
            )
        if draft is not None:
            st.code(draft.text, language=None)
        else:
            st.info("This draft no longer exists in the saved state.")
        reason = None
        if operation == "reject":
            reason = st.text_input("Reason (optional)", key=reason_key)
        confirm_col, cancel_col = st.columns(2)
        confirmed = confirm_col.button("Confirm", key="confirm_action_btn")
        cancelled = cancel_col.button("Cancel", key="cancel_action_btn")
        if cancelled:
            st.session_state.pop("confirm_action", None)
            st.session_state.pop(reason_key, None)
            st.info("Cancelled - nothing was changed.")
        elif confirmed:
            st.session_state.pop("confirm_action", None)
            try:
                if operation == "approve":
                    result = actions.approve_draft(
                        case_id, kind, state_dir=state_dir, audit_path=audit_path
                    )
                else:
                    result = actions.reject_draft(
                        case_id,
                        kind,
                        state_dir=state_dir,
                        audit_path=audit_path,
                        reason=(reason or None),
                    )
                st.success(result.message)
                # Reload from disk so the tables below show the new truth.
                cases, _ = data.load_cases(csv_dir, state_dir)
                rows = data.pending_draft_rows(cases)
            except (actions.DashboardActionError, data.DashboardDataError) as error:
                st.error(str(error))
            finally:
                st.session_state.pop(reason_key, None)

    # Step 3: tables reflect the (possibly just-updated) saved state.
    st.markdown("#### Approved wording (in use)")
    approved_rows = [
        {"Case": case_id, "Kind": kind, "Wording": text}
        for case_id, case in sorted(cases.items())
        for kind, text in sorted(case.approved_messages.items())
    ]
    _table(approved_rows, "No human-approved wording yet; templates remain in use.")

    st.markdown("#### Draft history (reviewed)")
    history_rows = [
        {key: value for key, value in row.items()}
        for row in rows
        if row["Status"] != "PENDING_REVIEW"
    ]
    _table(history_rows, "No reviewed drafts yet.")


def render_audit() -> None:
    """View the append-only audit log (read-only)."""
    st.subheader("Audit log (append-only)")
    entries, notice, error = data.load_audit(config.audit_path())
    if error:
        st.error(error)
        return
    if notice:
        st.info(notice)
        return
    st.metric("Entries", len(entries))
    _table(data.audit_rows(entries), "The audit log is empty.")


def main() -> None:
    st.set_page_config(
        page_title="AI Revenue Recovery Agent (Demo)",
        page_icon="🧭",
        layout="wide",
    )
    st.title("AI Revenue Recovery Agent")
    st.warning(
        f"**{DEMO_BANNER}** - local demo over synthetic data. "
        "No real customers, no SMS, no payments, no live AI calls. "
        "Rendering only reads files; the only controls that change "
        "anything are the two-step confirmed Approve / Reject actions "
        "on the Drafts page."
    )

    section = st.sidebar.radio("Section", SECTIONS, key="section")
    st.sidebar.caption("Runs locally. Nothing here contacts the network.")

    if section == "Audit":
        render_audit()
    else:
        try:
            cases, notice = data.load_cases(config.csv_dir(), config.state_dir())
        except data.DashboardDataError as error:
            st.error(str(error))
            st.stop()
        if notice:
            st.info(notice)
        if section == "Overview":
            render_overview(cases, notice)
        elif section == "Cases":
            render_cases(cases)
        else:
            render_drafts(
                cases, config.csv_dir(), config.state_dir(), config.audit_path()
            )

    st.caption(
        "DEMO MODE - synthetic data only. No real SMS, payments, or live AI "
        "calls. Workflows are never run from this interface."
    )


if __name__ == "__main__":
    main()
