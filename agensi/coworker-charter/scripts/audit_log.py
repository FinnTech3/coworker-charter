#!/usr/bin/env python3
"""audit_log.py: a hash-chained audit log for an AI coworker.

  log    LOGFILE --charter CHARTER.json --event JSON|-    append one entry (the only write this script makes)
  verify LOGFILE [--head SEQ:HASH] [--json]               check the chain and say which line broke
  report LOGFILE [--since D] [--until D] [--as-of D] [--json]    decisions, asks, denials, spend, by tool and day

Standard library only. Appends only to the LOGFILE you name. No network.
Exit codes: 0 ok, 1 findings (flagged entry, broken chain, violations), 2 bad input.
Tamper evidence, not tamper proof: whoever can write the file can rebuild the chain, and entries cut off
the end go unnoticed unless you keep the head (SEQ:HASH) where the agent cannot write.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
import unicodedata
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no lock is taken, so run one writer at a time
    fcntl = None

GENESIS, MAX_LINE, MAX_EVENT, MAX_NUM = "0" * 64, 262144, 16384, 10**15  # 11 text fields of 1000 characters fit
KINDS = ("action", "takeover", "release", "escalation", "charter_change", "note")
VERDICTS, OUTCOMES = ("allow", "ask", "deny"), ("executed", "declined", "blocked", "failed", "escalated")
FIELDS = ("kind", "tool", "action", "verdict", "rule", "outcome", "amount", "recipient", "approved_by")
FIELDS += ("approval_ref", "actor", "by", "note")
SELF = {"agent", "self", "me", "myself", "auto", "system", "bot", "assistant"}


class Bad(Exception):
    """Input the tool cannot work with (exit 2)."""


def safe(s) -> str:
    """Escape control and invisible characters so text is safe to print."""
    bad = ("Cc", "Cf", "Zl", "Zp")
    return "".join(c.encode("unicode_escape").decode() if unicodedata.category(c) in bad else c for c in str(s))


def _pairs(pairs):
    d: dict = {}
    for k, v in pairs:
        if k in d:
            raise Bad(f"duplicate key {k!r}")
        d[k] = v
    return d


def _const(name):
    raise Bad(f"{name} is not valid JSON here")


def parse(text: str):
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_const)
    except (RecursionError, ValueError) as e:
        raise Bad(f"not valid JSON ({e})") from None


def canon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(entry: dict) -> str:
    return hashlib.sha256(canon({k: v for k, v in entry.items() if k != "hash"}).encode("ascii")).hexdigest()


def sok(x) -> bool:
    return isinstance(x, str) and bool(x.strip())


def clean_event(ev) -> dict:
    """Validate an event the agent wants to record. Raise Bad with a message the agent can act on."""
    if not isinstance(ev, dict):
        raise Bad("the event must be a JSON object")
    if extra := sorted(set(ev) - set(FIELDS)):
        raise Bad(f"unknown event field(s) {extra}; allowed: {', '.join(FIELDS)}")
    ev = {"kind": "action", **ev}
    if ev["kind"] not in KINDS:
        raise Bad(f"kind must be one of {', '.join(KINDS)}")
    for k, v in ev.items():
        if k == "amount":
            if not (isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= MAX_NUM):
                raise Bad("amount must be a number from 0 to 1e15")
        elif not sok(v) or len(v) > 1000:
            raise Bad(f"{k} must be a non-empty string of at most 1000 characters")
    kind = ev["kind"]
    need = ("tool", "action", "verdict", "outcome") if kind == "action" else ("note",)
    need += ("by",) if kind in ("takeover", "release", "charter_change") else ()
    if missing := [k for k in need if k not in ev]:
        raise Bad(f"a {kind} event needs {', '.join(missing)}")
    if kind == "action" and (ev["verdict"] not in VERDICTS or ev["outcome"] not in OUTCOMES):
        raise Bad(f"verdict must be one of {', '.join(VERDICTS)}; outcome one of {', '.join(OUTCOMES)}")
    if isinstance(ev.get("amount"), float) and ev["amount"] == int(ev["amount"]):
        ev["amount"] = int(ev["amount"])
    return ev


def read_charter(path: str):
    try:
        with Path(path).open("rb") as f:
            raw = f.read(1_000_001)
        ch = json.loads(raw.decode("utf-8-sig"), parse_float=Decimal)
    except (OSError, ValueError) as e:
        raise Bad(f"cannot use the charter {path}: {e}") from None
    if len(raw) > 1_000_000 or not isinstance(ch, dict):
        raise Bad(f"{path} is not a usable charter")
    return ch, hashlib.sha256(raw).hexdigest()


def dig(o, *keys):
    for k in keys:
        o = o.get(k) if isinstance(o, dict) else None
    return o


def flags_for(ev: dict, ch: dict) -> list[str]:
    """Process violations visible in one executed action, judged against the charter."""
    if ev["kind"] != "action" or ev["outcome"] != "executed":
        return []
    out = ["executed_despite_deny"] if ev["verdict"] == "deny" else []
    if dig(ch, "tools", ev["tool"], "tier") is None:
        out.append("unknown_tool_executed")
    if ev["verdict"] == "ask":
        who = ev.get("approved_by", "").casefold()
        people = [dig(ch, "owner")] + [a for a in (ch.get("approvers") or []) if isinstance(a, dict)]
        known = {str(p[k]).casefold() for p in people if isinstance(p, dict) for k in ("name", "contact") if k in p}
        me = SELF | {str(ev.get("actor", "")).casefold(), str(ch.get("agent_name", "")).casefold()}
        if not who:
            out.append("executed_without_approval")
        elif who in me:
            out.append("self_approved")
        elif who not in known:
            out.append("approver_not_in_charter")
        elif "approval_ref" not in ev:
            out.append("approval_ref_missing")
    cap = dig(ch, "budget", "per_action_max")
    if "amount" in ev and isinstance(cap, int | Decimal) and Decimal(repr(ev["amount"])) > cap:
        out.append("over_cap_executed")
    return out


def lock(fd: int, seconds: float = 10.0):
    """Take an advisory lock, waiting up to `seconds`. No lock on Windows."""
    end = time.monotonic() + seconds
    while fcntl:
        try:
            return fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if time.monotonic() > end:
                raise Bad("the log is locked by another process; try again soon") from None
            time.sleep(0.02)


def tail(fd: int):
    """The last entry of an open log, or None for an empty log. Reads only the tail, however big the file."""
    size = os.lseek(fd, 0, os.SEEK_END)
    if size == 0:
        return None
    os.lseek(fd, max(0, size - MAX_LINE - 1), os.SEEK_SET)
    chunk = os.read(fd, MAX_LINE + 1)
    try:
        if not chunk.endswith(b"\n"):
            raise ValueError
        entry = json.loads(chunk[:-1].rsplit(b"\n", 1)[-1].decode("utf-8"))
        if digest(entry) != entry["hash"]:
            raise ValueError
    except (ValueError, KeyError, TypeError, RecursionError):
        raise Bad("the end of the log is cut off or corrupt; run verify. Nothing was written") from None
    return entry


def cmd_log(a) -> int:
    ch, sha = read_charter(a.charter)
    text = sys.stdin.read(MAX_EVENT + 1) if a.event == "-" else a.event
    if len(text) > MAX_EVENT:
        raise Bad(f"the event is over {MAX_EVENT} characters")
    ev = clean_event(parse(text))
    ts = a.ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise Bad("--ts must look like 2026-11-14T09:30:00Z") from None
    flags, tier = flags_for(ev, ch), dig(ch, "tools", ev.get("tool"), "tier")
    mode = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    if os.path.islink(a.logfile):
        raise Bad(f"cannot open {a.logfile}: a symlink is refused on purpose")
    try:
        fd = os.open(a.logfile, mode, 0o600)
    except OSError as e:
        raise Bad(f"cannot open {a.logfile}: {e.strerror or e} (a symlink is refused on purpose)") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Bad(f"{a.logfile} is not a regular file")
        lock(fd)
        last = tail(fd)
        entry = {"seq": last["seq"] + 1 if last else 1, "ts": ts, **ev, **({"tier": tier} if tier else {})}
        entry |= {"charter_sha256": sha, "flags": flags, "prev": last["hash"] if last else GENESIS}
        entry["hash"] = digest(entry)
        data = (canon(entry) + "\n").encode("ascii")
        while data:
            data = data[os.write(fd, data) :]
        os.fsync(fd)
    finally:
        os.close(fd)
    if a.json:
        print(json.dumps(entry, indent=2))
        return 1 if flags else 0
    print(f"logged seq {entry['seq']}: {ev.get('verdict', ev['kind'])} {ev.get('outcome', '')}".rstrip())
    print(f"head: {entry['seq']}:{entry['hash']}")
    for f in flags:
        print(f"FLAG {f}. Tell the human.")
    return 1 if flags else 0


def walk(path: str):
    """Yield (line number, entry or None, problems) for every line. Streams, so memory stays bounded."""
    try:
        f = Path(path).open("rb")
    except OSError as e:
        raise Bad(f"cannot read {path}: {e.strerror or e}") from None
    prev, seq, n = GENESIS, 0, 0
    with f:
        while raw := f.readline(MAX_LINE + 1):
            n, seq, probs, entry = n + 1, seq + 1, [], None
            if len(raw) > MAX_LINE and not raw.endswith(b"\n"):
                while (more := f.readline(MAX_LINE)) and not more.endswith(b"\n"):
                    pass
                yield n, None, [f"line is longer than {MAX_LINE} bytes"]
                prev = None
                continue
            try:
                entry = parse(raw.decode("utf-8"))
                keys = all(sok(entry.get(k)) for k in ("ts", "prev", "hash"))
                if not (isinstance(entry, dict) and isinstance(entry.get("seq"), int) and keys):
                    raise Bad("not an entry (needs seq, ts, prev and hash)")
            except (UnicodeDecodeError, Bad, AttributeError) as e:
                yield (
                    n,
                    None,
                    [f"unreadable: {e if isinstance(e, Bad) else 'not valid UTF-8'} (blank, cut off or edited)"],
                )
                prev = None
                continue
            if not raw.endswith(b"\n"):
                probs.append("no newline at the end (an append was cut short, or the line was edited)")
            if entry["seq"] != seq:
                how = "entries are missing before this line" if entry["seq"] > seq else "out of order or repeated"
                probs.append(f"seq is {entry['seq']} but {seq} was expected ({how})")
            if prev is not None and entry["prev"] != prev:
                probs.append("prev hash does not match the line above (entries missing, replaced or reordered)")
            try:
                same = digest(entry) == entry["hash"]
            except (ValueError, TypeError, RecursionError):
                same = False
            if not same:
                probs.append("content does not match its own hash (the line was edited)")
            prev, seq = entry["hash"], entry["seq"]
            yield n, entry, probs


def verify(path: str, head=None) -> dict:
    anchor = re.fullmatch(r"(\d+):([0-9a-fA-F]{8,64})", head) if head else None
    if head and not anchor:
        raise Bad("--head must look like 12:3fa9c21e... (the SEQ:HASH that log and verify print)")
    res = {"ok": True, "entries": 0, "lines": 0, "head_seq": 0, "head_hash": GENESIS, "problems": [], "anchor": None}
    at = None
    for n, e, probs in walk(path):
        res["lines"] = n
        res["problems"] += [{"line": n, "message": "; ".join(probs)}] if probs else []
        if e is not None:
            res["entries"] += 1
            res["head_seq"], res["head_hash"] = e["seq"], e["hash"]
            if anchor and e["seq"] == int(anchor[1]):
                at = (n, e["hash"])
    if anchor:
        want, short = int(anchor[1]), anchor[2].lower()
        if res["head_seq"] < want:
            msg = f"cut off: the log ends at seq {res['head_seq']} but your anchor is seq {want}"
        elif at is None or not at[1].startswith(short):
            msg = f"seq {want} does not match your anchor (rewritten, or not this log)"
        else:
            msg = None
            res["anchor"] = f"matches at line {at[0]}; {res['head_seq'] - want} entries added since"
        if msg:
            res["problems"].append({"line": res["lines"], "message": msg})
    res["ok"] = not res["problems"]
    res["first_break"] = res["problems"][0]["line"] if res["problems"] else None
    return res


def cmd_verify(a) -> int:
    r = verify(a.logfile, a.head)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0 if r["ok"] else 1
    if r["ok"]:
        print(f"OK: {r['entries']} entries, chain intact." + (f" Anchor {r['anchor']}." if r["anchor"] else ""))
    else:
        print(f"BROKEN: {len(r['problems'])} problem(s). First break at line {r['first_break']}.")
        for p in r["problems"][:20]:
            print(f"  line {p['line']}: {p['message']}")
    print(f"head: {r['head_seq']}:{r['head_hash']}")
    return 0 if r["ok"] else 1


COLS = ("entries", "allow", "ask", "deny", "executed", "spend")


def report(path: str, since=None, until=None, as_of=None) -> dict:
    """Stream the log once. Counts only entries inside the period; today and month spend ignore the period."""
    as_of = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r = {"as_of": as_of, "entries": 0, "decisions": Counter(), "asks": Counter(), "denials": Counter(), "flags": []}
    r |= {"events": [], "charters": {}, "by_tool": {}, "by_day": {}, "spend": {"total": 0, "today": 0, "month": 0}}
    problems, head = [], (0, GENESIS)
    for n, e, probs in walk(path):
        problems += [{"line": n, "message": "; ".join(probs)}] if probs else []
        if e is None:
            continue
        head = (e["seq"], e["hash"])
        day, o, tool = e["ts"][:10], e.get("outcome"), str(e.get("tool"))
        v = e["verdict"] if e.get("verdict") in VERDICTS else None
        act, amt = e.get("kind", "action") == "action", e.get("amount")
        ok = isinstance(amt, int | float) and not isinstance(amt, bool) and math.isfinite(amt) and 0 <= amt <= MAX_NUM
        paid = Decimal(repr(amt)) if act and o == "executed" and ok else 0
        r["spend"]["today"] += paid if day == as_of else 0
        r["spend"]["month"] += paid if day[:7] == as_of[:7] else 0
        if (since and day < since) or (until and day > until):
            continue
        r["entries"] += 1
        c = r["charters"].setdefault(str(e.get("charter_sha256")), {"first_seq": e["seq"], "entries": 0})
        c["last_seq"], c["entries"] = e["seq"], c["entries"] + 1
        if isinstance(e.get("flags"), list) and e["flags"]:
            r["flags"].append({"seq": e["seq"], "ts": e["ts"], "tool": tool, "flags": [str(x) for x in e["flags"]]})
        if not act:
            r["events"].append({k: str(e.get(k, "")) for k in ("seq", "ts", "kind", "by", "note")})
            continue
        if v:
            r["decisions"][v] += 1
        r["spend"]["total"] += paid
        if v == "ask":
            clean = o == "executed" and e.get("approved_by") and not e.get("flags")
            r["asks"][
                "approved and done" if clean else "ran without valid approval" if o == "executed" else str(o)
            ] += 1
        if v == "deny":
            r["denials"][str(e.get("rule", "unknown"))] += 1
        for table, key, tier in ((r["by_tool"], tool, str(e.get("tier", "?"))), (r["by_day"], day, "")):
            row = table.setdefault(key, dict.fromkeys(COLS, 0) | ({"tier": tier} if tier else {}))
            row["entries"] += 1
            if v:
                row[v] += 1
            row["executed"] += o == "executed"
            row["spend"] += paid
    r["verify"] = {"ok": not problems, "problems": problems, "head": f"{head[0]}:{head[1]}"}
    return r


def md_table(head, rows):
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    return lines + ["| " + " | ".join(map(str, row)) + " |" for row in rows]


def cmd_report(a) -> int:
    for d in (a.since, a.until, a.as_of):
        try:
            date.fromisoformat(d) if d else None
        except ValueError:
            raise Bad(f"{d!r} is not a date; use YYYY-MM-DD") from None
    r = report(a.logfile, a.since, a.until, a.as_of)
    bad = r["verify"]["problems"]
    code = 1 if bad or r["flags"] else 0
    if a.json:
        print(json.dumps(r, indent=2, default=str))
        return code
    d, s = r["decisions"], r["spend"]
    out = [f"# Audit report for {safe(a.logfile)}", "", f"Period {a.since or 'start'} to {a.until or 'now'}:"]
    out[-1] += f" {r['entries']} entries."
    if bad:
        out.append(f"CHAIN BROKEN, first at line {bad[0]['line']}: {bad[0]['message']}. Treat the rest as unproven.")
    else:
        out.append(f"Chain intact. Head {r['verify']['head']}")
    seen = ", ".join(f"{safe(k)[:12]} (seq {v['first_seq']}-{v['last_seq']})" for k, v in r["charters"].items())
    out.append(f"Charter versions seen: {seen or 'none'}.")
    out += ["", "## Decisions", "", f"allow {d['allow']}, ask {d['ask']}, deny {d['deny']}."]
    if r["asks"]:
        out.append("Asks: " + ", ".join(f"{k} {v}" for k, v in sorted(r["asks"].items())) + ".")
    if r["denials"]:
        out.append("Denials by rule: " + ", ".join(f"{safe(k)} x{v}" for k, v in r["denials"].most_common()) + ".")
    out += ["", "## Spend (executed, in the charter's currency)", ""]
    out.append(f"Total {s['total']}. Today ({r['as_of']}) {s['today']}. This month {s['month']}.")
    rows = [[safe(k), safe(v["tier"])] + [v[x] for x in COLS] for k, v in sorted(r["by_tool"].items())]
    out += ["", "## By tool", ""] + md_table(["Tool", "Tier", *map(str.title, COLS)], rows)
    rows = [[k] + [v[x] for x in COLS] for k, v in sorted(r["by_day"].items())]
    out += ["", "## By day", ""] + md_table(["Day", *map(str.title, COLS)], rows)
    if r["flags"]:
        out += ["", "## Flags: process violations", ""]
        out += [f"- seq {f['seq']} ({f['ts']}) {safe(f['tool'])}: {', '.join(f['flags'])}" for f in r["flags"]]
    if r["events"]:
        out += ["", "## Take-overs, escalations, charter changes", ""]
        out += [
            f"- seq {x['seq']} ({x['ts']}) {x['kind']} by {safe(x['by'] or '-')}: {safe(x['note'])}"
            for x in r["events"]
        ]
    print("\n".join(out))
    return code


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(prog="audit_log.py", description=(__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("log", help="append one entry")
    p.add_argument("logfile")
    p.add_argument("--charter", required=True)
    p.add_argument("--event", required=True, help="the event as JSON, or - to read it from stdin")
    p.add_argument("--ts", help="override the timestamp, like 2026-11-14T09:30:00Z")
    p = sub.add_parser("verify", help="check the chain")
    p.add_argument("logfile")
    p.add_argument("--head", help="SEQ:HASH recorded earlier; catches cut-off entries")
    p = sub.add_parser("report", help="summarise the log")
    p.add_argument("logfile")
    p.add_argument("--since", help="first day to count, YYYY-MM-DD")
    p.add_argument("--until", help="last day to count, YYYY-MM-DD")
    p.add_argument("--as-of", help="the day to treat as today (default: today, UTC)")
    for q in sub.choices.values():
        q.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    try:
        return {"log": cmd_log, "verify": cmd_verify, "report": cmd_report}[a.cmd](a)
    except Bad as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # the reader (for example head) closed the pipe early
        sys.exit(1)
