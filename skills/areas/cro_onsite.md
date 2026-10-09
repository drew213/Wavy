---
key: area.cro_onsite
kind: area
area: cro_onsite
title: CRO and onsite alignment
owner_role: strategist
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Every email, SMS and pop-up makes a promise. This area checks that the page, cart and checkout keep it, and that the onsite path converts the traffic retention sends. A founder should care because a broken promise wastes the click and the audience's trust, and the cheapest revenue fix is often a mismatched offer, a dead link or a pop-up that fires at the wrong person.

## Client facts and rules
Call `get_context` for `cro_onsite`, `offers` and `economics`. If a fact is missing, `flag_insight` naming what it blocks and `propose_fact` only with a written or observed source. Never fill a gap with what the site "probably" does.

- `site_platform` – storefront platform, theme and checkout type. Missing: record as unknown; don't propose platform-specific fixes.
- `primary_domain` – the live store domain and any retired domains. Missing: stop link checks; you can't tell a stale link from a live one.
- `site_change_owner` – who changes the theme, product pages and checkout (client team, developer, app vendor) and how to request it. Missing: every onsite item is blocked at handoff; flag it.
- `approved_offers` – current offer terms, eligibility, expiry and stacking rules. Missing: don't judge offer consistency; flag to `offers`.
- `free_shipping_threshold` – threshold and where it shows. Missing: don't reference it.
- `subscription_terms` – discount, cadences, skip, pause and cancel rules, and the platform. Missing: don't propose subscription positioning.
- `popup_rules` – tool, triggers, timing, targeting, suppression and the welcome incentive. Missing: test the live behavior and `propose_fact` with what you saw.
- `approved_claims` and `prohibited_claims` – product claims allowed onsite and in messages. Missing: no new claim copy.
- `consent_language` – approved email and SMS consent text. Missing: no pop-up copy that collects a phone number.

Common exceptions: product pages for limited editions or bundles can show different prices; review counts can be pooled across variants.

## Inputs and sources
- **Rendered messages and their real click destinations** – trusted for what the customer is promised and where they land. Trap: a saved template isn't a received message; check the link in the sent version.
- **Live site in a fresh session, mobile and desktop** – trusted for what a new visitor sees. Traps: a logged-in or returning browser hides pop-ups and changes prices; reading page text can't establish pop-up timing.
- **Checkout run up to the payment step** – the only proof that an offer applies, stacks or expires as promised. Trap: a discount settings screen isn't a checkout result. Never submit payment.
- **ESP click and form reports** – trusted for which destinations get traffic and form views and submits. Trap: a form submit isn't a unique new consented subscriber.
- **Reviews, support tickets, FAQs, return reasons** – trusted for objections. Trap: product page reviews and badges are brand assertions, not verified claims.
- **Analytics (if connected)** – device mix, landing-page conversion. Missing analytics means conversion rates are unavailable, not zero.

## SOP
1. `get_context` for the three areas. List facts as approved, proposed or missing.
2. Build the destination inventory: every landing URL from campaigns and live flows in the last 90 days, ranked by clicks, plus each pop-up and form.
3. Message-to-page check, top destinations first: open the rendered message, follow the real link on mobile, and compare offer, product, price, deadline and imagery against the page. Record match, mismatch or broken (404, retired domain, out of stock, redirect to home).
4. Offer visibility: for each live offer, check where it shows (banner, product page, cart, checkout) and whether it applies at checkout. Compute stacked percentages multiplicatively.
5. Product page objections: from reviews, tickets and return reasons, list the top recurring objections (fit, taste, results, shipping, price, how to use). Mark which ones the product page answers, and where.
6. Cart friction: from the cart and checkout, record unexpected costs, a hidden shipping threshold, forced account creation, missing express pay, coupon field behavior and error states.
7. Subscription positioning: compare one-time and subscribe options on the product page against `subscription_terms`. Check that savings, cadence and cancel terms match the messages.
8. Pop-up experience: on a fresh session for each device, record trigger time or scroll, step order, offer, consent text and dismissal behavior. Check that known subscribers and purchasers are suppressed and that the welcome flow delivers the promised incentive.
9. Mobile purchase path: count taps from message click to payment step, note sticky add-to-cart, page weight problems you can see and anything that covers the main content.
10. Write the onsite test backlog with the hypothesis template from `experimentation`, ranked by value ÷ effort, each with owner and dependency.
11. `flag_insight` for every broken promise or broken link, with the message ID, URL and screenshot reference as evidence.
12. Write each fix as a draft spec (exact element, current state, required state, acceptance test) addressed to `site_change_owner`. `save_output` (kind `draft_spec` for fixes, `analysis` for the audit). `finish`.

