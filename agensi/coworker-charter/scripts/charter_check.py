#!/usr/bin/env python3
"""charter_check.py: decide allow, ask or deny for one action, from a charter.

  charter_check.py CHARTER.json --tool NAME --action TEXT [--amount N] [--recipient X]
                   [--spent-today N] [--spent-month N] [--json]

A pure function of the charter file and the arguments: same inputs, same verdict, every time.
Standard library only. Reads the one charter file you name, writes nothing, no network.
Exit codes: 0 allow, 1 ask or deny, 2 bad input or an unusable charter (treat 2 as "ask a person").
It sees only what the agent says it will do. It cannot stop an agent that never calls it.
"""

from __future__ import annotations

import argparse
import difflib
import functools
import hashlib
import json
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path

TIERS = ("read", "write", "send", "spend", "delete", "publish")
CONDS = ("tools", "tiers", "action_contains", "amount_over", "amount_at_most")
CONDS += ("recipient_domain_in", "recipient_domain_not_in")
RANK = {"allow": 0, "ask": 1, "deny": 2}
MAX_BYTES, MAX_ACTION, MAX_NUM = 1_000_000, 10_000, Decimal(10) ** 15
NEXT = {
    "allow": "Proceed, then log the action.",
    "ask": "Send tell_human to the approver, wait for an explicit yes, and do nothing until it arrives.",
    "deny": "Stop. Do not retry, rephrase or use another tool. Tell the human and log the denial.",
}


class Bad(Exception):
    """Input the tool cannot work with (exit 2)."""


def safe(s) -> str:
    """Escape control and invisible characters so text is safe to print."""
    bad = ("Cc", "Cf", "Zl", "Zp")
    return "".join(c.encode("unicode_escape").decode() if unicodedata.category(c) in bad else c for c in str(s))


def isnum(x) -> bool:
    return isinstance(x, int | Decimal) and not isinstance(x, bool) and abs(x) <= MAX_NUM


def sok(x) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _pairs(pairs):
    d: dict = {}
    for k, v in pairs:
        if k in d:
            raise Bad(f"duplicate key {k!r}: JSON allows it but the last one silently wins, so I refuse it")
        d[k] = v
    return d


def _const(name):
    raise Bad(f"{name} is not valid in a charter")


def load(path: str):
    """Return (charter dict, sha256 of the file bytes). Raise Bad on anything unusable."""
    try:
        with Path(path).open("rb") as f:
            raw = f.read(MAX_BYTES + 1)
    except OSError as e:
        raise Bad(f"cannot read {path}: {e.strerror or e}") from None
    if len(raw) > MAX_BYTES:
        raise Bad(f"{path} is over {MAX_BYTES // 1000} KB; a charter should be short enough to read")
    try:
        c = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_float=Decimal, parse_constant=_const)
    except UnicodeDecodeError:
        raise Bad(f"{path} is not valid UTF-8") from None
    except json.JSONDecodeError as e:
        raise Bad(f"{path} is not valid JSON: {e.msg} (line {e.lineno}, column {e.colno})") from None
    except (RecursionError, ValueError):
        raise Bad(f"{path} is not usable JSON (nested too deeply, or a number is too long)") from None
    if not isinstance(c, dict):
        raise Bad(f"{path} must hold one JSON object")
    return c, hashlib.sha256(raw).hexdigest()


