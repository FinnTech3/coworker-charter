import hashlib
import json
import os
import stat
import subprocess
import sys
import time

import audit_log as al
import pytest
from conftest import SCRIPTS, run_script

TS = "2026-11-10T09:00:00Z"


@pytest.fixture
def env(tmp_path, base):
    """A charter file, a log path and a helper that appends events in-process."""
    charter = tmp_path / "charter.json"
    charter.write_text(json.dumps(base))
    log = tmp_path / "audit.jsonl"

    class Env:
        pass

    e = Env()
    e.dir, e.charter, e.log = tmp_path, charter, log

    def add(event, ts=TS, expect=None):
        argv = ["log", str(log), "--charter", str(charter), "--event", json.dumps(event), "--ts", ts]
        code = al.main(argv)
        if expect is not None:
            assert code == expect
        return code

    e.add = add
    return e


def action(tool="r.read", verdict="allow", outcome="executed", **extra):
    return {"tool": tool, "action": f"use {tool}", "verdict": verdict, "outcome": outcome, **extra}


def lines(path):
    return path.read_text().splitlines()


def entries(path):
    return [json.loads(x) for x in lines(path)]


def fill(env, n=6):
    for i in range(n):
        env.add(action(), ts=f"2026-11-10T09:{i:02d}:00Z", expect=0)


def rehash(entry):
    entry["hash"] = al.digest(entry)
    return entry


def save(path, rows):
    path.write_text("\n".join(x if isinstance(x, str) else al.canon(x) for x in rows) + "\n")


# --- logging --------------------------------------------------------------------------------------------------


def test_first_entry_starts_the_chain(env, capsys):
    assert (
        al.main(["log", str(env.log), "--charter", str(env.charter), "--event", json.dumps(action()), "--ts", TS]) == 0
    )
    first = entries(env.log)[0]
    assert (first["seq"], first["prev"], first["ts"], first["tier"]) == (1, "0" * 64, TS, "read")
    assert first["hash"] == al.digest(first) and first["flags"] == []
    assert f"head: 1:{first['hash']}" in capsys.readouterr().out


def test_every_entry_links_to_the_one_before(env):
    fill(env, 4)
    rows = entries(env.log)
    assert [r["seq"] for r in rows] == [1, 2, 3, 4]
    assert all(rows[i]["prev"] == rows[i - 1]["hash"] for i in range(1, 4))


def test_entry_records_the_charter_hash_of_the_file_bytes(env):
    env.add(action())
    assert entries(env.log)[0]["charter_sha256"] == hashlib.sha256(env.charter.read_bytes()).hexdigest()


def test_a_charter_change_shows_up_as_a_new_hash(env, base):
    env.add(action())
    base["role"] = "Changed role text for the test."
    env.charter.write_text(json.dumps(base))
    env.add(action())
    first, second = entries(env.log)
    assert first["charter_sha256"] != second["charter_sha256"]


def test_event_can_come_from_stdin(env):
    done = run_script(
        "audit_log.py", "log", env.log, "--charter", env.charter, "--event", "-", "--ts", TS, stdin=json.dumps(action())
    )
    assert done.returncode == 0 and len(lines(env.log)) == 1


def test_json_flag_prints_the_entry(env, capsys):
    al.main(["log", str(env.log), "--charter", str(env.charter), "--event", json.dumps(action()), "--json", "--ts", TS])
    assert json.loads(capsys.readouterr().out)["seq"] == 1


def test_log_file_is_ascii_one_line_per_entry_and_private(env):
    env.add({"tool": "r.read", "action": "line one\nline two ‮ é 日本 😀", "verdict": "allow", "outcome": "executed"})
    raw = env.log.read_bytes()
    assert raw.isascii() and raw.count(b"\n") == 1
    if os.name == "posix":
        assert stat.S_IMODE(env.log.stat().st_mode) == 0o600


