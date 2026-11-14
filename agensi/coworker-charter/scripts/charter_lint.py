#!/usr/bin/env python3
"""charter_lint.py: start and validate the charter of an AI coworker.

  init                                    print a starter charter (JSON)
  lint CHARTER.json [--strict] [--json]   report errors and warnings

Standard library only. Reads the charter file you name, writes nothing, no network.
Exit codes: 0 no errors (and no warnings with --strict), 1 findings, 2 bad input.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path

TIERS = ("read", "write", "send", "spend", "delete", "publish")
TOP = ("charter_version", "status", "role", "agent_name", "owner", "approvers", "escalation", "tools")
TOP += ("data_boundaries", "never", "approval_rules", "budget", "audit")
REQUIRED = ("charter_version", "role", "owner", "escalation", "tools", "approval_rules", "budget", "audit")
CONDS = ("tools", "tiers", "action_contains", "amount_over", "amount_at_most")
CONDS += ("recipient_domain_in", "recipient_domain_not_in")
MAX_BYTES, MAX_NUM = 1_000_000, Decimal(10) ** 15
HINTS = {
    "delete": "delete remove purge wipe destroy",
    "send": "send reply forward email sms",
    "spend": "pay refund charge purchase transfer payout",
    "publish": "publish deploy release post",
}
HINT = {word: tier for tier, words in HINTS.items() for word in words.split()}
VAGUE = re.compile(
    r"\b(judge?ment|appropriate(ly)?|as needed|if needed|when needed|(if|when|as) necessary|reasonabl[ey]|sensible"
    r"|common sense|significant(ly)?|substantial(ly)?|large|big|small|minor|major|important|sensitive|risky|etc"
    r"|and so on|and the like|where possible|try to|usually|typically|generally)\b",
    re.I,
)
PLACEHOLDER = re.compile(r"(?i)\b(fill in|todo|tbd|changeme|replace me)\b|<[A-Za-z][A-Za-z _-]{0,30}>")
SECRET_KEY = re.compile(r"(?i)(^|[_-])(password|passwd|secret|api[_-]?key|token|private[_-]?key|credentials?)$")
SECRETS = [
    (kind, re.compile(rx))
    for kind, rx in (
        ("cloud access key id", r"\bAKIA[0-9A-Z]{16}\b"),
        ("API key", r"\b(?:sk|rk|pk)[-_][A-Za-z0-9_-]{20,}"),
        ("code-host token", r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
        ("chat token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
        ("private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        ("JWT", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        ("password inside a URL", r"://[^/\s:@]+:[^/\s@]+@"),
        ("credential assignment", r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token|bearer)\b\s*[:=]\s*\S{6,}"),
        ("long hex string", r"\b[0-9a-fA-F]{40,}\b"),
        ("long random string", r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])[A-Za-z0-9]{32,}\b"),
    )
]
STARTER = {
    "charter_version": 1,
    "status": "active",
    "role": "FILL IN: the job this coworker does and who for, in one sentence",
    "agent_name": "FILL IN: the name used in the audit log",
    "owner": {"name": "FILL IN: the accountable person", "contact": "FILL IN: email or phone"},
    "approvers": [{"name": "FILL IN: another person who may approve", "contact": "FILL IN: email or phone"}],
    "escalation": {
        "contact": "FILL IN: who to reach when blocked",
        "when": [
            "a deny verdict on something a person asked for",
            "two declined approvals in a row",
            "an amount or recipient that cannot be confirmed",
        ],
        "response_minutes": 60,
    },
    "tools": {f"app.{t}": {"tier": t, "description": "FILL IN: what this tool does"} for t in TIERS},
    "data_boundaries": {
        "may_access": ["FILL IN: systems and folders it may read"],
        "must_not_access": ["FILL IN: a system or record type that is off limits"],
        "must_not_share": ["FILL IN: data that must never leave"],
    },
    "never": [
        {
            "id": "N1",
            "reason": "FILL IN: one thing that must never happen",
            "when": {"action_contains": ["FILL IN: a phrase that would appear in it"]},
        }
    ],
    "approval_rules": [
        {"id": f"A{i}", "reason": f"A {t}-tier action needs a person to confirm it first.", "when": {"tiers": [t]}}
        for i, t in enumerate(TIERS[1:], 1)
    ],
    "budget": {"currency": "USD", "per_action_max": 50, "per_day_max": 200, "per_month_max": 1000},
    "audit": {"retention_days": 365, "log_path": "coworker-audit.jsonl"},
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
        raise Bad(f"{path} must hold one JSON object (run init to see the shape)")
    return c, hashlib.sha256(raw).hexdigest()


def sub(path: str, key) -> str:
    """Path of a child key: .name, or ["dotted.name"] when the name would read ambiguously."""
    return f"{path}.{key}" if re.fullmatch(r"\w+", str(key)) else f'{path}["{key}"]'


def strings(x, path="$"):
    """Yield (path, text) for every string; yield (path, None) for a secret-named key holding a string."""
    if isinstance(x, dict):
        for k, v in x.items():
            if SECRET_KEY.search(str(k)) and sok(v):
                yield sub(path, k), None
            yield from strings(v, sub(path, k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from strings(v, f"{path}[{i}]")
    elif isinstance(x, str):
        yield path, x


class Lint:
    """One pass over a charter. Findings are dicts with level, code, path, message and fix."""

    def __init__(self, c: dict):
        self.c, self.out, self.seen, self.texts = c, [], set(), []
        self.tools = c["tools"] if isinstance(c.get("tools"), dict) else {}
        self.tier_of: dict = {}
        self.names = {"owner"}
        owner = c.get("owner")
        if isinstance(owner, dict) and sok(owner.get("name")):
            self.names.add(owner["name"])  # a rule may name the owner by name

    def e(self, code, path, msg, fix="", level="error"):
        self.out.append({"level": level, "code": code, "path": path, "message": msg, "fix": fix})

    def w(self, code, path, msg, fix=""):
        self.e(code, path, msg, fix, "warning")

    def run(self) -> list[dict]:
        c = self.c
        for k in c:
            if k not in TOP:
                near = difflib.get_close_matches(k, TOP, 1)
                fix = f"did you mean {near[0]!r}?" if near else ""
                self.w("unknown-key", k, f"unknown key {k!r}, so nothing reads it", fix)
        for k in REQUIRED:
            if k not in c:
                self.e("missing-section", k, f"section {k!r} is missing", "run init to see the shape")
        if "charter_version" in c and c["charter_version"] != 1:
            self.e("version", "charter_version", "only charter_version 1 exists", "")
        if c.get("status", "active") not in ("active", "suspended"):
            self.e("status", "status", 'status must be "active" or "suspended"')
        if "role" in c and not sok(c["role"]):
            self.e("missing-role", "role", "role is empty", "say what job this coworker does and for whom")
        self.people()
        self.tool_table()
        never, approvals = self.rules("never", True), self.rules("approval_rules", False)
        if not never:
            self.w("no-never-rules", "never", "nothing is listed as never allowed", "write down what must never happen")
        for name, tier in self.tier_of.items():
            if tier != "read" and not any(
                name in r["when"].get("tools", []) or tier in r["when"].get("tiers", []) for r in approvals
            ):
                self.e("tier-needs-rule", sub("tools", name), f"{name!r} is {tier} tier, no rule covers it", "add one")
        for path, x in self.texts:
            if m := VAGUE.search(x):
                self.e("vague-rule", path, f"vague wording {m.group(0)!r}", "use a number, a list or a named case")
        self.budget()
        self.audit_and_data()
        for path, s in strings(c):
            if s is None:
                self.e(
                    "secret-in-charter", path, "a secret-like key holds a value; never put secrets here", "remove it"
                )
                continue
            if PLACEHOLDER.search(s):
                self.e("placeholder", path, "still has template text", "replace it with the real value")
            for kind, rx in SECRETS:
                if rx.search(s):
                    self.e(
                        "secret-in-charter",
                        path,
                        f"looks like a {kind} (not shown); secrets never belong here",
                        "remove it",
                    )
                    break
        return self.out

    def people(self):
        c, o = self.c, self.c.get("owner")
        if "owner" in c and not (isinstance(o, dict) and sok(o.get("name")) and sok(o.get("contact"))):
            self.e("owner-missing", "owner", "the owner needs a name and a contact", "name one accountable person")
        ap = c.get("approvers", [])
        if not isinstance(ap, list) or not all(isinstance(a, dict) and sok(a.get("name")) for a in ap):
            self.e("approvers", "approvers", "approvers must be a list of objects that each have a name")
        else:
            self.names |= {a["name"] for a in ap}
        e = c.get("escalation")
        if "escalation" in c:
            if not (isinstance(e, dict) and sok(e.get("contact"))):
                self.e("escalation-missing", "escalation.contact", "no escalation contact", "name who to reach")
            w = e.get("when") if isinstance(e, dict) else None
            if not (isinstance(w, list) and w and all(sok(x) for x in w)):
                self.e("escalation-missing", "escalation.when", "list at least one trigger", "")
            else:
                self.texts += [(f"escalation.when[{i}]", x) for i, x in enumerate(w)]

    def tool_table(self):
        if "tools" in self.c and not self.tools:
            self.e("tools", "tools", "tools must be an object with at least one tool")
        for name, t in self.tools.items():
            p, tier = sub("tools", name), t.get("tier") if isinstance(t, dict) else None
            if re.search(r"[*?]", name) or name.strip().casefold() in ("", "all", "any", "everything"):
                self.e("wildcard-tool", p, f"{name!r} is a wildcard or blanket grant", "list each tool by exact name")
            if tier not in TIERS:
                self.e("tool-no-tier", f"{p}.tier", f"{name!r} has no valid tier", "use one of " + ", ".join(TIERS))
                continue
            self.tier_of[name] = tier
            if not sok(t.get("description")):
                self.w("tool-no-description", p, f"tool {name!r} has no description", "say what it does and where")
            implied = {HINT[x] for x in re.split(r"[^a-z0-9]+", name.lower()) if x in HINT}
            if tier in ("read", "write") and implied:
                self.w(
                    "tier-looks-low", f"{p}.tier", f"{name!r} is {tier} but its name says {'/'.join(sorted(implied))}"
                )

    def rules(self, key, never):
        lst, good = self.c.get(key, []), []
        if not isinstance(lst, list):
            self.e("rules", key, f"{key} must be a list")
            return good
        for i, r in enumerate(lst):
            p = f"{key}[{i}]"
            if not isinstance(r, dict):
                self.e("rules", p, "a rule must be an object")
            elif self.rule(p, r, never):
                good.append(r)
        return good

    def rule(self, p, r, never) -> bool:
        """Check one rule. True when it is well formed enough to count towards coverage."""
        if not sok(r.get("id")):
            self.e("rule-no-id", p, "rule has no id", 'give it a short unique id such as "A1"')
        elif r["id"] in self.seen:
            self.e("duplicate-id", p, f"rule id {r['id']!r} is used twice", "ids must be unique")
        else:
            self.seen.add(r["id"])
        if not sok(r.get("reason")):
            self.e("rule-no-reason", p, "rule has no reason", "one plain sentence; the human is shown it")
        else:
            self.texts.append((f"{p}.reason", r["reason"]))
        w, ok = r.get("when"), True
        if not isinstance(w, dict) or not w:
            self.e("rule-matches-everything", f"{p}.when", "a rule with no conditions matches everything", "add one")
            return False
        for k, v in w.items():
            q = f"{p}.when.{k}"
            if k not in CONDS:
                self.e("unknown-condition", q, f"unknown condition {k!r}", "use one of " + ", ".join(CONDS))
            elif k.startswith("amount") and not (isnum(v) and v >= 0):
                ok = False
                self.e("bad-condition", q, f"{k} must be a number of 0 or more")
            elif not k.startswith("amount") and not (isinstance(v, list) and v and all(sok(x) for x in v)):
                ok = False
                self.e("bad-condition", q, f"{k} must be a non-empty list of strings")
            elif k == "tools":
                for t in v:
                    if re.search(r"[*?]", t):
                        self.e("wildcard-tool", q, f"{t!r} is a wildcard", "list exact tool names")
                    elif t not in self.tools:
                        self.e("unknown-tool-ref", q, f"{t!r} is not in tools", "add the tool or fix the name")
            elif k == "tiers" and any(t not in TIERS for t in v):
                ok = False
                self.e("bad-condition", q, "tiers must come from " + ", ".join(TIERS))
        if never or not ok:
            return ok
        if "tools" not in w and "tiers" not in w:
            self.e("rule-matches-everything", f"{p}.when", "an approval rule must name tools or tiers", "add one")
            return False
        d = r.get("decision", "ask")
        if d not in ("ask", "allow", "deny"):
            self.e("bad-decision", f"{p}.decision", "decision must be ask, allow or deny")
        elif d == "ask" and not (
            isinstance(r.get("approver", "owner"), str) and r.get("approver", "owner") in self.names
        ):
            self.e(
                "unknown-approver",
                f"{p}.approver",
                f"{safe(r.get('approver'))!r} is not the owner or a listed approver",
            )
        elif d == "allow":
            ts = set(w.get("tiers", [])) | {self.tier_of.get(t) for t in w.get("tools", [])}
            if "send" in ts and "recipient_domain_in" not in w:
                self.e("auto-allow-unbounded", p, "allow on send needs recipient_domain_in", "name the domains")
            if "spend" in ts and "amount_at_most" not in w:
                self.e("auto-allow-unbounded", p, "allow on spend needs amount_at_most", "set a ceiling, or use ask")
            if ts & {"delete", "publish"}:
                self.w("auto-allow-delete-publish", p, "delete or publish runs without a person", "use ask unless sure")
        return True

    def budget(self):
        b = self.c.get("budget")
        if "budget" not in self.c:
            return
        b = b if isinstance(b, dict) else {}
        for k in ("per_action_max", "per_day_max", "per_month_max"):
            if (k in b or k != "per_month_max") and not (isnum(b.get(k)) and b[k] >= 0):
                self.e("spend-cap", f"budget.{k}", f"{k} must be a number, 0 or more", "0 means no spending")
        if not sok(b.get("currency")):
            self.e("spend-cap", "budget.currency", "budget needs a currency", 'for example "USD"')

    def audit_and_data(self):
        c, au = self.c, self.c.get("audit")
        if "audit" in c:
            r = au.get("retention_days") if isinstance(au, dict) else None
            if not isinstance(r, int) or isinstance(r, bool) or r < 1:
                self.e("audit-retention", "audit.retention_days", "must be a whole number of days, 1+", "e.g. 365")
            elif r < 90:
                self.w("short-retention", "audit.retention_days", f"{r} days is short for an audit trail", "90+")
        d, keys = c.get("data_boundaries"), ("may_access", "must_not_access", "must_not_share")
        lists = [d.get(k, []) for k in keys] if isinstance(d, dict) else [None]
        if not all(isinstance(x, list) and all(sok(i) for i in x) for x in lists):
            self.w("data-boundaries", "data_boundaries", "missing or malformed", "list " + ", ".join(keys))
        elif not d.get("may_access") or not (d.get("must_not_access") or d.get("must_not_share")):
            self.w("data-boundaries", "data_boundaries", "say what may be read and what is off limits")


def lint(c: dict) -> list[dict]:
    return Lint(c).run()


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(prog="charter_lint.py", description=(__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="print a starter charter")
    p = sub.add_parser("lint", help="validate a charter")
    p.add_argument("charter")
    p.add_argument("--strict", action="store_true", help="warnings also give exit 1")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    if a.cmd == "init":
        print(json.dumps(STARTER, indent=2))
        return 0
    try:
        c, sha = load(a.charter)
    except Bad as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    found = lint(c)
    errors = [f for f in found if f["level"] == "error"]
    if a.json:
        summary = {"errors": len(errors), "warnings": len(found) - len(errors), "findings": found}
        print(json.dumps(summary | {"charter_sha256": sha}, indent=2))
    else:
        for f in found:
            fix = f"  [fix: {f['fix']}]" if f["fix"] else ""
            print(f"{f['level'].upper():8}{f['code']:22} {f['path']}: {safe(f['message'])}{fix}")
        print(
            f"{len(errors)} error(s), {len(found) - len(errors)} warning(s)" if found else "OK: no errors, no warnings"
        )
    return 1 if errors or (a.strict and found) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # the reader (for example head) closed the pipe early
        sys.exit(1)
