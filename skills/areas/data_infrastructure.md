---
key: area.data_infrastructure
kind: area
area: data_infrastructure
title: Account and data infrastructure
owner_role: analyst
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Every number, segment and trigger depends on the account's data being wired correctly: the real order event, live triggers, complete product data, the right timezone and honest consent records. This area proves that before anyone reports or builds on it. A founder should care because a dormant integration or a dead trigger makes good marketing look broken, and broken marketing look fine.

## Client facts and rules
Look each up with `get_context` (area `data_infrastructure`). If a fact is missing, run the check that establishes it; if a person must confirm it, `flag_insight` and stop the dependent work. Never assume a platform default.

- `account_ids` – the ESP, SMS and store account IDs the client confirmed. Missing: stop; a wrong account is easy when brands share a login.
- `account_timezone` and `account_currency` – as reported by the platform. Missing: no period reporting.
- `canonical_order_metric` – the pinned order event (metric ID, integration, verified window) and any past platform eras. Missing: no revenue figure ships.
- `canonical_event_map` – the metric ID for checkout started, added to cart, viewed product, fulfilled/delivered, subscription events. Missing: flows on those events are unverified.
- `integrations` – each connected app (store, SMS, subscription, reviews, forms, helpdesk), what it sends and receives, and its owner. Missing: inventory it from the account and flag unknown owners.
- `catalog_sync` – catalog source, sync status, last sync time. Missing: product blocks and recommendations are unverified.
- `attribution_settings` – the account's actual attribution windows and conversion metric, read from settings or the export. Missing: label every attributed figure "attribution settings unconfirmed".
- `metric_source_of_truth` – which system is authoritative for each metric (store for orders and revenue, ESP for message metrics, subscription platform for active contracts). Missing: use that default and record it as proposed.
- `consent_records` – how consent is captured per channel, the consent text, timestamps, source, and where opt-outs sync. Missing: no audience expansion, no SMS sends to imported numbers.
- `access_owners` – who granted each connection, its scope (read-only or not) and its revocation date. Missing: flag; untracked access is a risk the agency carries.

## Inputs and sources
- **Platform metrics and events** – trusted for event counts by integration. Trap: stores that switched platforms keep dormant metrics with the same name.
- **Flow definitions** – trusted for trigger, filters and status. Trap: a live flow on a dead metric looks live and sends nothing; custom API metrics need a person to confirm their source.
- **Order events** – trusted for line items, value, currency. Trap: missing line items or mixed currencies quietly break product and value analysis.
- **Reporting exports** – trusted for message metrics within their stated window and settings. Trap: now-relative windows and pinned windows must not be mixed unlabelled; recipients are exposures, not people.
- **Consent and suppression data** – trusted for sendable reach. Trap: list and segment counts include unsubscribed and suppressed people; overlapping lists cannot be summed.
- **Opens** – diagnostic only. Trap: Apple Mail Privacy Protection generates automatic opens, so opens never prove engagement.
- **DNS records** – trusted for configuration. Trap: DNS alone does not prove aligned live mail; use received headers.

## SOP
1. Call `get_context` for `data_infrastructure`. Read the account to confirm account ID, timezone and currency match `account_ids`. Mismatch: `flag_insight` critical and stop.
2. Pin the order source. List every order-type metric with monthly counts over full history. Pick the one with current, continuous events from the confirmed platform. Record past eras. `propose_fact` for `canonical_order_metric` with the evidence.
3. Map canonical events. For each live flow, resolve its trigger to a metric and integration and compare with the canonical map. Record dead, duplicate or custom triggers.
4. Check order data quality over the last 12 months: share of orders with line items, currency mix, and refunds or cancellations as separate events.
5. Check periods: buckets cut at local midnight in the account timezone, daylight saving handled, partial periods labelled.
6. Check completeness: every collection step reports complete or partial with a reason; no truncated list, flow or report. Missing data is unavailable, never zero.
7. Reconcile attribution: attributed value per month must not exceed store order value; flow totals must not exceed the channel total; two reports of the same thing should agree. Record the actual attribution settings.
8. Measure sendable reach per channel: deduplicate profiles across lists, join current consent and suppression, and report union, sendable, and the ratio to raw count.
9. Validate tracking for each release dependency: trigger a test event on a test profile, confirm it reaches the expected flow with the right properties, and confirm the catalog item it references is current. Record the test, time and result.
10. Inventory consent and access: capture method and text per channel, opt-out sync between every sending tool, and each connection's grantor, scope and expiry.
11. Save the data-health report with `save_output` (kind `report`). For any fix that needs an account setting or integration change, draft it and call `request_approval` with `account.settings_change`. `finish`.

