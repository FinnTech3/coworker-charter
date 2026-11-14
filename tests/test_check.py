import json
from decimal import Decimal

import charter_check as cc
import pytest
from conftest import REFS, run_script


@pytest.fixture
def ask(write, capsys):
    """Run `check` on a charter object and return (exit code, parsed --json result or None, stderr)."""

    def _ask(charter, tool, action="do the thing", **kw):
        argv = [str(write(charter)), "--tool", tool, "--action", action, "--json"]
        for key, value in kw.items():
            argv.append(f"--{key.replace('_', '-')}={value}")
        code = cc.main(argv)
        captured = capsys.readouterr()
        return code, (json.loads(captured.out) if captured.out else None), captured.err

    return _ask


def decision(ask, charter, tool, action="do the thing", **kw):
    code, res, err = ask(charter, tool, action, **kw)
    assert res is not None, err
    assert code == (0 if res["decision"] == "allow" else 1)
    return res


# --- the basic paths -----------------------------------------------------------------------------------------


def test_read_tier_is_allowed_by_default(ask, base):
    res = decision(ask, base, "r.read", "read the demo records")
    assert (res["decision"], res["rule"], res["tell_human"]) == ("allow", "read-default", None)


@pytest.mark.parametrize(
    "tool, rule, approver",
    [
        ("w.write", "A1", "Owen Owner"),
        ("s.send", "A2", "Owen Owner"),
        ("p.pay", "A3", "Owen Owner"),
        ("d.remove", "A4", "Ada Approver"),
        ("u.publish", "A5", "Owen Owner"),
    ],
)
def test_every_tier_above_read_asks_the_named_approver(ask, base, tool, rule, approver):
    res = decision(ask, base, tool, amount=5, recipient="a@example.com", spent_today=0)
    assert (res["decision"], res["rule"], res["approver"]) == ("ask", rule, approver)


def test_unknown_tool_is_denied_with_a_suggestion(ask, base):
    res = decision(ask, base, "s.sned")
    assert (res["decision"], res["rule"]) == ("deny", "unknown-tool")
    assert "s.send" in res["reason"] and res["tier"] is None


def test_tool_names_are_matched_exactly(ask, base):
    assert decision(ask, base, "R.READ")["decision"] == "deny"
    assert decision(ask, base, "  r.read  ")["decision"] == "allow"  # only surrounding spaces are forgiven


def test_suspended_charter_denies_everything_including_reads(ask, base):
    base["status"] = "suspended"
    res = decision(ask, base, "r.read")
    assert (res["decision"], res["rule"]) == ("deny", "charter-suspended")


def test_a_rule_that_names_the_tool_beats_one_that_names_only_the_tier(ask, base):
    base["approval_rules"].append(
        {"id": "A6", "reason": "Demo writes are fine.", "when": {"tools": ["w.write"]}, "decision": "allow"}
    )
    assert decision(ask, base, "w.write")["rule"] == "A6"


def test_a_narrow_allow_falls_back_to_the_tier_rule_when_its_conditions_fail(ask, base):
    rule = {
        "id": "A6",
        "reason": "Internal mail only.",
        "when": {"tools": ["s.send"], "recipient_domain_in": ["example.com"]},
    }
    base["approval_rules"].append(rule | {"decision": "allow"})
    assert decision(ask, base, "s.send", recipient="bob@example.com")["decision"] == "allow"
    res = decision(ask, base, "s.send", recipient="bob@elsewhere.test")
    assert (res["decision"], res["rule"]) == ("ask", "A2")


def test_deny_rules_beat_allow_rules_whatever_their_specificity(ask, base):
    base["approval_rules"].append(
        {"id": "A6", "reason": "Named allow.", "when": {"tools": ["w.write"]}, "decision": "allow"}
    )
    base["approval_rules"].append(
        {"id": "A7", "reason": "Tier deny.", "when": {"tiers": ["write"]}, "decision": "deny"}
    )
    assert decision(ask, base, "w.write")["rule"] == "A7"


def test_within_equal_specificity_the_stricter_rule_wins(ask, base):
    base["approval_rules"].append({"id": "A6", "reason": "Allow.", "when": {"tiers": ["write"]}, "decision": "allow"})
    assert decision(ask, base, "w.write")["decision"] == "ask"


# --- never rules, boundaries and text matching ---------------------------------------------------------------


