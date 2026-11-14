# Tool risk tiers

Every tool in a charter gets exactly one tier. The tier decides how much a person has to be involved.

**Tier a tool by the worst thing it can do with the credentials it holds, not by what you plan to use it for.**
A tool that can send email is a `send` tool even if you only mean to draft with it.

## The six tiers

| Tier | What the tool does | Can it be undone? | Default posture |
|---|---|---|---|
| `read` | Looks, searches, lists, downloads to read. No side effects. | Nothing to undo. | Allow. Boundaries still apply. |
| `write` | Creates or changes records that stay inside your own systems: drafts, notes, tickets, cells in a working copy. | Yes, by editing or reverting. | Ask, unless a narrow rule allows a low-harm tool. |
| `send` | Anything that reaches another person or system: email, chat, SMS, public comments, calendar invites, webhooks. | No. Once it is out, it is out. | Ask. Allow only to named internal domains. |
| `spend` | Moves or commits money or credit: payments, refunds, purchases, ad spend, paid API calls, billable cloud resources. | Rarely, and with effort. | Ask. Allow only under a small ceiling. Caps always apply. |
| `delete` | Destroys or overwrites data that may not come back: deletes, purges, truncates, force-pushes, revoking access. | Only if there is a backup. | Ask. Never in bulk. |
| `publish` | Makes something public or live: website posts, releases, deploys, social posts, public docs, DNS changes. | Partly. The world may have seen it. | Ask. |

`check` treats every tier above `read` the same way when no rule says otherwise: it asks. The tiers matter because
they tell you which rules to write, and because `lint` refuses an `allow` rule on `send` without recipient domains,
and on `spend` without a ceiling.

## How to decide in four questions

1. What is the worst thing this tool can do? Start from the highest tier it can reach.
2. Can I undo it in seconds, with effort, or not at all? Not at all means `send`, `spend`, `delete` or `publish`.
3. Does it touch anyone outside the team? If yes it is at least `send`.
4. Does it touch money? If yes it is `spend`, even when the amount is tiny.

When two tiers fit, pick the higher one. You can lower it later, with a person's say-so, once the logs show it is safe.

## Tools that do several things

A charter can only govern a tool by its name and its action text. A tool that can do anything cannot be governed by a
list, so shrink it first.

| Tool | Tier it really is | What to do |
|---|---|---|
| A shell or "run command" tool | `delete`, and reaches `send`, `spend`, `publish` | Do not grant it. Or tier it `delete`, ask every time, and read the command before approving. |
| A browser with saved logins | The highest action available behind those logins | Tier by what the logins can do. Add `must_not_access` phrases for the sites that matter. |
| A database client | `read` with a read-only role, `write` with a write role, `delete` if it may `DELETE` or `DROP` | Give it a role that matches the tier you want. The charter cannot change what the role allows. |
| A mail tool with draft and send in one | `send` | Split it into a draft tool (`write`) and a send tool (`send`) if the connector allows. |
| A file tool that can overwrite | `delete` | Treat an overwrite as a delete unless there is a backup. |
| A "post a comment" tool | `send`, or `publish` if the comment is public | Tier by who can see it. |
| A paid API | `spend` | Set `per_action_max` to a few times the price of one call. |

## Examples by system

| System | `read` | `write` | `send` | `spend` | `delete` | `publish` |
|---|---|---|---|---|---|---|
| Email | search, open | save draft | send, reply, forward | none | delete message | none |
| Chat | read channel | none | post, DM | none | delete message | post in a public channel |
| Helpdesk | search, view ticket | add note, save draft | reply to customer | issue refund | delete ticket | publish article |
| Payments | list, view | schedule a draft batch | send remittance advice | pay, refund, payout | void | none |
| Documents | open, search | edit a working copy | share link with outsiders | none | delete, empty trash | publish to the web |
| Code host | clone, read | commit to a branch | comment on an issue | none | delete branch, force-push | merge to main, release |
| Cloud | list, describe | tag, resize in a sandbox | none | start billable resources | terminate | deploy to production |

## Mistakes that come up

- **Tiering a risky tool as `read` so nobody gets interrupted.** `lint` warns when a tool name says `send` or
  `delete` and the tier says `read` or `write`. The warning is a heuristic, not a proof. Read the tool's description.
- **One tool called `all` or `*`.** Refused by `lint`. List each tool.
- **Allowing a `send` tool to anyone.** Refused by `lint`. Name the domains.
- **Skipping caps because "we use approvals".** Approvals get tired. Caps do not.
- **Forgetting that a tool that reads can still leak.** A `read` tool that fetches a URL can send data out in the URL.
  If a read tool takes free text that leaves your network, tier it `send`.