## Decision rules
- If the order source is ambiguous, then stop every revenue figure until it is pinned with evidence.
- If a live flow triggers on a metric with no events in the last two full months, then flag it critical; fixing it needs `flow.change_live`.
- If two sending tools hold the same channel's subscribers, then require opt-out reconciliation before the next send; a newer opt-out always beats an older consent import.
- If consent source or timestamp is missing for SMS numbers, then those numbers are not sendable for marketing.
- If attribution settings are unknown, then attributed figures ship only with that label and are never called incremental.
- If a check's precondition is absent (no SMS program, no subscription product), then score it not applicable, never pass.
- If a fix requires a key with write scope, then do not request one; the account lead decides how the change is made.
- If a key or token appears in any text, then flag it critical for immediate revocation.

## Examples
### Good
"Order source pinned: the current store integration's order metric holds 99% of last-90-day order events; a dormant integration from the previous platform holds the rest and stopped two years ago (monthly counts attached). All five live recovery flows trigger on canonical metrics with events last month. Line items present on 97% of orders. Attribution windows read from settings and recorded. Sendable email reach is 61% of the raw main list after dedupe, consent and suppression."
### Not good
"Revenue is up 30% this quarter based on the Placed Order metric." It does not say which of two same-named metrics was used, gives no window or timezone, and may be counting a dormant integration.

## Quality checklist
- [ ] Account ID, timezone and currency are stated and match the client's confirmation.
- [ ] The canonical order metric is named with its evidence and past eras.
- [ ] Every live flow's trigger is classified canonical, duplicate, custom or dead.
- [ ] Line-item and currency shares are reported with the window.
- [ ] Attribution settings are stated as read, or labelled unconfirmed.
- [ ] Sendable reach is reported deduplicated with consent and suppression applied, beside the raw count.
- [ ] Each step is complete or partial with a reason; nothing missing is shown as zero.
- [ ] No personal data or credential appears in the output.
- [ ] Tracking tests record profile type (test), time and result.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| `data.read` | autonomous | Agent, logged |
| `analysis.run` | autonomous | Agent, logged |
| `insight.create` | autonomous | Agent, logged |
| `fact.propose` | autonomous | Agent; stays proposed |
| `job.run` | autonomous | Scheduled checks |
| `connection.manage` | autonomous | Org admin or account lead only (human) |
| `audience.prepare` | approved_plan | Inside an approved decision |
| `work.implement_nonlive` | approved_plan | Inside an approved decision (for example a test profile setup) |
| `flow.change_live` | explicit_approval | Account lead, org admin or client owner/approver |
| `account.settings_change` | explicit_approval | Org admin or client owner (locked) |

## Outputs and destination
- **Data-health report** (report): account confirmation, source contract, event map, data quality, period check, completeness, attribution reconciliation, reach, consent and access inventory. Task output; summary to Client Hub after review.
- **Source contract** (other): pinned metric IDs and verified window. Proposed fact; every reporting task reads it.
- **Tracking validation log** (other): each test with time and result.
Thresholds are Wavy starting defaults unless noted: canonical order metric holds at least 95% of last-90-day order events (warn 80–95%, fail below 80%); at least 95% of orders carry line items (fail below 70%); at least 99% of value in one currency; attributed share of store revenue 10–50% (warn 50–65%, fail above 65% or any month where attributed exceeds total); two reports of the same metric within 15% (fail above 30%).

## Maintenance
The analyst reviews this skill every 90 days. Review early after a client changes store platform, ESP, SMS tool or subscription app; when a platform changes its attribution or consent model; or after a reporting error traced to data wiring.