def test_never_rule_denies_before_anything_else(ask, base):
    base["approval_rules"].append({"id": "A6", "reason": "Allow.", "when": {"tools": ["r.read"]}, "decision": "allow"})
    res = decision(ask, base, "r.read", "Prepare a wire transfer to the new supplier")
    assert (res["decision"], res["rule"]) == ("deny", "N1")


@pytest.mark.parametrize(
    "action",
    [
        "WIRE TRANSFER now",
        "wire    transfer",
        "wire\ttransfer",
        "ｗｉｒｅ ｔｒａｎｓｆｅｒ",  # full-width letters
        "wire​ transfer",  # zero-width space inside the phrase
        "wi­re transfer",  # soft hyphen
        "Wire Transfer.",
    ],
)
def test_phrases_match_despite_case_spacing_and_invisible_characters(ask, base, action):
    assert decision(ask, base, "r.read", action)["rule"] == "N1"


@pytest.mark.parametrize("action", ["wire transfers are banned", "rewire transfer", "wire a transfer", "wiretransfer"])
def test_phrases_need_whole_words_so_list_plurals_separately(ask, base, action):
    res = decision(ask, base, "r.read", action)
    assert (res["decision"], res["rule"]) == ("allow", "read-default")


def test_a_short_phrase_does_not_match_inside_a_longer_word(ask, base):
    base["data_boundaries"]["must_not_access"] = ["HR"]
    assert decision(ask, base, "r.read", "ask about the three options")["decision"] == "allow"
    assert decision(ask, base, "r.read", "open the HR folder")["rule"] == "data-boundary"


def test_never_rule_with_a_tool_and_a_phrase_needs_both(ask, base):
    base["never"] = [
        {"id": "N2", "reason": "No bulk deletes.", "when": {"tools": ["d.remove"], "action_contains": ["bulk"]}}
    ]
    assert decision(ask, base, "d.remove", "bulk delete")["rule"] == "N2"
    assert decision(ask, base, "d.remove", "delete one record")["rule"] == "A4"
    assert decision(ask, base, "w.write", "bulk update")["rule"] == "A1"


@pytest.mark.parametrize("key, phrase", [("must_not_access", "payroll"), ("must_not_share", "card number")])
def test_data_boundary_phrases_are_denied(ask, base, key, phrase):
    res = decision(ask, base, "r.read", f"look at the {phrase.upper()} file")
    assert (res["decision"], res["rule"]) == ("deny", "data-boundary") and phrase in res["reason"]


def test_hostile_action_text_is_neutralised_in_the_message(ask, base):
    res = decision(ask, base, "w.write", "update \x1b[2J the record ‮ evil")
    assert "\x1b" not in res["tell_human"] and "‮" not in res["tell_human"]
    assert "\\x1b" in res["tell_human"] and "\\u202e" in res["tell_human"]


# --- recipients ----------------------------------------------------------------------------------------------


@pytest.fixture
def mail(base):
    internal = {
        "id": "A6",
        "reason": "Internal.",
        "when": {"tools": ["s.send"], "recipient_domain_in": ["example.com"]},
    }
    external = {
        "id": "A7",
        "reason": "External.",
        "when": {"tools": ["s.send"], "recipient_domain_not_in": ["example.com"]},
    }
    base["approval_rules"] += [internal | {"decision": "allow"}, external | {"decision": "ask"}]
    return base


@pytest.mark.parametrize(
    "recipient, want",
    [
        ("a@example.com", "allow"),
        ("A@EXAMPLE.COM", "allow"),
        ("Ann Lee <ann@example.com>", "allow"),
        ("a@example.com, b@example.com", "allow"),
        ("a@example.com; b@example.com", "allow"),
        ("a@example.com, b@elsewhere.test", "ask"),
        ("a@elsewhere.test", "ask"),
        ("a@example.com.evil.test", "ask"),
        ("a@evilexample.com", "ask"),
        ("a@sub.example.com", "ask"),
        ("a@@example.com", "ask"),
        ("example.com", "allow"),
        ("https://example.com:8443/path", "allow"),
        ("a@example.com.", "allow"),
        ("a@example.com, evil.test", "ask"),
        ("a@example.com evil.test", "ask"),
        ("a@example.com;evil.test", "ask"),
        ("Dr. Ann Lee <ann@example.com>", "allow"),
        ("a@example.com, https://evil.test/x", "ask"),
    ],
)
def test_recipient_domains_are_matched_exactly_and_all_must_be_inside(ask, mail, recipient, want):
    assert decision(ask, mail, "s.send", recipient=recipient)["decision"] == want