def sane(c: dict):
    """First structural problem that would make a verdict unreliable, or None. Full checks: charter_lint.py."""
    t, b, o, e = c.get("tools"), c.get("budget"), c.get("owner"), c.get("escalation")
    if not (isinstance(t, dict) and t and all(isinstance(v, dict) and v.get("tier") in TIERS for v in t.values())):
        return "tools is missing, or a tool has no valid tier"
    caps = ("per_action_max", "per_day_max")
    if not (isinstance(b, dict) and sok(b.get("currency")) and all(isnum(b.get(k)) and b[k] >= 0 for k in caps)):
        return "budget needs a currency and numeric per_action_max and per_day_max"
    if "per_month_max" in b and not (isnum(b["per_month_max"]) and b["per_month_max"] >= 0):
        return "budget.per_month_max is not a number"
    if not (isinstance(o, dict) and sok(o.get("name")) and sok(o.get("contact"))):
        return "owner needs a name and a contact"
    if not (isinstance(e, dict) and sok(e.get("contact"))):
        return "escalation.contact is missing"
    if c.get("status", "active") not in ("active", "suspended"):
        return 'status must be "active" or "suspended"'
    if "fill in" in json.dumps(c, default=str).casefold():
        return "the charter still has FILL IN template text"
    for key in ("never", "approval_rules"):
        rules = c.get(key, [])
        if not isinstance(rules, list):
            return f"{key} must be a list"
        for r in rules:
            if not (isinstance(r, dict) and sok(r.get("id")) and sok(r.get("reason")) and r.get("when")):
                return f"a rule in {key} is missing its id, reason or when"
            if not isinstance(r["when"], dict) or r.get("decision", "ask") not in tuple(RANK):
                return f"rule {r['id']} is malformed"
            for k, v in r["when"].items():
                if k not in CONDS:
                    return f"rule {r['id']} uses unknown condition {k!r}"
                if not (isnum(v) if k.startswith("amount") else isinstance(v, list) and all(sok(x) for x in v)):
                    return f"rule {r['id']} has a malformed {k}"
    return None


def norm(s: str) -> str:
    """NFKC, drop invisible format characters, case-fold, collapse whitespace."""
    s = unicodedata.normalize("NFKC", s)
    return " ".join("".join(ch for ch in s if unicodedata.category(ch) != "Cf").casefold().split())


@functools.lru_cache(maxsize=512)
def phrase(p: str):
    words = norm(p).split()
    return re.compile(r"(?<!\w)" + " ".join(map(re.escape, words)) + r"(?!\w)") if words else None


def has(text: str, p: str) -> bool:
    rx = phrase(p)
    return bool(rx and rx.search(text))


HOSTLIKE = re.compile(r"([a-z][a-z0-9+.-]*://\S+|[a-z0-9_-]+(\.[a-z0-9_-]+)*\.[a-z]{2,}(:\d+)?(/\S*)?)")


def domains(rec):
    """Domain of every recipient in a string, or None when nothing usable was given.

    Every comma or semicolon separated part counts. In a part with an address, a bare host name next
    to it (an address followed by a host name) is a second recipient, not a display name.
    """
    if rec is None:
        return None
    found = []
    for part in re.split(r"[,;]+", norm(rec)):
        at = re.findall(r"@([^\s,;<>()\"']+)", part)
        loose = [t for t in re.sub(r"<[^>]*>", " ", part).split() if "@" not in t]
        found += at + [t.strip("\"'()<>") for t in loose if not at or HOSTLIKE.fullmatch(t.strip("\"'()<>"))]
    out = [re.sub(r":\d+$", "", re.sub(r"^[a-z][a-z0-9+.-]*://", "", d).split("/")[0]).strip(".") for d in found]
    return out or None


def cond(when: dict, ctx: tuple, miss: set):
    """True, False, or None when an input the rule needs was not supplied (the input is added to miss)."""
    tool, tier, text, amount, doms = ctx
    unknown = False
    for k, v in when.items():
        if k == "tools":
            ok = tool in v
        elif k == "tiers":
            ok = tier in v
        elif k == "action_contains":
            ok = any(has(text, p) for p in v)
        elif k.startswith("amount"):
            if amount is None:
                miss.add("the amount")
                unknown = True
                continue
            ok = amount > v if k == "amount_over" else amount <= v
        else:
            if doms is None:
                miss.add("the recipient")
                unknown = True
                continue
            inside = [d in [norm(x) for x in v] for d in doms]
            ok = all(inside) if k == "recipient_domain_in" else not all(inside)
        if not ok:
            return False
    return None if unknown else True


