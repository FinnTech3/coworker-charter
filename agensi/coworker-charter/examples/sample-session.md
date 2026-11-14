# A real session, start to finish

Every block below is the captured output of a real run of the three scripts, from an empty folder. Nothing
was edited by hand. A test regenerates this file with `tools/make_sample_session.py` and fails if a word
differs. Timestamps are fixed with `--ts` and `--as-of` so the run repeats exactly.

`$LINT`, `$CHECK` and `$AUDIT` stand for the temporary files your agent writes `charter_lint.py`,
`charter_check.py` and `audit_log.py` to. The charter is `references/example-support-agent.json`, saved as
`charter.json` after the interview.

## 1. Start from the template and see what is missing

```console
$ python3 "$LINT" init | head -24
{
  "charter_version": 1,
  "status": "active",
  "role": "FILL IN: the job this coworker does and who for, in one sentence",
  "agent_name": "FILL IN: the name used in the audit log",
  "owner": {
    "name": "FILL IN: the accountable person",
    "contact": "FILL IN: email or phone"
  },
  "approvers": [
    {
      "name": "FILL IN: another person who may approve",
      "contact": "FILL IN: email or phone"
    }
  ],
  "escalation": {
    "contact": "FILL IN: who to reach when blocked",
    "when": [
      "a deny verdict on something a person asked for",
      "two declined approvals in a row",
      "an amount or recipient that cannot be confirmed"
    ],
    "response_minutes": 60
  },
[exit 0]
```

The starter is not valid on purpose. Lint lists every blank that someone has to fill in:

```console
$ python3 "$LINT" lint starter.json
ERROR   placeholder            $.role: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.agent_name: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.owner.name: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.owner.contact: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.approvers[0].name: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.approvers[0].contact: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.escalation.contact: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.tools["app.read"].description: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.tools["app.write"].description: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.tools["app.send"].description: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.tools["app.spend"].description: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.tools["app.delete"].description: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.tools["app.publish"].description: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.data_boundaries.may_access[0]: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.data_boundaries.must_not_access[0]: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.data_boundaries.must_not_share[0]: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.never[0].reason: still has template text  [fix: replace it with the real value]
ERROR   placeholder            $.never[0].when.action_contains[0]: still has template text  [fix: replace it with the real value]
18 error(s), 0 warning(s)
[exit 1]
```

## 2. Lint the finished charter

```console
$ python3 "$LINT" lint charter.json --strict
OK: no errors, no warnings
[exit 0]
```

## 3. Five sample actions through `check`

The agent and the person run these together before the charter goes live, and ask: is that what you meant?

Read a ticket. Allowed, nobody is interrupted:

```console
$ python3 "$CHECK" charter.json --tool helpdesk.search --action "Search open tickets about invoice 1182"
decision: allow
rule:     read-default
tool:     helpdesk.search (read tier)
why:      Read-tier tools are allowed unless a rule or boundary says otherwise.
next:     Proceed, then log the action.
[exit 0]
```

A refund of 8 USD, under the 10 USD ceiling in rule A4. Allowed, but only because today's spend was supplied:

```console
$ python3 "$CHECK" charter.json --tool refunds.issue --action "Refund order 1182" --amount 8 --spent-today 0 --spent-month 0
decision: allow
rule:     A4
tool:     refunds.issue (spend tier)
why:      Refunds of 10 USD or less are routine goodwill refunds the coworker may issue.
next:     Proceed, then log the action.
[exit 0]
```

Send a reply to a customer. The message under "Tell the human" is what the agent shows the person:

```console
$ python3 "$CHECK" charter.json --tool helpdesk.send_reply --action "Reply on ticket 4412: the replacement ships Monday and the refund of 8 USD is on its way" --recipient customer@example.org
decision: ask
rule:     A3
tool:     helpdesk.send_reply (send tier)
why:      A customer email cannot be recalled, so a person reads the final text first.
next:     Send tell_human to the approver, wait for an explicit yes, and do nothing until it arrives.

Tell the human:
  Approval needed before I act.
  What will happen: Reply on ticket 4412: the replacement ships Monday and the refund of 8 USD is on its way
  Tool: helpdesk.send_reply (send tier)
  Recipient: customer@example.org
  Why I am asking: A customer email cannot be recalled, so a person reads the final text first. (rule A3)
  Who can approve: Sam Rivera (sam.rivera@example.com)
  Reply "approve" to go ahead or "decline" to stop. I will not do anything until you answer.
[exit 1]
```

A refund of 140 USD. The per-action cap is 100, so it is denied, whatever any rule says:

