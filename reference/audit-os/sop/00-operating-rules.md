---
id: rules
order: 0
title: Operating rules
owner: Everyone
tracks: snapshot, deep
summary: The rules that apply at every stage. Breaking one stops the audit until it's fixed.
---
## The rules

1. **Read-only, always.** Audits never change a client account: no flow edits, no sends, no list changes, no hosted-page assignment. If a fix is obvious, write it in the roadmap.
2. **Keys never travel in chat, email, Slack, ClickUp or files.** Keys go into the Keychain with `wavy-key add` and reach collectors only through `wavy-key run`. If a key is pasted anywhere else, treat it as exposed: revoke it and issue a new one.
3. **Read-only keys only.** If a client sends a full-access key, don't use it. Ask for a read-only one.
4. **One brand per run.** Never join data across brands, even sister brands on the same team.
5. **No personal data leaves collection.** Profiles, emails, phones, addresses and names stay in collector memory or sanitized exports. Reports and AI packets get aggregates or salted pseudonyms only.
6. **Missing data is never zero.** A metric with no events, a failed step or a truncated list is reported as unavailable, with the reason.
7. **Every client-facing number has a source.** It traces to a check result or a claim-ledger row. If it can't be traced, it doesn't ship.
8. **The author doesn't QA their own work.** The verify stage is done by a different person or a separate AI reviewer with only the evidence.
9. **Machine timestamps only.** Record times with `date -u` or file and manifest times, never estimates.
10. **AI tools get only what they need.** Send AI reviewers the sanitized packet, never raw exports or keys. Sending client files to an outside AI service (ChatGPT, Codex) needs the account lead's OK for that client.

## When something goes wrong

| Situation | Do this |
|---|---|
| A key was pasted in chat or a file | Revoke it in Klaviyo now, log it with `wavy-key revoke`, ask the client for a new one |
| A collector step fails | Record it as PARTIAL in the run log; don't fill the gap with estimates |
| The order metric is ambiguous | Stop. Pin it in `source-contract.json` with evidence before any revenue number is written |
| A finding would embarrass the client's team | Write it factually, with the fix; the account lead decides how to present it |
| The client asks for a live change during the audit | Log it as a roadmap item; changes start after delivery under a separate approval |
