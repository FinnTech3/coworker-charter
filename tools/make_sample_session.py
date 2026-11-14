#!/usr/bin/env python3
"""Run a real session of the three scripts and write it up as agensi/coworker-charter/examples/sample-session.md.

Every output block in that file is captured from a real run: nothing is typed in by hand. Timestamps are fixed
with --ts and --as-of so the run is repeatable, and a test regenerates the file and compares it.

    python3 tools/make_sample_session.py            # rewrite the example files
    python3 tools/make_sample_session.py --stdout   # print the markdown instead
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "agensi" / "coworker-charter"
EXAMPLES = SKILL / "examples"
NAMES = {"LINT": "charter_lint.py", "CHECK": "charter_check.py", "AUDIT": "audit_log.py"}
CHARTER = "charter.json"
LOG = "audit.jsonl"


class Session:
    """Runs commands in a scratch folder and keeps the markdown that describes them."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.md: list[str] = []

    def say(self, text: str) -> None:
        self.md += [text, ""]

    def run(self, shown: str, script: str, args: list[str], stdin: str | None = None, head: int | None = None) -> str:
        """Run one script with `args`, show it as `shown`, and record the real output and exit code."""
        done = subprocess.run(
            [sys.executable, "-I", str(SKILL / "scripts" / NAMES[script]), *args],
            input=stdin,
            capture_output=True,
            text=True,
            cwd=self.folder,
            timeout=60,
            check=False,
        )
        out = (done.stdout + done.stderr).rstrip("\n").splitlines()
        if head is not None:
            out = out[:head]
        self.md += ["```console", f"$ {shown}", *out, f"[exit {done.returncode}]", "```", ""]
        return done.stdout


def command(script: str, *parts: str) -> str:
    return " ".join([f'python3 "${script}"', *parts])