def test_missing_recipient_is_never_treated_as_internal(ask, mail):
    res = decision(ask, mail, "s.send")
    assert res["decision"] == "ask" and res["rule"] != "A6"
    res = decision(ask, mail, "s.send", recipient="   ")
    assert res["decision"] == "ask"


def test_allow_that_depends_on_a_missing_recipient_is_asked_instead(ask, base):
    base["approval_rules"] = [r for r in base["approval_rules"] if r["id"] != "A2"]
    base["approval_rules"].append(
        {
            "id": "A6",
            "reason": "Internal.",
            "when": {"tools": ["s.send"], "recipient_domain_in": ["example.com"]},
            "decision": "allow",
        }
    )
    res = decision(ask, base, "s.send")
    assert res["decision"] == "ask" and res["rule"] == "unverifiable" and "recipient" in res["reason"]
    assert any("recipient" in n for n in res["notes"])
    assert decision(ask, base, "s.send", recipient="a@example.com")["decision"] == "allow"


def test_a_tier_rule_that_asks_still_wins_when_the_narrow_allow_cannot_be_evaluated(ask, base):
    base["approval_rules"].append(
        {
            "id": "A6",
            "reason": "Internal.",
            "when": {"tools": ["s.send"], "recipient_domain_in": ["example.com"]},
            "decision": "allow",
        }
    )
    res = decision(ask, base, "s.send")
    assert (res["decision"], res["rule"]) == ("ask", "A2") and any("recipient" in n for n in res["notes"])


# --- amounts and caps ----------------------------------------------------------------------------------------


def test_spend_without_an_amount_is_asked_and_says_so(ask, base):
    res = decision(ask, base, "p.pay", "pay the invoice")
    assert res["decision"] == "ask" and any("the amount" in n for n in res["notes"])
    assert "Note: Not supplied" in res["tell_human"]


@pytest.mark.parametrize(
    "amount, want, rule",
    [
        ("0", "ask", "A3"),
        ("99.99", "ask", "A3"),
        ("100", "ask", "A3"),
        ("100.00", "ask", "A3"),
        ("100.01", "deny", "budget.per_action_max"),
    ],
)
def test_per_action_cap_boundary(ask, base, amount, want, rule):
    res = decision(ask, base, "p.pay", amount=amount, spent_today=0)
    assert (res["decision"], res["rule"]) == (want, rule)


@pytest.mark.parametrize("spent, want", [("200", "ask"), ("200.00", "ask"), ("200.01", "deny")])
def test_daily_cap_boundary(ask, base, spent, want):
    res = decision(ask, base, "p.pay", amount=100, spent_today=spent)
    assert res["decision"] == want
    if want == "deny":
        assert res["rule"] == "budget.per_day_max" and "daily" in res["reason"]


def test_monthly_cap_boundary(ask, base):
    assert decision(ask, base, "p.pay", amount=50, spent_today=0, spent_month=1950)["decision"] == "ask"
    res = decision(ask, base, "p.pay", amount=50, spent_today=0, spent_month="1950.01")
    assert (res["decision"], res["rule"]) == ("deny", "budget.per_month_max")


def test_decimal_arithmetic_is_exact(ask, base):
    base["budget"]["per_action_max"] = 0.3
    base["budget"]["per_day_max"] = 0.3
    assert decision(ask, base, "p.pay", amount="0.1", spent_today="0.2")["decision"] == "ask"
    assert decision(ask, base, "p.pay", amount="0.1", spent_today="0.2000001")["decision"] == "deny"


def test_cap_check_applies_to_any_tier_when_an_amount_is_given(ask, base):
    assert decision(ask, base, "w.write", amount=5000)["rule"] == "budget.per_action_max"


def test_spend_without_todays_total_notes_the_gap_but_still_asks(ask, base):
    res = decision(ask, base, "p.pay", amount=10)
    assert res["decision"] == "ask" and any("today's spend" in n for n in res["notes"])


@pytest.fixture
def small_spend(base):
    base["approval_rules"].append(
        {"id": "A6", "reason": "Small spend.", "when": {"tools": ["p.pay"], "amount_at_most": 25}, "decision": "allow"}
    )
    return base


