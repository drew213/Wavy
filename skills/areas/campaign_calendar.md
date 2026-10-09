---
key: area.campaign_calendar
kind: area
area: campaign_calendar
title: Campaign strategy and calendar
owner_role: strategist
version: 2
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
The calendar decides what each subscriber hears about, how often and why, for the month ahead. A good calendar ties every send to the month's objective, balances education, product and promotion so the list does not learn to wait for a discount, and clears stock, consent, offer and capacity questions before production starts. It is the approved plan every brief and build depends on: nothing is produced from an unapproved row.

## Client facts and rules
Look up each fact with `get_context` for `campaign_calendar`, then `offers`, `sms` and `segmentation`.

- `monthly_objective` – the agreed goal for the month (first orders, repeat orders, launch sell-through, list health) and how it is measured. Missing: `flag_insight` (high) and stop. Never plan without an objective.
- `target_basis` – whether targets are store revenue, attributed revenue or contribution. Missing: plan without a revenue number and label the plan "target not agreed".
- `email_frequency_cap` – maximum campaign emails per recipient per rolling 7 days. Missing: use the Wavy starting default below, label it, and `propose_fact`.
- `sms_frequency_cap` – the shared text allowance. Missing: use the `sms` skill default and `propose_fact`.
- `contact_ledger` – a person-level sending ledger across email, SMS and flows, or an independently verified cap enforcement rule. Missing: every row that could breach a cap is conditional.
- `approved_offers` – offer keys approved for the period, with eligibility. Missing: plan no-offer content; mark promotional slots "offer pending, client decision".
- `launch_schedule` – launches with dates, stock and embargoes. Missing: do not date a launch row.
- `inventory_source`, `low_stock_rule` – where stock is read and the level below which a product cannot lead a send. Missing: `flag_insight`; every product-led row carries "stock unverified" and cannot pass QA.
- `key_dates` – client sale periods, shipping cutoffs, observed holidays, freeze windows. Missing: `propose_fact` from public carrier cutoffs and mark them proposed.
- `engaged_segment_definitions` – click- or site-activity windows (for example 30/60/90/180 days). Missing: `flag_insight` for `segmentation`; never define engagement by opens alone.
- `excluded_audiences` – recent purchasers for acquisition promotions, open service cases, retailer, wholesale and affiliate populations, marketplace-only buyers without consent. Missing: `flag_insight` and stop before audience selection.
- `calendar_approver`, `approval_lead_time_days` – who approves and how far ahead. Missing: account lead approves; `propose_fact` a lead time.
- `production_capacity` – sends per week the team can brief, build and QA.

Common exceptions: peak-season freeze windows; sister brands that share customers; regulated categories where claims and channels are limited; client blackout dates around their own events.

## Inputs and sources
- **Campaign history** (sends per week, revenue per recipient, click and unsubscribe rates): trusted for comparisons within the account. Traps: recipients summed across sends are exposures, not people; attributed revenue is platform credit, not lift; A/B metrics belong to the parent campaign; opens are inflated by machine opens; sends to inbox-provider test segments measure deliverability, not content.
- **Store orders by day**: trusted for demand and seasonality. Traps: account timezone; subscription renewals and marketplace orders are not campaign responses.
- **Segment counts**: membership, not sendable reach. Consent, suppression and smart sending cut it.
- **Inventory system**: trusted only on the day read. Read again at brief and before scheduling.
- **Live flows**: welcome, abandonment, post-purchase and winback already reach the same people and count toward pressure.
- **Last year's same period**: dates, themes, offers and results, labelled with their attribution basis.
- **Competitor archives**: show what ran, not what worked. Mailbox cadence is not per-customer frequency.

## SOP
1. Trigger: the monthly planning task, at least `approval_lead_time_days` plus production time before the month starts. Run `get_context` for this area, `offers`, `sms` and `segmentation`.
2. Confirm `monthly_objective`. If missing, `flag_insight` and `finish`.
3. Pull the last 13 full weeks and the same month last year: sends per week, revenue per recipient, unsubscribe rate, and the top and bottom themes by revenue per recipient with n.
4. Lay down fixed dates: launches, key dates, shipping cutoffs, freezes, client events.
5. Set the slot count: the lower of the frequency cap and production capacity, per audience tier.
6. Assign themes using the mix rule below. Every theme names the job: education, product story, social proof, launch, promotion or list care.
7. Write each row: date and time with timezone; theme and job; objective it serves; include and exclude audiences by segment name; channels; offer key or "none"; products and stock status; paired SMS (if any); dependencies; owner; brief due date.
8. Include a contact-pressure table for each overlap population: planned emails, planned texts, applicable live-flow paths, effective caps, priority and resulting exclusions. Use a person-level sending ledger or an independently verified enforcement rule. Tier-level totals and a note that flows may fire do not prove a cross-channel cap. Count every promotional text, welcome texts included, per the `sms` skill.
9. Check stock for every product-led row against `low_stock_rule`. Swap or mark the row conditional.
10. `save_output` (kind `brief`, label "<Month> campaign calendar v<n>") with the table, the mix summary, open dependencies and a conditional-rows list.
11. `flag_insight` for each blocking dependency (missing offer, unverified stock, consent gap).
12. Ask the `calendar_approver` to approve the calendar as a decision; a pending request is not approval. To send it to the client, `request_approval` with action `client.message.send` and the exact message as payload. `finish` and list the open decisions.

