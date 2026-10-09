---
key: area.design_system
kind: area
area: design_system
title: Design and creative system
owner_role: creative
version: 2
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
A design system lets the team ship on-brand emails fast without each one becoming a new design project. It defines reusable modules and templates, how a message reads on a phone, and the technical limits that decide whether the email arrives whole: Gmail clipping, image blocking, dark mode and accessibility. A good system protects the brand and the inbox at the same time.

## Client facts and rules
Look up each fact with `get_context` for `design_system`, then `voice_copy`.

- `brand_guidelines` – logo files and clear space, colour hex values, typefaces with email-safe fallbacks, photography style. Missing: `flag_insight`; propose a starter system from the live website and mark it proposed.
- `module_library` – approved modules (header, hero, text block, product card, product grid, review, offer, CTA, footer) with their template IDs. Missing: build from the Wavy base modules and `propose_fact`.
- `campaign_templates`, `flow_templates` – master templates per job. Missing: one base template per job before production starts.
- `approved_imagery` – the photo and product-image library with usage rights. Missing: no lifestyle images; use packshots from the store only after checking rights.
- `claims_in_imagery_check` – whether each image's visible claims, badges, ratings and packaging statements have been reviewed against the approved claim/asset record. Missing: review every image before use (see Decision rules).
- `dark_mode_assets` – logo and icon variants for dark backgrounds. Missing: `flag_insight`; add a stroke or a padded light container.
- `accessibility_minimums` – client-specific requirements beyond the Wavy defaults. Missing: apply Wavy defaults.
- `footer_requirements` – physical postal address, unsubscribe and preference links, legal lines. Missing: stop; CAN-SPAM requires the address and a working unsubscribe.
- `asset_storage_location` – where source files and exports live, with access rules. Missing: `flag_insight`.
- `design_reviewer` – who signs off design. Missing: account lead.

Common exceptions: regulated products need claim review on every image; marketplace badges and third-party logos need permission; seasonal skins override colours, never the module structure.

## Inputs and sources
- **Brand guidelines and website**: trusted for the visual identity. Trap: web fonts often do not render in email clients; always set fallbacks.
- **Template HTML**: trusted for structure. Trap: a template is not a received message; size is measured on the decoded delivered HTML of the actual message.
- **Received-message render receipts across clients**: supplied evidence, the only proof of clipping, mobile, dark-mode and image-off behaviour. They exist only after seed messages of the exact built version are sent, and are judged at preflight.
- **Image library**: trusted for rights only if recorded. Trap: store product images often carry unapproved claim badges, marketplace badges or old packaging; check each against the approved claim/asset record.
- **Check library results** (Gmail clipping, image-only emails, link checks): scored evidence with thresholds.
- **Historical emails**: visual references, not proof that a layout won.

## SOP
1. Trigger: a new client system build, a template request, or a design-direction step in production. Run `get_context` for `design_system` and `voice_copy`. Note the task phase: neither a design-direction nor a build task can prove received HTML; that evidence belongs to preflight.
2. For a system build: document the tokens (colours with hex, type scale, spacing, button style), then the module list with purpose, required fields and mobile behaviour, then one base template per job (campaign, welcome, recovery, post-purchase, winback, transactional).
3. For a message: write the design direction section by section in the order a phone shows them. For each section: module, exact copy reference (copy version), image or asset, CTA and destination, must-retain fields and designer-discretion fields.
4. Set the mobile hierarchy: brand, headline, the main visual and the primary CTA must sit on the first mobile screen (about 600 px tall at 375 px wide).
5. Specify imagery: approved asset IDs only, alt text for each image, no text-heavy images (headline, offer and CTA stay live text).
6. Design-direction phase: check what the direction can show (live-text plan, alt text, contrast of specified colours, tap-target sizes, image weights of chosen assets) and list build and received-message checks as pending for their phases with owner and required receipts.
7. Build phase: confirm the draft build matches the direction (live text, alt text, assets, copy version) and list received-message checks as pending for preflight, naming the authorized executor who sends seed messages and the receipts required. Preflight phase: from supplied received-message receipts for the exact build version, record decoded delivered HTML bytes after personalization and tracking, clipping, and renders in Gmail (web and app), Apple Mail (iOS) and Outlook (desktop), light and dark, images on and off. Missing receipts: not verified, and release stays blocked.
8. `save_output` (kind `design_direction`, label "<message> design v<n>") with the section table, asset list, technical results and open issues. For a system build, save the system as kind `other`, labelled "Design system v<n>".
9. `flag_insight` for any failed technical check you cannot fix in the direction. `finish`.

