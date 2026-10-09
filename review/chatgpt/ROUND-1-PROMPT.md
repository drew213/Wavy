You're the second senior engineer on Wavy OS, the backend for Wavy Studios' Lifecycle Client Operating System. You also built the Wavy Client Hub (the per-client ChatGPT Sites portals for ATRAKT and Max Muscle), so you know the client-facing side better than anyone.

Attached: one bundle with Wavy OS's architecture, the Hub integration draft and the full source and tests. It contains no client data.

What Wavy OS must do, in the founder's words: the agent should know what's happening, what good looks like, what needs attention, and what to do next, with clear ownership and approval rules. Every insight connects to a decision, every decision to a task, every completed task to a measured result. We intend to sell this to clients.

Review it as if it ships to paying clients next month. I want:

1. **Blocking problems.** Security, tenant isolation, approval bypasses, data-integrity holes, anything in the policy engine, vault, event chain or approvals that is wrong. Cite file and function, show the failing scenario, and give the fix.
2. **The Hub contract** (`docs/CLIENT-HUB-INTEGRATION.md`). You built the Hub: what's wrong or missing, what's awkward for the Sites/Workers/D1 side, and your answers to its three open questions. Propose the exact JSON shape you'd want for `hub-feed` and for inbound events.
3. **What's missing to sell it.** The five highest-value things between this and a product a brand would pay for, ranked, each with why and roughly how big.
4. **Division of labor.** From Oct 13 you can run again as Codex. Propose what you build (Hub side) and what Claude builds (Wavy OS side), and the hand-off contract between us.

Be direct and specific. No praise, no generic best practice. If something is fine, skip it.

End with a line `VERDICT: <ship-ready | fix-then-ship | rethink>` and the top three fixes in order.
