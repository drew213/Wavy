"""Flow definitions as a lifecycle map: which stages have a live flow, what each sends and when, and the
gaps a customer falls through (flows that are live but silent, built but never switched on, or doubled up)."""
from __future__ import annotations

from collections import defaultdict

from ..audit.flows import CORE_TYPES
from ..audit.snapshot import MissingData, Snapshot, ref
from .base import Analysis
from .series import try_monthly

MODULE = "journey_graph"
STAGES = ("capture", "browse", "cart", "checkout", "post_purchase", "replenishment", "winback", "sunset")
STAGE_OF = {"welcome": "capture", "browse": "browse", "back_in_stock": "browse", "abandoned_cart": "cart",
            "abandoned_checkout": "checkout", "post_purchase": "post_purchase", "replenishment": "replenishment",
            "winback": "winback", "sunset": "sunset"}
REGULATED_SMS = ("CBD / hemp / THC",)


def run(snap: Snapshot, a: Analysis, context: dict) -> None:
    snap.require("api")
    flows = snap.flows()
    if not flows:
        raise MissingData("no flow definitions in the snapshot", ref("api", "flows", "details"))
    nodes, by_stage = [], defaultdict(list)
    for f in flows:
        stage = STAGE_OF.get(f.kind or "", "other")
        node = {"id": f.id, "name": f.name, "stage": stage, "kind": f.kind, "status": f.status, "live": f.classified_live,
                "trigger": f.trigger_metric or f.trigger_type, "emails": f.main_path_emails, "sms": f.main_path_sms,
                "first_send_days": f.first_send_days, "recipients_90d": f.recipients90}
        nodes.append(node)
        by_stage[stage].append(node)
    a.table("nodes", nodes)
    a.table("stages", [{"stage": s, "live": [n["name"] for n in by_stage[s] if n["live"]],
                        "not_live": [n["name"] for n in by_stage[s] if not n["live"]]} for s in STAGES])
    covered = [s for s in STAGES if any(n["live"] for n in by_stage[s])]
    a.fact("stages_covered", len(covered), "stages", f"Lifecycle stages with a live flow (of {len(STAGES)})",
           source=ref("api", "flows", "details"))
    a.fact("live_flows", sum(1 for n in nodes if n["live"]), "flows", "Live flows with at least one live message")
    a.fact("stages_missing", ", ".join(s for s in STAGES if s not in covered) or "none", "list", "Stages with no live flow")

    silent = [n for n in nodes if n["live"] and n["recipients_90d"] == 0]
    if silent:
        a.fact("live_silent_flows", len(silent), "flows", "Live flows that sent nothing in 90 days")
        a.find("live_but_silent", "Live flows that haven't sent anything in 90 days",
               f"{len(silent)} flow(s) are switched on but sent no messages in the last 90 days.",
               "Not sending: " + ", ".join(n["name"] for n in silent[:6]) + ".",
               "Check each trigger: a metric that stopped arriving (often after a platform switch), a list nobody joins, "
               "or filters that exclude everyone. Fix the trigger or retire the flow.",
               severity="high", category="fix_first", facts=[f"{MODULE}.live_silent_flows"], effort_days=1,
               readout={"metric": "messages sent by the repaired flows", "days": 14})
    shelved = [n for n in nodes if n["stage"] in STAGES and not n["live"] and n["status"] in ("draft", "manual")
               and not any(x["live"] for x in by_stage[n["stage"]])]
    if shelved:
        a.find("built_not_live", "Lifecycle flows built but never switched on",
               f"{len(shelved)} flow(s) cover a stage that has nothing live, but sit in draft or manual.",
               "; ".join(f"{n['name']} ({n['stage']}, {n['status']})" for n in shelved[:6]) + ".",
               "QA these drafts against the brand's offer rules and switch the best one on per stage; they're the "
               "fastest coverage gain available.", severity="high", category="flow_build", effort_days=1,
               readout={"metric": "stages with a live flow", "days": 14})
    doubled = {}
    for n in nodes:
        if n["live"] and n["stage"] in STAGES and n["trigger"]:
            doubled.setdefault((n["stage"], n["trigger"]), []).append(n)
    overlaps = [v for v in doubled.values() if len(v) > 1]
    if overlaps:
        a.find("overlapping_flows", "Several live flows fire on the same trigger",
               f"{len(overlaps)} trigger(s) start more than one live flow, so the same person can get both sequences.",
               "; ".join(" + ".join(x["name"] for x in v) for v in overlaps[:4]) + ".",
               "Merge each pair into one flow with a split, or add a filter so a profile can only be in one.",
               severity="medium", category="flow_optimization", effort_days=1,
               readout={"metric": "profiles receiving overlapping sequences", "days": 30})

    regulated = context.get("regulated_category") in REGULATED_SMS
    sms_live = [n for n in nodes if n["live"] and n["sms"]]
    if regulated and sms_live and str(context.get("sms_platform") or "Klaviyo SMS").startswith("Klaviyo"):
        a.find("regulated_sms", "SMS is live in a category Klaviyo doesn't allow",
               "The brand sells CBD/hemp/THC products and has live flows sending SMS from Klaviyo.",
               "SMS in: " + ", ".join(n["name"] for n in sms_live[:6]) + ".",
               "Pause Klaviyo SMS in these flows now and move SMS to a provider that permits the category, after legal review.",
               severity="critical", category="fix_first", effort_days=1,
               readout={"metric": "live Klaviyo SMS sends", "days": 7})
    sms_consent = try_monthly(snap, "subscribed_sms", "unique")
    intent = [n for n in nodes if n["live"] and n["stage"] in ("checkout", "cart") and not n["sms"]]
    if not regulated and sms_consent and sum(sms_consent.values()) > 0 and intent and not sms_live:
        a.find("sms_missing_from_intent", "SMS subscribers aren't reached when they abandon",
               "The account collects SMS consent, but no live cart or checkout flow sends an SMS.",
               f"{int(sum(sms_consent.values())):,} SMS subscriptions in the last 12 full months; intent flows: "
               + ", ".join(n["name"] for n in intent[:4]) + ".",
               "Add one SMS step to the abandoned checkout flow (about an hour after entry, consented profiles only).",
               severity="medium", category="flow_optimization", lever="abandoned_checkout", effort_days=1,
               readout={"metric": "SMS-attributed checkout recoveries", "days": 30})
    missing_core = [k for k in CORE_TYPES if STAGE_OF.get(k) not in covered]
    a.fact("core_types_missing", ", ".join(missing_core) or "none", "list", "Core flow types with nothing live")