## Decision rules
- **Gmail clipping:** treat the roughly 102 KB clipping behavior as approximate, not an exact 102,400-byte platform guarantee; clipping can hide the unsubscribe link. Wavy release budget: less than 90,000 bytes of decoded final delivered HTML, measured after personalization and tracking, with received-message clipping evidence judged at preflight. Do not count downloaded image files as HTML bytes. If over, remove hidden blocks, inline-style bloat and long product grids before cutting content.
- **Image-only risk:** if a message has under 300 characters of live text and 3 or more images, then it is image-heavy (Wavy starting default) and fails for any welcome or checkout first email. Headline, offer, CTA and legal text must be live HTML text so the message reads with images blocked and with a screen reader.
- **Claims in images:** inspect every visible claim, badge, rating and packaging statement against the approved claim/asset record, scope, substantiation and rights. Reject unsupported claims, unlicensed badges, misleading before/after imagery and outdated packaging. Do not reject approved current packaging merely because it states an approved benefit. If no compliant asset exists, mark the asset missing and request one; do not assume a clean packshot exists or remove mandatory label information.
- **Accessibility (WCAG 2.1 AA, W3C):** text contrast at least 4.5:1, large text at least 3:1; every meaningful image has alt text describing its purpose; decorative images have empty alt text; links say where they go. Wavy starting defaults: body text at least 16 px on mobile, CTA buttons at least 44 × 44 points (Apple Human Interface Guidelines touch target), buttons built as live HTML, never images.
- **Dark mode:** if the logo or icons are dark on transparent backgrounds, then supply a light variant or a padded light container. Never put dark text on a transparent image. Check that inverted colours keep brand colours legible.
- **Image weight, Wavy starting default:** each image under 500 KB, total images under 2 MB; animated GIFs under 1 MB with the key message on the first frame (older Outlook desktop versions show only the first frame).
- **Preserve effective creative:** when revising an existing or proven design, keep the approved proof (sourced reviews, ratings, price framing), product imagery and module order the brief marks must-retain. Acceptable changes: resizing, re-cropping, rebuilding image text as live text, dark-mode variants. Rejected: swapping approved proof or product shots for generic stock imagery or blank space to look cleaner. Remove a retained element only for a recorded claim, rights, accessibility or technical defect.
- **Templates and modules:** if a request can be met with existing modules, then reuse them. A new module needs a reviewed reason and goes into the library, not a one-off.
- **Product presentation:** show the product the customer will receive (current packaging, correct variant), with name and price only when the brief confirms them.
- **Asset organization:** name exports as `client-key_YYYYMMDD_message_module_v<n>` (Wavy starting default) and keep editable sources with the export.
- If copy changes after design starts, then design waits for the new copy version; designers never rewrite approved copy.
- If any technical check fails and cannot be fixed, then the message cannot pass QA.

## Examples
### Good
"Section 1, header module: logo (dark-mode variant asset ID), no navigation bar. Section 2, hero: live-text headline from copy v3, approved lifestyle image (asset ID, alt text 'Customer holding the product at a kitchen counter'), button 'Shop the routine' to the product page. Section 3, review module: verbatim quote with source ID. Section 4, footer: address, unsubscribe, preferences. Live text 820 characters; contrast 7.2:1; review module kept from the prior winning version as must-retain. Pending for the preflight task after build v1: decoded delivered HTML size, clipping, dark-mode and images-off renders (owner: the authorized production executor who sends seed messages of build v1; evidence: received-message receipts, which QA judges at preflight)."

### Not good
A single 1,400 px tall image containing the headline, the offer and a "clinically proven" badge from the product page, with a button that is part of the image and no alt text. With images off it is blank, a screen reader reads nothing, the claim is unapproved, and the offer cannot be updated without a redesign.

## Quality checklist
- [ ] The output declares its phase and version; checks that need received messages are marked pending for preflight with owner and required receipts, never passed in a design-direction or build task.
- [ ] Preflight: decoded delivered HTML of the exact build version is under 90,000 bytes after personalization and tracking, with received-message clipping evidence.
- [ ] Headline, offer, CTA and legal text are live text; the message is not image-heavy.
- [ ] Every image has an approved asset ID and appropriate alt text (empty for decorative images).
- [ ] Every visible claim, badge, rating and packaging statement in an image matches the approved claim/asset record with scope, substantiation and rights; unsupported claims, unlicensed badges, misleading before/after and outdated packaging are rejected, and a missing compliant asset is marked missing and requested.
- [ ] Text contrast meets 4.5:1 (3:1 for large text), and CTA buttons are live HTML at least 44 × 44 points.
- [ ] Brand, headline and primary CTA appear on the first mobile screen.
- [ ] Preflight: received-message dark-mode and images-off render receipts for the exact build version are cited for Gmail, Apple Mail and Outlook.
- [ ] The footer has a physical address, an unsubscribe link and a preferences link.
- [ ] Each section cites its copy version and marks must-retain versus designer-discretion fields; any removed approved proof or imagery has a recorded defect reason.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| data.read | autonomous | Agent (logged) |
| analysis.run | autonomous | Agent (logged) |
| draft.create | autonomous | Agent (logged) |
| insight.create | autonomous | Agent (logged) |
| fact.propose | autonomous | Agent (logged); stays proposed until approved |
| work.implement_nonlive | approved_plan | Only inside an approved decision (for example a template library build) |
| campaign.build_draft | approved_plan | Only inside the approved decision for that campaign |
| fact.approve | explicit_approval | Org admin, account lead, or client owner/approver |
| account.settings_change | explicit_approval | Org admin or client owner; locked (for example brand defaults in the platform) |

## Outputs and destination
- **Design direction** (kind `design_direction`): section table, asset list with IDs and alt text, mobile hierarchy, technical results for the current phase, pending build checks with owners, and open issues. Goes to the task output and the designer handoff.
- **Design system** (kind `other`): tokens, module library, base templates per job and the review checklist. Goes to the Client Hub once approved.
- Ready for design means copy is approved and assets are listed. It does not mean approved to launch.

## Maintenance
The creative lead reviews this skill every 90 days. Review early when Gmail, Apple Mail or Outlook changes rendering, clipping or dark-mode behaviour, when WCAG guidance updates, when a send fails a clipping or image check, or when a client rebrands.
