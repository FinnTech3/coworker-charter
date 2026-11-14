import json

import charter_lint as cl
import pytest
from conftest import REFS, codes, run_script

EXAMPLES = sorted(REFS.glob("example-*.json"))


# --- the starter and the shipped examples -------------------------------------------------------------------


def test_init_prints_json_that_lint_rejects_only_for_template_text(write, capsys):
    assert cl.main(["init"]) == 0
    starter = json.loads(capsys.readouterr().out)
    path = write(starter)
    charter, _ = cl.load(str(path))
    found = cl.lint(charter)
    assert codes(found, "error") == {"placeholder"}
    assert cl.main(["lint", str(path)]) == 1


def test_init_is_deterministic():
    assert run_script("charter_lint.py", "init").stdout == run_script("charter_lint.py", "init").stdout


def test_starter_covers_every_tier():
    assert {t["tier"] for t in cl.STARTER["tools"].values()} == set(cl.TIERS)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_shipped_example_charters_pass_strict_lint(path):
    assert len(EXAMPLES) == 3
    assert cl.main(["lint", str(path), "--strict"]) == 0


def test_base_fixture_is_clean(load_lint, base):
    assert load_lint(base) == []


# --- structure ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", cl.REQUIRED)
def test_every_required_section_is_enforced(load_lint, base, key):
    del base[key]
    assert any(f["code"] == "missing-section" and f["path"] == key for f in load_lint(base))


def test_unknown_key_warns_and_suggests_the_right_spelling(load_lint, base):
    base["aproval_rules"] = base.pop("approval_rules")
    found = load_lint(base)
    hit = next(f for f in found if f["code"] == "unknown-key")
    assert hit["level"] == "warning" and "approval_rules" in hit["fix"]


def test_version_and_status_are_validated(load_lint, base):
    base["charter_version"] = 2
    assert "version" in codes(load_lint(base))
    base["charter_version"] = 1
    base["status"] = "paused"
    assert "status" in codes(load_lint(base))
    base["status"] = "suspended"
    assert load_lint(base) == []


def test_owner_needs_name_and_contact(load_lint, base):
    for owner in ({"name": "Owen"}, {"contact": "o@example.com"}, {"name": " ", "contact": "x"}, "Owen", None):
        base["owner"] = owner
        assert "owner-missing" in codes(load_lint(base)), owner


def test_escalation_needs_contact_and_a_trigger(load_lint, base):
    base["escalation"] = {"when": ["something specific"]}
    assert "escalation-missing" in codes(load_lint(base))
    base["escalation"] = {"contact": "oncall@example.com", "when": []}
    assert "escalation-missing" in codes(load_lint(base))
    base["escalation"] = {"contact": "oncall@example.com"}
    assert "escalation-missing" in codes(load_lint(base))


def test_approvers_must_be_named(load_lint, base):
    base["approvers"] = [{"contact": "x@example.com"}]
    assert "approvers" in codes(load_lint(base))


# --- tools ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["*", "gmail.*", "docs.?", "all", "Everything", "ANY"])
def test_wildcard_tool_grants_are_errors(load_lint, base, name):
    base["tools"][name] = {"tier": "read", "description": "blanket"}
    assert "wildcard-tool" in codes(load_lint(base), "error")


def test_wildcard_in_a_rule_is_an_error(load_lint, base):
    base["approval_rules"][0]["when"] = {"tools": ["w.*"]}
    assert "wildcard-tool" in codes(load_lint(base), "error")


@pytest.mark.parametrize("tool", [{}, {"tier": "danger"}, {"tier": None}, "read", None])
def test_every_tool_needs_a_valid_tier(load_lint, base, tool):
    base["tools"]["x.tool"] = tool
    assert "tool-no-tier" in codes(load_lint(base), "error")


def test_tools_object_must_not_be_empty(load_lint, base):
    base["tools"] = {}
    assert "tools" in codes(load_lint(base), "error")


def test_every_tool_above_read_needs_an_approval_rule(load_lint, base):
    base["approval_rules"] = []
    found = [f for f in load_lint(base) if f["code"] == "tier-needs-rule"]
    assert len(found) == 5  # write, send, spend, delete, publish
    assert not any("r.read" in f["path"] for f in found)


def test_a_rule_naming_the_tool_covers_it_even_without_the_tier(load_lint, base):
    base["approval_rules"] = [
        {"id": f"T{i}", "reason": "Named rule.", "when": {"tools": [name]}}
        for i, name in enumerate(n for n, t in base["tools"].items() if t["tier"] != "read")
    ]
    assert load_lint(base) == []


