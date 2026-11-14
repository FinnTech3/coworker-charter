# Agensi listing copy

Paste these into the submit form at agensi.io/dashboard/submit. Upload `dist/coworker-charter.zip`
(build it with `python3 tools/agensi_build.py`, which also checks this file against Agensi's field limits).
Logo and screenshots are in `agensi/media/`.

| Field | Value |
|---|---|
| Title | Coworker Charter: Permissions, Approvals, Audit for Agents |
| Categories | Agents & Orchestration; Security; Business & Operations |
| Suggested price | $12 one-time |
| Tags | agents, permissions, approvals, audit-log, governance, risk-management |
| Compatibility note | Requires Python 3.10+. No network, no packages, no API key. Works with any agent that reads SKILL.md and can run Python scripts. |
| Permissions | Terminal (runs three Python scripts from temporary files). Reads only the charter and log files you name. Appends to the one audit log file you name and writes nothing else. No network access, no environment variables. |
| Docs link | https://github.com/FinnTech3/coworker-charter |

## Summary

Write a charter for an AI coworker, get an allow, ask or deny verdict before every risky action, and keep a tamper-evident audit log. Policy and evidence for agents that send, spend, delete or publish. Not a sandbox.

## Description

Businesses want an AI agent that can use tools like a coworker, but with a person approving anything risky, clear limits on its tools, a way to take over, and a record of what it did. This skill makes your agent, and you, write that down and stick to it.

Your agent interviews you about the job, then drafts a **charter**: the role, an owner, every tool sorted into a risk tier (read, write, send, spend, delete, publish), approval rules, a list of what must never happen, data boundaries, spend caps and an escalation contact. A linter then refuses the usual mistakes: wildcard tool grants, tools with no tier, risky tiers with no approval rule, missing or non-numeric spend caps, no owner, secrets in the text and vague rules. Before the charter goes live the agent runs five sample actions through it and you check the answers are what you meant.

**How it works.** Three bundled Python scripts (standard library only) do the deciding and the record keeping. Before each risky action the agent runs `check`, which returns allow, ask or deny, the rule that decided it, and a plain-English message to show you. The same inputs give the same verdict every time, so the agent never decides for itself. Every decision goes into a hash-chained log. `verify` names the line that broke if one is edited, deleted or reordered, and `report` summarises decisions, asks, denials and spend by tool and by day. The skill also gives the agent a take-over and escalation procedure.

**Where it stops.** This is a policy-and-evidence layer. It works only while the agent calls it. It cannot stop an agent that ignores it, it sees only what the agent says it will do, and it is not a security sandbox. The log is tamper evident, not tamper proof.

## See it in action

**Request:** "My support agent wants to do four things. Check them against its charter, ask me for anything that needs me, and log it all."

**Result:**

I ran each action through the charter (`example-support-agent.json`). One was allowed, one needed Sam, and two are refused.

| Action | Decision | Rule | Why |
|---|---|---|---|
| Refund order 1182, 8 USD | Allow | A4 | Refunds of 10 USD or less are routine goodwill refunds the coworker may issue. |
| Reply on ticket 4412 to customer@example.org | Ask | A3 | A customer email cannot be recalled, so a person reads the final text first. |
| Refund order 1190, 140 USD | Deny | budget.per_action_max | 140 USD would take spend over the per-action cap of 100. |
| Delete all tickets from 2024 | Deny | N2 | Tickets are deleted one at a time, never in bulk. |

I issued the 8 USD refund. For the reply I showed Sam this message, and sent it after Sam answered "yes, send it" (09:09). I did **not** do the other two.

```
Approval needed before I act.
What will happen: Reply on ticket 4412: the replacement ships Monday and the refund of 8 USD is on its way
Tool: helpdesk.send_reply (send tier)
Recipient: customer@example.org
Why I am asking: A customer email cannot be recalled, so a person reads the final text first. (rule A3)
Who can approve: Sam Rivera (sam.rivera@example.com)
Reply "approve" to go ahead or "decline" to stop. I will not do anything until you answer.
```

The two denials stand. Only Sam Rivera can change what is allowed, by editing the charter, and I will not try another way round either of them. I told Sam about both and logged it as an escalation.

All of it is in the audit log (five entries: four decisions and the escalation). `verify` says:

```
OK: 5 entries, chain intact.
head: 5:cd97a53c09f65171da1306b31a610c91424be4ee8c92953778a4d2e79612e559
```

Keep that head somewhere I cannot write. If anyone cuts entries off the end of the log, it is the only thing that will show.

If someone deletes line 3 of that log, `verify` names the line and the reason:

```
BROKEN: 1 problem(s). First break at line 3.
  line 3: seq is 4 but 3 was expected (entries are missing before this line); prev hash does not match the line above (entries missing, replaced or reordered)
head: 5:cd97a53c09f65171da1306b31a610c91424be4ee8c92953778a4d2e79612e559
```

Exit code 1. This is the same five-entry log, with the line removed (section 7 of `examples/sample-session.md`).

## Limitations

- Policy and evidence only. It cannot stop an agent that does not call `check`, and it is not a sandbox. Give the agent least-privilege credentials as well.
- `check` sees the action text the agent writes. If the agent describes an action falsely, the charter cannot know.
- Phrase matching is whole words, case-insensitive, with look-alike letters from other alphabets not unified. A phrase list is a net with holes.
- The hash chain is tamper evidence, not tamper proof. Anyone who can write the log can rebuild it, and entries cut off the end are noticed only if you kept the head hash somewhere the agent cannot write.
- `approved_by` is text the agent records. The log cannot prove a person said yes; it keeps their quoted words so you can check.
- The log lock works on POSIX systems on a local disk. On Windows or a network drive, run one writer at a time.
- The scripts never delete anything. Retention is a policy field for you to apply, not something they enforce.

## FAQ

**Does it stop my agent doing something I did not approve?** Only if the agent routes the action through it. It is policy and evidence, not a sandbox. If the agent ignores it, nothing stops that, but the gaps show in the log.

**Do I need an API key, an account or a network connection?** No. The scripts read the files you name and append to one log you name.

**Who decides allow, ask or deny?** The script, from the charter, the same way every time. The agent is told never to override it.

**Can the agent edit the charter or approve its own actions?** The skill tells it never to. The log flags a self-approval, an approval from someone not named in the charter, and an action that ran after a deny.

**What if the charter is missing or broken?** `check` gives no verdict and the agent must treat that as "ask a person" for anything above a read.

**Which agents does it work with?** Any agent that supports SKILL.md and can run a Python script.

## Use cases

- Draft and lint a charter before giving an agent new tools
- Get an allow, ask or deny verdict and a plain-English approval message for a risky action
- Keep a tamper-evident log of every decision and produce a weekly report
- Set spend caps for an agent that can pay, refund or buy
- Hand a task to a person mid-run and record the take-over
