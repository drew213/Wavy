"""The journey map: every flow drawn as the customer moves through it (trigger, filters, waits, splits, and
every email and SMS with its subject, preview line, offer, rendered preview and results), the lifecycle stage
strip, a program coverage checklist, and a review of each live message with concrete improvements.

Message suggestions are rules over measured numbers (results against the flow's other steps, unsubscribe and
complaint rates, timing, size, accessibility, copy). The rules that no other module covers are rolled up into
findings, so they become opportunity cards and their outcomes feed the track record. attach_history() marks
what changed on each message since the brand's previous audit.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Optional

from ..audit.snapshot import MissingData, Snapshot, ref
from .base import Analysis, num, pct, ratio, usd
from .flow_history import roll
from .journey_graph import STAGE_OF, STAGES
from .messages import IMAGE_ONLY_CHARS, _hits, _terms

MODULE = "journey_map"
STAGE_INFO = {
    "capture": ("Subscribes", "joins a list or signs up"),
    "browse": ("Browses", "views a product"),
    "cart": ("Adds to cart", "adds to cart and leaves"),
    "checkout": ("Starts checkout", "starts checkout and leaves"),
    "post_purchase": ("Buys", "places, receives and uses an order"),
    "replenishment": ("Runs low", "is due to reorder"),
    "winback": ("Lapses", "stops buying"),
    "sunset": ("Goes quiet", "stops opening and clicking"),
    "other": ("Other", "dates, partners, internal and unclassified flows"),
}
NAME_STAGE = (
    (re.compile(r"sunset|unengaged", re.I), "sunset"),
    (re.compile(r"win[\s-]?back|lapsed|re-?engage|reactivat", re.I), "winback"),
    (re.compile(r"refill|replen|reorder", re.I), "replenishment"),
    (re.compile(r"checkout", re.I), "checkout"),
    (re.compile(r"cart", re.I), "cart"),
    (re.compile(r"browse|site abandon|viewed", re.I), "browse"),
    (re.compile(r"post[\s-]?purchase|thank|review|shipp|deliver|order|cross[\s-]?sell|upsell", re.I), "post_purchase"),
    (re.compile(r"welcome|pop-?up|signup|sign-up|subscrib|nurture", re.I), "capture"),
)
GMAIL_CLIP_KB = 102
MIN_RECIPIENTS = 500
WEAK_SHARE = 0.4          # a step below 40% of its flow's typical revenue per recipient
DECLINE = 0.6             # 90-day revenue per recipient below 60% of the 12-month rate
UNSUB_HIGH = 0.01
UNSUB_VS_TYPICAL = 1.5    # and 1.5x the account's typical live flow message
SPAM_HIGH = 0.001
SPAM_CRITICAL = 0.003
LOW_CLICK = 0.005
SUBJECT_MAX = 55
FIRST_TOUCH_MAX_DAYS = {"abandoned_checkout": 4 / 24, "abandoned_cart": 4 / 24, "browse": 1.0}
INTENT_KINDS = ("abandoned_checkout", "abandoned_cart", "browse")
SMART_SENDING_KINDS = ("winback", "sunset", "replenishment", None)   # welcome, intent and order flows often run without it
SEVERITY = {"critical": 4, "high": 3, "medium": 2, "low": 1}
OPS = {"equals": "=", "greater-than": ">", "less-than": "<", "greater-than-or-equal": "≥", "less-than-or-equal": "≤"}
UTIL = {"send-webhook": "Webhook", "webhook": "Webhook", "update-profile": "Update profile",
        "internal-alert": "Internal alert", "list-update": "Update list"}


# ---------------------------------------------------------------- readable conditions

def _unit(u: Any, n: Any) -> str:
    u = str(u or "day").rstrip("s")
    return f"{n} {u}{'' if n == 1 else 's'}"


def _timeframe(tf: Optional[dict]) -> str:
    tf = tf or {}
    op = tf.get("operator")
    if op == "flow-start":
        return "since starting this flow"
    if op == "alltime":
        return "all time"
    if op == "in-the-last":
        return f"in the last {_unit(tf.get('unit'), tf.get('quantity'))}"
    if op in ("before", "after"):
        return f"{op} {str(tf.get('date') or '')[:10]}"
    return op or ""


class Names:
    def __init__(self, snap: Snapshot):
        items = snap.at("api", "metrics", "items", default=[]) or []
        self.metrics = {str(x.get("id")): re.sub(r" \((Shopify|API)\)$", "", (x.get("attributes") or {}).get("name") or "")
                        for x in items}
        self.groups = {}
        for key in ("lists", "segments"):
            for x in snap.at("api", key, "items", default=[]) or []:
                self.groups[str(x.get("id"))] = (x.get("attributes") or {}).get("name") or str(x.get("id"))

    def metric(self, mid: Any) -> str:
        return self.metrics.get(str(mid)) or str(mid)

    def group(self, gid: Any) -> str:
        return self.groups.get(str(gid)) or str(gid)

    def condition(self, c: dict) -> str:
        t = c.get("type")
        if t == "profile-metric":
            mf = c.get("measurement_filter") or {}
            op, v = OPS.get(mf.get("operator"), mf.get("operator")), mf.get("value")
            name = self.metric(c.get("metric_id"))
            did = "placed an order" if re.search(r"placed order", name, re.I) else f"done {name}"
            tf = _timeframe(c.get("timeframe_filter"))
            if op == "=" and v == 0:
                return f"Has not {did} {tf}".strip()
            if op == ">" and v == 0:
                return f"Has {did} {tf}".strip()
            return f"{name} {op} {v} ({tf})"
        if t == "profile-not-in-flow":
            return f"Not in this flow {_timeframe(c.get('timeframe_filter'))}" if c.get("timeframe_filter") \
                else "Never been in this flow"
        if t == "profile-sample":
            return f"Random {c.get('percentage')}%"
        if t == "profile-marketing-consent":
            consent = c.get("consent") or {}
            neg = consent.get("can_receive_marketing") is False or "not" in str(
                (consent.get("consent_status") or {}).get("subscription") or "").lower()
            return f"{'Not ' if neg else ''}subscribed to {consent.get('channel') or c.get('channel') or 'email'}"
        if t == "profile-group-membership":
            ids = c.get("group_ids") or [c.get("group_id")]
            return f"{'Not in' if c.get('is_member') is False else 'In'} " + " or ".join(self.group(i) for i in ids if i)
        if t == "profile-property":
            f = c.get("filter") or {}
            prop = re.sub(r"^properties\['?|'?\]$", "", str(c.get("property") or ""))
            v = f.get("value")
            return f"{prop} {f.get('operator') or ''} {', '.join(map(str, v)) if isinstance(v, list) else v if v is not None else ''}".strip()
        if t == "profile-region":
            return f"{'Outside' if c.get('in_region') is False else 'In'} {c.get('region') or 'region'}"
        if t == "profile-predictive-analytics":
            f = c.get("filter") or {}
            return f"Predicted {c.get('dimension') or ''} {f.get('operator') or ''} {f.get('value') if f.get('value') is not None else ''}".strip()
        return str(t or "condition")

    def groups_text(self, pf: Optional[dict]) -> list[str]:
        return [" or ".join(self.condition(c) for c in g.get("conditions") or [])
                for g in (pf or {}).get("condition_groups") or []]

    @staticmethod
    def trigger_filter(tf: Optional[dict]) -> list[str]:
        out = []
        for g in (tf or {}).get("condition_groups") or []:
            for c in g.get("conditions") or []:
                f = c.get("filter") or {}
                v = f.get("value")
                field = str(c.get("field") or c.get("property") or "").lstrip("$")
                out.append(f"{field} {f.get('operator') or ''} {', '.join(map(str, v)) if isinstance(v, list) else v if v is not None else ''}".strip())
        return out

    def trigger(self, defn: dict, fallback: Optional[str]) -> str:
        parts = []
        for t in defn.get("triggers") or []:
            typ = t.get("type")
            if typ == "metric":
                parts.append(self.metric(t.get("id")))
            elif typ == "list":
                parts.append(f"Joins list: {self.group(t.get('id'))}")
            elif typ == "segment":
                parts.append(f"Enters segment: {self.group(t.get('id'))}")
            elif typ == "date":
                parts.append("Date property")
            else:
                parts.append(str(typ))
        return " + ".join(parts) or fallback or "No trigger set"


def offer_of(text: str) -> Optional[str]:
    t = text or ""
    if mt := re.search(r"(\d{1,2})\s?%\s*off", t, re.I) or re.search(r"(\d{1,2})\s?%", t):
        return f"{mt.group(1)}% off"
    if mt := re.search(r"\$(\d+)\s*off", t, re.I):
        return f"${mt.group(1)} off"
    if mt := re.search(r"free (shipping|gift|sample)", t, re.I):
        return mt.group(0).lower()
    if re.search(r"\bbogo\b|buy \w+,? get", t, re.I):
        return "BOGO"
    return None


def delay_text(action: dict) -> str:
    d = action.get("data") or {}
    if action.get("type") == "target-date":
        return "Wait for a date on the profile"
    v, unit = d.get("value", d.get("delay", 0)), str(d.get("unit") or "days")
    s = f"Wait {_unit(unit, v)}"
    if d.get("delay_until_time"):
        s += f" · at {d['delay_until_time']}"
    days = d.get("delay_until_weekdays")
    if isinstance(days, list) and 0 < len(days) < 7:
        s += " · " + "/".join(str(x)[:3] for x in days)
    return s


def _stage(kind: Optional[str], name: str) -> str:
    if kind in STAGE_OF:
        return STAGE_OF[kind]
    for rx, stage in NAME_STAGE:
        if rx.search(name):
            return stage
    return "other"


# ---------------------------------------------------------------- results

def _perf(snap: Snapshot, report: str) -> Optional[dict[str, dict]]:
    if not snap.has("perf"):
        return None
    try:
        rows = snap.report_results(report)
    except MissingData:
        return None
    return roll(rows, "flow_message_id", "flow_message_name")


def _stat(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {"recipients": int(row["recipients"]), "revenue": round(row["conversion_value"], 2), "rpr": row["rpr"],
            "open_rate": row["open_rate"], "click_rate": row["click_rate"], "unsubscribe_rate": row["unsubscribe_rate"],
            "spam_rate": row["spam_rate"], "conversions": int(row.get("conversion_uniques") or 0)}


# ---------------------------------------------------------------- the tree

class Builder:
    def __init__(self, snap: Snapshot, names: Names, templates: dict[str, dict], p90, p365):
        self.snap, self.names, self.templates, self.p90, self.p365 = snap, names, templates, p90, p365

    def tree(self, f) -> list[dict]:
        defn = f.definition or {}
        acts = {str(a.get("id")): a for a in defn.get("actions") or []}
        offsets = {s.action_id: s.offset_days for s in f.sends}
        seen: set[str] = set()
        count = [0]

        def walk(start: Any) -> list[dict]:
            seq: list[dict] = []
            cur = str(start) if start is not None else None
            while cur and cur in acts:
                if cur in seen:
                    seq.append({"kind": "join", "action_id": cur})
                    break
                seen.add(cur)
                a = acts[cur]
                t, d = a.get("type"), a.get("data") or {}
                links = a.get("links") or {}
                if t in ("time-delay", "target-date"):
                    seq.append({"kind": "delay", "text": delay_text(a)})
                elif t in ("send-email", "send-sms", "send-push-notification") or (
                        t == "ab-test" and (d.get("main_action") or {}).get("type") in ("send-email", "send-sms")):
                    main = (d.get("main_action") or {}) if t == "ab-test" else a
                    count[0] += 1
                    seq.append(self.message(f, cur, main, count[0], offsets.get(cur), ab=d if t == "ab-test" else None))
                    if t == "ab-test" and links.get("next") is None:
                        links = main.get("links") or {}
                elif t in ("conditional-split", "trigger-split"):
                    pf = d.get("profile_filter") or {}
                    conds = self.names.trigger_filter(d.get("trigger_filter")) if t == "trigger-split" \
                        else self.names.groups_text(pf)
                    sample = any(c.get("type") == "profile-sample" for g in pf.get("condition_groups") or []
                                 for c in g.get("conditions") or [])
                    seq.append({"kind": "split", "sample": sample, "text": " and ".join(conds) or t,
                                "yes": walk(links.get("next_if_true")), "no": walk(links.get("next_if_false"))})
                    break
                else:
                    seq.append({"kind": "util", "text": UTIL.get(t, str(t))})
                cur = str(links["next"]) if links.get("next") is not None else None
            return seq

        entry = defn.get("entry_action_id")
        return walk(entry if entry is not None else next(iter(acts), None))

    def message(self, f, action_id: str, action: dict, n: int, day: Optional[float], ab: Optional[dict]) -> dict:
        d = action.get("data") or {}
        msg = d.get("message") or {}
        channel = {"send-email": "email", "send-sms": "sms"}.get(action.get("type"), "push")
        tpl = self.templates.get(str(msg.get("template_id"))) or {}
        mid = str(msg.get("id") or "")
        subject = msg.get("subject_line") or msg.get("body") or msg.get("title") or ""
        preview = msg.get("preview_text") or ""
        variants = ((ab or {}).get("current_experiment") or {}).get("variations") or []
        node = {
            "kind": channel, "n": n, "action_id": action_id, "message_id": mid, "name": msg.get("name") or "",
            "subject": subject, "preview": preview, "from_label": msg.get("from_label") or "",
            "status": d.get("status") or "", "live": f.live and d.get("status") == "live",
            "smart_sending": msg.get("smart_sending_enabled"), "transactional": bool(msg.get("transactional")),
            "template_id": str(msg.get("template_id") or "") or None, "html_hash": tpl.get("htmlHash"),
            "kb": round((tpl.get("htmlBytes") or 0) / 1024, 1) if tpl.get("htmlBytes") else None,
            "images": tpl.get("imageCount"), "images_without_alt": tpl.get("imagesWithoutAlt"),
            "has_unsubscribe": tpl.get("hasUnsubscribe"),
            "image_only": bool(tpl) and (tpl.get("imageCount") or 0) > 0 and (tpl.get("textChars") or 0) < IMAGE_ONLY_CHARS,
            "offer": offer_of(f"{subject} {preview} {tpl.get('textSample') or ''}"),
            "ab_test": ab is not None, "variants": len(variants) if isinstance(variants, list) else 0,
            "day": round(day, 2) if day is not None else None,
            "p90": _stat((self.p90 or {}).get(mid)), "p365": _stat((self.p365 or {}).get(mid)), "suggestions": [],
        }
        node["_text"] = tpl.get("text") or tpl.get("textSample") or ""
        return node


def walk_messages(seq: list[dict]):
    for x in seq:
        if x["kind"] == "split":
            yield from walk_messages(x["yes"])
            yield from walk_messages(x["no"])
        elif x["kind"] in ("email", "sms", "push"):
            yield x


# ---------------------------------------------------------------- message review

def _s(rule: str, severity: str, title: str, why: str, fix: str) -> dict:
    return {"rule": rule, "severity": severity, "title": title, "why": why, "fix": fix}


def review(flow: dict, kind: Optional[str], terms: list[str], regulated: bool, subjects: dict[str, int],
           perf: bool, unsub_typical: Optional[float]) -> None:
    """Attach suggestions to every message of one flow (mutates the nodes)."""
    msgs = list(walk_messages(flow["tree"]))
    sized = [x["p365"]["rpr"] for x in msgs if x["p365"] and x["p365"]["recipients"] >= MIN_RECIPIENTS
             and x["p365"]["rpr"] is not None]
    typical = sorted(sized)[len(sized) // 2] if len(sized) >= 2 else None
    first = min((x for x in msgs if x["kind"] == "email" and x["live"] and x["day"] is not None),
                key=lambda x: (x["day"], x["n"]), default=None)
    if typical:
        best = max((x for x in msgs if x["p365"] and x["p365"]["recipients"] >= MIN_RECIPIENTS),
                   key=lambda x: x["p365"]["rpr"] or 0, default=None)
        if best is not None:
            best["best_in_flow"] = True
    for x in msgs:
        out = x["suggestions"]
        y, q = x["p365"], x["p90"]
        email = x["kind"] == "email"
        if flow["live"] and not x["live"]:
            out.append(_s("switched_off", "medium", "This step is switched off",
                          f"The flow is live but this {x['kind']} is {x['status'] or 'not live'}, so everyone passes it "
                          "without receiving anything.",
                          "QA it and set it live, or delete the step so the flow's timing reads the way it actually runs."))
            continue
        if not x["live"]:
            continue
        if perf and flow["live"] and not (q and q["recipients"]) and any(
                (o["p90"] or {}).get("recipients") for o in msgs if o is not x):
            out.append(_s("unreached", "low", "Nobody reached this step in 90 days",
                          "Other steps in this flow sent, but this one didn't: a split or filter before it excludes everyone.",
                          "Check the split above it; fix the condition or remove the dead branch."))
        if y and y["recipients"] >= 200 and (y["spam_rate"] or 0) > SPAM_HIGH:
            out.append(_s("spam", "critical" if y["spam_rate"] > SPAM_CRITICAL else "high", "Spam complaints are high",
                          f"{pct(y['spam_rate'], 2)} of recipients marked it as spam over 12 months (over {pct(SPAM_HIGH)} "
                          "puts inbox placement at risk).",
                          "Pause or rework it now: check the audience filter, the wait before it, and whether the content "
                          "matches why people signed up."))
        if y and y["recipients"] >= 200 and (y["unsubscribe_rate"] or 0) > max(UNSUB_HIGH, (unsub_typical or 0) * UNSUB_VS_TYPICAL):
            out.append(_s("unsubscribes", "high", "Drives unsubscribes",
                          f"{pct(y['unsubscribe_rate'])} of recipients unsubscribed after it over 12 months"
                          + (f", against {pct(unsub_typical)} for a typical live flow message." if unsub_typical else "."),
                          "Lengthen the wait before it, tighten who gets it, and lead with something useful rather than "
                          "another ask."))
        if typical and y and y["recipients"] >= MIN_RECIPIENTS and y["rpr"] is not None and y["rpr"] < typical * WEAK_SHARE:
            out.append(_s("weak_step", "medium", "Earns far less than the flow's other steps",
                          f"{usd(y['rpr'])} per recipient over 12 months against {usd(typical)} typical for this flow "
                          f"({y['recipients']:,} recipients).",
                          "Rewrite it with one clear angle (the product they looked at, proof, or a reason to act now) and a "
                          "single call to action; if a rewrite doesn't lift it, cut the step."))
        if q and y and q["recipients"] >= 300 and y["rpr"] and q["rpr"] is not None and q["rpr"] < y["rpr"] * DECLINE:
            out.append(_s("declining", "medium", "Earning less than it used to",
                          f"{usd(q['rpr'])} per recipient in the last 90 days against {usd(y['rpr'])} over 12 months.",
                          "Refresh the creative and offer; check whether a product, link or discount code in it went stale."))
        if email and y and y["recipients"] >= 1000 and y["click_rate"] is not None and y["click_rate"] < LOW_CLICK:
            out.append(_s("low_clicks", "medium", "Few people click",
                          f"{pct(y['click_rate'], 2)} click rate over 12 months.",
                          "Put one obvious button above the fold and make the image clickable; cut competing links."))
        if email and x["kb"] and x["kb"] > GMAIL_CLIP_KB:
            out.append(_s("clipped", "medium", "Gmail clips this email",
                          f"The HTML is {x['kb']} KB; Gmail cuts anything over {GMAIL_CLIP_KB} KB, hiding the footer and "
                          "unsubscribe link.",
                          "Trim it under 100 KB: fewer nested tables, no hidden blocks, smaller product grids."))
        if email and x["image_only"]:
            out.append(_s("image_only", "medium", "Mostly image, little live text",
                          "Image-blocking inboxes show almost nothing, and spam filters read it as a flyer.",
                          "Move the headline, key line and button into live text."))
        if email and (x["images_without_alt"] or 0) > 0:
            out.append(_s("missing_alt", "low", f"{x['images_without_alt']} image(s) without alt text",
                          "With images off (common in Outlook and corporate inboxes) these show as blank boxes.",
                          "Add short alt text that says what the image says."))
        if email and x["has_unsubscribe"] is False and not x["transactional"]:
            out.append(_s("no_unsubscribe", "high", "No unsubscribe link found",
                          "Marketing email without a visible unsubscribe invites spam complaints and breaks bulk-sender rules.",
                          "Add the unsubscribe and manage-preferences links to the footer."))
        if email and not x["preview"].strip():
            out.append(_s("no_preview", "low", "No preview text",
                          "The inbox fills the preview line with whatever text comes first, often a 'view in browser' link.",
                          "Write a 40 to 90 character preview line that finishes the subject's thought."))
        if email and len(x["subject"]) > SUBJECT_MAX:
            out.append(_s("long_subject", "low", "Subject gets cut off on phones",
                          f"{len(x['subject'])} characters; phones show about 35 to 40.",
                          "Front-load the point in the first 35 characters."))
        if email and x["smart_sending"] is False and not x["transactional"] and kind in SMART_SENDING_KINDS:
            out.append(_s("smart_sending_off", "low", "Smart Sending is off",
                          "The same person can get this on top of a campaign or another flow on the same day.",
                          "Turn Smart Sending on unless the timing is critical."))
        if kind in INTENT_KINDS and x is first and x["offer"]:
            out.append(_s("discount_first", "medium", "Discount in the very first reminder",
                          f"The first email already offers {x['offer']}; many people who come back within hours would have "
                          "bought at full price.",
                          "Lead with a plain reminder (the product, reviews, shipping and returns) and hold the offer for "
                          "email 2 or 3."))
        if kind in FIRST_TOUCH_MAX_DAYS and x is first and x["day"] is not None and x["day"] > FIRST_TOUCH_MAX_DAYS[kind]:
            hours = x["day"] * 24
            out.append(_s("slow_first_touch", "medium", "The first reminder goes out late",
                          f"First email at {hours:.0f} hours; intent fades fast and most recoveries come in the first few hours.",
                          f"Move the first email to {'about an hour' if kind != 'browse' else 'a few hours'} after entry."))
        if email and x["subject"] and subjects.get(x["subject"].strip().lower(), 0) > 1:
            out.append(_s("duplicate_subject", "low", "A flow at another stage uses the same subject",
                          "Someone who moves through both stages sees the same line twice and skims past the second.",
                          "Give each step its own subject that says what's new in it."))
        hits = _hits(" ".join([x["subject"], x["preview"], x.get("_text") or ""]), terms) if terms else []
        if hits:
            out.append(_s("claims", "high" if regulated else "medium", "Claims-risk wording",
                          f"Uses: {', '.join(hits[:6])}.",
                          "Have the brand's approver replace these lines with compliant wording."))
        x["priority"] = sum(SEVERITY[s["severity"]] for s in out)


# ---------------------------------------------------------------- run

COVERAGE = (
    ("Welcome series", ("welcome",), re.compile(r"welcome", re.I)),
    ("Browse abandonment", ("browse",), re.compile(r"browse|site abandon", re.I)),
    ("Abandoned cart", ("abandoned_cart",), re.compile(r"cart", re.I)),
    ("Abandoned checkout", ("abandoned_checkout",), re.compile(r"checkout", re.I)),
    ("Post-purchase", ("post_purchase",), re.compile(r"post[\s-]?purchase|thank|review|cross[\s-]?sell|upsell", re.I)),
    ("Replenishment", ("replenishment",), re.compile(r"refill|replen|reorder", re.I)),
    ("Winback", ("winback",), re.compile(r"win[\s-]?back|lapsed", re.I)),
    ("Sunset", ("sunset",), re.compile(r"sunset", re.I)),
    ("Back in stock", ("back_in_stock",), re.compile(r"back in stock", re.I)),
)


def run(snap: Snapshot, a: Analysis, context: dict) -> None:
    snap.require("api")
    infos = snap.flows()
    if not infos:
        raise MissingData("no flow definitions in the snapshot", ref("api", "flows", "details"))
    names = Names(snap)
    templates = {}
    if snap.has("templates"):
        templates = {str(t.get("templateId")): t for t in snap.at("templates", "templates", default=[]) or []
                     if not t.get("error")}
    p90, p365 = _perf(snap, "flow90"), _perf(snap, "flow365")
    builder = Builder(snap, names, templates, p90, p365)
    details = snap.at("api", "flows", "details", default=[]) or []
    flows = []
    for f in infos:
        defn = f.definition or {}
        attrs = ((details[f.index] if f.index < len(details) else {}).get("flow") or {}).get("attributes") or {}
        filters = names.groups_text(defn.get("profile_filter"))
        exits = [x for x in filters if x.startswith("Has not placed an order since starting this flow")]
        re_entry = defn.get("reentry_criteria") or None
        tree = builder.tree(f)
        msgs = list(walk_messages(tree))
        rev = lambda p, k: round(sum(((x[p] or {}).get(k) or 0) for x in msgs), 2) if any(x[p] for x in msgs) else None  # noqa: E731
        flows.append({
            "id": f.id, "name": f.name, "status": f.status, "live": f.classified_live, "kind": f.kind,
            "stage": _stage(f.kind, f.name), "trigger": names.trigger(defn, f.trigger_metric or f.trigger_type),
            "trigger_filters": [x for t in defn.get("triggers") or [] for x in names.trigger_filter(t.get("trigger_filter"))],
            "filters": [x for x in filters if x not in exits], "exits_on_purchase": bool(exits),
            "reentry": f"Can re-enter after {_unit(re_entry.get('unit'), re_entry.get('duration'))}" if isinstance(re_entry, dict)
            and re_entry.get("duration") else None,
            "updated": str(attrs.get("updated") or "")[:10] or None, "created": str(attrs.get("created") or "")[:10] or None,
            "emails": sum(1 for x in msgs if x["kind"] == "email"), "sms": sum(1 for x in msgs if x["kind"] == "sms"),
            "first_send_days": f.first_send_days, "revenue_90d": rev("p90", "revenue"), "revenue_365d": rev("p365", "revenue"),
            "recipients_90d": f.recipients90, "needs_review": f.needs_review, "tree": tree,
        })

    terms, regulated = _terms(context), context.get("regulated_category") not in (None, "No")
    stages_of: dict[str, set] = defaultdict(set)
    unsubs = []
    for fl in flows:
        if fl["live"]:
            for x in walk_messages(fl["tree"]):
                if x["live"] and x["subject"]:
                    stages_of[x["subject"].strip().lower()].add(fl["stage"])
            unsubs += [x["p365"]["unsubscribe_rate"] for x in walk_messages(fl["tree"]) if x["live"] and x["p365"]
                       and x["p365"]["recipients"] >= 200 and x["p365"]["unsubscribe_rate"] is not None]
    subjects = {k: len(v) for k, v in stages_of.items()}   # cloned flows within one stage reach different people
    unsub_typical = sorted(unsubs)[len(unsubs) // 2] if len(unsubs) >= 5 else None
    kinds = {f.id: f.kind for f in infos}
    for fl in flows:
        review(fl, kinds.get(fl["id"]), terms, regulated, subjects, perf=p90 is not None, unsub_typical=unsub_typical)
        for x in walk_messages(fl["tree"]):
            x.pop("_text", None)
    flows.sort(key=lambda fl: (not fl["live"], (STAGES + ("other",)).index(fl["stage"]), -(fl["revenue_365d"] or 0)))

    messages = []
    for fl in flows:
        for x in walk_messages(fl["tree"]):
            if fl["live"]:
                messages.append({"flow_id": fl["id"], "flow": fl["name"], "stage": fl["stage"], "kind": x["kind"],
                                 "n": x["n"], "message_id": x["message_id"], "name": x["name"], "subject": x["subject"],
                                 "preview": x["preview"], "day": x["day"], "live": x["live"], "html_hash": x["html_hash"],
                                 "p365": x["p365"], "p90": x["p90"], "suggestions": x["suggestions"],
                                 "priority": x.get("priority", 0)})
    messages.sort(key=lambda r: (-r["priority"], -((r["p365"] or {}).get("recipients") or 0)))
    a.table("flows", flows)
    a.table("messages", messages)

    stages = []
    for st in STAGES + ("other",):
        here = [fl for fl in flows if fl["stage"] == st]
        live = [fl for fl in here if fl["live"]]
        label, note = STAGE_INFO[st]
        status = ("live" if any(fl["recipients_90d"] for fl in live) or (live and p90 is None) else
                  "silent" if live else "draft" if here else "gap")
        issues = [s for fl in live for x in walk_messages(fl["tree"]) for s in x["suggestions"]]
        stages.append({"stage": st, "label": label, "note": note, "status": status,
                       "flows": [{"id": fl["id"], "name": fl["name"], "live": fl["live"], "status": fl["status"],
                                  "emails": fl["emails"], "sms": fl["sms"], "revenue_365d": fl["revenue_365d"]} for fl in here],
                       "live_messages": sum(fl["emails"] + fl["sms"] for fl in live),
                       "revenue_365d": round(sum(fl["revenue_365d"] or 0 for fl in live), 2) if live else None,
                       "suggestions": len(issues),
                       "top_issue": max(issues, key=lambda s: SEVERITY[s["severity"]])["title"] if issues else None})
    a.table("stages", [s for s in stages if s["stage"] != "other" or s["flows"]])

    coverage = []
    by_kind = defaultdict(list)
    for fl in flows:
        by_kind[kinds.get(fl["id"])].append(fl)
    for label, kinds_, rx in COVERAGE:
        live = [fl["name"] for k in kinds_ for fl in by_kind[k] if fl["live"]]
        drafts = [f"{fl['name']} ({fl['status']})" for fl in flows if not fl["live"] and rx.search(fl["name"])]
        coverage.append({"program": label, "live": live, "drafts": drafts})
    sms_live = [fl["name"] for fl in flows if fl["live"] and fl["sms"]]
    coverage.append({"program": "SMS inside flows", "live": sms_live, "drafts": [],
                     "note": None if sms_live else "No live flow sends SMS from Klaviyo"})
    a.table("coverage", coverage)

    live_msgs = [x for fl in flows if fl["live"] for x in walk_messages(fl["tree"]) if x["live"]]
    src = ref("api", "flows", "details")
    a.fact("flows_mapped", len(flows), "flows", "Flows drawn on the journey map", source=src)
    a.fact("live_messages", len(live_msgs), "messages", "Live messages in live flows", source=src)
    a.fact("live_emails", sum(1 for x in live_msgs if x["kind"] == "email"), "emails", "Live emails in live flows", source=src)
    a.fact("live_sms", sum(1 for x in live_msgs if x["kind"] == "sms"), "messages", "Live SMS in live flows", source=src)
    a.fact("previews_captured", sum(1 for x in live_msgs if x["html_hash"]), "emails", "Live emails with a rendered preview",
           source=ref("templates", "templates"))
    a.fact("messages_with_suggestions", sum(1 for x in live_msgs if x["suggestions"]), "messages",
           "Live messages with at least one suggested improvement")
    a.fact("suggestions", sum(len(x["suggestions"]) for x in live_msgs), "suggestions", "Suggested message improvements")
    if not templates:
        a.notes.append("Live templates weren't captured on this run, so the map has no email previews.")
    if p365 is None:
        a.notes.append("Per-message results weren't available on this run; suggestions use copy, timing and HTML only.")
    _findings(a, flows, kinds)


def _list(rows: list[tuple[dict, dict]], n: int = 5) -> str:
    return "; ".join(f"{fl['name']} / {x['name'] or 'step ' + str(x['n'])}" for fl, x in rows[:n])


def _findings(a: Analysis, flows: list[dict], kinds: dict) -> None:
    by_rule: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for fl in flows:
        if not fl["live"]:
            continue
        for x in walk_messages(fl["tree"]):
            for s in x["suggestions"]:
                by_rule[s["rule"]].append((fl, x))

    weak = sorted(by_rule["weak_step"], key=lambda r: -r[1]["p365"]["recipients"])
    if weak:
        gap = 0.0
        for fl, x in weak:
            sized = [o["p365"]["rpr"] for o in walk_messages(fl["tree"]) if o["p365"]
                     and o["p365"]["recipients"] >= MIN_RECIPIENTS and o["p365"]["rpr"] is not None]
            typical = sorted(sized)[len(sized) // 2]
            gap += max(0.0, typical * 0.5 - x["p365"]["rpr"]) * x["p365"]["recipients"]
        a.fact("weak_steps", len(weak), "messages", "Live flow steps earning under 40% of their flow's typical step")
        a.find("weak_steps", "Flow steps that earn far less than the rest of their flow",
               f"{len(weak)} live step(s) each earn under 40% of what a typical step in the same flow earns per recipient.",
               f"Rewrite first: {_list(weak)}.",
               "Rewrite each with one clear angle and a single call to action (the journey map shows each email and its "
               "numbers); cut any step a rewrite doesn't lift.",
               severity="medium", category="flow_optimization", facts=[f"{MODULE}.weak_steps"], effort_days=2,
               readout={"metric": "revenue per recipient of the rewritten steps", "days": 30},
               value={"status": "sized", "lever": "weak_steps", "label": "Rewrite weak flow steps",
                      "pool_key": f"{MODULE}.weak_steps", "evidence_class": "inferred",
                      "annual": {"low": round(gap * 0.5, 2), "base": round(gap, 2), "high": round(gap * 1.5, 2)},
                      "basis": "assumption: each weak step lifted to half its flow's typical revenue per recipient, "
                               "at its 12-month recipients (low half that, high one and a half times)"}
               if gap > 0 else None)
    slow = by_rule["slow_first_touch"]
    if slow:
        a.find("slow_first_touch", "Abandonment reminders go out hours late",
               f"{len(slow)} intent flow(s) send their first email well after the person left.",
               "; ".join(f"{fl['name']}: first email at {x['day'] * 24:.0f}h" for fl, x in slow[:4]) + ".",
               "Move the first email to about an hour after entry (a few hours for browse); keep the later steps where they are.",
               severity="medium", category="flow_optimization", lever=kinds.get(slow[0][0]["id"]), effort_days=1,
               readout={"metric": "recovered orders from the first step", "days": 30})
    off = by_rule["switched_off"]
    if off:
        a.find("switched_off_steps", "Live flows with steps switched off inside them",
               f"{len(off)} step(s) inside live flows are in draft or manual, so people pass them without receiving anything.",
               f"Switched off: {_list(off, 6)}.",
               "QA each one and set it live, or delete it so the flow's timing reads the way it really runs.",
               severity="medium", category="flow_optimization", effort_days=1,
               readout={"metric": "messages sent by the restored steps", "days": 14})
    first_offer = by_rule["discount_first"]
    if first_offer:
        a.find("discount_first_touch", "Abandonment flows lead with a discount",
               f"{len(first_offer)} intent flow(s) offer a discount in the very first reminder.",
               "; ".join(f"{fl['name']}: {x['offer']} in email 1" for fl, x in first_offer[:4]) + ".",
               "Test a plain first reminder with the offer moved to email 2 or 3; measure revenue per recipient for the whole "
               "flow, not just the first step.", severity="medium", category="flow_optimization", effort_days=1,
               readout={"metric": "flow revenue per recipient and discount cost", "days": 30})
    no_preview = by_rule["no_preview"]
    if len(no_preview) >= 2:
        a.find("missing_preview_text", "Live emails without preview text",
               f"{len(no_preview)} live flow emails have no preview line, so the inbox shows whatever text comes first.",
               f"Missing: {_list(no_preview, 6)}.",
               "Write a 40 to 90 character preview line for each that finishes the subject's thought.",
               severity="low", category="flow_optimization", effort_days=1,
               readout={"metric": "open rate of the updated emails", "days": 30})
    dup = by_rule["duplicate_subject"]
    if len(dup) >= 2:
        a.find("duplicate_subjects", "Different flows send the same subject line",
               f"{len(dup)} live emails share a subject with an email at a different lifecycle stage.",
               f"Shared subjects in: {_list(dup, 6)}.",
               "Give each step a subject that says what's new in that email.",
               severity="low", category="flow_optimization", effort_days=1,
               readout={"metric": "open and click rates of the renamed steps", "days": 30})


# ---------------------------------------------------------------- since the previous audit

def attach_history(result: dict, previous: Optional[dict]) -> None:
    """Mark each live message with what changed since the brand's previous audit (subject, preview, results,
    suggestions resolved or new). previous is that audit's stored analysis result."""
    mod = (result.get("modules") or {}).get(MODULE) or {}
    old = (((previous or {}).get("modules") or {}).get(MODULE) or {}).get("tables", {}).get("messages") or []
    if mod.get("status") != "ok" or not old:
        return
    before = {r["message_id"]: r for r in old if r.get("message_id")}
    rewritten = resolved = 0
    now_by_id = {}
    for r in mod["tables"].get("messages") or []:
        now_by_id[r["message_id"]] = r
        b = before.get(r["message_id"])
        if b is None:
            r["since_last"] = {"new": True}
            continue
        was = {s["rule"] for s in b.get("suggestions") or []}
        is_ = {s["rule"] for s in r.get("suggestions") or []}
        ch = {"subject_changed": (b.get("subject") or "") != (r.get("subject") or ""),
              "preview_changed": (b.get("preview") or "") != (r.get("preview") or ""),
              "html_changed": bool(b.get("html_hash") and r.get("html_hash") and b["html_hash"] != r["html_hash"]),
              "resolved": sorted(was - is_), "new_suggestions": sorted(is_ - was),
              "rpr_365d_before": (b.get("p365") or {}).get("rpr"), "rpr_90d_before": (b.get("p90") or {}).get("rpr")}
        rewritten += ch["subject_changed"] or ch["preview_changed"] or ch["html_changed"]
        resolved += len(ch["resolved"])
        r["since_last"] = ch
    for fl in mod["tables"].get("flows") or []:
        for x in walk_messages(fl.get("tree") or []):
            if x.get("message_id") in now_by_id and "since_last" in now_by_id[x["message_id"]]:
                x["since_last"] = now_by_id[x["message_id"]]["since_last"]
    for name, value, label in (("messages_changed_since_last", rewritten, "Live messages edited since the previous audit"),
                               ("suggestions_resolved_since_last", resolved, "Message suggestions resolved since the previous audit")):
        result["facts"].append({"id": f"{MODULE}.{name}", "module": MODULE, "value": value, "unit": "messages",
                                "label": label, "window": "", "time_basis": "event", "source": "", "evidence_class": "documented"})