## Decision rules
- If a message promises an offer the checkout doesn't honor, then flag severity critical and recommend holding the affected sends until fixed or reworded. Pausing a live flow is `flow.change_live`; request it, don't do it.
- If a link points to a retired domain or a 404, then flag high and list every asset using that link.
- If a fix needs a theme, app or checkout change, then Wavy writes the spec only. There is no registered action for live site changes. The named `site_change_owner` makes them.
- If a fix needs a new or changed discount, then draft the terms and `request_approval` with `discount.change`.
- If a pop-up change is agreed, then build it unpublished only inside an approved decision (`work.implement_nonlive`); publishing it is the client's or the account lead's call.
- If a product page objection needs a new claim, then check `approved_claims`; if it isn't there, propose wording to the account lead and don't use it.
- If pop-up timing or targeting wasn't observed in a fresh session, then say "not verified". Don't infer it.
- If the expected benefit of a fix can't be sized without analytics, then rank it by severity of the broken promise, not by invented revenue.
- If `site_change_owner` is missing, then `flag_insight` to the account lead and stop the handoff.

## Examples
### Good
"Mobile, fresh session: the welcome pop-up appears at 3 seconds over the hero image, before any scroll, and offers a percentage the welcome email doesn't deliver (email offers free shipping). Returning purchasers still see it. Fix spec for the client's developer: trigger at the agreed delay or first scroll, suppress subscribers and purchasers, show the welcome email's actual incentive. Acceptance: fresh-session recordings on two devices, plus a test signup that receives the matching email. Severity high: the promise mismatch affects every new subscriber."
### Not good
"The PDP should be optimized with better imagery and urgency; we updated the pop-up to 10 seconds." It names no element, objection or evidence, and it reports a live change that Wavy has no authority to make.

## Quality checklist
- [ ] Every finding names the message or page, device, session type and evidence reference.
- [ ] Message-to-page checks use real click destinations from rendered messages.
- [ ] Offer claims are backed by a checkout run, with stacking computed multiplicatively.
- [ ] Pop-up timing, targeting and suppression are marked observed or "not verified".
- [ ] Product page objections cite their source (reviews, tickets, returns), not opinion.
- [ ] Every fix is a draft spec with current state, required state and acceptance test.
- [ ] Every backlog item has an owner outside the agent, a dependency and a hypothesis.
- [ ] No output claims a live site, pop-up or discount change was made by the agent.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| `data.read` | autonomous | Agent, logged |
| `analysis.run` | autonomous | Agent, logged |
| `insight.create` | autonomous | Agent, logged |
| `issue.flag` | autonomous | Agent, logged |
| `draft.create` | autonomous | Agent, logged |
| `fact.propose` | autonomous | Agent; stays proposed |
| `task.manage` | autonomous | Agent, logged |
| `work.implement_nonlive` | approved_plan | Covered by an approved decision that lists it |
| `flow.change_live` | explicit_approval | Account lead, org admin or client owner/approver |
| `discount.change` | explicit_approval | Org admin or client owner/approver (locked) |
| `client.message.send` | explicit_approval | Account lead or org admin |

## Outputs and destination
- **Onsite alignment audit** (analysis): destination inventory with match/mismatch/broken status, offer visibility table, objection map, cart friction list, pop-up and mobile path observations. Task output.
- **Fix specs** (draft_spec): one per change, addressed to the site owner. Task output; sent to the client only through `client.message.send`.
- **Onsite test backlog** (analysis): hypotheses ranked by value ÷ effort with owners. Feeds `experimentation` and `quarterly_planning`.

Thresholds. Published (Google Search Central): intrusive interstitials that cover main content on mobile landing can hurt search visibility. Wavy starting defaults: no entry pop-up before about 5 seconds or the first scroll on mobile; a dismissal suppresses for 7 days; known subscribers and purchasers are always suppressed from acquisition pop-ups; recheck the top 10 destinations after every offer change and monthly otherwise.

## Maintenance
The lead strategist reviews this skill every 90 days. Review early after a site replatform, theme change, checkout change or domain change, after any broken-promise incident reached customers, or when the pop-up tool changes.