def verdict(c, tool, text, amount, doms, day, month, notes):
    """Return (decision, rule, reason, approver). Order: suspended, unknown tool, boundaries, never, caps, rules."""
    if c.get("status") == "suspended":
        return (
            "deny",
            "charter-suspended",
            "The owner has suspended this charter, so nothing may run until they lift it.",
            None,
        )
    tools = c["tools"]
    if tool not in tools:
        near = difflib.get_close_matches(tool, list(tools), 1)
        hint = f" (did you mean {near[0]!r}?)" if near else ""
        return "deny", "unknown-tool", f"{safe(tool)!r} is not a tool in the charter{hint}.", None
    tier, miss, unsure, maybe = tools[tool]["tier"], set(), [], []
    bd = c.get("data_boundaries") or {}
    for p in list(bd.get("must_not_access", [])) + list(bd.get("must_not_share", [])):
        if has(text, p):
            return "deny", "data-boundary", f"The action mentions {p!r}, which the charter puts out of bounds.", None
    ctx = (tool, tier, text, amount, doms)
    for r in c.get("never", []):
        m = cond(r["when"], ctx, miss)
        if m:
            return "deny", r["id"], r["reason"], None
        if m is None:
            unsure.append(r["id"])
    b = c["budget"]
    if amount is not None:
        caps = (
            ("budget.per_action_max", 0, b["per_action_max"], "per-action"),
            ("budget.per_day_max", day, b["per_day_max"], "daily"),
            ("budget.per_month_max", month, b.get("per_month_max"), "monthly"),
        )
        for rule, spent, cap, label in caps:
            if cap is not None and spent is not None and spent + amount > cap:
                return "deny", rule, f"{amount} {b['currency']} would take spend over the {label} cap of {cap}.", None
    hits = []
    for r in c.get("approval_rules", []):
        m, d = cond(r["when"], ctx, miss), r.get("decision", "ask")
        if m:
            hits.append(r)
        elif m is None:
            (maybe if d == "allow" else unsure).append(r["id"])
    deny = [r for r in hits if r.get("decision") == "deny"]
    if deny:
        return "deny", deny[0]["id"], deny[0]["reason"], None
    if hits:
        top = max("tools" in r["when"] for r in hits)  # a rule that names the tool beats one that names a tier
        pool = [r for r in hits if ("tools" in r["when"]) == top]
        win = max(pool, key=lambda r: RANK[r.get("decision", "ask")])
        d, rule, reason, who = win.get("decision", "ask"), win["id"], win["reason"], win.get("approver", "owner")
    elif tier == "read":
        d, rule, who = "allow", "read-default", None
        reason = "Read-tier tools are allowed unless a rule or boundary says otherwise."
    else:
        d, rule, who = "ask", "default-ask", "owner"
        reason = f"No rule lets a {tier}-tier action on {tool!r} run without a person, so I must ask."
        if maybe:
            rule = "unverifiable"
            reason = f"Rule {', '.join(maybe)} could allow this, but I was not given {' or '.join(sorted(miss))}."
    if tier == "spend" and amount is None:
        miss.add("the amount")
    elif tier == "spend":
        if day is None and "per_day_max" in b:
            miss.add("today's spend so far")
        if month is None and b.get("per_month_max") is not None:
            miss.add("this month's spend so far")
    if miss:
        notes.append("Not supplied: " + ", ".join(sorted(miss)) + ". Rules and caps that need it were not checked.")
    if d == "allow" and (miss or unsure):
        why = " or ".join(sorted(miss)) or "enough detail"
        return "ask", "unverifiable", f"I cannot confirm this is allowed because I was not given {why}.", "owner"
    return d, rule, reason, who


def person(c, who):
    """The owner, or the listed approver called `who`, as a dict with a name and a contact."""
    for a in c.get("approvers", []) if who not in (None, "owner") else []:
        if a.get("name") == who:
            return a
    return c["owner"]