def test_tool_without_description_warns(load_lint, base):
    del base["tools"]["r.read"]["description"]
    assert any(f["code"] == "tool-no-description" and f["level"] == "warning" for f in load_lint(base))


@pytest.mark.parametrize(
    "name, tier", [("mail.send_email", "read"), ("db.delete_rows", "write"), ("bank.refund", "write")]
)
def test_tool_names_that_suggest_a_higher_tier_warn(load_lint, base, name, tier):
    base["tools"][name] = {"tier": tier, "description": "x"}
    base["approval_rules"].append({"id": "Z1", "reason": "Named.", "when": {"tools": [name]}})
    assert any(f["code"] == "tier-looks-low" for f in load_lint(base))


# --- rules ----------------------------------------------------------------------------------------------------


def test_never_rule_with_no_conditions_matches_everything(load_lint, base):
    base["never"] = [{"id": "N9", "reason": "Everything is refused.", "when": {}}]
    assert "rule-matches-everything" in codes(load_lint(base), "error")
    base["never"] = [{"id": "N9", "reason": "Everything is refused."}]
    assert "rule-matches-everything" in codes(load_lint(base), "error")


def test_approval_rule_must_name_tools_or_tiers(load_lint, base):
    base["approval_rules"][0]["when"] = {"action_contains": ["anything"]}
    assert "rule-matches-everything" in codes(load_lint(base), "error")


def test_rule_ids_are_required_and_unique(load_lint, base):
    base["approval_rules"][1]["id"] = "A1"
    assert "duplicate-id" in codes(load_lint(base))
    del base["approval_rules"][1]["id"]
    assert "rule-no-id" in codes(load_lint(base))


def test_rule_without_a_reason_is_an_error(load_lint, base):
    del base["approval_rules"][0]["reason"]
    assert "rule-no-reason" in codes(load_lint(base), "error")


@pytest.mark.parametrize(
    "bad",
    [
        {"amount_over": "10"},
        {"amount_over": -1},
        {"amount_at_most": True},
        {"tiers": ["danger"]},
        {"tiers": []},
        {"tools": "w.write"},
        {"action_contains": [""]},
        {"recipient_domain_in": "example.com"},
    ],
)
def test_malformed_conditions_are_errors(load_lint, base, bad):
    base["approval_rules"][0]["when"] = {"tiers": ["write"], **bad}
    assert "bad-condition" in codes(load_lint(base), "error")


def test_unknown_condition_and_unknown_tool_reference(load_lint, base):
    base["approval_rules"][0]["when"] = {"tiers": ["write"], "amount_gt": 5}
    assert "unknown-condition" in codes(load_lint(base), "error")
    base["approval_rules"][0]["when"] = {"tools": ["not.a.tool"]}
    assert "unknown-tool-ref" in codes(load_lint(base), "error")


def test_decision_must_be_ask_allow_or_deny(load_lint, base):
    base["approval_rules"][0]["decision"] = "maybe"
    assert "bad-decision" in codes(load_lint(base), "error")


def test_ask_rule_must_name_the_owner_or_a_listed_approver(load_lint, base):
    base["approval_rules"][0]["approver"] = "Mallory"
    assert "unknown-approver" in codes(load_lint(base), "error")
    base["approval_rules"][0]["approver"] = "Ada Approver"
    assert load_lint(base) == []
    base["approval_rules"][0]["approver"] = base["owner"]["name"]
    assert load_lint(base) == []


@pytest.mark.parametrize("approver", [["Ada Approver"], {"name": "Ada Approver"}, 7, None])
def test_an_approver_that_is_not_text_is_a_finding_not_a_crash(load_lint, base, approver):
    base["approval_rules"][0]["approver"] = approver
    assert "unknown-approver" in codes(load_lint(base), "error")


def test_auto_allow_on_send_needs_recipient_domains(load_lint, base):
    base["approval_rules"].append({"id": "A9", "reason": "Open.", "when": {"tools": ["s.send"]}, "decision": "allow"})
    assert "auto-allow-unbounded" in codes(load_lint(base), "error")
    base["approval_rules"][-1]["when"]["recipient_domain_in"] = ["example.com"]
    assert load_lint(base) == []


