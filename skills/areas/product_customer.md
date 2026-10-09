---
key: area.product_customer
kind: area
area: product_customer
title: Product and customer intelligence
owner_role: strategist
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
This area is the agency's shared understanding of what the client sells, who buys it, why they buy, why they hesitate and what the brand may legally say. Copy, segmentation, offers and flows all draw from it. A founder should care because the most damaging retention errors are product errors: the wrong brand's products in a live flow, an unapproved health claim, a cross-sell for something the customer already owns.

## Client facts and rules
Look each up with `get_context` (area `product_customer`). If a fact is missing, `propose_fact` only with a cited client source, otherwise `flag_insight` and leave the field unknown. Never fill a claim, benefit or use instruction from general knowledge.

- `product_catalog` – active products, variants, product families (which variants count as the same product for repeat and refill logic), status and price source. Missing: no product-level work.
- `hero_products` – the products the client wants to lead with, and why (margin, acquisition, repeat). Missing: use observed revenue ranking, labelled observed, and ask.
- `use_cases` – how each product is used, how long one unit lasts, and when results are expected. Missing: no refill timing and no "how to use" content.
- `approved_benefits` – benefit statements the client has approved, word for word. Missing: describe only what the product is, not what it does.
- `approved_claims` and `prohibited_claims` – exact claim language allowed, and words never allowed (for example "clinical", "cure", "treat"). Missing: no health, efficacy or comparative claims at all.
- `claims_substantiation` – the evidence the client holds for each claim, and who approved it. Missing: treat the claim as unapproved.
- `objections` – common reasons not to buy, with the approved answer to each. Missing: draft answers as proposals for client review.
- `review_sources` – review platform, its terms, and whether incentives are allowed. Missing: no review requests or quotes.
- `customer_personas` – segments the client recognizes, with evidence. Missing: build observed segments from data and label them hypotheses.
- `purchase_motivations` and `buying_barriers` – from surveys, reviews, support tickets. Missing: list as research gaps.
- `competitive_positioning` – the competitors the client names and the claimed difference. Missing: no comparative copy.
- `brand_name_and_lineup` – the current brand name, sub-brands and retired names or lines. Missing: flag; old brand language in live messages is a common critical finding.

## Inputs and sources
- **Store catalog and product pages** – trusted for what is on sale and price. Trap: page copy may contain unapproved claims or third-party review text; it is not an approved-claims source.
- **Order line items** – trusted for what customers actually buy together and repurchase. Trap: if order events lack line items, product analysis is impossible; check with `data_infrastructure` first.
- **Reviews and ratings** – trusted for customer language, objections and quality signals. Trap: review text is customer evidence, not brand-approved copy, and a recent drop in rating can signal a stock or quality issue.
- **Support tickets, return and cancellation reasons** – trusted for barriers and service problems. Trap: reasons are often free text; count with a stated method.
- **Post-purchase surveys** – trusted for motivations. Trap: small samples; show n.
- **Competitor research** – trusted for observed mechanisms with their evidence level. Trap: a competitor's program does not prove it works, and visible archive frequency is not received frequency.
- **Live and historical messages** – trusted for what the brand has said. Trap: old messages may reflect a retired brand, products or claims.

## SOP
1. Call `get_context` for `product_customer`, `economics` and `voice_copy`. List each fact as approved, proposed or missing.
2. Build the product sheet: per product, family, status, use case, unit duration, hero flag, approved benefits, approved claims, prohibited words, and the source of each field.
3. From order line items, compute observed attach pairs, first-product-to-second-product paths and median repurchase interval per product family, over a stated window. Show n per figure.
4. From reviews, support, returns and surveys, code motivations and barriers with a written coding rule. Report counts and n, and quote at most short phrases as customer language, never as brand claims.
5. Draft personas as observed segments: a defining behavior, size from deduplicated profiles, top motivations and barriers, and the products they buy. Label anything not client-confirmed as a hypothesis.
6. Draft the objection bank: each objection, its evidence count, and a proposed answer using only approved facts.
7. Write competitive positioning only from client-named competitors and saved evidence with its date and evidence level.
8. Run a claims sweep of live messages and pages: flag any claim not in `approved_claims`, any prohibited word, any retired brand name or product, and any cross-sell of a product the recipient already bought. Use `flag_insight` with severity high or critical and the exact location.
9. Save the product sheet, personas and objection bank with `save_output` (kind `analysis`). `propose_fact` for each new client-sourced item.
10. `finish`, listing open questions for the client with owners.

