# Coworker charter

An agent skill that makes an AI coworker, and the person running it, write down what it may do, enforce it before every
risky action, and keep evidence of what happened.

## What you get

- A **charter**: the role, the owner, every tool sorted into a risk tier (read, write, send, spend, delete, publish),
  approval rules, what must never happen, data boundaries, spend caps, an escalation contact and a retention period.
- A **linter** that refuses wildcard tool grants, tools without a tier, risky tiers without an approval rule, missing
  or non-numeric spend caps, a missing owner or escalation contact, secrets, and vague rules.
- A **verdict** for each action: allow, ask or deny, with the rule that decided it and a plain-English message to show
  the person. The same inputs always give the same verdict.
- A **hash-chained audit log** that shows which line broke if one is edited, deleted or reordered, and a report by
  decision, tool and day.
- A **take-over and escalation procedure** the agent follows.

## What it needs

- Python 3.10 or newer. No packages, no network, no API key.
- Permission for your agent to run three Python scripts from temporary files and to append to one log file you name.

## How to use it

Ask your agent, for example:

- "Write a charter for my support agent."
- "What should my agent be allowed to do with these tools?"
- "Set up approvals before my agent sends or spends anything."
- "Check this refund against the charter."
- "Audit what my agent did this week."

## Files

| File | Purpose |
|---|---|
| `SKILL.md` | The workflow the agent follows |
| `scripts/charter_lint.py` | `init` prints a starter charter, `lint` validates one |
| `scripts/charter_check.py` | Decides allow, ask or deny for one action |
| `scripts/audit_log.py` | `log`, `verify` and `report` for the audit log |
| `references/tool-risk-tiers.md` | How to tier a tool |
| `references/charter-fields.md` | Every field, the order of decisions, the log format and flags |
| `references/approval-message-template.md` | The approval, denial, take-over and escalation messages |
| `references/example-*.json` | Three charters that pass lint: support, finance operations, research |
| `examples/sample-session.md` | A real session with captured output |
| `examples/sample-audit.jsonl` | The audit log from that session |

## Limits

This is a policy-and-evidence layer. It works only while the agent calls it. It cannot stop an agent that ignores it,
it sees only what the agent says it will do, and it is not a security sandbox. The hash chain is tamper evidence, not
tamper proof: anyone who can write the log can rebuild it, and entries cut off the end are noticed only if you kept the
head hash somewhere the agent cannot write. Approvals are text the agent records. The log cannot prove a person said
yes. Give the agent least-privilege credentials as well.

The log lock works on POSIX systems on a local disk. On Windows, or on a network drive, run one writer at a time.