def test_amounts_are_normalised_and_summed_exactly(env, capsys):
    env.add(action("p.pay", "ask", amount=0.1, approved_by="Owen Owner", approval_ref="yes"))
    env.add(action("p.pay", "ask", amount=0.2, approved_by="Owen Owner", approval_ref="yes"))
    env.add(action("p.pay", "ask", amount=42.0, approved_by="Owen Owner", approval_ref="yes"))
    rows = entries(env.log)
    assert [r["amount"] for r in rows] == [0.1, 0.2, 42] and isinstance(rows[2]["amount"], int)
    assert al.report(str(env.log))["spend"]["total"] == al.Decimal("42.3")


def test_the_largest_possible_entry_fits_the_line_limit_and_the_chain_survives_it(env):
    big = "\U0001f600" * 1000  # an astral character escapes to 12 bytes, the worst case
    event = {"tool": big, "action": big, "verdict": "allow", "outcome": "executed", "rule": big, "recipient": big,
             "approved_by": big, "approval_ref": big, "actor": big, "by": big, "note": big}  # fmt: skip
    raw = json.dumps(event, ensure_ascii=False)  # sent as raw text, so it fits the 16,384 character event limit
    argv = ["log", str(env.log), "--charter", str(env.charter), "--event", raw, "--ts", TS]
    assert al.main(argv) == 1  # flagged: the tool is not in the charter
    env.add(action())
    assert max(len(x) for x in lines(env.log)) < al.MAX_LINE and al.verify(str(env.log))["ok"]


def test_text_fields_stop_at_1000_characters(env):
    assert env.add(action(note="a" * 1000)) == 0
    assert (
        al.main(["log", str(env.log), "--charter", str(env.charter), "--event", json.dumps(action(note="a" * 1001))])
        == 2
    )


def test_only_the_named_file_is_written(env):
    before = {p.name for p in env.dir.iterdir()}
    env.add(action())
    assert {p.name for p in env.dir.iterdir()} - before == {"audit.jsonl"}


# --- event validation -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event",
    [
        "[]",
        '"text"',
        "{",
        "",
        '{"kind": "action"}',
        json.dumps({"tool": "r.read", "action": "x", "verdict": "allow"}),
        json.dumps(action(verdict="maybe")),
        json.dumps(action(outcome="done")),
        json.dumps(action(kind="gossip")),
        json.dumps(action(amount="12")),
        json.dumps(action(amount=-1)),
        json.dumps(action(amount=True)),
        json.dumps(action(amount=1e16)),
        '{"tool":"r.read","action":"x","verdict":"allow","outcome":"executed","amount":NaN}',
        '{"tool":"r.read","action":"x","verdict":"allow","outcome":"executed","amount":Infinity}',
        '{"tool":"r.read","tool":"w.write","action":"x","verdict":"allow","outcome":"executed"}',
        json.dumps(action(hash="f" * 64)),
        json.dumps(action(seq=99)),
        json.dumps(action(prev="x")),
        json.dumps(action(flags=[])),
        json.dumps(action(tool="")),
        json.dumps(action(tool=5)),
        json.dumps(action(note="a" * 1001)),
        json.dumps({**action(), "action": "a" * 1001}),
        json.dumps(action(approved_by="a" * 1001)),
        json.dumps({"kind": "takeover", "note": "n"}),
        json.dumps({"kind": "release", "by": "Sam"}),
        json.dumps({"kind": "charter_change", "note": "n"}),
        json.dumps({"kind": "note"}),
    ],
)
def test_invalid_events_are_refused_and_nothing_is_written(env, event, capsys):
    code = al.main(["log", str(env.log), "--charter", str(env.charter), "--event", event, "--ts", TS])
    err = capsys.readouterr().err
    assert code == 2 and err.startswith("error:") and "Traceback" not in err
    assert not env.log.exists()


def test_overlong_event_is_refused(env, capsys):
    assert al.main(["log", str(env.log), "--charter", str(env.charter), "--event", " " * 20000 + "{}"]) == 2
    assert not env.log.exists()


@pytest.mark.parametrize("ts", ["2026-11-10", "yesterday", "2026-13-01T00:00:00Z", "2026-11-10T09:00:00", ""])
def test_bad_timestamps_are_refused(env, ts, capsys):
    argv = ["log", str(env.log), "--charter", str(env.charter), "--event", json.dumps(action()), "--ts", ts]
    assert al.main(argv) == (2 if ts else 0)