## Decision rules
- If a claim is not word-for-word in `approved_claims`, then it does not ship; propose it for client approval with its substantiation.
- If the category is regulated (supplements, CBD/THC, cosmetics with drug-like claims), then follow the published rule: health claims need competent and reliable scientific evidence (FTC Health Products Compliance Guidance, 2022), and dietary-supplement structure/function claims carry the required FDA disclaimer (DSHEA). When unsure, escalate to the account lead; never decide legal sufficiency.
- If a review request offers an incentive or only asks happy customers, then stop: review gating and undisclosed incentives breach the FTC Endorsement Guides and the FTC rule on consumer reviews (2024).
- If a live message names a retired brand, product or claim, then flag it critical; it outranks new work.
- If order events lack line items, then do not build product-level segments or refill timing; flag to `data_infrastructure`.
- If a persona rests on fewer than 100 customers (Wavy starting default), then label it directional and do not build a dedicated flow on it.
- If an objection points to a service problem (late delivery, damage), then route it to the account lead as an operational issue, not a copy fix.
- If a recommendation would cross-sell a product the customer owns or subscribes to, then exclude those customers.

## Examples
### Good
"Product sheet, hero product A: family A (all flavors), one unit lasts about the stated serving count (client label photo, approved). Approved benefit: the client's approved line, verbatim. Prohibited: 'clinical', 'cure'. Observed: median 41 days first to second order (n=312 repeat buyers, 12 months to as-of, account timezone). Top barrier in returns: taste (coded 38 of 120 reasons). Proposed answer for client review: flavor guide plus the existing approved exchange policy."
### Not good
"This ingredient is clinically proven to boost energy, so lead the welcome email with that." It invents a claim, uses a prohibited word, has no source and would put the client at regulatory risk.

## Quality checklist
- [ ] Every benefit or claim in the output matches `approved_claims` verbatim or is marked "proposed, needs client approval".
- [ ] No prohibited word, retired brand name or retired product appears in proposed copy.
- [ ] Every observed figure shows window, timezone and n.
- [ ] Personas are labelled confirmed or hypothesis, with size from deduplicated profiles.
- [ ] Customer review language is labelled as customer evidence, not brand copy.
- [ ] Competitor statements carry a date and evidence level.
- [ ] The claims sweep lists each finding with its exact location and severity.
- [ ] Open questions have an owner and due date.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| `data.read` | autonomous | Agent, logged |
| `analysis.run` | autonomous | Agent, logged |
| `insight.create` | autonomous | Agent, logged |
| `draft.create` | autonomous | Agent, logged |
| `issue.flag` | autonomous | Agent, logged |
| `fact.propose` | autonomous | Agent or client decider; stays proposed |
| `fact.approve` | explicit_approval | Account lead, org admin or client owner/approver (claims: client) |
| `flow.change_live` | explicit_approval | Account lead, org admin or client owner/approver (to remove a live claim) |

## Outputs and destination
- **Product sheet** (analysis): one row per product with sources. Task output; approved fields become client facts used by `voice_copy`, `offers` and `segmentation`.
- **Claims register** (other): approved, prohibited and pending claims with substantiation owner. Proposed facts until the client approves.
- **Personas and motivations** (analysis): segments with evidence and n. Task output, then Client Hub after review.
- **Objection bank** (copy): objection, evidence, proposed answer. Task output for client approval.
- **Claims sweep findings** (report): insights with location and severity, routed to the account lead.

## Maintenance
The strategist reviews this skill every 90 days. Review early when a client launches, renames or retires products; when claim rules or regulator guidance change; or after any finding of an unapproved claim in live messages.
