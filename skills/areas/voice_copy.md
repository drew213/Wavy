---
key: area.voice_copy
kind: area
area: voice_copy
title: Brand voice and copy
owner_role: creative
version: 2
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Copy is where the brand earns attention or loses it. This area turns an approved brief into subject lines, preview text, body copy, CTAs and texts that sound like the client, say only what is true and approved, and ask for one clear action. For health and supplement brands, one unapproved claim can create legal exposure for the client, so factual discipline matters as much as voice.

## Client facts and rules
Look up each fact with `get_context` for `voice_copy`, then `product_customer` and `offers`.

- `voice_guide` – voice traits written as "is / is never", tone settings per message job (welcome, education, launch, promotion, recovery, post-purchase, winback). Missing: `flag_insight`; draft in a restrained, plain register and label it "voice unconfirmed".
- `approved_examples` – client-approved past messages to imitate, by job. Missing: use the voice guide only; never treat a high-revenue old email as approved.
- `prohibited_language` – banned words, competitor names, claims and tones. Missing: apply the Wavy stop-list below.
- `customer_vocabulary` – the customer's own words for problems and outcomes, from reviews and surveys. Missing: write in plain category language; do not invent customer quotes.
- `messaging_pillars` – the ranked reasons to buy, each with its approved proof. Missing: `flag_insight`; lead with the product fact in the brief.
- `approved_claims` – the claim register: exact approved benefit wording per product, and the substantiation on file. Missing: no benefit claims beyond the product's listed purpose.
- `claims_approver` – who signs off claim language. Missing: stop before any new claim.
- `product_facts` – directions, servings, sizes, ingredients, verified against the physical label. Missing or conflicting: use a placeholder and `flag_insight`.
- `regulated_category` – supplement, cosmetic, CBD/THC, alcohol or none. Missing: treat as regulated until confirmed.
- `required_disclaimers` – the disclaimer each product, market and channel requires, from its labeling context (for example the dietary-supplement structure/function disclaimer). Missing: `propose_fact` if the category may need one; hold claim copy.
- `review_source` – the review platform and whether quotes may be used. Missing: no customer quotes.
- `personalization_fields` – available profile fields, fill rates and approved fallbacks. Missing: no personalization.
- `founder_voice_permission` – whether a founder may sign, with their approved samples. Missing: no founder-signed copy.

Common exceptions: transactional and service messages carry no promotion; retailer or wholesale audiences have their own voice; sister brands never share copy or vocabulary.

## Inputs and sources
- **Current brief**: the only source of facts, offer, audience, deadline and CTA.
- **Voice guide and approved examples**: trusted for rhythm, structure and vocabulary. Traps: old emails contain expired offers, prices, claims, wrong product names and earlier brand names; take style, never facts.
- **Copy library search**: retrieval context. Repeated variants are one example; a template snapshot is not proof of what was sent; image text found by OCR must be checked against the image.
- **Reviews and surveys**: customer evidence for vocabulary. Traps: a review asserting a health result is not an approved claim; paraphrase in quote marks is a fabricated quote.
- **Product page and label**: trusted for product facts after reconciliation. If they disagree, the label wins and the conflict is flagged.
- **Founder samples**: one person's evidence only. A signature does not prove they wrote it; never blend founders.