@pytest.mark.parametrize("amount, want", [("0", "allow"), ("25", "allow"), ("25.00", "allow"), ("25.01", "ask")])
def test_allow_ceiling_boundary(ask, small_spend, amount, want):
    assert decision(ask, small_spend, "p.pay", amount=amount, spent_today=0, spent_month=0)["decision"] == want


def test_allow_on_spend_is_not_given_without_the_amount_or_the_daily_total(ask, small_spend):
    res = decision(ask, small_spend, "p.pay", amount=5)  # the allow rule matches, but today's total is unknown
    assert (res["decision"], res["rule"]) == ("ask", "unverifiable") and "today's spend" in res["reason"]
    res = decision(ask, small_spend, "p.pay", spent_today=0)  # the tier rule asks, and the note says why
    assert (res["decision"], res["rule"]) == ("ask", "A3") and any("the amount" in n for n in res["notes"])
    small_spend["approval_rules"] = [r for r in small_spend["approval_rules"] if r["id"] != "A3"]
    res = decision(ask, small_spend, "p.pay", spent_today=0)
    assert (res["decision"], res["rule"]) == ("ask", "unverifiable") and "amount" in res["reason"]


def test_amount_over_condition(ask, base):
    base["never"].append(
        {"id": "N2", "reason": "Large writes are refused.", "when": {"tools": ["w.write"], "amount_over": 50}}
    )
    assert decision(ask, base, "w.write", amount=50)["rule"] == "A1"
    assert decision(ask, base, "w.write", amount="50.01")["rule"] == "N2"
    assert decision(ask, base, "w.write")["rule"] == "A1"  # amount unknown: the rule cannot fire, the tier rule asks


def test_unknown_input_never_upgrades_a_deny_or_hides_an_ask(ask, base):
    base["never"].append(
        {"id": "N2", "reason": "Large writes are refused.", "when": {"tools": ["w.write"], "amount_over": 50}}
    )
    res = decision(ask, base, "w.write")
    assert res["decision"] == "ask"


@pytest.mark.parametrize("bad", ["-1", "abc", "NaN", "Infinity", "-Infinity", "1e16", "1,000", "", "0x10", "٫"])
@pytest.mark.parametrize("flag", ["amount", "spent_today", "spent_month"])
def test_bad_numbers_are_rejected_with_exit_2(ask, base, bad, flag):
    code, res, err = ask(base, "p.pay", **{flag: bad})
    assert code == 2 and res is None and err.startswith("error:") and "Traceback" not in err


def test_large_valid_numbers_and_unicode_digits_are_handled(ask, base):
    base["budget"] = {"currency": "USD", "per_action_max": 10**15, "per_day_max": 10**15}
    assert decision(ask, base, "p.pay", amount="999999999999999", spent_today=0)["decision"] == "ask"
    assert ask(base, "p.pay", amount="١٢")[0] in (0, 1, 2)  # Arabic-Indic digits parse or are refused; no crash


# --- input and charter refusal -------------------------------------------------------------------------------


@pytest.mark.parametrize("tool, action", [("", "x"), (" ", "x"), ("r.read", ""), ("r.read", "   ")])
def test_empty_tool_or_action_is_exit_2(ask, base, tool, action):
    assert ask(base, tool, action)[0] == 2


def test_overlong_action_is_exit_2_and_the_limit_is_exact(ask, base):
    assert ask(base, "r.read", "x" * 10_001)[0] == 2
    assert ask(base, "r.read", "x" * 10_000)[0] == 0


def test_a_charter_with_structural_problems_gives_no_verdict(ask, base):
    for mutate in (
        lambda c: c.pop("budget"),
        lambda c: c["tools"].__setitem__("r.read", {"tier": "danger"}),
        lambda c: c["approval_rules"][0].__setitem__("when", {"tiers": ["write"], "amount_gt": 5}),
        lambda c: c["approval_rules"][0].__setitem__("decision", "maybe"),
        lambda c: c.__setitem__("owner", {"name": "x"}),
        lambda c: c.__setitem__("status", "paused"),
        lambda c: c["budget"].__setitem__("per_action_max", "100"),
        lambda c: c.__setitem__("never", "nope"),
    ):
        broken = json.loads(json.dumps(base))
        mutate(broken)
        code, res, err = ask(broken, "r.read")
        assert (code, res) == (2, None) and "charter is not usable" in err, err


def test_a_charter_with_template_text_gives_no_verdict(ask, base):
    base["role"] = "FILL IN: the job"
    code, res, err = ask(base, "r.read")
    assert code == 2 and "FILL IN" in err