def build(folder: Path) -> str:
    shutil.copy(SKILL / "references" / "example-support-agent.json", folder / CHARTER)
    s = Session(folder)
    s.md += [
        "# A real session, start to finish",
        "",
        "Every block below is the captured output of a real run of the three scripts, from an empty folder. Nothing",
        "was edited by hand. A test regenerates this file with `tools/make_sample_session.py` and fails if a word",
        "differs. Timestamps are fixed with `--ts` and `--as-of` so the run repeats exactly.",
        "",
        "`$LINT`, `$CHECK` and `$AUDIT` stand for the temporary files your agent writes `charter_lint.py`,",
        "`charter_check.py` and `audit_log.py` to. The charter is `references/example-support-agent.json`, saved as",
        f"`{CHARTER}` after the interview.",
        "",
    ]

    s.say("## 1. Start from the template and see what is missing")
    init = subprocess.run(
        [sys.executable, "-I", str(SKILL / "scripts" / NAMES["LINT"]), "init"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    (folder / "starter.json").write_text(init)
    s.run(f"{command('LINT', 'init')} | head -24", "LINT", ["init"], head=24)
    s.say("The starter is not valid on purpose. Lint lists every blank that someone has to fill in:")
    s.run(command("LINT", "lint", "starter.json"), "LINT", ["lint", "starter.json"])

    s.say("## 2. Lint the finished charter")
    s.run(command("LINT", "lint", CHARTER, "--strict"), "LINT", ["lint", CHARTER, "--strict"])

    s.say("## 3. Five sample actions through `check`")
    s.say("The agent and the person run these together before the charter goes live, and ask: is that what you meant?")
    s.say("Read a ticket. Allowed, nobody is interrupted:")
    s.run(
        command("CHECK", CHARTER, "--tool helpdesk.search", '--action "Search open tickets about invoice 1182"'),
        "CHECK",
        [CHARTER, "--tool", "helpdesk.search", "--action", "Search open tickets about invoice 1182"],
    )
    s.say(
        "A refund of 8 USD, under the 10 USD ceiling in rule A4. Allowed, but only because today's spend was supplied:"
    )
    s.run(
        command(
            "CHECK",
            CHARTER,
            "--tool refunds.issue",
            '--action "Refund order 1182"',
            "--amount 8",
            "--spent-today 0",
            "--spent-month 0",
        ),
        "CHECK",
        [
            CHARTER,
            "--tool",
            "refunds.issue",
            "--action",
            "Refund order 1182",
            "--amount",
            "8",
            "--spent-today",
            "0",
            "--spent-month",
            "0",
        ],
    )
    s.say('Send a reply to a customer. The message under "Tell the human" is what the agent shows the person:')
    s.run(
        command(
            "CHECK",
            CHARTER,
            "--tool helpdesk.send_reply",
            '--action "Reply on ticket 4412: the replacement ships Monday and the refund of 8 USD is on its way"',
            "--recipient customer@example.org",
        ),
        "CHECK",
        [
            CHARTER,
            "--tool",
            "helpdesk.send_reply",
            "--action",
            "Reply on ticket 4412: the replacement ships Monday and the refund of 8 USD is on its way",
            "--recipient",
            "customer@example.org",
        ],
    )
    s.say("A refund of 140 USD. The per-action cap is 100, so it is denied, whatever any rule says:")
    s.run(
        command(
            "CHECK",
            CHARTER,
            "--tool refunds.issue",
            '--action "Refund order 1190"',
            "--amount 140",
            "--spent-today 8",
            "--spent-month 8",
        ),
        "CHECK",
        [
            CHARTER,
            "--tool",
            "refunds.issue",
            "--action",
            "Refund order 1190",
            "--amount",
            "140",
            "--spent-today",
            "8",
            "--spent-month",
            "8",
        ],
    )
    s.say("Delete every ticket from 2024. Rule N2 forbids bulk deletes:")
    s.run(
        command("CHECK", CHARTER, "--tool helpdesk.delete_ticket", '--action "Delete all tickets from 2024"'),
        "CHECK",
        [CHARTER, "--tool", "helpdesk.delete_ticket", "--action", "Delete all tickets from 2024"],
    )

    s.say("## 4. Log what happened")
    s.say(
        "Allowed reads are not logged. Everything else is. The agent sends the event on stdin, which avoids shell"
        " quoting trouble. Each entry carries the hash of the one before it, and the last line prints the head."
    )
    events = [
        (
            "09:02",
            {
                "tool": "refunds.issue",
                "action": "Refund order 1182",
                "verdict": "allow",
                "rule": "A4",
                "amount": 8,
                "outcome": "executed",
            },
        ),
        (
            "09:10",
            {
                "tool": "helpdesk.send_reply",
                "action": "Reply on ticket 4412: the replacement ships Monday and the refund of 8 USD is on its way",
                "verdict": "ask",
                "rule": "A3",
                "recipient": "customer@example.org",
                "outcome": "executed",
                "approved_by": "Sam Rivera",
                "approval_ref": "Sam, 09:09: yes, send it",
            },
        ),
        (
            "09:15",
            {
                "tool": "refunds.issue",
                "action": "Refund order 1190",
                "verdict": "deny",
                "rule": "budget.per_action_max",
                "amount": 140,
                "outcome": "blocked",
            },
        ),
        (
            "09:16",
            {
                "tool": "helpdesk.delete_ticket",
                "action": "Delete all tickets from 2024",
                "verdict": "deny",
                "rule": "N2",
                "outcome": "blocked",
            },
        ),
        (
            "09:20",
            {
                "kind": "escalation",
                "note": "Told Sam about the 140 USD refund request and the bulk delete request. Waiting for a decision.",
            },
        ),
    ]
    for stamp, event in events:
        body = json.dumps(event, indent=2)
        shown = (
            f"{command('AUDIT', 'log', LOG, '--charter', CHARTER, '--event -', '--ts', '2026-11-12T' + stamp + ':00Z')}"
        )
        s.run(
            shown + " <<'EOF'\n" + body + "\nEOF",
            "AUDIT",
            ["log", LOG, "--charter", CHARTER, "--event", "-", "--ts", f"2026-11-12T{stamp}:00Z"],
            stdin=body,
        )

    s.say("## 5. Verify the chain")
    s.run(command("AUDIT", "verify", LOG), "AUDIT", ["verify", LOG])
    s.say(
        "Keep that head (`5:` and the hash) somewhere the agent cannot write, such as a ticket or an email to"
        " yourself. It is the only thing that can show entries were cut off the end."
    )

    s.say("## 6. The weekly report")
    s.run(command("AUDIT", "report", LOG, "--as-of 2026-11-12"), "AUDIT", ["report", LOG, "--as-of", "2026-11-12"])

    s.say("## 7. What tampering looks like")
    s.say("Delete line 3 of the log and verify again. It says which line broke, and why:")
    lines = (folder / LOG).read_text().splitlines()
    shutil.copy(folder / LOG, folder / "audit-untouched.jsonl")
    (folder / LOG).write_text("\n".join(lines[:2] + lines[3:]) + "\n")
    s.run(command("AUDIT", "verify", LOG), "AUDIT", ["verify", LOG])
    s.say("Cutting entries off the end is invisible to the file alone. With the head you kept, it is not:")
    (folder / LOG).write_text("\n".join(lines[:4]) + "\n")
    head = json.loads(lines[4])
    s.run(
        command("AUDIT", "verify", LOG, "--head", f"5:{head['hash']}"),
        "AUDIT",
        ["verify", LOG, "--head", f"5:{head['hash']}"],
    )
    shutil.copy(folder / "audit-untouched.jsonl", folder / LOG)

    s.say("## 8. What a process violation looks like")
    s.say(
        "This one I staged to show the flag: the agent logs a 42 USD refund as approved by itself. The log still"
        " records it, then says so and exits 1, so the agent has to tell the person."
    )
    bad = {
        "tool": "refunds.issue",
        "action": "Refund order 1204",
        "verdict": "ask",
        "rule": "A5",
        "amount": 42,
        "outcome": "executed",
        "approved_by": "agent",
    }
    body = json.dumps(bad, indent=2)
    s.run(
        command("AUDIT", "log", LOG, "--charter", CHARTER, "--event -", "--ts 2026-11-12T10:30:00Z")
        + " <<'EOF'\n"
        + body
        + "\nEOF",
        "AUDIT",
        ["log", LOG, "--charter", CHARTER, "--event", "-", "--ts", "2026-11-12T10:30:00Z"],
        stdin=body,
    )
    s.run(command("AUDIT", "report", LOG, "--as-of 2026-11-12"), "AUDIT", ["report", LOG, "--as-of", "2026-11-12"])

    shutil.copy(folder / "audit-untouched.jsonl", folder / "sample-audit.jsonl")
    return "\n".join(s.md).rstrip() + "\n"


def generate() -> tuple[str, str]:
    """Return (sample-session.md text, the untouched five-entry audit log)."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        text = build(folder)
        return text, (folder / "sample-audit.jsonl").read_text()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stdout", action="store_true", help="print the markdown instead of writing the files")
    args = ap.parse_args(argv)
    text, log = generate()
    if args.stdout:
        print(text, end="")
        return 0
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    (EXAMPLES / "sample-session.md").write_text(text)
    (EXAMPLES / "sample-audit.jsonl").write_text(log)
    print(f"wrote {EXAMPLES / 'sample-session.md'} ({len(text.splitlines())} lines) and sample-audit.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
