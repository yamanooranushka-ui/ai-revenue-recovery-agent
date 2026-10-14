"""Path configuration for the local demo dashboard.

Only path-related environment variables are honoured:
DASHBOARD_STATE_DIR, DASHBOARD_CSV_DIR, DASHBOARD_AUDIT_PATH.
Their sole purpose is to let tests point the dashboard at
temporary directories.

No credential-related environment variable is ever read here,
and no network endpoint is ever configured.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def state_dir() -> Path:
    """Directory holding the per-case JSON state files."""
    override = os.environ.get("DASHBOARD_STATE_DIR")
    return Path(override) if override else PROJECT_ROOT / "state"


def csv_dir() -> Path:
    """Directory holding the four read-only source CSVs."""
    override = os.environ.get("DASHBOARD_CSV_DIR")
    return Path(override) if override else PROJECT_ROOT


def audit_path() -> Path:
    """Path of the append-only JSON-lines audit log."""
    override = os.environ.get("DASHBOARD_AUDIT_PATH")
    return Path(override) if override else PROJECT_ROOT / "audit" / "audit_log.jsonl"
