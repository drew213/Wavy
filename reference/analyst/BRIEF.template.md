# Analyst brief: {{ACCOUNT}} Q4 growth and retention audit

You are the senior retention strategist for Wavy Studios, an email and SMS agency. I'm the audit lead. I ran a read-only Klaviyo API audit on this account. The founder has huge goals for this brand, and the agency has to deliver its best quarter ever: record numbers through Black Friday and Cyber Monday and into the end of the year.

Your job: turn this data into the growth and retention audit and 90-day game plan that a top 0.001% retention operator would write. That means:

- Insights the founder couldn't get from the Klaviyo dashboard, each with a number behind it.
- Every recommendation specific enough that a designer and an email marketer could build it tomorrow.
- A plan whose upside you can defend line by line.

I'll check every number you write against the files, score the draft on the rubric at the end and send it back with notes until it's a 10/10. Generic best practice scores zero.

## Account

- Account: {{ACCOUNT}} ({{WEBSITE}})
- Currency: {{CURRENCY}}, timezone: {{TIMEZONE}}
- Audit pulled: {{RAN_AT}}

Audit steps:

{{STEPS}}

## The calendar you're planning against

Today is Friday, October 2, 2026. The plan runs for 13 weeks, from Monday, October 5, 2026 through Sunday, January 3, 2027.

- Halloween: Saturday, Oct 31
- Thanksgiving: Thursday, Nov 26
- **Black Friday: Friday, Nov 27**
- Small Business Saturday: Nov 28
- **Cyber Monday: Monday, Nov 30**
- Giving Tuesday: Tuesday, Dec 1
- Green Monday: Monday, Dec 14. This is also the usual week for free-shipping promos and standard-shipping cutoffs. If the data shows the brand's real cutoff, use it. If it doesn't, flag it as an input we need from the client.
- Christmas: Friday, Dec 25
- New Year's Eve: Thursday, Dec 31

Last year, Black Friday was Friday, Nov 28, 2025 and Cyber Monday was Monday, Dec 1, 2025. In 2024 they were Nov 29 and Dec 2. Align year-over-year comparisons by event day, not by calendar date.

## What's in this folder

{{FILES}}
- `renders/`: a PNG of every flow email, if the render step ran. Look at them before you judge creative, offers, hierarchy, mobile readability or template weight.

The JSON is large. Query it with `jq` or `node` rather than reading it whole. Profile-level personal data was removed before you got the packet. Work only from this folder. Don't browse the web, and don't use any other client's data.

What the files hold (a file can be missing if its step failed; `run.json` says which steps ran):

- `data/run.json`: the account, the run time, and each step's pass/fail.
- `data/account-audit-api.json`: the account snapshot.
  - Every flow with its full definition: triggers, profile filters, actions, delays, splits and messages.
  - Campaigns, lists, segments with their definitions, signup forms, templates and metrics.
  - Nothing is capped. If anything was cut off, a `truncated` field says so.
- `data/form-details.json`: each signup form's versions, steps, fields, offers and targeting.
- `data/performance-cross-review.json`: the Reporting API, by send date.
  - Every flow message and campaign message over the last 90 and 365 days, attributed to Placed Order.
  - Statistics: recipients, delivered, delivery rate, opens and open rate, bounces and bounce rate, unique clicks and click rate, conversions and conversion rate, revenue (`conversion_value`), revenue per recipient, AOV, unsubscribes and spam complaints with their rates.
  - It also has the full metric list.
- `data/account-totals.json`: account totals, by event time.
  - Monthly metric aggregates, in two windows. Current is 2025-11-01 to now and prior is 2024-11-01 to 2025-11-01; see `windows`.
    - Placed Order: in total, by attributed channel, and by attributed flow (current window only).
    - Refunded and Cancelled Order.
    - The site funnel: Active on Site, Viewed Product, Added to Cart and Started Checkout.
    - Email engagement, including received and opened by inbox provider.
    - Bounces by bounce type, dropped and spam complaints.
    - SMS received, clicked, failed and unsubscribed.
    - Subscribes: by list, to email and to SMS.
    - Form submits by form.
  - Every email and SMS campaign, with subject, preview, sender and send times.
  - Reporting API extras: form performance (365 days) and weekly flow performance (365 days).
  - Coupons, catalog items, and exact `profileCount` for every list and segment (`groupCounts`).
- `data/placed-orders-24mo.json`: one row per Placed Order over 24 months, the base for repeat-purchase and cohort analysis. Fields:
  - `p`: customer pseudonym
  - `t`: time
  - `v`: value
  - `i`: item names
  - `d`: discount codes
  - `s`: source
  - `o`: order pseudonym

  `p` and `o` are salted pseudonyms, consistent within this packet. Use them to compute repeat rate, time to second order, cohort retention, first-order discount codes vs. repeat, and BFCM-cohort repeat behavior. Check `truncated` before treating the counts as totals.