```console
$ python3 "$CHECK" charter.json --tool refunds.issue --action "Refund order 1190" --amount 140 --spent-today 8 --spent-month 8
decision: deny
rule:     budget.per_action_max
tool:     refunds.issue (spend tier)
why:      140 USD would take spend over the per-action cap of 100.
next:     Stop. Do not retry, rephrase or use another tool. Tell the human and log the denial.

Tell the human:
  I will not do this: Refund order 1190
  Why: 140 USD would take spend over the per-action cap of 100. (rule budget.per_action_max)
  I have not done it and I will not try another way round it.
  Only Sam Rivera (sam.rivera@example.com) can change what is allowed, by editing the charter. Escalation contact: support-oncall@example.com.
[exit 1]
```

Delete every ticket from 2024. Rule N2 forbids bulk deletes:

```console
$ python3 "$CHECK" charter.json --tool helpdesk.delete_ticket --action "Delete all tickets from 2024"
decision: deny
rule:     N2
tool:     helpdesk.delete_ticket (delete tier)
why:      Tickets are deleted one at a time, never in bulk.
next:     Stop. Do not retry, rephrase or use another tool. Tell the human and log the denial.

Tell the human:
  I will not do this: Delete all tickets from 2024
  Why: Tickets are deleted one at a time, never in bulk. (rule N2)
  I have not done it and I will not try another way round it.
  Only Sam Rivera (sam.rivera@example.com) can change what is allowed, by editing the charter. Escalation contact: support-oncall@example.com.
[exit 1]
```

## 4. Log what happened

Allowed reads are not logged. Everything else is. The agent sends the event on stdin, which avoids shell quoting trouble. Each entry carries the hash of the one before it, and the last line prints the head.

```console
$ python3 "$AUDIT" log audit.jsonl --charter charter.json --event - --ts 2026-11-12T09:02:00Z <<'EOF'
{
  "tool": "refunds.issue",
  "action": "Refund order 1182",
  "verdict": "allow",
  "rule": "A4",
  "amount": 8,
  "outcome": "executed"
}
EOF
logged seq 1: allow executed
head: 1:e5d23f6f1d1aeb89e6291f5a2472427ec24553b7b9891a0f114898b196fb166b
[exit 0]
```

```console
$ python3 "$AUDIT" log audit.jsonl --charter charter.json --event - --ts 2026-11-12T09:10:00Z <<'EOF'
{
  "tool": "helpdesk.send_reply",
  "action": "Reply on ticket 4412: the replacement ships Monday and the refund of 8 USD is on its way",
  "verdict": "ask",
  "rule": "A3",
  "recipient": "customer@example.org",
  "outcome": "executed",
  "approved_by": "Sam Rivera",
  "approval_ref": "Sam, 09:09: yes, send it"
}
EOF
logged seq 2: ask executed
head: 2:65b4669528a523769fbcf83a3206ed84608cd83f868f7b7f50a483068ebde37f
[exit 0]
```

```console
$ python3 "$AUDIT" log audit.jsonl --charter charter.json --event - --ts 2026-11-12T09:15:00Z <<'EOF'
{
  "tool": "refunds.issue",
  "action": "Refund order 1190",
  "verdict": "deny",
  "rule": "budget.per_action_max",
  "amount": 140,
  "outcome": "blocked"
}
EOF
logged seq 3: deny blocked
head: 3:d81ee68d86a95e09e89d7df308b2087ae94c96e1257dd3c54e9beae5c1cd1613
[exit 0]
```

```console
$ python3 "$AUDIT" log audit.jsonl --charter charter.json --event - --ts 2026-11-12T09:16:00Z <<'EOF'
{
  "tool": "helpdesk.delete_ticket",
  "action": "Delete all tickets from 2024",
  "verdict": "deny",
  "rule": "N2",
  "outcome": "blocked"
}
EOF
logged seq 4: deny blocked
head: 4:4033c8c0ca0069b38e212a983f1484e0efc71b7d064c5f5a99048dc35b50fc30
[exit 0]
```

```console
$ python3 "$AUDIT" log audit.jsonl --charter charter.json --event - --ts 2026-11-12T09:20:00Z <<'EOF'
{
  "kind": "escalation",
  "note": "Told Sam about the 140 USD refund request and the bulk delete request. Waiting for a decision."
}
EOF
logged seq 5: escalation
head: 5:cd97a53c09f65171da1306b31a610c91424be4ee8c92953778a4d2e79612e559
[exit 0]
```

## 5. Verify the chain

```console
$ python3 "$AUDIT" verify audit.jsonl
OK: 5 entries, chain intact.
head: 5:cd97a53c09f65171da1306b31a610c91424be4ee8c92953778a4d2e79612e559
[exit 0]
```

Keep that head (`5:` and the hash) somewhere the agent cannot write, such as a ticket or an email to yourself. It is the only thing that can show entries were cut off the end.

## 6. The weekly report