def test_default_timestamp_is_utc_now(env):
    al.main(["log", str(env.log), "--charter", str(env.charter), "--event", json.dumps(action())])
    assert entries(env.log)[0]["ts"][:4] == time.strftime("%Y", time.gmtime())


def test_missing_or_bad_charter_is_exit_2(env, tmp_path, capsys):
    for charter in (tmp_path / "none.json", tmp_path):
        assert al.main(["log", str(env.log), "--charter", str(charter), "--event", json.dumps(action())]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert al.main(["log", str(env.log), "--charter", str(bad), "--event", json.dumps(action())]) == 2
    bad.write_text("[]")
    assert al.main(["log", str(env.log), "--charter", str(bad), "--event", json.dumps(action())]) == 2
    assert not env.log.exists()


def test_log_in_a_missing_folder_is_exit_2(env, tmp_path, capsys):
    assert (
        al.main(
            ["log", str(tmp_path / "no" / "dir.jsonl"), "--charter", str(env.charter), "--event", json.dumps(action())]
        )
        == 2
    )
    assert "cannot open" in capsys.readouterr().err


# --- flags: process violations --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event, flag",
    [
        (action("d.remove", "deny", "executed"), "executed_despite_deny"),
        (action("w.write", "ask", "executed"), "executed_without_approval"),
        (action("w.write", "ask", "executed", approved_by="agent"), "self_approved"),
        (action("w.write", "ask", "executed", approved_by="Self"), "self_approved"),
        (action("w.write", "ask", "executed", approved_by="test-coworker"), "self_approved"),
        (action("w.write", "ask", "executed", approved_by="Rex", actor="Rex"), "self_approved"),
        (action("w.write", "ask", "executed", approved_by="Mallory", approval_ref="ok"), "approver_not_in_charter"),
        (action("w.write", "ask", "executed", approved_by="Owen Owner"), "approval_ref_missing"),
        (action("not.a.tool", "allow", "executed"), "unknown_tool_executed"),
        (
            action("p.pay", "ask", "executed", amount=100.01, approved_by="Owen Owner", approval_ref="ok"),
            "over_cap_executed",
        ),
    ],
)
def test_executed_actions_that_break_the_process_are_flagged_and_exit_1(env, event, flag, capsys):
    assert env.add(event) == 1
    assert flag in entries(env.log)[0]["flags"]
    assert f"FLAG {flag}" in capsys.readouterr().out


@pytest.mark.parametrize(
    "event",
    [
        action("w.write", "ask", "executed", approved_by="Owen Owner", approval_ref="Owen: yes"),
        action("w.write", "ask", "executed", approved_by="ada@example.com", approval_ref="Ada: approved in chat"),
        action("d.remove", "ask", "executed", approved_by="ADA APPROVER", approval_ref="ok"),
        action("p.pay", "ask", "executed", amount=100, approved_by="Owen Owner", approval_ref="ok"),
        action("d.remove", "deny", "blocked"),
        action("w.write", "ask", "declined"),
        action("w.write", "ask", "blocked"),
        action("w.write", "allow", "failed"),
        action("not.a.tool", "deny", "blocked"),
    ],
)
def test_proper_and_non_executed_events_are_not_flagged(env, event):
    assert env.add(event) == 0 and entries(env.log)[0]["flags"] == []


def test_an_unknown_tool_has_no_tier_in_the_entry(env):
    env.add(action("not.a.tool", "deny", "blocked"))
    assert "tier" not in entries(env.log)[0]


@pytest.mark.parametrize(
    "event",
    [
        {"kind": "takeover", "by": "Owen Owner", "note": "Owen is driving the browser"},
        {"kind": "release", "by": "Owen Owner", "note": "Handed back"},
        {"kind": "escalation", "note": "Asked oncall@example.com about ticket 9"},
        {"kind": "charter_change", "by": "Owen Owner", "note": "Raised the refund cap to 150"},
        {"kind": "note", "note": "Weekly review done"},
    ],
)
def test_non_action_events(env, event):
    assert env.add(event) == 0
    row = entries(env.log)[0]
    assert row["kind"] == event["kind"] and "tier" not in row


