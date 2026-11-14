# Charter and log reference

The exact meaning of every charter field, how `check` reaches a verdict, and what the log records. Examples that
pass `lint` with no findings: `example-support-agent.json`, `example-finance-ops-agent.json`,
`example-research-agent.json`.

## Charter fields

| Field | Required | Meaning |
|---|---|---|
| `charter_version` | yes | Always `1`. |
| `status` | no | `"active"` (default) or `"suspended"`. While suspended, `check` denies everything, reads included. The owner's kill switch. |
| `role` | yes | One sentence: the job, and who it is for. |
| `agent_name` | no | The name the coworker uses in the log. An approval that names it is flagged as self-approval. |
| `owner` | yes | `{"name", "contact"}`. One accountable person. The default approver. |
| `approvers` | no | List of `{"name", "contact"}`. Other people who may approve. |
| `escalation` | yes | `{"contact", "when": [...], "response_minutes"}`. At least one trigger. |
| `tools` | yes | Object: exact tool name to `{"tier", "description"}`. Tiers: `read`, `write`, `send`, `spend`, `delete`, `publish`. |
| `data_boundaries` | no, but lint warns | `{"may_access", "must_not_access", "must_not_share"}`, each a list of phrases. |
| `never` | no, but lint warns | Rules that deny. See below. |
| `approval_rules` | yes | Rules that allow, ask or deny. Every tool above `read` needs one that names it or its tier. |
| `budget` | yes | `{"currency", "per_action_max", "per_day_max", "per_month_max"}`. The month cap is optional. Numbers, 0 or more. |
| `audit` | yes | `{"retention_days", "log_path"}`. Informational: the scripts never delete anything. |

Secrets never belong in a charter. `lint` refuses key-like strings, passwords in URLs, private-key headers and
long random strings, and it never prints the value it found.

## Rules

Both `never` and `approval_rules` hold rules with these keys:

```json
{"id": "A4", "reason": "One plain sentence, shown to the human.",
 "when": {"tools": ["refunds.issue"], "amount_at_most": 10},
 "decision": "allow", "approver": "owner"}
```

`decision` and `approver` belong to approval rules only. `decision` is `ask` (default), `allow` or `deny`.
`approver` is `"owner"` (default) or the name of a listed approver, and matters for `ask`.

### Conditions

A rule fires when every condition in `when` is true.

| Condition | True when |
|---|---|
| `tools` | The tool is one of these exact names. |
| `tiers` | The tool's tier is one of these. |
| `action_contains` | The action text contains any of these phrases. |
| `amount_over` | The amount is greater than this. |
| `amount_at_most` | The amount is this or less. |
| `recipient_domain_in` | Every address in `--recipient` is at one of these domains. |
| `recipient_domain_not_in` | At least one address is at a domain not in this list. |

An approval rule must name `tools` or `tiers`. A `never` rule needs at least one condition of any kind, because a rule
with none would match everything.

### How text is matched

- Phrases match whole words, case-insensitively, with any run of spaces treated as one. `wire transfer` matches
  `Wire   Transfer` and does not match `wire transfers` or `rewire transfer`. List plurals separately.
- Text is normalised first: full-width letters become ordinary letters, and invisible characters such as zero-width
  spaces and soft hyphens are removed. So `ｗｉｒｅ transfer` and `wire​ transfer` still match.
- Letters from other alphabets that merely look the same (a Cyrillic `а` for a Latin `a`) are **not** unified. A
  phrase list is a net with holes, not a guarantee.
- Domains match exactly. `example.com` does not cover `sub.example.com` or `example.com.evil.test`. List each one.
- A recipient string may hold several addresses separated by commas, semicolons or spaces. `Name <a@example.com>` and a URL
  are understood.

### Order of decisions

`check` stops at the first of these that applies.

1. `status` is `suspended`: **deny** (`charter-suspended`).
2. The tool is not in `tools`: **deny** (`unknown-tool`), with a "did you mean" when a name is close.
3. The action text contains a `must_not_access` or `must_not_share` phrase: **deny** (`data-boundary`).
4. A `never` rule fires: **deny**, with that rule's id.
5. An amount was given and it would pass the per-action, daily or monthly cap: **deny** (`budget.per_action_max`,
   `budget.per_day_max`, `budget.per_month_max`). A cap is exceeded only when spend is **over** it. Equal is allowed.
6. Approval rules that fire:
   - a `deny` rule wins over everything;
   - otherwise rules that name the tool beat rules that name only the tier (a narrow exception to a tier default);
   - among equals the stricter wins: `deny`, then `ask`, then `allow`.
7. No rule fired: a `read` tool is **allowed** (`read-default`). Any other tier **asks** (`default-ask`).

### Missing information never opens a door

If a condition needs an input that was not given (`--amount`, `--recipient`), the rule cannot be evaluated.