def test_auto_allow_on_spend_needs_a_ceiling(load_lint, base):
    base["approval_rules"].append({"id": "A9", "reason": "Open.", "when": {"tools": ["p.pay"]}, "decision": "allow"})
    assert "auto-allow-unbounded" in codes(load_lint(base), "error")
    base["approval_rules"][-1]["when"]["amount_at_most"] = 10
    assert load_lint(base) == []


@pytest.mark.parametrize("tool", ["d.remove", "u.publish"])
def test_auto_allow_on_delete_or_publish_warns(load_lint, base, tool):
    base["approval_rules"].append({"id": "A9", "reason": "Open.", "when": {"tools": [tool]}, "decision": "allow"})
    found = load_lint(base)
    assert "auto-allow-delete-publish" in codes(found, "warning") and not codes(found, "error")


@pytest.mark.parametrize(
    "text",
    [
        "Use your judgement here.",
        "Ask as appropriate.",
        "Escalate if necessary.",
        "Refund a reasonable amount.",
        "Small refunds only.",
        "Handle sensitive data carefully.",
        "Check invoices, contracts etc.",
        "Be sensible about it.",
        "Anything risky needs a person.",
    ],
)
def test_vague_rule_text_is_an_error(load_lint, base, text):
    base["approval_rules"][0]["reason"] = text
    found = load_lint(base)
    assert any(f["code"] == "vague-rule" and f["path"] == "approval_rules[0].reason" for f in found)


def test_vague_wording_in_escalation_and_never_rules_is_caught(load_lint, base):
    base["escalation"]["when"] = ["when it looks important"]
    base["never"][0]["reason"] = "Never do large wire transfers."
    paths = {f["path"] for f in load_lint(base) if f["code"] == "vague-rule"}
    assert paths == {"escalation.when[0]", "never[0].reason"}


def test_no_never_rules_warns(load_lint, base):
    base["never"] = []
    assert "no-never-rules" in codes(load_lint(base), "warning")


# --- budget and audit -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["per_action_max", "per_day_max"])
@pytest.mark.parametrize("value", [None, "50", True, -1, [], {}])
def test_spend_caps_must_be_present_and_numeric(load_lint, base, key, value):
    if value is None:
        del base["budget"][key]
    else:
        base["budget"][key] = value
    assert any(f["code"] == "spend-cap" and f["path"] == f"budget.{key}" for f in load_lint(base))


def test_month_cap_is_optional_but_must_be_numeric_when_present(load_lint, base):
    del base["budget"]["per_month_max"]
    assert load_lint(base) == []
    base["budget"]["per_month_max"] = "lots"
    assert "spend-cap" in codes(load_lint(base))


def test_budget_needs_a_currency_and_may_hold_decimals(load_lint, base):
    del base["budget"]["currency"]
    assert "spend-cap" in codes(load_lint(base))
    base["budget"]["currency"] = "USD"
    base["budget"]["per_action_max"] = 99.5
    assert load_lint(base) == []


def test_zero_cap_is_allowed(load_lint, base):
    base["budget"]["per_action_max"] = 0
    assert not codes(load_lint(base), "error")


@pytest.mark.parametrize("value", [None, 0, -5, "365", True, 1.5])
def test_retention_must_be_a_positive_whole_number(load_lint, base, value):
    if value is None:
        del base["audit"]["retention_days"]
    else:
        base["audit"]["retention_days"] = value
    assert "audit-retention" in codes(load_lint(base), "error")


def test_short_retention_warns(load_lint, base):
    base["audit"]["retention_days"] = 30
    assert "short-retention" in codes(load_lint(base), "warning")


def test_data_boundaries_missing_or_hollow_warns(load_lint, base):
    del base["data_boundaries"]
    assert "data-boundaries" in codes(load_lint(base), "warning")
    base["data_boundaries"] = {"may_access": ["x"], "must_not_access": [], "must_not_share": []}
    assert "data-boundaries" in codes(load_lint(base), "warning")


# --- secrets and template text --------------------------------------------------------------------------------

SECRET_SAMPLES = {
    "cloud access key id": "AKIA" + "IOSFODNN7EXAMPLE",
    "API key": "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3",
    "code-host token": "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "chat token": "xoxb-" + "1234567890-abcdefghij",
    "private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "JWT": "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk1234567",
    "password inside a URL": "https://svc:" + "hunter2hunter2@host.example/path",
    "credential assignment": "password" + ": correct-horse-battery",
    "long hex string": "0123456789abcdef" * 3,
    "long random string": "aB3" * 11,
}


