---
key: area.campaign_production
kind: area
area: campaign_production
title: Campaign production and deployment
owner_role: account_lead
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Production turns an approved calendar row into a message that reaches the right people, renders correctly, links to working pages and honours every offer it makes. It is where most customer-visible mistakes happen: a broken code, a wrong audience, an unsubscribed person mailed, a clipped email. A disciplined chain with a policy gate at each step lets agents do most of the work while people approve only what goes live.

## Client facts and rules
Look up each fact with `get_context` for `campaign_production`, then `campaign_calendar`, `offers`, `voice_copy`, `design_system` and `segmentation`.

- `approved_calendar_entry` – the approved row this campaign comes from, with its decision. Missing: stop. No production without an approved row.
- `sending_identity` – sender name, from address, reply-to and the authenticated sending domain. Missing: stop; `flag_insight` to `deliverability`.
- `utm_convention` – source, medium, campaign and content values. Missing: use the Wavy starting default below and `propose_fact`.
- `store_domain` – the current live domain for links. Missing: stop; old-domain links are a known failure.
- `exclusion_segments` – suppressed, unengaged, recent purchasers (where relevant), open service cases, wholesale or retailer, other brands, internal test segments. Missing: stop audience setup.
- `seed_list` – internal test recipients across Gmail, Apple Mail and Outlook. Missing: `flag_insight`; test sends cannot pass.
- `send_timezone_rule` – account timezone or recipient local time. Missing: account timezone, stated in the schedule.
- `smart_sending_default` – on for marketing sends unless the approval says otherwise. Missing: on.
- `campaign_approver` – who approves the final send. Missing: account lead.
- `attribution_window` – the account's conversion window, for post-send review timing. Missing: `propose_fact` from account settings; label results provisional.

Common exceptions: transactional or service announcements may skip smart sending only with a recorded reason; sister brands never share audiences; a freeze window blocks new structural sends.

## Inputs and sources
- **Approved calendar row and decision**: the scope of what may be built.
- **Offer spec and checkout test**: the only proof an offer works.
- **Copy (version-bound) and design direction**: what gets built, exactly.
- **Platform recipient estimate**: trusted as an estimate of reach after exclusions. Trap: list or segment size is not sendable reach.
- **Test sends to the seed list**: the only proof of render, links and footer. Trap: a preview pane is not a received message.
- **Send report**: trusted for delivery, bounce, click, unsubscribe and spam complaint counts. Traps: opens are inflated by machine opens; attributed revenue is not incremental.

## SOP
Each step names its policy action and level.

1. **Approved calendar entry** (`decision.approve`, explicit_approval, done before this task). Run `get_context` for all areas above. If the row or its decision is missing, `flag_insight` and `finish`.
2. **Brief** (`draft.create`, autonomous). `save_output` kind `brief`: objective, audience include and exclude, offer key, products with stock read and date, key message, CTA and destination, deadlines, owner, approver.
3. **Research** (`data.read`, `analysis.run`, autonomous). Past sends on this theme with n, current stock, live flows that will hit the same people.
4. **Copy** (`draft.create`, autonomous). Follow the `voice_copy` skill. `save_output` kind `copy`.
5. **Design direction** (`draft.create`, autonomous). Follow the `design_system` skill. `save_output` kind `design_direction`.
6. **Audience setup** (`audience.prepare`, approved_plan). Include and exclude segments by name, without applying them to any live send.
7. **Platform build in draft** (`campaign.build_draft`, approved_plan). Call `build_draft` with kind `campaign` and a spec covering: copy version, template, dynamic blocks with fallbacks, links with UTMs, audiences, sender, smart sending, send time and timezone. Never schedule.
8. **QA** (`analysis.run`, autonomous; QA records each item with `record_check`). Run the checks in the Quality checklist plus: every link resolves 2xx on `store_domain` in at most 2 redirects; every dynamic block tested with a profile for each branch and with an empty profile; the offer works at checkout as the spec says; test send to the seed list received with correct subject, preview, sender, footer and a working unsubscribe.
9. **Approval request** (`approval.request`, autonomous, for `campaign.publish`, explicit_approval). `request_approval` with action `campaign.publish`, a summary, and the exact payload: campaign ID, copy version, audience include and exclude, recipient estimate, send time and timezone, offer key. `finish`. The approval binds to that payload; any change invalidates it.
10. **Scheduled send** (`campaign.publish`, explicit_approval). Only after approval, and only the approved payload.
11. **Launch verification** (`data.read`, autonomous). Within 1 hour of the send: recipients against estimate, bounces, spam complaints, clicks registering, first orders or code redemptions.
12. **Post-send review** (`analysis.run`; `result.record` once the attribution window closes). `save_output` kind `report`: delivered, click rate, unsubscribe rate, spam complaint rate, revenue per recipient (attributed, labelled), offer redemptions, comparison with the account's median for the same job, and one lesson.