# --- the file itself ------------------------------------------------------------------------------------------


def test_log_refuses_to_write_through_a_symlink(env):
    target = env.dir / "victim.txt"
    target.write_text("keep me\n")
    link = env.dir / "link.jsonl"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("no symlinks here")
    code = al.main(["log", str(link), "--charter", str(env.charter), "--event", json.dumps(action())])
    assert code == 2 and target.read_text() == "keep me\n"


def test_log_refuses_a_directory(env, tmp_path):
    assert al.main(["log", str(tmp_path), "--charter", str(env.charter), "--event", json.dumps(action())]) == 2


def test_log_refuses_to_append_to_a_cut_or_corrupt_tail(env, capsys):
    fill(env, 3)
    good = env.log.read_bytes()
    env.log.write_bytes(good[:-20])  # cut mid-line, no newline
    before = env.log.read_bytes()
    assert env.add(action()) == 2 and env.log.read_bytes() == before
    assert "cut off or corrupt" in capsys.readouterr().err
    env.log.write_bytes(good.replace(b'"seq":3', b'"seq":9'))  # last line edited, newline intact
    assert env.add(action()) == 2


def test_log_refuses_when_the_last_line_is_not_json(env):
    fill(env, 2)
    with env.log.open("ab") as f:
        f.write(b"garbage\n")
    assert env.add(action()) == 2


# --- verify: tamper detection ---------------------------------------------------------------------------------


