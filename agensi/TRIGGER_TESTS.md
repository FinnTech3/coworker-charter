# Trigger tests: Coworker Charter

Agensi's pre-publish rule: the skill should trigger on 5 of 5 relevant prompts and 0 of 5 unrelated ones. If it
triggers on fewer than 4 relevant prompts, the description is too narrow. If it triggers on more than 1 unrelated
prompt, it is too broad.

Status: **not yet run in an agent.** Run each prompt in a fresh session with the skill installed, tick the box when
the result is right, and record the agent and date. Run again after a model update.

## Should trigger

- [ ] Write a charter for my support agent: it can read tickets, draft replies and issue small refunds.
- [ ] What should my agent be allowed to do with these tools? It has email, a payments API and a shared drive.
- [ ] Set up approvals so my agent has to ask me before it sends, spends or deletes anything.
- [ ] Check this against the charter: the agent wants to refund order 1190 for 140 dollars.
- [ ] Audit what my agent did this week and tell me if the log has been tampered with.

## Should not trigger

- [ ] Run my agent in a Docker sandbox with no network access.
- [ ] Set up OAuth scopes and rotate the API keys for my agent.
- [ ] Write a Python function that retries a failed HTTP request.
- [ ] Is our agent's data handling compliant with GDPR?
- [ ] Summarise this week's customer support tickets.

## Record

| Agent | Date | Relevant (of 5) | Unrelated that triggered (of 5) |
|---|---|---|---|