## Decision rules
- If any step's approval or decision is missing, then stop at that step. Never skip ahead to a later step.
- If the copy, audience, offer, send time or destination changes after `campaign.publish` was requested, then request it again with the new payload.
- If a recipient estimate differs from the brief by more than 20% (Wavy starting default), then check exclusions before requesting approval.
- If any link returns 4xx or 5xx, points to an old domain, or takes more than 2 redirects, then QA fails.
- If an offer fails the checkout test, then QA fails and the send stops; `flag_insight` (critical).
- If a dynamic block has no fallback, then QA fails.
- **UTM convention, Wavy starting default:** `utm_source=<esp>`, `utm_medium=email` or `sms`, `utm_campaign=<YYYYMMDD>_<campaign-slug>`, `utm_content=<module>`; tracking on for every link.
- If smart sending is off on a marketing send, then the approval summary must say so and why. Never ignore unsubscribes on a marketing send (CAN-SPAM requires honouring opt-outs within 10 business days).
- **Launch verification, Wavy starting defaults:** if the spam complaint rate exceeds 0.1% or the bounce rate exceeds 2%, then `flag_insight` (high) and pause further sends to that audience pending review. Gmail and Yahoo bulk-sender rules require staying below 0.3% spam complaints and supporting one-click unsubscribe.
- If clicks are not registering within the first hour while delivery is normal, then test a link from the received email and `flag_insight`.
- **Post-send review timing:** provisional at 3 days (Wavy starting default), final only after `attribution_window` closes. Never call attributed revenue lift.
- If an unsubscribe rate exceeds 0.5% on a send (Wavy starting default), then record it in the review and flag it for the calendar.

## Examples
### Good
A QA record: "Copy v4 matches build. Subject and preview correct in Gmail app, Apple Mail, Outlook. 11 links, all 200 on the live domain, max 1 redirect, UTMs present. Two dynamic blocks tested with buyer, non-buyer and empty profiles. Recent purchasers excluded via exclusion segment; sale code tested at checkout with a full-price and an excluded item, value matches copy, does not stack with subscription. HTML 74 KB. Unsubscribe works. Estimate 41,200 against brief 43,000 (within 20%). Approval requested for campaign.publish with this payload."

### Not good
"Built and scheduled for 9 AM, looks good in the preview." No approval was requested, the timezone is unstated, no test send or link check was recorded, the offer was never tested, and "looks good in the preview" is not a received-message test.

## Quality checklist
- [ ] The build matches the approved copy version exactly (subject, preview, body, CTA, destination).
- [ ] Every link resolves 2xx on the current store domain within 2 redirects and carries UTMs.
- [ ] Include and exclude segments match the approved calendar row; suppressed and unengaged profiles are excluded.
- [ ] Every dynamic block and personalization token has a tested fallback.
- [ ] The promised offer was tested at checkout and behaves as the offer spec states.
- [ ] A test send reached the seed list, and render, footer address and unsubscribe were checked in Gmail, Apple Mail and Outlook.
- [ ] Send time, timezone and smart-sending setting are stated in the approval payload.
- [ ] The `campaign.publish` approval matches the exact payload that was scheduled.
- [ ] Launch verification and the post-send review are recorded, with attribution labelled.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| data.read | autonomous | Agent (logged) |
| analysis.run | autonomous | Agent (logged) |
| draft.create | autonomous | Agent (logged) |
| insight.create | autonomous | Agent (logged) |
| task.manage | autonomous | Agent (logged) |
| approval.request | autonomous | Agent (logged) |
| result.record | autonomous | Agent (logged), after the window closes |
| audience.prepare | approved_plan | Only inside the approved calendar decision |
| campaign.build_draft | approved_plan | Only inside the approved calendar decision |
| decision.approve | explicit_approval | Org admin, account lead, or client owner/approver |
| campaign.publish | explicit_approval | Org admin, account lead, or client owner/approver |
| discount.change | explicit_approval | Org admin or client owner/approver; locked |
| client.message.send | explicit_approval | Org admin or account lead |

## Outputs and destination
- **Brief** (kind `brief`), **copy** (kind `copy`) and **design direction** (kind `design_direction`) in the task output.
- **Draft build spec** (kind `draft_spec`, written by `build_draft`).
- **QA record**: one `record_check` per checklist item, with notes.
- **Approval request** for `campaign.publish`, bound to the payload.
- **Post-send report** (kind `report`) to the task output; summarized in the Client Hub and the next client meeting.

## Maintenance
The account lead reviews this skill every 90 days. Review early after any send error that reached customers, when the email platform changes scheduling, smart-sending or link-tracking behaviour, when mailbox providers change bulk-sender rules, or when the Klaviyo write connector changes what `build_draft` does.
