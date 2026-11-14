import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "agensi" / "coworker-charter"
SCRIPTS = SKILL / "scripts"
REFS = SKILL / "references"

BASE = {
    "charter_version": 1,
    "role": "Test coworker that files and sends demo records for the unit tests.",
    "agent_name": "test-coworker",
    "owner": {"name": "Owen Owner", "contact": "owen@example.com"},
    "approvers": [{"name": "Ada Approver", "contact": "ada@example.com"}],
    "escalation": {"contact": "oncall@example.com", "when": ["a deny the person disagrees with"]},
    "tools": {
        "r.read": {"tier": "read", "description": "Read demo records"},
        "w.write": {"tier": "write", "description": "Write demo records"},
        "s.send": {"tier": "send", "description": "Send a demo message"},
        "p.pay": {"tier": "spend", "description": "Pay a demo invoice"},
        "d.remove": {"tier": "delete", "description": "Delete a demo record"},
        "u.publish": {"tier": "publish", "description": "Publish a demo page"},
    },
    "data_boundaries": {
        "may_access": ["demo records"],
        "must_not_access": ["payroll"],
        "must_not_share": ["card number"],
    },
    "never": [
        {"id": "N1", "reason": "Wire transfers are never made.", "when": {"action_contains": ["wire transfer"]}},
    ],
    "approval_rules": [
        {"id": "A1", "reason": "Writes need a person.", "when": {"tiers": ["write"]}},
        {"id": "A2", "reason": "Messages cannot be recalled.", "when": {"tiers": ["send"]}},
        {"id": "A3", "reason": "Money cannot be recalled.", "when": {"tiers": ["spend"]}},
        {"id": "A4", "reason": "Deletes may be permanent.", "when": {"tiers": ["delete"]}, "approver": "Ada Approver"},
        {"id": "A5", "reason": "Published pages are public.", "when": {"tiers": ["publish"]}},
    ],
    "budget": {"currency": "USD", "per_action_max": 100, "per_day_max": 300, "per_month_max": 2000},
    "audit": {"retention_days": 365},
}


@pytest.fixture
def base():
    """A small charter that passes lint with no findings. Each test gets its own copy."""
    return copy.deepcopy(BASE)


@pytest.fixture
def write(tmp_path):
    def _write(obj, name="charter.json"):
        path = tmp_path / name
        path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def load_lint(write):
    """Lint a charter object the way the command line does: through the file loader."""
    import charter_lint

    def _lint(obj):
        charter, _ = charter_lint.load(str(write(obj, "lintme.json")))
        return charter_lint.lint(charter)

    return _lint


def codes(findings, level=None):
    return {f["code"] for f in findings if level in (None, f["level"])}


def run_script(name, *args, stdin=None, cwd=None):
    """Run a script as a separate process, the way an agent does, from any folder."""
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPTS / name), *map(str, args)],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=120,
    )