```console
$ python3 "$AUDIT" report audit.jsonl --as-of 2026-11-12
# Audit report for audit.jsonl

Period start to now: 5 entries.
Chain intact. Head 5:cd97a53c09f65171da1306b31a610c91424be4ee8c92953778a4d2e79612e559
Charter versions seen: d73a5838c9ce (seq 1-5).

## Decisions

allow 1, ask 1, deny 2.
Asks: approved and done 1.
Denials by rule: budget.per_action_max x1, N2 x1.

## Spend (executed, in the charter's currency)

Total 8. Today (2026-11-12) 8. This month 8.

## By tool

| Tool | Tier | Entries | Allow | Ask | Deny | Executed | Spend |
|---|---|---|---|---|---|---|---|
| helpdesk.delete_ticket | delete | 1 | 0 | 0 | 1 | 0 | 0 |
| helpdesk.send_reply | send | 1 | 0 | 1 | 0 | 1 | 0 |
| refunds.issue | spend | 2 | 1 | 0 | 1 | 1 | 8 |

## By day

| Day | Entries | Allow | Ask | Deny | Executed | Spend |
|---|---|---|---|---|---|---|
| 2026-11-12 | 4 | 1 | 1 | 2 | 2 | 8 |

## Take-overs, escalations, charter changes

- seq 5 (2026-11-12T09:20:00Z) escalation by -: Told Sam about the 140 USD refund request and the bulk delete request. Waiting for a decision.
[exit 0]
```

## 7. What tampering looks like

Delete line 3 of the log and verify again. It says which line broke, and why:

```console
$ python3 "$AUDIT" verify audit.jsonl
BROKEN: 1 problem(s). First break at line 3.
  line 3: seq is 4 but 3 was expected (entries are missing before this line); prev hash does not match the line above (entries missing, replaced or reordered)
head: 5:cd97a53c09f65171da1306b31a610c91424be4ee8c92953778a4d2e79612e559
[exit 1]
```

Cutting entries off the end is invisible to the file alone. With the head you kept, it is not:

```console
$ python3 "$AUDIT" verify audit.jsonl --head 5:cd97a53c09f65171da1306b31a610c91424be4ee8c92953778a4d2e79612e559
BROKEN: 1 problem(s). First break at line 4.
  line 4: cut off: the log ends at seq 4 but your anchor is seq 5
head: 4:4033c8c0ca0069b38e212a983f1484e0efc71b7d064c5f5a99048dc35b50fc30
[exit 1]
```

## 8. What a process violation looks like

This one I staged to show the flag: the agent logs a 42 USD refund as approved by itself. The log still records it, then says so and exits 1, so the agent has to tell the person.

```console
$ python3 "$AUDIT" log audit.jsonl --charter charter.json --event - --ts 2026-11-12T10:30:00Z <<'EOF'
{
  "tool": "refunds.issue",
  "action": "Refund order 1204",
  "verdict": "ask",
  "rule": "A5",
  "amount": 42,
  "outcome": "executed",
  "approved_by": "agent"
}
EOF
logged seq 6: ask executed
head: 6:8cb5b612ce9f11be24cdc8b1b6a6373e339b7db899c88e1f3e69701b4494c81d
FLAG self_approved. Tell the human.
[exit 1]
```

```console
$ python3 "$AUDIT" report audit.jsonl --as-of 2026-11-12
# Audit report for audit.jsonl

Period start to now: 6 entries.
Chain intact. Head 6:8cb5b612ce9f11be24cdc8b1b6a6373e339b7db899c88e1f3e69701b4494c81d
Charter versions seen: d73a5838c9ce (seq 1-6).

## Decisions

allow 1, ask 2, deny 2.
Asks: approved and done 1, ran without valid approval 1.
Denials by rule: budget.per_action_max x1, N2 x1.

## Spend (executed, in the charter's currency)

Total 50. Today (2026-11-12) 50. This month 50.

## By tool

| Tool | Tier | Entries | Allow | Ask | Deny | Executed | Spend |
|---|---|---|---|---|---|---|---|
| helpdesk.delete_ticket | delete | 1 | 0 | 0 | 1 | 0 | 0 |
| helpdesk.send_reply | send | 1 | 0 | 1 | 0 | 1 | 0 |
| refunds.issue | spend | 3 | 1 | 1 | 1 | 2 | 50 |

## By day

| Day | Entries | Allow | Ask | Deny | Executed | Spend |
|---|---|---|---|---|---|---|
| 2026-11-12 | 5 | 1 | 2 | 2 | 3 | 50 |

## Flags: process violations

- seq 6 (2026-11-12T10:30:00Z) refunds.issue: self_approved

## Take-overs, escalations, charter changes

- seq 5 (2026-11-12T09:20:00Z) escalation by -: Told Sam about the 140 USD refund request and the bulk delete request. Waiting for a decision.
[exit 1]
```