## SOP
1. Trigger: an approved brief for a campaign, flow message or text. Run `get_context` for `voice_copy`, `product_customer` and `offers`.
2. Check the brief has audience, job, approved facts, offer key (or "none"), CTA and destination. If a blocking fact is missing, `flag_insight` and `finish`. Use a visible placeholder like [CONFIRM: serving size] for a nonblocking one.
3. Pull 3–5 distinct approved examples for the same job and channel.
4. Write the messaging hierarchy: one main message, one proof, one action. Demote secondary points, but keep the brief's approved high-value proof (Decision rules).
5. Email: draft the subject/preview variants the approved brief requires (Wavy default: three subject lines with distinct angles such as benefit, curiosity and specificity), each with a preview line that adds information rather than repeating the subject. SMS: draft the requested text variants; an SMS has no subject or preview.
6. Draft the body: the main message in the first two lines; short paragraphs; concrete nouns; one primary CTA written as verb plus object; the CTA may repeat once lower down to the same destination.
7. Add personalization only from `personalization_fields`, each with its fallback written out.
8. Run the claims review: list every claim and every number in the draft, map each to `approved_claims`, `product_facts` or the brief, and mark anything unmapped. Remove or rewrite unmapped claims.
9. Verify every customer quote verbatim against the supplied review export or approved quote record from `review_source`, recording the source ID and its public-display status. Without that evidence, cut the quote.
10. Run the effective stop-list check (the client's `prohibited_language`; the Wavy stop-list only when the client has none) and the offer-consistency check: the offer value, eligibility and expiry are identical in every element the channel has (email subject, preview, body, image text and terms; SMS text and terms).
11. Save two deliverables: client-facing copy as kind `copy`, and a separate internal evidence companion as kind `analysis` with the approved-fact excerpts, source/version/as-of references, claims map, preservation log and unresolved gates. Source IDs and editor notes never sit inside the client-visible copy artifact. Both artifact IDs refer to the same copy version.
12. If a claim needs sign-off, `flag_insight` to the `claims_approver` and mark the copy "claims pending" in the evidence companion. `finish`.

## Decision rules
- **Health and results claims.** Use a health, efficacy, comparative or results claim ("clinically proven", "science-backed", "doctor-recommended", percentages, study results, outcomes, timelines) only with the exact approved wording, its substantiation reference, the applicable product, market and channel, and any required disclaimer. Approval does not legalize a prohibited claim: for supplements, disease claims (treat, cure, prevent, mitigate, diagnose, or naming a disease) are blocked unless qualified legal review establishes a lawful product-specific basis. Apply the disclaimer the product's labeling context requires (for dietary-supplement structure/function claims, 21 CFR 101.93), not a universal footer. FTC rules require substantiation for health claims and truthful endorsements (16 CFR Part 255). Keep approved, substantiated benefits; never swap them for generic ingredient copy.
- **Review quotes:** verbatim only, from a publicly displayed review, with the source ID kept in the evidence companion. Never edit a quote, never use one that asserts a health result the brand cannot claim, never imply typical results, and disclose incentives if the reviewer was rewarded.
- **Wavy stop-list (default when the client has none):** detox, cleanse (as a health claim), fat-burning, miracle, cure, guaranteed, risk-free, "you need this", shaming language about bodies, fake scarcity.
- **Email subject lines, Wavy starting default:** 30–50 characters; the key word in the first 35 characters (mobile truncation); at most one emoji, and only if the voice guide allows it; no all-caps words beyond brand style; never deceptive about content (CAN-SPAM requires subject lines that are not misleading).
- **Email preview text, Wavy starting default:** 40–90 characters, adds a second reason to open, never repeats the subject, never left to default to "View in browser".
- **Body:** the main message and CTA visible on the first mobile screen; most paragraphs 1–3 sentences; one primary action per message.
- **Personalization:** if a field's fill rate is under 80% (Wavy starting default), then do not use it in the subject line. Never personalize on inferred health conditions or sensitive data.
- **Founder copy:** only with `founder_voice_permission`, only that person's evidence, no invented memories, credentials, opinions or product use. Final founder copy needs the founder's own approval.
- **Old copy:** if a phrase comes from an archive email, then keep the rhythm and drop its facts. Never reuse a whole old email with a new product name.
- If two approved sources disagree on a fact, then use neither; `flag_insight` and use a placeholder.
- SMS copy follows the `sms` skill (prefix, encoding, length, compliance).
- **Preserve approved proof.** Keep a brief's approved high-value proof, price framing, customer phrasing and relationship commitments unless a specific factual, legal or voice defect requires a change. State the defect and source in the evidence companion's preservation log. Acceptable transformations shorten or reorder without changing meaning; never replace distinctive approved language with generic product description. Example, synthetic: approved "$30 for 30 daily servings" may support "about a dollar a day"; do not replace it with "30 servings" merely to sound safer.

## Examples
### Good
Subject: "The 2-minute morning routine" · Preview: "Where it fits between coffee and the commute." Body opens with the approved usage direction from the label, adds one verbatim review line about the experience with its source ID in notes, and ends with "Shop the routine" to the product page. The copy artifact holds only client-facing text and the visible placeholder [CONFIRM: water amount, label and listing disagree]. A separate `analysis` evidence companion for the same version holds the review source ID, maps every claim to the register with its excerpt and source version, logs the brief's approved price framing as kept verbatim, and lists the placeholder as an unresolved gate.

### Not good
Subject: "Clinically proven to flatten your stomach in 7 days 🔥🔥" It makes an unapproved clinical claim, a body-outcome promise and a timeline, uses two emojis, and would expose a supplement client to FTC and FDA risk. A "customer quote" in the body was paraphrased from a review, which makes it a fabricated quote.

## Quality checklist
- [ ] Every claim and number in the copy maps to the approved claim register, verified product facts or the brief, and the map is in the separate `analysis` evidence companion for the same copy version, not in the client-visible copy.
- [ ] Every health, clinical, timeline or results claim uses exact approved wording with substantiation reference, applicable scope and required disclaimer; no supplement disease claim appears without a recorded legal basis.
- [ ] Every customer quote is verbatim, from a public review, with its source ID recorded in the evidence companion.
- [ ] The offer (or "no offer") is identical across subject, preview, body, image text and terms.
- [ ] Email provides the subject/preview variants required by the approved brief, with non-repeating previews. SMS provides the requested text variants and has no email subject/preview requirement.
- [ ] There is one primary CTA, written as verb plus object, with its destination stated.
- [ ] Every personalization token has a written fallback.
- [ ] No word from the effective stop-list appears: the client's `prohibited_language` when it exists; the Wavy stop-list only when the client has none.
- [ ] Placeholders for missing facts are visible and listed; none are disguised as facts.
- [ ] The brief's approved proof, price framing, customer phrasing and commitments are retained, or each removal names its defect and source in the preservation log.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| data.read | autonomous | Agent (logged) |
| draft.create | autonomous | Agent (logged) |
| insight.create | autonomous | Agent (logged) |
| fact.propose | autonomous | Agent (logged); stays proposed until approved |
| approval.request | autonomous | Agent (logged) |
| fact.approve | explicit_approval | Org admin, account lead, or client owner/approver |
| decision.approve | explicit_approval | Org admin, account lead, or client owner/approver |
| client.message.send | explicit_approval | Org admin or account lead |
| campaign.publish | explicit_approval | Org admin, account lead, or client owner/approver |

## Outputs and destination
- **Copy deliverable** (kind `copy`): client-facing text only. Email: subject options with previews, body, CTA and destination, personalization with fallbacks. SMS: the requested text variants. Visible placeholders only; no source IDs or editor notes. Saved to the task output.
- **Evidence companion** (kind `analysis`): approved-fact excerpts, source/version/as-of references, claims map, preservation log, placeholders, open questions and unresolved gates, for the same copy version. Internal only.
- Approved copy is version-bound: any later change to subject, preview, body, CTA or destination clears the copy approval and returns it to review.
- Copy reaches the client only through the Client Hub review or an approved `client.message.send`.
- Client corrections are recorded as reviewed examples for the voice guide; they become a standard only through a reviewed promotion with client data removed.

## Maintenance
The creative lead reviews this skill every 90 days. Review early when a client rejects copy for voice twice in a row, a claim is challenged by a client, platform or regulator, FTC or FDA guidance changes, or a new regulated category client is onboarded.