- An unevaluable `allow` rule is not applied. If nothing else decides, the result is `ask` with rule `unverifiable`.
- An `allow` that would otherwise stand is turned into `ask` when a stricter rule could not be evaluated, or when a
  `spend` action has no amount, or no `--spent-today` while the charter has a daily cap, or no `--spent-month` while it has a monthly cap.
- `check` lists what was missing under `notes`.

The daily and monthly caps need `--spent-today` and `--spent-month`. Get them from `audit_log.py report --json`
(`spend.today` and `spend.month`). `check` never reads the log itself, so the verdict stays a pure function of the
charter file and the arguments.

### When the charter is unusable

`check` gives exit 2 and no verdict if the file is missing, is not valid JSON, has duplicate keys, has `NaN` or
`Infinity`, still has `FILL IN` text, or has a structure it cannot follow (a tool with no tier, a non-numeric cap, an
unknown condition). Treat exit 2 as "ask a person".

## `check` output

```json
{"decision": "ask", "rule": "A3", "reason": "...", "tool": "...", "tier": "send", "action": "...", "amount": null,
 "recipient": "...", "approver": "Sam Rivera", "notes": [], "charter_sha256": "...", "tell_human": "...", "next": "..."}
```

`approver` is set only for `ask`. `tell_human` is `null` for `allow`. `charter_sha256` is the SHA-256 of the charter
file's bytes, so you can reproduce it with any hashing tool.

Exit codes: `0` allow, `1` ask or deny, `2` bad input or unusable charter. Only an allow exits 0, so
`check && act` is safe.

## Log entries

`audit_log.py log` appends one JSON line. You supply an event; the script adds `seq`, `ts`, `tier`,
`charter_sha256`, `flags`, `prev` and `hash`.

| Event field | Used for | Notes |
|---|---|---|
| `kind` | all | `action` (default), `takeover`, `release`, `escalation`, `charter_change`, `note` |
| `tool`, `action` | action | Exact tool name, and the same plain sentence you gave `check`. |
| `verdict` | action | `allow`, `ask` or `deny`: what `check` said. |
| `outcome` | action | `executed`, `declined`, `blocked`, `failed`, `escalated` |
| `rule` | action | The rule id `check` returned. |
| `amount`, `recipient` | action | As given to `check`. |
| `approved_by`, `approval_ref` | action | The approver's name, and their words or a link to their message. |
| `actor` | any | Who is writing the entry. Defaults to nothing; `agent_name` is used for self-approval checks. |
| `by`, `note` | non-action | `note` is required. `by` is required for `takeover`, `release` and `charter_change`. |

Text fields are at most 1000 characters. Anything else in the event is refused, including `hash`, `seq` and `flags`.

### Hash chain

Each entry's `hash` is the SHA-256 of its own canonical JSON (keys sorted, no spaces, ASCII only, `hash` left out),
and `prev` is the previous entry's `hash`. The first entry's `prev` is 64 zeros. `verify` recomputes all of it, in
one pass, however big the file is.

| What was done to the file | What `verify` says |
|---|---|
| A line edited | `content does not match its own hash`, at that line |
| A line edited and re-hashed | `prev hash does not match the line above`, at the next line |
| A line deleted | `seq is N but M was expected (entries are missing before this line)`, at the line after the gap |
| Two lines swapped | `out of order`, at the first swapped line |
| A line repeated or inserted | `out of order or repeated` |
| The last line cut in half | `unreadable ... cut off`, at that line |
| Whole lines cut off the end | **Nothing**, unless you pass `--head SEQ:HASH` from when you last looked |
| The whole chain rebuilt by someone who can write the file | **Nothing**, unless you hold the head hash |

### Flags

`log` judges an executed action against the charter and writes `flags` into the entry. Any flag makes `log` exit 1
and `report` exit 1.

| Flag | Meaning |
|---|---|
| `executed_despite_deny` | The verdict was `deny` and the action ran. |
| `executed_without_approval` | The verdict was `ask`, the action ran, and there is no `approved_by`. |
| `self_approved` | `approved_by` is the agent (`agent`, `self`, `me`, `system`, `bot`, its `agent_name`, or `actor`). |
| `approver_not_in_charter` | `approved_by` is not the owner or a listed approver (by name or contact). |
| `approval_ref_missing` | Approved by a listed person, but their words or message link were not recorded. |
| `unknown_tool_executed` | The tool is not in the charter. |
| `over_cap_executed` | The amount is over `per_action_max`. |

Flags catch an agent that is careless or confused. They do not catch an agent that writes a false `approved_by`
naming the real owner. That is why `approval_ref` exists: a person can read the quoted words and check.

### Exit codes

| Script | 0 | 1 | 2 |
|---|---|---|---|
| `charter_lint.py lint` | no errors | errors (or any finding with `--strict`) | unusable file |
| `charter_check.py` | allow | ask or deny | unusable input or charter |
| `audit_log.py log` | written | written, and flagged | nothing written |
| `audit_log.py verify` | chain intact | chain broken, or anchor mismatch | unusable file or arguments |
| `audit_log.py report` | clean | broken chain or flags present | unusable file or arguments |