@pytest.mark.parametrize("kind", SECRET_SAMPLES)
def test_secrets_in_charter_text_are_errors_and_never_echoed(load_lint, base, kind):
    secret = SECRET_SAMPLES[kind]
    base["role"] = f"Support coworker. Setup note {secret} do not lose."
    found = load_lint(base)
    hit = [f for f in found if f["code"] == "secret-in-charter"]
    assert hit and hit[0]["path"] == "$.role"
    assert secret not in json.dumps(found)


def test_secret_looking_key_names_are_errors(load_lint, base):
    base["tools"]["r.read"]["api_key"] = "x1y2z3w4"
    base["audit"]["access_token"] = "abcd"
    paths = {f["path"] for f in load_lint(base) if f["code"] == "secret-in-charter"}
    assert paths == {'$.tools["r.read"].api_key', "$.audit.access_token"}


def test_ordinary_text_is_not_mistaken_for_a_secret(load_lint, base):
    base["role"] = "Refund order 1182 for customer support-oncall@example.com using the helpdesk.send_reply tool."
    base["tools"]["r.read"]["description"] = "Token budget is tracked per day, not stored here"
    assert "secret-in-charter" not in codes(load_lint(base))


@pytest.mark.parametrize("text", ["FILL IN: the owner", "TODO", "tbd", "CHANGEME", "<owner name>"])
def test_template_text_is_an_error(load_lint, base, text):
    base["owner"]["name"] = text
    assert "placeholder" in codes(load_lint(base), "error")


def test_a_comparison_in_a_rule_is_not_template_text(load_lint, base):
    base["approval_rules"][0]["reason"] = "Amounts <50 and >10 need a person."
    assert "placeholder" not in codes(load_lint(base))


# --- exit codes and bad input ---------------------------------------------------------------------------------


def test_exit_codes(write, base, capsys):
    ok = write(base, "ok.json")
    assert cl.main(["lint", str(ok)]) == 0
    assert "OK" in capsys.readouterr().out
    del base["owner"]
    assert cl.main(["lint", str(write(base, "bad.json"))]) == 1
    warn_only = json.loads(ok.read_text())
    warn_only["never"] = []
    path = write(warn_only, "warn.json")
    assert cl.main(["lint", str(path)]) == 0
    assert cl.main(["lint", str(path), "--strict"]) == 1


def test_json_output_is_parseable(write, base, capsys):
    del base["owner"]
    assert cl.main(["lint", str(write(base)), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["errors"] >= 1 and data["findings"][0].keys() >= {"level", "code", "path", "message", "fix"}
    assert len(data["charter_sha256"]) == 64


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        '"text"',
        "",
        '{"a": 1, "a": 2}',
        '{"budget": {"per_action_max": NaN}}',
        '{"budget": {"per_action_max": Infinity}}',
        "[" * 200000,
    ],
)
def test_unusable_files_give_exit_2_and_no_traceback(write, content, capsys):
    assert cl.main(["lint", str(write(content))]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "Traceback" not in err


def test_missing_file_directory_and_invalid_utf8(tmp_path, capsys):
    assert cl.main(["lint", str(tmp_path / "nope.json")]) == 2
    assert cl.main(["lint", str(tmp_path)]) == 2
    bad = tmp_path / "bad.json"
    bad.write_bytes(b'{"role": "\xff\xfe"}')
    assert cl.main(["lint", str(bad)]) == 2
    assert "UTF-8" in capsys.readouterr().err


def test_oversized_charter_is_refused(tmp_path, capsys):
    big = tmp_path / "big.json"
    big.write_text('{"role": "' + "x" * 1_100_000 + '"}')
    assert cl.main(["lint", str(big)]) == 2
    assert "over" in capsys.readouterr().err


def test_a_byte_order_mark_and_unicode_text_are_fine(tmp_path, base):
    base["owner"]["name"] = "Zoë Łukasiewicz 田中"
    base["data_boundaries"]["must_not_access"] = ["Gehaltsabrechnung", "給与"]
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(base, ensure_ascii=False).encode("utf-8"))
    assert cl.main(["lint", str(path), "--strict"]) == 0


def test_runs_standalone_from_an_empty_folder(tmp_path, base):
    path = tmp_path / "c.json"
    path.write_text(json.dumps(base))
    empty = tmp_path / "empty"
    empty.mkdir()
    done = run_script("charter_lint.py", "lint", path, cwd=empty)
    assert done.returncode == 0 and "OK" in done.stdout
    assert list(empty.iterdir()) == []
