---
id: sizing
order: 5
title: Size the opportunities
owner: Strategist
tracks: snapshot, deep
time_snapshot: 30 min
time_deep: 3 h
summary: Turn failed and warned checks into dollar ranges a founder can trust, with every assumption visible.
inputs: Scorecard
outputs: "Opportunity table: each lever with eligible pool, assumption, low/base/high, dependencies"
exit: Every lever in the report has a formula someone else can reproduce, and no customer or order is counted by two levers.
---
## The formula

Every lever uses the same shape:

```
eligible pool × expected rate change × value per conversion × months = revenue range
```

- **Eligible pool**: people who can actually receive the fix. Current consent, suppressions removed, overlaps removed. Never raw list size.
- **Expected rate change**: from the account's own history where possible (the account's best flow, last year's Q4), otherwise a stated Wavy default. Low, base and high are separate assumptions, not ±20%.
- **Value per conversion**: AOV from the pinned order metric. Use contribution margin when the client gave it at intake; label gross revenue as gross.
- **Months**: the realistic run time inside the plan window, after the build time.

## Steps

1. Take every fail and warn from the scorecard that has an `impact.method`.
2. Fill the formula from the account's data. Write each input with its source.
3. Remove double counting. One order can't be a winback win, a campaign win and a replenishment win. Assign each order pool to one lever.
4. List dependencies. A lever that needs a client action (DNS fix, new offer, subscription data) shows that gate; if the gate fails, the lever goes to zero.
5. **Snapshot**: size the top 5 levers only, base case, as a range. **Deep**: size all levers, low/base/high, and rank them by base value ÷ build effort.

## Checklist

- [ ] Top 5 levers sized as ranges with their inputs shown <!-- id:sizing-top5 tracks:snapshot,deep -->
- [ ] All levers sized low/base/high <!-- id:sizing-all-levers tracks:deep -->
- [ ] Pools deduplicated; no order counted twice <!-- id:sizing-no-double-count tracks:snapshot,deep -->
- [ ] Each lever's dependency gate written <!-- id:sizing-gates tracks:deep -->
- [ ] Gross vs. margin labelled on every figure <!-- id:sizing-gross-label tracks:snapshot,deep -->
- [ ] Levers ranked by value ÷ effort <!-- id:sizing-ranked tracks:deep -->

## Guardrails

- A scenario is arithmetic, not proof of lift. Say "could" and show the range.
- The low case is a scenario, not a guaranteed floor.
- Attribution isn't incrementality. Don't promise incremental revenue from attributed numbers.

## Tooling

| Tool | Status | Where |
|---|---|---|
| Sizing formulas in the check library (`impact.method`) | Built (spec) | `checks/check-library.json` |
| Sizing engine with dedup and gates | v0.3 (R-305) | |