## Decision rules
- **Mix, Wavy starting default (non-peak month):** at least 40% of sends carry no offer (education, product story, social proof, list care); no more than 40% are promotional; no audience gets more than two promotional sends in a row. Peak months may rise to 60% promotional, with at least one no-offer send per week to the engaged tier.
- **Frequency, Wavy starting default:** 2–3 campaign emails per week to the 90-day engaged tier. The 180-day tier only for major promotions, with unengaged profiles excluded. Never "all subscribers". A cadence under 2 per week, or any empty week, is flagged.
- If an overlap population would exceed a cap, then drop or exclude the lower-priority send for that population. Never raise the cap in the calendar. If no ledger or verified enforcement rule exists, the row is conditional on one.
- If a product is below `low_stock_rule`, then it cannot lead a send. If launch stock is unconfirmed 7 days before the send (Wavy starting default), then mark the row conditional and `flag_insight`.
- If a row needs an offer not in `approved_offers`, then write "offer pending" and route it to the `offers` area. Never write a discount value into the calendar.
- **SMS pairing:** pair a text only with the highest-intent rows (launch, sale start, last day), to the SMS-consented segment only, at least 12 hours from the same-topic email (Wavy starting default). Follow the `sms` skill caps.
- If a reminder targets "non-openers", then replace it with a click- or site-intent audience. A last-call send is an alternative for a held-out high-intent group, never a second same-day promotional email to someone who received the last-day send.
- If no peak-season plan exists by mid-October, then `flag_insight` (high).
- If an approved row changes its date, audience, offer or product, then it needs re-approval. Copy edits do not.
- If a row depends on unconfirmed economics, consent or stock, then keep it as a conditional row with its release gate. A failed gate removes the row; it is never forced through.

## Examples
### Good
"Week 2, Tue 10:00 recipient time. Theme: how-to-use story for the hero product (education, no offer). Objective: second orders. Include: 90-day engaged, purchased once 20–60 days ago. Exclude: purchased in last 14 days, open service case, wholesale. Channel: email only. Offer: none. Stock: 3,000+ units read Oct 4 (above low-stock rule). Dependency: approved usage instructions from the label. Owner: strategist. Brief due: Oct 8." It names the job and the objective, sets audiences by segment, carries a stock read with a date, and makes no offer promise.

### Not good
"Nov 3: 25% off sitewide, all subscribers. Nov 4: resend to non-openers. Nov 5: extra 15% off, stacks." It invents offers, sends to the whole list, uses machine-inflated opens and ignores frequency caps. The combined saving is unknown: 36.25% if applied in sequence, 40% if both apply to the same subtotal, so it depends on configured, checkout-tested mechanics (see `offers`). There is no objective, stock check or exclusion.

## Quality checklist
- [ ] Every row names the monthly objective it serves and its job (education, product, promotion, launch, social proof, list care).
- [ ] Every row has a date, a send time and a timezone.
- [ ] Every row lists include and exclude audiences by segment name; no row targets the whole list.
- [ ] A contact-pressure table per overlap population shows planned emails, texts and live-flow paths against effective caps, backed by a person-level ledger or a verified enforcement rule; tier totals alone do not pass.
- [ ] The mix summary is present and meets the rule for the month (or the deviation is explained and approved).
- [ ] Every offer reference is an approved offer key or "none"/"offer pending"; no discount values appear.
- [ ] Every product-led row shows a dated stock read that passes the low-stock rule, or is marked conditional; re-reads at brief and preflight are listed as later gates, not claimed.
- [ ] Every paired SMS row is to an SMS-consented segment and respects the cross-channel spacing.
- [ ] Open dependencies and conditional rows are listed, each with an owner and a due date.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| data.read | autonomous | Agent (logged) |
| analysis.run | autonomous | Agent (logged) |
| draft.create | autonomous | Agent (logged) |
| insight.create | autonomous | Agent (logged) |
| fact.propose | autonomous | Agent (logged); stays proposed until approved |
| decision.propose | autonomous | Agent (logged) |
| approval.request | autonomous | Agent (logged) |
| audience.prepare | approved_plan | Only inside the approved calendar decision |
| decision.approve | explicit_approval | Org admin, account lead, or client owner/approver |
| fact.approve | explicit_approval | Org admin, account lead, or client owner/approver |
| client.message.send | explicit_approval | Org admin or account lead |

## Outputs and destination
- **Campaign calendar** (artifact kind `brief`): one table row per send with the fields in SOP step 7, then a mix summary, the contact-pressure table per overlap population, conditional rows with gates, and open client decisions. Saved to the task output.
- **Insights** for blocking gaps, via `flag_insight`.
- After approval, the calendar appears in the Client Hub calendar. A client message goes out only through an approved `client.message.send`.
- Each approved row becomes the input to a `campaign_production` brief.

## Maintenance
The strategist lead reviews this skill every 90 days. Review early after a peak-season post-mortem, when two monthly calendars in a row are sent back by the client, when a platform changes frequency or smart-sending behaviour, or when a check-library campaign threshold changes.
