# Reference material (read-only)

Source material for porting the audit into Wavy OS. Nothing here runs in the app, and nothing here is maintained: change the Python port, not these files.

| Folder | What it is | Use it for |
|---|---|---|
| `klaviyo-audit/` | The Node.js collector Wavy ran for its first four audits (steps 24, 31, 34, 35, 37, 38, the runner, and the helpers for metric selection, source contracts, aggregate time boundaries and order items, with their tests) | The exact Klaviyo endpoints, parameters, pacing, retries, pagination caps and output shapes to reproduce in `src/wavyos/collector/` |
| `audit-os/` | The official audit standard: operating rules, the 10-stage SOP (intake → re-audit), PRD, roadmap and intake questionnaire | What a finished audit must contain, sizing rules, verification gates, re-audit cadence |
| `analyst/` | The LLM analyst brief (role, data caveats, report sections, 10-criterion rubric) and the packet builder that strips personal data before anything reaches a model | The AI analysis stage (phase 5 of `docs/AUDIT-PLAN.md`) |

Client names in these files are replaced with Brand A–D. Collector outputs, client data and keys are never copied here. The collector's own outputs (`output/`, `deep-dive/`) stay on the machine that ran them.
