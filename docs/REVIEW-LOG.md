# Independent review log

Wavy OS's agent skills are written by Claude and reviewed by ChatGPT Pro, a different model with no stake in the drafts. Each round's findings are applied, validated and tested before release.

## Round 1 · 2026-10-09 · verdict: rework

ChatGPT Pro reviewed all 28 skills (5 roles, 23 areas) with the format, permission catalog and tool list. It scored executability 3–6/10 and client value 5–7/10 per file. Its five priorities:

1. **Authorization and capability boundaries.** Skills told agents to do things no tool supports (build forms and segments, crawl links, test checkouts, archive data). Policy action names were being treated as tools.
2. **Phase-specific, evidence-based QA.** Checklists mixed phases (a preflight asking for post-send results; a test plan asking for its final interval). An author's "tested successfully" counted as evidence. A blocker outside the checklist could still yield "passed".
3. **Platform and regulatory rules.**
   - Discount stacking was asserted as always multiplicative; under Shopify's discount classes, two combinable order discounts on one subtotal add.
   - SMS cart recovery lacked abandonment-specific consent and an event deadline.
   - Gmail and Yahoo spam-rate bases were conflated with monthly ESP rates.
   - DMARC alignment was described too narrowly.
   - Client approval was treated as making a claim lawful.
   - The FTC review rule was overstated (it bans sentiment-conditioned incentives, not neutral ones).
   - SMS segments were counted on visible characters.
4. **Measurement and economics.**
   - Experiment sizing didn't power a holdout arm.
   - Two conclusiveness rules contradicted each other.
   - The outcome clock started at build completion.
   - Cohort effects were multiplied by months.
   - The list-growth bridge double-counted.
   - AOV was confused with median order value.
5. **Shared facts and customer state.** There were no canonical fact keys, no enforceable cross-channel caps and no safe cutovers, and the copy rules could strip approved selling proof.

### What changed

**Code**
- Tasks carry a fixed `phase`.
- A high or critical insight flagged by QA blocks release ("NOT RELEASE-READY") even when every checklist item passes.
- The executor system prompt adds three rules: capabilities, untrusted content and phase.
- The catalog shows which actions are human-only (enforced at runtime).

**Skills.** Every finding was applied and carried through each file's checklists, examples and thresholds, and the coverage gaps were added inside the existing areas. In detail:
- Phase-split checklists.
- Truthful tool handoffs to named owners with receipts.
- Corrected rules for discounts, SMS, deliverability, claims, reviews and segments.
- Corrected sizing, timing, maturity and forecast math.
- Canonical fact map and aliases.
- Contact-pressure ledger.
- Recipient-state coordination.
- Safe cutovers.
- Subscription retention, back-in-stock alerts and a consent matrix.
- Preservation of approved proof.

**Not changed, by decision.** `standard.publish` and `connection.manage` stay autonomous for authorized people instead of needing a second person's approval. They're enforced human-only in code, so agents are refused at runtime. Round 2 was asked to challenge this.

## Round 2 · 2026-10-09 · verdict: fix-then-ready

ChatGPT Pro verified each round-1 fix. Two were fully applied (SMS recovery, link classification) and the rest partly. It accepted keeping `standard.publish` and `connection.manage` autonomous for authorized people, since they're enforced human-only in code; no second approver is needed. Scores rose to 6.5–8.5/10 for executability and 7.5–9/10 for client value.

Remaining fixes it gave, all applied:
- **Phase-safe QA:**
  - Out-of-phase items get an applicability-only pass with a stated reason.
  - Every mandatory applicable check is carried (no truncation).
  - QA judges against the standards pinned in its packet.
  - Concrete pre/post-call, preflight and channel mappings.
- **Handoffs:** every artifact and handoff is saved before the terminal approval request. Client copy and the internal evidence companion are separate artifacts.
- **Release gates:**
  - Verified opt-outs apply automatically (catalog consent exception).
  - Unknown discount stacking stays unknown and blocks release.
  - Provisional SMS segment counts fail preflight.
- **Customer paths:**
  - Alert-only capture.
  - Purpose-specific SMS permission.
  - Standalone alerts.
  - Usage-based replenishment.
- **Measurement and finance:**
  - Form tests use the experimentation design.
  - Inconclusive, benefit-ruled-out and equivalence are kept distinct.
  - Repair tests are kept apart from optimization tests.
  - High-case target math.
  - Positive-baseline break-even.
- **One ESP complaint-band contract** across reporting, deliverability and production.
- **Shared facts:** `shared_fact_map` is proposed as approved knowledge, and every role resolves shared facts through it.
- **Creative:** approved packaging and proof are kept, judged on claim and rights evidence. The client's stop-list applies, with Wavy's list as a fallback only.
- **Mechanical:**
  - Audit-status precedence.
  - The reporting checklist count.
  - The 13-link example.
  - All skills bumped to version 2.