@pytest.mark.parametrize("content", ["{nope", "[]", '{"a": 1, "a": 2}', ""])
def test_bad_charter_files_are_exit_2(write, capsys, content):
    assert cc.main([str(write(content)), "--tool", "r.read", "--action", "x"]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_missing_charter_file_is_exit_2(tmp_path, capsys):
    assert cc.main([str(tmp_path / "none.json"), "--tool", "r.read", "--action", "x"]) == 2


# --- output --------------------------------------------------------------------------------------------------


def test_the_same_inputs_always_give_the_same_answer(write, base):
    path = write(base)
    argv = [
        str(path),
        "--tool",
        "p.pay",
        "--action",
        "Pay invoice 9",
        "--amount",
        "42.5",
        "--recipient",
        "x@y.test",
        "--json",
    ]
    outputs = {run_script("charter_check.py", *argv).stdout for _ in range(5)}
    assert len(outputs) == 1


def test_json_result_has_the_documented_fields(ask, base):
    res = decision(ask, base, "p.pay", "Pay invoice 9", amount="42.50", recipient="x@y.test", spent_today=0)
    assert set(res) == {
        "decision", "rule", "reason", "tool", "tier", "action", "amount", "recipient",
        "approver", "notes", "charter_sha256", "tell_human", "next",
    }  # fmt: skip
    assert res["amount"] == 42.5 and len(res["charter_sha256"]) == 64


def test_the_charter_hash_is_the_sha256_of_the_file_bytes(write, base):
    import hashlib

    path = write(base)
    res = json.loads(run_script("charter_check.py", path, "--tool", "r.read", "--action", "x", "--json").stdout)
    assert res["charter_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_ask_message_names_what_will_happen_who_approves_and_how_to_answer(ask, base):
    res = decision(ask, base, "p.pay", "Pay invoice 9 to Acme", amount=42, recipient="acme@x.test", spent_today=0)
    msg = res["tell_human"]
    for needle in (
        "Pay invoice 9 to Acme",
        "42 USD",
        "acme@x.test",
        "Owen Owner",
        "owen@example.com",
        "A3",
        '"approve"',
        '"decline"',
    ):
        assert needle in msg
    assert "wait for an explicit yes" in res["next"]


def test_deny_message_says_it_was_not_done_and_who_can_change_the_charter(ask, base):
    res = decision(ask, base, "r.read", "wire transfer please")
    assert "I have not done it" in res["tell_human"] and "Owen Owner" in res["tell_human"]
    assert "oncall@example.com" in res["tell_human"] and "Do not retry" in res["next"]


def test_text_output_shape(write, base, capsys):
    assert cc.main([str(write(base)), "--tool", "s.send", "--action", "Send the note", "--recipient", "a@b.test"]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "decision: ask" and "Tell the human:" in out and "rule:     A2" in out
    assert cc.main([str(write(base)), "--tool", "zzz", "--action", "x"]) == 1
    assert "not in the charter" in capsys.readouterr().out


def test_check_writes_nothing(tmp_path, write, base):
    path = write(base)
    before = path.read_bytes()
    empty = tmp_path / "empty"
    empty.mkdir()
    done = run_script("charter_check.py", path, "--tool", "w.write", "--action", "x", cwd=empty)
    assert done.returncode == 1 and list(empty.iterdir()) == [] and path.read_bytes() == before


# --- the shipped examples, end to end ------------------------------------------------------------------------


@pytest.fixture
def example(write):
    def _load(name):
        return json.loads((REFS / f"example-{name}.json").read_text())

    return _load


def test_support_example_decisions(ask, example):
    c = example("support-agent")
    assert decision(ask, c, "helpdesk.search", "find tickets")["decision"] == "allow"
    assert decision(ask, c, "helpdesk.save_draft", "save a draft reply")["rule"] == "A1"
    assert decision(ask, c, "refunds.issue", "refund", amount=10, spent_today=0, spent_month=0)["decision"] == "allow"
    res = decision(ask, c, "refunds.issue", "refund", amount="10.01", spent_today=0)
    assert (res["rule"], res["approver"]) == ("A5", "Jo Ellis")
    assert decision(ask, c, "refunds.issue", "refund", amount=101, spent_today=0)["rule"] == "budget.per_action_max"
    assert decision(ask, c, "helpdesk.delete_ticket", "delete every ticket")["rule"] == "N2"
    assert decision(ask, c, "helpdesk.search", "read the payroll file")["rule"] == "data-boundary"


def test_finance_example_decisions(ask, example):
    c = example("finance-ops-agent")
    assert decision(ask, c, "vendors.update_bank_details", "change bank details")["decision"] == "deny"
    assert decision(ask, c, "payments.schedule", "pay a new supplier", amount=100)["rule"] == "A3"
    assert decision(ask, c, "remittance.send", "send advice", recipient="team@example.com")["decision"] == "allow"
    assert decision(ask, c, "remittance.send", "send advice", recipient="ap@supplier.test")["rule"] == "A5"
    assert decision(ask, c, "payments.schedule", "pay", amount=2500, spent_today=7500)["decision"] == "ask"
    assert (
        decision(ask, c, "payments.schedule", "pay", amount=2500, spent_today="7500.01")["rule"] == "budget.per_day_max"
    )


def test_research_example_decisions(ask, example):
    c = example("research-agent")
    assert decision(ask, c, "web.fetch", "read a page")["decision"] == "allow"
    assert (
        decision(ask, c, "paid_data.query", "one query", amount=2, spent_today=0, spent_month=0)["decision"] == "allow"
    )
    assert decision(ask, c, "paid_data.query", "one query", amount="2.5", spent_today=0)["rule"] == "A4"
    assert decision(ask, c, "paid_data.query", "one query", spent_today=0)["rule"] == "unverifiable"
    assert decision(ask, c, "web.search", "find the home address of a founder")["rule"] == "N1"


def test_amounts_round_trip_through_the_decimal_loader():
    c, _ = cc.load(str(REFS / "example-support-agent.json"))
    assert c["budget"]["per_action_max"] == Decimal(100)


def test_runs_standalone_from_an_empty_folder(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    done = run_script(
        "charter_check.py",
        REFS / "example-support-agent.json",
        "--tool",
        "helpdesk.search",
        "--action",
        "look up ticket 5",
        cwd=empty,
    )
    assert done.returncode == 0 and done.stdout.startswith("decision: allow")


def test_a_missing_month_total_stops_an_allow_but_not_without_a_monthly_cap(ask, small_spend):
    res = decision(ask, small_spend, "p.pay", amount=5, spent_today=0)
    assert (res["decision"], res["rule"]) == ("ask", "unverifiable") and "this month's spend" in res["reason"]
    assert decision(ask, small_spend, "p.pay", amount=5, spent_today=0, spent_month=0)["decision"] == "allow"
    del small_spend["budget"]["per_month_max"]
    assert decision(ask, small_spend, "p.pay", amount=5, spent_today=0)["decision"] == "allow"


def test_amounts_in_exponent_form_read_as_plain_numbers(ask, base):
    res = decision(ask, base, "p.pay", amount="1e1", spent_today=0, spent_month=0)
    assert "Amount: 10 " in res["tell_human"]


def test_a_non_text_decision_in_a_rule_is_an_error_not_a_traceback(write, capsys):
    import copy

    from conftest import BASE

    c = copy.deepcopy(BASE)
    c["approval_rules"][0]["decision"] = ["ask"]
    code = cc.main([str(write(c)), "--tool", "w.write", "--action", "x"])
    assert code == 2 and "not usable" in capsys.readouterr().err


def test_recipient_not_in_matches_when_any_recipient_is_outside(ask, base):
    base["approval_rules"].append(
        {
            "id": "A9",
            "reason": "Mail that leaves the company is blocked.",
            "when": {"tools": ["s.send"], "recipient_domain_not_in": ["example.com"]},
            "decision": "deny",
        }
    )
    assert decision(ask, base, "s.send", recipient="a@example.com, b@elsewhere.test")["rule"] == "A9"
    assert decision(ask, base, "s.send", recipient="a@example.com, b@example.com")["rule"] == "A2"


def test_an_unevaluable_never_rule_turns_a_read_allow_into_ask(ask, base):
    base["never"].append(
        {
            "id": "N9",
            "reason": "Nothing over 5 USD is ever read-priced.",
            "when": {"tools": ["r.read"], "amount_over": 5},
        }
    )
    res = decision(ask, base, "r.read")
    assert (res["decision"], res["rule"]) == ("ask", "unverifiable")
    assert decision(ask, base, "r.read", amount=1)["decision"] == "allow"
