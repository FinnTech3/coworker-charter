import ast
import json
import re
import shutil
import subprocess
import sys
import zipfile

import agensi_build as ab
import make_sample_session as mss
import pytest
from conftest import REFS, ROOT, SCRIPTS, SKILL, run_script

SCRIPT_FILES = sorted(SCRIPTS.glob("*.py"))


def findings(level):
    root = SKILL
    out = ab.check(root) + ab.check_listing(root.parent / "LISTING.md")
    return [f for f in out if f[0] == level]


def test_the_checker_finds_no_failures_and_no_warnings():
    assert findings("FAIL") == [] and findings("WARN") == []


def test_the_zip_has_one_top_folder_and_only_text(tmp_path):
    target = ab.build_zip(SKILL, tmp_path)
    names = zipfile.ZipFile(target).namelist()
    assert {n.split("/")[0] for n in names} == {"coworker-charter"}
    assert "coworker-charter/SKILL.md" in names and not [n for n in names if n.endswith((".png", ".pyc"))]


def test_frontmatter_is_valid_yaml_with_the_fields_agensi_reads():
    yaml = pytest.importorskip("yaml")
    fm = yaml.safe_load((SKILL / "SKILL.md").read_text().split("---")[1])
    assert fm["name"] == "coworker-charter" and fm["version"] == "1.0.0"
    assert len(fm["description"]) < 1024 and len(fm["summary"]) <= 300
    assert "Use when" in fm["description"] and "Do not use for" in fm["description"]
    assert fm["docs_url"] == "https://github.com/FinnTech3/coworker-charter"
    assert len(fm["tags"]) <= 10 and len(fm["keywords"]) <= 10 and len(fm["use_cases"]) <= 8


def test_skill_md_names_every_subcommand_and_tells_the_agent_to_use_temporary_files():
    text = (SKILL / "SKILL.md").read_text()
    for needle in ("init", "lint", "log", "verify", "report", "--head", "temporary file", "Requires:", "## Rules"):
        assert needle in text
    assert "python3 scripts/" not in text and text.count("\n") < 500


@pytest.mark.parametrize("path", SCRIPT_FILES, ids=lambda p: p.name)
def test_each_script_is_small_standard_library_only_and_self_contained(path):
    assert len(path.read_bytes()) <= 20 * 1024
    tree = ast.parse(path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add((node.module or "").split(".")[0])
    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
    assert not re.search(
        r"shell\s*=\s*True|os\.system|\beval\(|\bexec\(|urllib|socket|requests|subprocess", path.read_text()
    )


def test_there_are_three_scripts():
    assert [p.name for p in SCRIPT_FILES] == ["audit_log.py", "charter_check.py", "charter_lint.py"]


@pytest.mark.parametrize("name", ["charter_lint.py", "charter_check.py", "audit_log.py"])
def test_help_runs_from_an_empty_folder(tmp_path, name):
    done = run_script(name, "--help", cwd=tmp_path)
    assert done.returncode == 0 and "usage" in done.stdout.lower()


def test_the_sample_session_is_exactly_what_the_scripts_print_today():
    text, log = mss.generate()
    assert (SKILL / "examples" / "sample-session.md").read_text() == text
    assert (SKILL / "examples" / "sample-audit.jsonl").read_text() == log


def test_the_sample_audit_log_verifies():
    done = run_script("audit_log.py", "verify", SKILL / "examples" / "sample-audit.jsonl")
    assert done.returncode == 0 and "OK: 5 entries" in done.stdout


def test_the_listing_example_is_taken_from_the_real_session():
    listing = (ROOT / "agensi" / "LISTING.md").read_text()
    session = (SKILL / "examples" / "sample-session.md").read_text()
    block = re.search(r"```\n(Approval needed before I act\..*?)```", listing, re.S).group(1)
    assert "\n".join("  " + ln for ln in block.rstrip().splitlines()) in session
    head = re.search(r"head: (5:[0-9a-f]{64})", listing).group(1)
    assert f"head: {head}" in session


def test_readme_quick_start_commands_work(tmp_path):
    ex = REFS / "example-support-agent.json"
    log = tmp_path / "audit.jsonl"
    event = '{"tool":"helpdesk.search","action":"Search open tickets","verdict":"allow","outcome":"executed"}'
    steps = [
        (["charter_lint.py", "init"], 0),
        (["charter_lint.py", "lint", ex, "--strict"], 0),
        (["charter_check.py", ex, "--tool", "helpdesk.search", "--action", "Search open tickets"], 0),
        (["charter_check.py", ex, "--tool", "refunds.issue", "--action", "Refund order 9", "--amount", "140"], 1),
        (["audit_log.py", "log", log, "--charter", ex, "--event", event], 0),
        (["audit_log.py", "verify", log], 0),
        (["audit_log.py", "report", log], 0),
    ]
    for args, code in steps:
        assert run_script(*args, cwd=tmp_path).returncode == code, args


def test_readme_test_count_matches_reality():
    count = int(re.search(r"(\d+) offline tests", (ROOT / "README.md").read_text()).group(1))
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False).stdout
    assert f"{count} tests collected" in out, (count, out[-100:])


def test_example_charters_are_valid_json_without_duplicate_keys():
    for path in REFS.glob("example-*.json"):
        json.loads(path.read_text(), object_pairs_hook=lambda p: dict(p) if len({k for k, _ in p}) == len(p) else 1 / 0)


def test_copied_tooling_is_present():
    assert (ROOT / "tools" / "AGENSI_CHECKLIST.md").is_file() and shutil.which("python3")