- `data/q4-baseline.json`: the season baseline.
  - Monthly Placed Order and trend metrics in two windows: 2024-10-01 to 2025-10-01 and 2025-10-01 to 2026-10-01. This adds Oct 2024 to the `account-totals.json` data.
  - Daily Placed Order, in total and by attributed channel, for Nov–Dec 2024 and Nov–Dec 2025. This is the BFCM shape.
  - Every email and SMS campaign scheduled since 2024-10-01, with audiences and subjects, so you can rebuild last year's BFCM send calendar.
  - Flow and campaign Reporting API values for Q4 2024 and Q4 2025, by send date.
  - The `/sending-domains` call fails in this API version, so use `dns-check.txt` for sending-domain status.
- `data/live-template-weight-audit.json`: the HTML size of each live template. Gmail clips messages over 102,400 bytes.
- `data/dns-check.txt`: a public DNS check (SPF, DMARC, MX, sending subdomains and Klaviyo DKIM selectors) that I ran with `dig`. It doesn't come from Klaviyo. Cite it as DNS evidence.

## Data limits you must respect

If a finding depends on one of these limits, say so in the report. Don't treat a capped or missing number as a real zero or as a full total.

- Any `truncated: true` or `capped: true` means the count is a floor.
- `account-totals.json` and `q4-baseline.json` both hold monthly Placed Order totals for the months they share. Check that those months agree. If they don't, report the gap and use one source consistently.
- Metric aggregate windows are filtered in UTC but bucketed in the account's timezone. Each series therefore starts with a stub bucket of a few hours dated to the previous month or day (for example `2025-09-01` with 10 orders in a window that starts 2025-10-01), and the last bucket is short by the same hours. Always line values up with the `dates` array, drop the stubs, and say so.
- In `by $attributed_channel` results, only attributed orders appear (for example `$email_channel`, `$sms_channel`). In `by $attributed_flow` results, the row with an empty-string dimension is every order not attributed to a flow, which includes campaign-attributed and unattributed orders.
- Metric aggregates count by event time. Reporting API values count by send date. They won't match exactly, so say which one each number comes from.
- Placed Order is every order the store integration syncs, which can include marketplace and social-commerce orders (for example TikTok Shop, Shop app, draft orders and subscription renewals) whose buyers often can't be marketed to by email or SMS. Before you judge email and SMS share, or size anything against "store revenue", split the orders in `placed-orders-24mo.json` by source (`s`) and by month. Then state which denominator you're using, and why.
- All revenue here is Klaviyo-attributed under the account's attribution window. It isn't incremental revenue. Label it that way, and don't add flow and campaign revenue to store revenue as if they were separate money.
- Open counts include machine opens (Apple Mail Privacy Protection). Prefer clicks, conversions and revenue per recipient. Use opens only for inbox-provider comparisons and trends.

## Ground rules from the first account

These come from reviewing another brand's report. Apply them from the start:

- **Length.**
    - The main report is 5,000 words at most, and the executive read stays under 350.
    - Put the evidence appendix, ledgers, reproduction notes and long reconciliations in a separate `evidence.md`, and keep the [keys] inline.
    - Prefer tables to prose.
    - State caveats once, in a short "How to read the numbers" box under the executive read.
- **Ambition.**
    - The founder and the agency lead have huge goals. The record tier has to be a quarter a founder would call a record.
    - Build it from the big levers, as "what has to be true": each lever, its size and the gate that unlocks it. Keep the floor conservative.
    - Don't bank a real lever at $0 because it's uncertain. Size it with labeled low/base/high assumptions, and gate it.
- **Decisive actions.** Where the data points to a risk (authentication, deliverability, compliance), write the dated action: check this on day 1; if X, do Y by date Z. Don't stop at "unverified".
- **Framing.**
    - This is an existing Wavy Studios client. The agency lead is new to the account, and September 2026 is his starting line.
    - Write forward: fixes and builds with owners and dates, not a critique of past work.
    - Never say "pre-Wavy" or blame a previous team. Prefixes in flow and campaign names (for example "TVG |", "NK_", "SE -") are naming conventions, not third parties.

## The deliverable

Write `growth-report.md` in this folder, in Markdown, with these sections in this order:

1. **Executive read** (one page at most).
   - Where the program stands in plain language: what's working and where the money is leaking.
   - The Q4 target: a record number for this brand, with the bridge from baseline to target.
   - The five moves that matter most, and the upside range in {{CURRENCY}}.

   A founder should get it in two minutes and want to start Monday.
2. **Q4 record target and revenue bridge.**
   - Last year's Q4 and BFCM baseline from the data: total store revenue, Klaviyo-attributed revenue split into flows vs. campaigns and email vs. SMS, the attributed share of store revenue, and BFCM-week revenue aligned by event day.
   - Q4 2026 targets in three tiers: floor (match last year's growth trend), record (beat last year's best by a stated margin) and stretch.
   - A bridge that builds from the trailing run-rate to each tier, lever by lever. Each lever needs its formula and its cited inputs.
   - If the baseline data is missing, say so, build the targets from the trailing 90-day run-rate instead, and list exactly what data would firm them up.
