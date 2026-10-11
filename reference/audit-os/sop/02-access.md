---
id: access
order: 2
title: Access & keys
owner: Audit lead
tracks: snapshot, deep
time_snapshot: 10 min
time_deep: 10 min
summary: Get a read-only Klaviyo key into the Keychain, confirm it opens the right account, and nowhere else.
inputs: Key instructions sent at intake
outputs: Keychain entry `wavy-klaviyo/<slug>`, account ID confirmed, key logged as active
exit: `wavy-key check <slug>` shows the expected account ID, the client confirms the key is Read-only, and the tracker shows the key as Active.
---
## What the client does

Send exactly this:

> In Klaviyo, go to **Settings → Account → API keys → Create Private API Key**. Name it **Wavy Studios audit**, choose **Read-only key**, and create it. Share it with us through a one-time secret link (1Password share, Bitwarden Send or onetimesecret.com), not email or chat. We revoke it the day we deliver.

If they can't make a one-time link, set up a 10-minute call and have them paste it straight into the `wavy-key add` prompt on screen share. The key doesn't go through chat.

## Steps

1. Open the one-time link and run `bin/wavy-key add <slug>`. Paste at the hidden prompt. The key goes to the macOS Keychain under service `wavy-klaviyo`, account `<slug>`.
2. Close the one-time link so it can't be opened again.
3. Run `bin/wavy-key check <slug>`. It calls the accounts endpoint and prints the Klaviyo account ID, name and timezone, never the key.
4. Compare the account ID with what the client told you. Sister brands often share a login, so a wrong account is easy to get.
5. Set the tracker's key status to **Active** and record the account ID in the audit's notes.

## Checklist

- [ ] Key received through a one-time link or live entry, not chat or email <!-- id:access-secure-channel tracks:snapshot,deep -->
- [ ] Key stored with `wavy-key add` <!-- id:access-keychain tracks:snapshot,deep -->
- [ ] `wavy-key check` shows the expected account ID <!-- id:access-account-confirmed tracks:snapshot,deep -->
- [ ] Client confirmed the key type is Read-only (Klaviyo doesn't report scope through the API) <!-- id:access-readonly tracks:snapshot,deep -->
- [ ] Tracker key status set to Active <!-- id:access-tracker-active tracks:snapshot,deep -->

## Guardrails

- **Never paste a key into Claude, ChatGPT, Codex, Slack, ClickUp, the tracker or a file.** AI sessions store transcripts on disk and send them to the provider.
- Never set the key as a shell export or put it on a command line. Other processes can read both.
- Never copy keys to the second Mac worker. Collection runs on the main machine; only sanitized outputs go to the worker.
- One key per brand. Sister brands get separate keys and separate slugs.

## Tooling

| Tool | Status | Where |
|---|---|---|
| `wavy-key add / check / run / revoke / list` | Built in v0.1; first live use pending | `bin/wavy-key` |
| Hidden prompt in `run-audit.js` | Exists (Gen 1) | `klaviyo-audit/run-audit.js`; replaced by `wavy-key run` in v0.2 |
| `KEY_FILE` in v2 scripts | Exists, retire | Use `wavy-key run <slug> --key-file -- <cmd>` until v0.2 |
| `analyst/reuse-running-key.sh` | Retire in v0.2 | Reads keys from other processes' environment |
