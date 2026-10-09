# Skill file format

Skills are the agency layer (layer 1) that agents work from. They are loaded into Wavy OS as versioned **standards** by `wavyos seed skills`, validated on every load, and put into each execution's work packet:

- the **role skill** for the agent doing the work (how this role thinks and works), and
- the **area skill** for the task's operating area (what good looks like there).

Client specifics never go in a skill. A skill says *what to look up* in the client's approved knowledge ("the approved welcome offer"), never the value ("10% off"). One client's result becomes a standard only through a reviewed promotion with client data removed.

## Area skills: `skills/areas/<area>.md`

One file per operating area (the 23 keys in `src/wavyos/taxonomy.py`). Front matter, then exactly these ten `##` sections, in this order. Each section becomes one standard (component) for that area.

```markdown
---
key: area.<area>
kind: area
area: <area>
title: <label from taxonomy>
owner_role: <account_lead | analyst | strategist | creative | qa>
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
What business outcome this area supports, in 2–4 sentences. Why a founder should care.

## Client facts and rules
The client facts the agent must find in approved knowledge before acting, as a list of
`fact key` – what it is – what to do if it's missing. Plus the exceptions that commonly apply.

## Inputs and sources
Data, documents and systems to consult, with what each is trusted for and its known traps
(e.g. attributed revenue is not incremental; list size is not sendable reach).

## SOP
Numbered steps the agent follows, from trigger to finished output. Concrete enough to execute.

## Decision rules
"If … then …" rules for choosing between actions, including when to stop and escalate.

## Examples
### Good
A short worked example of excellent output for this area (generic brand, no real client data).
### Not good
A short example of a common failure, and why it fails.

## Quality checklist
- [ ] Checkable items a QA reviewer can pass or fail from the output alone. 5–10 items.
  These become the default task checklist for this area.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
Rows use registered policy action keys (see `src/wavyos/policy.py`) and their level:
autonomous, approved_plan or explicit_approval.

## Outputs and destination
What the work produces (artifact kinds: brief, copy, design_direction, report, analysis,
draft_spec, other), the shape of each, and where it goes (task output, Client Hub, client message).

## Maintenance
Who reviews this skill, how often, and what triggers an early review.
```

## Role skills: `skills/roles/<role>.md`

```markdown
---
key: role.<role>
kind: role
role: <role>
title: <Role name>
version: 1
reviewed: 2026-10-09
---
## Mission
## You own
## You never
## How you work
## Decision rules
## Quality bar
## Output formats
## When to stop and ask
```

## Rules for every skill

- Write for an AI agent doing real work: imperative, specific, testable. No generic best practice.
- Thresholds and benchmarks are **Wavy starting defaults** unless they come from a published platform or regulator rule; say which.
- Never invent numbers about a client. Missing data is unavailable, never zero.
- Tools the agent has: `get_context`, `save_output`, `flag_insight`, `propose_fact`, `build_draft`, `request_approval`, `record_check` (QA only), `finish`. Skills refer to them by name.
- Plain language, short sentences. No client names, no internal file paths.