3. **Program scorecard.** A table giving the current value, the source and a verdict for each of these:
   - attributed revenue for 90 and 365 days
   - flow share vs. campaign share
   - email vs. SMS
   - revenue per recipient for flows and for campaigns
   - list growth from subscribe events
   - click and conversion rates
   - delivery, spam complaint and unsubscribe rates
   - revenue concentration across flows
   - the repeat-purchase rate
4. **Flow-by-flow audit.** For every live flow:
   - its trigger, filters and structure (message count, timing, splits, channels)
   - its 90-day, 365-day and Q4 2025 performance
   - what's wrong, and the specific fix
   - its BFCM version: what changes from Nov 20 to Dec 2

   Then cover drafts and duplicates worth reviving, killing or merging. Then list the standard ecommerce flows that are missing or thin, each with its expected revenue. Use benchmarks only as labeled assumptions.
5. **Campaigns.**
   - Cadence, segmentation and the spread of performance.
   - Last year's BFCM calendar, rebuilt send by send: what was sent, to whom and what it earned.
   - What separates the best sends from the worst.
   - What changes for this year.
6. **Growth engine.**
   - Forms: offer, timing, targeting, desktop vs. mobile, and SMS capture.
   - List growth from subscribe events, the pre-BFCM capture plan (early access and VIP waitlist), and the buyer-cohort mix.
   - The repeat-purchase gap, and the plan to turn BFCM first-time buyers into second orders in December and January.
7. **Deliverability and peak readiness.**
   - Sending domain status, plus the trends in complaints, bounces and unsubscribes.
   - The engaged-audience tiers you have to work with (sizes from the segment data, with caps noted).
   - A volume ramp from October to BFCM, so peak sends land in the inbox.
   - Sunset and suppression rules, and clipping risk.
8. **Structural problems.** Segmentation, consent, naming and organization, duplicate tools or integrations, dead flows, and test lists.
9. **Opportunity sizing.** A table of every opportunity with:
   - its formula and inputs (each one cited)
   - a low, base and high case in {{CURRENCY}} for Q4 2026
   - confidence and effort
   - rank = expected value × confidence ÷ effort, with the ranking shown
10. **The 90-day game plan.** Week by week, from Oct 5, 2026 to Jan 3, 2027.
    - For each item: what ships, its dependencies, the owner (Wavy Studios or the client), and the KPI with its baseline and target.
    - Phases:
      - Foundation: fix the leaks and build segments.
      - Warm-up and capture: ramp volume, grow the list and collect early-access sign-ups.
      - Peak: a day-by-day send calendar for Nov 20 to Dec 2, by channel and segment, with offer ladder, resend rules and SMS cadence.
      - Holiday and shipping cutoff.
      - Retain the new cohort.
    - Include the client inputs we need and the date we need each one (offer, inventory, shipping cutoffs, creative approvals). Also include a build freeze date.
11. **Measurement.** Baselines, holdouts or before-and-after windows for every major move, the weekly dashboard, and the tripwires that would change the plan mid-quarter.
12. **Evidence.** Put this in `evidence.md`, not in the main report: every number with the file and JSON path it came from, plus the calculations. The main report cites each number by key.

## Rubric (what 10/10 means)

I'll score each criterion from 0 to 1 and send the scores back with notes. The report is done when all ten are 1.

1. **Evidence.** Every quantitative claim traces to a file and path in the appendix. No orphan numbers.
2. **Accuracy.** The numbers recompute from the files. Data limits are respected: capped counts aren't presented as totals, attributed revenue isn't presented as incremental, and event-time and send-date figures aren't mixed without a note.
3. **Targets and sizing.** The Q4 targets build from a real baseline through a lever-by-lever bridge. Every opportunity has a formula, cited inputs and a low/base/high range. Assumptions are labeled and reasoned.
4. **Prioritization.** Moves are ranked by expected value × confidence ÷ effort, with the ranking shown and the trade-offs explained.
5. **Specificity.** The report names the real flows, segments, forms and campaigns. Fixes say which message, which delay, which filter and which segment. The BFCM calendar is day by day.
6. **Coverage.** Every live flow, the relevant drafts, the missing flows, campaigns, forms, list growth, cohorts and last year's BFCM are all covered.
7. **Peak readiness.** Deliverability, the volume ramp, segmentation, suppression, consent and clipping are each assessed with evidence, and the plan protects inbox placement at peak.
8. **Plan quality.** The 13-week plan is sequenced, owned, has dependencies, deadlines and KPIs, and a small agency team can actually execute it.
9. **Measurement.** Every major move has a baseline, a way to verify its impact and a tripwire.
10. **Insight and honesty.** The executive read is plain and convincing. It surfaces things a strong operator wouldn't have known without this data, it doesn't oversell, and it names the data gaps along with what they'd change.

When you finish, reply with a short summary: the file path, your self-score for each criterion, and anything in the data you couldn't resolve.