def test_a_clean_log_verifies(env, capsys):
    fill(env, 6)
    capsys.readouterr()
    assert al.main(["verify", str(env.log)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("OK: 6 entries, chain intact.") and f"head: 6:{entries(env.log)[-1]['hash']}" in out


def test_an_empty_log_verifies_but_an_anchor_would_not(env):
    env.log.write_text("")
    assert al.verify(str(env.log))["ok"]
    assert not al.verify(str(env.log), "3:" + "a" * 64)["ok"]


def first_break(env):
    r = al.verify(str(env.log))
    return r, r["first_break"]


def test_editing_a_line_is_caught_at_that_line(env):
    fill(env, 6)
    rows = entries(env.log)
    rows[2]["action"] = "something else entirely"
    save(env.log, rows)
    r, line = first_break(env)
    assert line == 3 and "edited" in r["problems"][0]["message"] and len(r["problems"]) == 1


def test_editing_the_amount_of_a_spend_is_caught(env):
    env.add(action("p.pay", "ask", amount=10, approved_by="Owen Owner", approval_ref="yes"))
    env.add(action())
    rows = entries(env.log)
    rows[0]["amount"] = 1
    save(env.log, rows)
    assert first_break(env)[1] == 1


def test_editing_and_rehashing_a_line_breaks_the_next_one(env):
    fill(env, 6)
    rows = entries(env.log)
    rows[2]["action"] = "forged"
    rehash(rows[2])
    save(env.log, rows)
    r, line = first_break(env)
    assert line == 4 and "prev hash does not match" in r["problems"][0]["message"]


def test_deleting_a_middle_line_is_caught_and_the_gap_is_named(env):
    fill(env, 6)
    rows = lines(env.log)
    del rows[2]
    save(env.log, rows)
    r, line = first_break(env)
    assert line == 3
    msg = r["problems"][0]["message"]
    assert "seq is 4 but 3 was expected" in msg and "missing before this line" in msg and "prev hash" in msg
    assert len(r["problems"]) == 1


def test_deleting_the_first_line_is_caught(env):
    fill(env, 4)
    save(env.log, lines(env.log)[1:])
    r, line = first_break(env)
    assert line == 1 and "seq is 2 but 1 was expected" in r["problems"][0]["message"]


def test_swapping_two_lines_is_caught(env):
    fill(env, 6)
    rows = lines(env.log)
    rows[2], rows[3] = rows[3], rows[2]
    save(env.log, rows)
    r, line = first_break(env)
    assert line == 3 and any("out of order" in p["message"] for p in r["problems"])


def test_duplicating_a_line_is_caught(env):
    fill(env, 4)
    rows = lines(env.log)
    rows.insert(3, rows[2])
    save(env.log, rows)
    r, line = first_break(env)
    assert line == 4 and "out of order or repeated" in r["problems"][0]["message"]


def test_inserting_a_forged_entry_is_caught(env):
    fill(env, 4)
    rows = entries(env.log)
    fake = dict(rows[1], seq=3, action="forged")
    rows.insert(2, rehash(fake))
    save(env.log, rows)
    assert first_break(env)[1] == 3


def test_truncating_the_tail_is_invisible_without_an_anchor_and_caught_with_one(env):
    fill(env, 6)
    head = f"6:{entries(env.log)[-1]['hash']}"
    save(env.log, lines(env.log)[:4])  # whole lines removed from the end
    assert al.verify(str(env.log))["ok"]  # the honest limit: nothing inside the file can show this
    r = al.verify(str(env.log), head)
    assert (
        not r["ok"]
        and "cut off" in r["problems"][0]["message"]
        and "seq 4 but your anchor is seq 6" in r["problems"][0]["message"]
    )


def test_a_matching_anchor_passes_even_after_the_log_has_grown(env):
    fill(env, 3)
    head = f"3:{entries(env.log)[-1]['hash']}"
    fill(env, 2)
    r = al.verify(str(env.log), head)
    assert r["ok"] and "2 entries added since" in r["anchor"]
    assert al.verify(str(env.log), "3:" + head[2:10])["ok"]  # a short prefix of the hash is enough


def test_an_anchor_catches_a_fully_rebuilt_chain(env):
    fill(env, 4)
    head = f"4:{entries(env.log)[-1]['hash']}"
    rows = entries(env.log)
    prev = "0" * 64
    for row in rows:  # an attacker who can write the file rewrites an entry and re-chains everything
        if row["seq"] == 2:
            row["action"] = "rewritten"
        row["prev"] = prev
        prev = rehash(row)["hash"]
    save(env.log, rows)
    assert al.verify(str(env.log))["ok"]  # the chain alone cannot tell
    r = al.verify(str(env.log), head)
    assert not r["ok"] and "does not match your anchor" in r["problems"][0]["message"]


def test_cutting_the_last_line_in_half_is_caught(env):
    fill(env, 3)
    raw = env.log.read_bytes()
    env.log.write_bytes(raw[:-40])
    r, line = first_break(env)
    assert line == 3 and "unreadable" in r["problems"][0]["message"]


def test_a_missing_final_newline_is_reported(env):
    fill(env, 2)
    env.log.write_bytes(env.log.read_bytes().rstrip(b"\n"))
    r = al.verify(str(env.log))
    assert not r["ok"] and "no newline" in r["problems"][0]["message"]


@pytest.mark.parametrize(
    "junk",
    [
        b"\n",
        b"   \n",
        b"not json\n",
        b"[1, 2]\n",
        b'{"seq": 1}\n',
        b"\xff\xfe\n",
        b'{"seq":1,"seq":2}\n',
        b'{"seq": NaN}\n',
    ],
)
def test_junk_lines_are_reported_without_a_crash(env, junk):
    fill(env, 3)
    rows = env.log.read_bytes().splitlines(keepends=True)
    env.log.write_bytes(rows[0] + junk + b"".join(rows[1:]))
    r = al.verify(str(env.log))
    assert not r["ok"] and r["first_break"] == 2


def test_a_junk_line_is_one_problem_and_does_not_cascade(env):
    fill(env, 4)
    rows = env.log.read_bytes().splitlines(keepends=True)
    rows[1] = b"junk\n"
    env.log.write_bytes(b"".join(rows))
    r = al.verify(str(env.log))
    assert [p["line"] for p in r["problems"]] == [2]  # the lines after it still chain to each other


def test_whitespace_only_changes_do_not_break_the_chain(env):
    fill(env, 3)
    rows = [json.dumps(json.loads(x), indent=1, sort_keys=False).replace("\n", " ") for x in lines(env.log)]
    save(env.log, rows)
    assert al.verify(str(env.log))["ok"]  # content is what is hashed, not layout


def test_a_giant_line_is_reported_not_read_into_memory(env):
    fill(env, 3)
    rows = env.log.read_bytes().splitlines(keepends=True)
    env.log.write_bytes(rows[0] + b"x" * 3_000_000 + b"\n" + rows[2])  # line 2 replaced by a 3 MB line
    r = al.verify(str(env.log))
    assert not r["ok"] and r["lines"] == 3 and [p["line"] for p in r["problems"]] == [2]
    assert "longer than" in r["problems"][0]["message"]


def test_a_giant_unterminated_line_does_not_hang(env):
    fill(env, 1)
    with env.log.open("ab") as f:
        f.write(b"y" * 2_000_000)
    assert not al.verify(str(env.log))["ok"]


def test_verify_exit_codes_and_json(env, tmp_path, capsys):
    fill(env, 3)
    capsys.readouterr()
    assert al.main(["verify", str(env.log), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["entries"] == 3
    save(env.log, lines(env.log)[:1] + lines(env.log)[2:])
    assert al.main(["verify", str(env.log)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("BROKEN: 1 problem(s). First break at line 2.")
    assert al.main(["verify", str(tmp_path / "missing.jsonl")]) == 2
    assert al.main(["verify", str(tmp_path)]) == 2
    assert al.main(["verify", str(env.log), "--head", "nonsense"]) == 2


# --- report ---------------------------------------------------------------------------------------------------


@pytest.fixture
def busy(env):
    env.add(action("r.read"), ts="2026-11-09T08:00:00Z")
    env.add(
        action("p.pay", "ask", amount=40, approved_by="Owen Owner", approval_ref="yes", rule="A3"),
        ts="2026-11-09T09:00:00Z",
    )
    env.add(action("p.pay", "ask", "declined", amount=60, rule="A3"), ts="2026-11-10T09:00:00Z")
    env.add(action("d.remove", "deny", "blocked", rule="N2"), ts="2026-11-10T10:00:00Z")
    env.add(action("d.remove", "deny", "blocked", rule="N2"), ts="2026-11-10T11:00:00Z")
    env.add(action("not.a.tool", "deny", "blocked", rule="unknown-tool"), ts="2026-11-10T12:00:00Z")
    env.add(action("w.write", "ask", "executed", approved_by="agent"), ts="2026-11-10T13:00:00Z")
    env.add({"kind": "takeover", "by": "Owen Owner", "note": "Owen is driving"}, ts="2026-11-10T14:00:00Z")
    env.add(
        action("p.pay", "ask", amount=2.5, approved_by="Owen Owner", approval_ref="yes", rule="A3"),
        ts="2026-11-11T09:00:00Z",
    )
    return env


def test_report_counts_decisions_asks_denials_and_spend(busy):
    r = al.report(str(busy.log), as_of="2026-11-11")
    assert r["entries"] == 9 and dict(r["decisions"]) == {"allow": 1, "ask": 4, "deny": 3}
    assert dict(r["asks"]) == {"approved and done": 2, "declined": 1, "ran without valid approval": 1}
    assert dict(r["denials"]) == {"N2": 2, "unknown-tool": 1}
    assert r["spend"] == {"total": al.Decimal("42.5"), "today": al.Decimal("2.5"), "month": al.Decimal("42.5")}
    assert r["by_tool"]["p.pay"] == {
        "entries": 3,
        "allow": 0,
        "ask": 3,
        "deny": 0,
        "executed": 2,
        "spend": al.Decimal("42.5"),
        "tier": "spend",
    }
    assert r["by_day"]["2026-11-10"]["entries"] == 5 and r["by_day"]["2026-11-09"]["spend"] == 40
    assert r["flags"][0]["flags"] == ["self_approved"] and r["events"][0]["kind"] == "takeover"
    assert r["verify"]["ok"]


def test_report_period_filters_but_today_and_month_ignore_it(busy):
    r = al.report(str(busy.log), since="2026-11-10", until="2026-11-10", as_of="2026-11-11")
    assert r["entries"] == 6 and r["spend"]["total"] == 0
    assert r["spend"]["today"] == al.Decimal("2.5") and r["spend"]["month"] == al.Decimal("42.5")
    assert set(r["by_day"]) == {"2026-11-10"}


def test_report_text_is_markdown_with_the_sections_a_reader_needs(busy, capsys):
    assert al.main(["report", str(busy.log), "--as-of", "2026-11-11"]) == 1  # one flag, so exit 1
    out = capsys.readouterr().out
    for needle in ("# Audit report", "Chain intact", "## Decisions", "allow 1, ask 4, deny 3", "N2 x2", "## Spend",
                   "Total 42.5. Today (2026-11-11) 2.5", "## By tool", "| p.pay | spend |", "## By day", "| 2026-11-10 |",
                   "## Flags: process violations", "self_approved", "## Take-overs", "Owen is driving", "Charter versions seen"):  # fmt: skip
        assert needle in out, needle


def test_report_json_and_exit_codes(env, busy, tmp_path, capsys):
    assert al.main(["report", str(busy.log), "--json", "--as-of", "2026-11-11"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["spend"]["total"] == "42.5" and data["decisions"]["deny"] == 3
    clean = tmp_path / "clean.jsonl"
    al.main(["log", str(clean), "--charter", str(env.charter), "--event", json.dumps(action()), "--ts", TS])
    capsys.readouterr()
    assert al.main(["report", str(clean)]) == 0
    assert al.main(["report", str(tmp_path / "missing.jsonl")]) == 2
    assert al.main(["report", str(clean), "--since", "yesterday"]) == 2


def test_report_on_an_empty_log(env, capsys):
    env.log.write_text("")
    assert al.main(["report", str(env.log)]) == 0
    assert "0 entries" in capsys.readouterr().out


def test_report_says_when_the_chain_is_broken(busy, capsys):
    rows = lines(busy.log)
    del rows[3]
    save(busy.log, rows)
    assert al.main(["report", str(busy.log)]) == 1
    out = capsys.readouterr().out
    assert "CHAIN BROKEN, first at line 4" in out and "unproven" in out


def test_report_lists_each_charter_version_seen(env, base, capsys):
    env.add(action())
    base["role"] = "A different role, so a different file hash."
    env.charter.write_text(json.dumps(base))
    env.add(action())
    r = al.report(str(env.log))
    assert len(r["charters"]) == 2
    assert sorted((v["first_seq"], v["last_seq"]) for v in r["charters"].values()) == [(1, 1), (2, 2)]


def test_report_neutralises_terminal_tricks_in_logged_text(env, capsys):
    env.add({"kind": "note", "note": "\x1b[31mred\x1b[0m ‮ reversed"}, ts=TS)
    al.main(["report", str(env.log)])
    out = capsys.readouterr().out
    assert "\x1b" not in out and "‮" not in out and "\\x1b" in out


def test_report_survives_a_rebuilt_log_full_of_odd_values(env, capsys):
    hostile = [
        {"seq": 1, "ts": "2026-11-10T09:00:00Z", "kind": "action", "tool": ["x"], "verdict": ["allow"], "outcome": {"a": 1},
         "amount": "NaN", "flags": 5, "tier": {"t": 1}, "charter_sha256": 7, "rule": [1], "approved_by": 3},
        {"seq": 2, "ts": "2026-11-10T09:00:00Z", "kind": "action", "tool": "t", "verdict": "ask", "outcome": "executed",
         "amount": 1e300, "flags": ["x", 4], "note": None},
        {"seq": 3, "ts": "2026-11-10T09:00:00Z", "kind": "weird", "by": [], "note": {"x": 1}},
    ]  # fmt: skip
    prev = "0" * 64
    for row in hostile:
        row["prev"] = prev
        prev = rehash(row)["hash"]
    env.log.write_bytes(("\n".join(json.dumps(r) for r in hostile) + "\n").encode())
    code = al.main(["report", str(env.log)])
    assert code in (0, 1) and "Traceback" not in capsys.readouterr().err
    al.main(["report", str(env.log), "--json"])


# --- unicode ---------------------------------------------------------------------------------------------------


def test_unicode_survives_the_round_trip_and_the_chain(env):
    text = "Rückerstattung für 田中さん 😀 — اختبار"
    env.add(
        {"tool": "r.read", "action": text, "verdict": "allow", "outcome": "executed", "recipient": "zoë@example.com"}
    )
    env.add(action())
    assert entries(env.log)[0]["action"] == text
    assert al.verify(str(env.log))["ok"]


# --- concurrency and size --------------------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="the lock is POSIX-only; see README for the Windows limit")
def test_concurrent_appends_from_many_processes_keep_the_chain_intact(env):
    workers, each = 8, 6
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import audit_log as a, json\n"
        "for i in range(int(sys.argv[4])):\n"
        "    ev = {'tool': 'r.read', 'action': 'w' + sys.argv[5] + ' n' + str(i), 'verdict': 'allow', 'outcome': 'executed'}\n"
        "    assert a.main(['log', sys.argv[2], '--charter', sys.argv[3], '--event', json.dumps(ev)]) == 0\n"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), str(env.log), str(env.charter), str(each), str(w)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        for w in range(workers)
    ]  # fmt: skip
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
    r = al.verify(str(env.log))
    assert r["ok"], r["problems"][:3]
    assert r["entries"] == workers * each
    assert [e["seq"] for e in entries(env.log)] == list(range(1, workers * each + 1))


@pytest.fixture(scope="module")
def huge(tmp_path_factory):
    """A 20,000-entry log built straight with the chain functions, so the fixture itself is quick."""
    path = tmp_path_factory.mktemp("huge") / "huge.jsonl"
    prev, out = "0" * 64, []
    for i in range(1, 20001):
        e = {"seq": i, "ts": f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}T09:00:00Z", "kind": "action", "tool": f"t{i % 7}",
             "action": "step " + "x" * 120, "verdict": ("allow", "ask", "deny")[i % 3], "outcome": "executed",
             "amount": i % 50, "flags": [], "prev": prev}  # fmt: skip
        prev = e["hash"] = al.digest(e)
        out.append(al.canon(e))
    path.write_text("\n".join(out) + "\n")
    return path


def test_verify_and_report_handle_a_huge_log(huge):
    start = time.monotonic()
    r = al.verify(str(huge))
    assert r["ok"] and r["entries"] == 20000
    rep = al.report(str(huge), as_of="2026-06-15")
    assert rep["entries"] == 20000 and len(rep["by_tool"]) == 7
    assert time.monotonic() - start < 30


def test_appending_to_a_huge_log_reads_only_the_tail(huge, env, tmp_path):
    copy = tmp_path / "copy.jsonl"
    copy.write_bytes(huge.read_bytes())
    start = time.monotonic()
    al.main(["log", str(copy), "--charter", str(env.charter), "--event", json.dumps(action()), "--ts", TS])
    assert time.monotonic() - start < 1.0
    assert entries_tail(copy)["seq"] == 20001


def entries_tail(path):
    return json.loads(path.read_text().splitlines()[-1])


# --- standalone ------------------------------------------------------------------------------------------------


def test_runs_standalone_from_an_empty_folder(env, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    log = empty / "a.jsonl"
    done = run_script("audit_log.py", "log", log, "--charter", env.charter, "--event", json.dumps(action()), cwd=empty)
    assert done.returncode == 0 and [p.name for p in empty.iterdir()] == ["a.jsonl"]
    assert run_script("audit_log.py", "verify", log, cwd=empty).returncode == 0
    assert run_script("audit_log.py", "report", log, cwd=empty).returncode == 0


def test_a_symlink_to_an_empty_file_is_refused_too(env):
    target = env.dir / "empty.txt"
    target.write_text("")
    link = env.dir / "link2.jsonl"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("no symlinks here")
    code = al.main(["log", str(link), "--charter", str(env.charter), "--event", json.dumps(action())])
    assert code == 2 and target.read_text() == ""