def tell(c, r, p):
    """The plain-English message the agent shows the human."""
    act = safe(r["action"])
    if r["decision"] == "ask":
        lines = [
            "Approval needed before I act.",
            f"What will happen: {act}",
            f"Tool: {safe(r['tool'])} ({r['tier']} tier)",
        ]
        if r["amount"] is not None:
            lines.append(f"Amount: {r['amount']} {c['budget']['currency']}")
        if r["recipient"]:
            lines.append(f"Recipient: {safe(r['recipient'])}")
        lines.append(f"Why I am asking: {safe(r['reason'])} (rule {r['rule']})")
        lines += [f"Note: {n}" for n in r["notes"]]
        lines.append(f"Who can approve: {p['name']} ({p.get('contact', 'no contact listed')})")
        lines.append('Reply "approve" to go ahead or "decline" to stop. I will not do anything until you answer.')
        return "\n".join(lines)
    if r["decision"] == "deny":
        o = c["owner"]
        return (
            f"I will not do this: {act}\nWhy: {safe(r['reason'])} (rule {r['rule']})\n"
            "I have not done it and I will not try another way round it.\n"
            f"Only {o['name']} ({o['contact']}) can change what is allowed, by editing the charter. "
            f"Escalation contact: {c['escalation']['contact']}."
        )
    return None


def decide(c, sha, tool, action, amount=None, recipient=None, day=None, month=None) -> dict:
    notes: list[str] = []
    d, rule, reason, who = verdict(c, tool, norm(action), amount, domains(recipient), day, month, notes)
    p = person(c, who)
    r = {
        "decision": d, "rule": rule, "reason": reason, "tool": tool, "tier": (c["tools"].get(tool) or {}).get("tier"),
        "action": action, "amount": amount, "recipient": recipient, "approver": p["name"] if d == "ask" else None,
        "notes": notes, "charter_sha256": sha,
    }  # fmt: skip
    r["tell_human"], r["next"] = tell(c, r, p), NEXT[d]
    return r


def jsonable(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(type(o).__name__)


def num(s, name):
    try:
        d = Decimal(str(s).strip())
    except (InvalidOperation, ValueError):
        raise Bad(f"{name} must be a plain number such as 42.50, got {safe(s)!r}") from None
    if not d.is_finite() or d < 0 or d > MAX_NUM:
        raise Bad(f"{name} must be between 0 and 1e15, got {safe(s)!r}")
    return Decimal(format(d, "f"))  # 1e2 reads as 100 in the message to the person


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(prog="charter_check.py", description=(__doc__ or "").splitlines()[0])
    ap.add_argument("charter")
    ap.add_argument("--tool", required=True)
    ap.add_argument("--action", required=True, help="plain English: exactly what will happen")
    ap.add_argument("--amount", help="money the action moves, as a plain number")
    ap.add_argument("--recipient", help="who it goes to: an address, a domain, or several separated by commas")
    ap.add_argument("--spent-today", help="money already spent today (from the audit report)")
    ap.add_argument("--spent-month", help="money already spent this month")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    try:
        c, sha = load(a.charter)
        if problem := sane(c):
            raise Bad(f"charter is not usable: {problem}. Run charter_lint.py lint and fix it; no verdict without one")
        if not sok(a.tool) or not sok(a.action):
            raise Bad("--tool and --action must not be empty")
        if len(a.action) > MAX_ACTION:
            raise Bad(f"--action is over {MAX_ACTION} characters; say in a few sentences what will happen")
        n = [(a.amount, "--amount"), (a.spent_today, "--spent-today"), (a.spent_month, "--spent-month")]
        amount, day, month = (None if v is None else num(v, name) for v, name in n)
        try:
            r = decide(c, sha, a.tool.strip(), a.action, amount, a.recipient, day, month)
        except (KeyError, TypeError, AttributeError, ValueError):
            raise Bad("charter is not usable: its structure is wrong. Run charter_lint.py lint") from None
    except Bad as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(r, indent=2, default=jsonable))
        return 0 if r["decision"] == "allow" else 1
    tier = f"{r['tier']} tier" if r["tier"] else "not in the charter"
    print(f"decision: {r['decision']}\nrule:     {r['rule']}\ntool:     {safe(r['tool'])} ({tier})")
    print(f"why:      {safe(r['reason'])}")
    for note in r["notes"]:
        print(f"note:     {note}")
    print(f"next:     {r['next']}")
    if r["tell_human"]:
        print("\nTell the human:\n" + "\n".join("  " + ln for ln in r["tell_human"].splitlines()))
    return 0 if r["decision"] == "allow" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # the reader (for example head) closed the pipe early
        sys.exit(1)
