---
id: close
order: 9
title: Close out
owner: Audit lead
tracks: snapshot, deep
time_snapshot: 10 min
time_deep: 20 min
summary: Revoke the key, archive the evidence, and hand the work to delivery.
inputs: Delivered report
outputs: Revoked key, archived audit folder, handoff to agency-os / ClickUp
exit: The key is revoked in Klaviyo and logged, and the tracker shows the key as Revoked.
---
## Steps

1. **Revoke the key within 24 hours of delivery.** Ask the client to delete the "Wavy Studios audit" key in Klaviyo (Settings → Account → API keys), or do it yourself if you have account access.
2. Run `bin/wavy-key revoke <slug>`. It deletes the Keychain entry and appends the revocation to the local revocation log (slug, date, no key).
3. Confirm the key no longer works with `bin/wavy-key check <slug>`; it should report the key is gone. If the client keeps the key alive for an ongoing retainer, record that decision and its owner.
4. Archive: keep `klaviyo-audit/output/<slug>/`, the scorecard, the report and `verification.json`. Delete scratch copies and any analyst packets on other machines.
5. Snapshot that converted: create the Deep audit in the tracker and link it. The collected data carries over.
6. Deep: create the delivery tasks in agency-os / ClickUp from the agreed 30-day plan.

## Checklist

- [ ] Key revoked in Klaviyo <!-- id:close-key-revoked tracks:snapshot,deep -->
- [ ] `wavy-key revoke` run and tracker key status set to Revoked <!-- id:close-keychain-revoked tracks:snapshot,deep -->
- [ ] Scratch copies and remote packets deleted <!-- id:close-scratch-cleaned tracks:snapshot,deep -->
- [ ] Converted prospect moved to a Deep audit <!-- id:close-converted tracks:snapshot -->
- [ ] Delivery tasks created from the 30-day plan <!-- id:close-handoff tracks:deep -->

## Guardrails

- An active key after delivery is a risk we carry for the client. The tracker flags it in red.
- Don't delete evidence that a delivered number depends on.

## Tooling

| Tool | Status | Where |
|---|---|---|
| `wavy-key revoke` and revocation log | Built (v0.1) | `bin/wavy-key` |
| agency-os task register | Exists | `agency-os/agency.py` |
