"""The collector stages. Each one reads part of the account and writes it in the shape the reference
collector step wrote (reference/klaviyo-audit/24-, 34-, 37-, 38-), so audit/snapshot.py reads it unchanged.

Differences from the Node collector, all because no check reads the dropped data:
- step 24 reads each flow's definition (one call per flow) but not every action and message;
- step 24 skips the one-profile sample (personal data) and leaves campaigns to step 38;
- step 38 skips coupons, catalog items, the weekly flow-series report (it fails on every saved run)
  and profile counts for every list and segment; stage `groups` counts only engagement segments.

Long stages save progress as they go (Collect.checkpoint), and a resumed stage skips what's saved.
"""
from __future__ import annotations

import copy
import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import email_preview, models as m
from ..connectors.klaviyo import REVISION, KlaviyoError
from ..scope import Scope
from . import budget
from .boundaries import boundary_to_utc, month_start, window_to_utc
from .metrics import PinError, describe, ecommerce_candidates, flatten, pick_metric, recent_activity
from .order_items import item_names

STATISTICS = [
    "recipients", "delivered", "delivery_rate", "bounced", "bounce_rate", "opens_unique", "open_rate",
    "clicks_unique", "click_rate", "conversion_uniques", "conversion_rate", "conversion_value",
    "revenue_per_recipient", "average_order_value", "unsubscribes", "unsubscribe_rate", "spam_complaints",
    "spam_complaint_rate",
]
GROUP_BY = {
    "flow": ["flow_message_id", "flow_id", "flow_message_name", "flow_name", "send_channel"],
    "campaign": ["campaign_message_id", "campaign_id", "campaign_message_name", "send_channel"],
}
FORM_STATISTICS = ["viewed_form", "viewed_form_uniques", "submits", "submit_rate", "closed_form", "qualified_form"]

# Step 38's aggregate list. windows: which 12-month windows to pull (current, prior).
AGGREGATES: list[dict] = [
    {"key": "placed_order", "names": ["Placed Order"], "prefer": "ecommerce", "measurements": ["count", "sum_value", "unique"], "windows": ["current", "prior"]},
    {"key": "placed_order_by_channel", "names": ["Placed Order"], "prefer": "ecommerce", "measurements": ["count", "sum_value"], "by": ["$attributed_channel"], "windows": ["current", "prior"]},
    {"key": "placed_order_by_flow", "names": ["Placed Order"], "prefer": "ecommerce", "measurements": ["count", "sum_value"], "by": ["$attributed_flow"], "windows": ["current"]},
    {"key": "refunded_order", "names": ["Refunded Order"], "prefer": "ecommerce", "measurements": ["count", "sum_value"], "windows": ["current", "prior"]},
    {"key": "cancelled_order", "names": ["Cancelled Order"], "prefer": "ecommerce", "measurements": ["count", "sum_value"], "windows": ["current"]},
    {"key": "active_on_site", "names": ["Active on Site"], "measurements": ["count", "unique"], "windows": ["current"]},
    {"key": "viewed_product", "names": ["Viewed Product"], "measurements": ["count", "unique"], "windows": ["current"]},
    {"key": "added_to_cart", "names": ["Added to Cart", "Add to Cart"], "prefer": "ecommerce", "measurements": ["count", "unique"], "windows": ["current"]},
    {"key": "started_checkout", "names": ["Started Checkout", "Checkout Started"], "prefer": "ecommerce", "measurements": ["count", "unique", "sum_value"], "windows": ["current"]},
    {"key": "received_email", "names": ["Received Email"], "prefer": "klaviyo", "measurements": ["count", "unique"], "windows": ["current", "prior"]},
    {"key": "received_email_by_inbox", "names": ["Received Email"], "prefer": "klaviyo", "measurements": ["count"], "by": ["Inbox Provider"], "windows": ["current"]},
    {"key": "opened_email", "names": ["Opened Email"], "prefer": "klaviyo", "measurements": ["count", "unique"], "windows": ["current", "prior"]},
    {"key": "opened_email_by_inbox", "names": ["Opened Email"], "prefer": "klaviyo", "measurements": ["unique"], "by": ["Inbox Provider"], "windows": ["current"]},
    {"key": "clicked_email", "names": ["Clicked Email"], "prefer": "klaviyo", "measurements": ["count", "unique"], "windows": ["current", "prior"]},
    {"key": "bounced_email", "names": ["Bounced Email"], "prefer": "klaviyo", "measurements": ["count", "unique"], "windows": ["current"]},
    {"key": "bounced_email_by_type", "names": ["Bounced Email"], "prefer": "klaviyo", "measurements": ["count"], "by": ["Bounce Type"], "windows": ["current"]},
    {"key": "dropped_email", "names": ["Dropped Email"], "prefer": "klaviyo", "measurements": ["count"], "windows": ["current"]},
    {"key": "marked_spam", "names": ["Marked Email as Spam"], "prefer": "klaviyo", "measurements": ["count"], "windows": ["current"]},
    {"key": "unsubscribed_email", "names": ["Unsubscribed from Email Marketing", "Unsubscribed"], "prefer": "klaviyo", "measurements": ["count", "unique"], "windows": ["current"]},
    {"key": "received_sms", "names": ["Received SMS"], "prefer": "klaviyo", "measurements": ["count", "unique"], "windows": ["current"]},
    {"key": "clicked_sms", "names": ["Clicked SMS"], "prefer": "klaviyo", "measurements": ["count", "unique"], "windows": ["current"]},
    {"key": "failed_sms", "names": ["Failed to Deliver SMS", "Failed to deliver SMS"], "prefer": "klaviyo", "measurements": ["count"], "windows": ["current"]},
    {"key": "unsubscribed_sms", "names": ["Unsubscribed from SMS Marketing", "Unsubscribed from SMS"], "prefer": "klaviyo", "measurements": ["count", "unique"], "windows": ["current"]},
    {"key": "subscribed_to_list", "names": ["Subscribed to List"], "prefer": "klaviyo", "measurements": ["count", "unique"], "by": ["List"], "windows": ["current"]},
    {"key": "subscribed_email", "names": ["Subscribed to Email Marketing"], "prefer": "klaviyo", "measurements": ["count", "unique"], "windows": ["current", "prior"]},
    {"key": "subscribed_sms", "names": ["Subscribed to SMS Marketing"], "prefer": "klaviyo", "measurements": ["count", "unique"], "windows": ["current", "prior"]},
    {"key": "form_submits", "names": ["Form submitted by profile", "Submitted Form"], "prefer": "klaviyo", "measurements": ["count", "unique"], "by": ["form_id"], "windows": ["current"]},
]
SUBSCRIPTION_METRICS = 8
GMAIL_CLIP_BYTES = 102_400
MAX_EVENT_PAGES = 1000
MAX_CAMPAIGN_PAGES = 100
ENGAGEMENT_SEGMENTS = 10
PROFILE_COUNT_PACE_S = 4.1     # profile_count drops the lists/segments family to 15 a minute
CHECKPOINT_EVERY = 10
ORDER_OVERLAP = timedelta(days=2)  # a re-audit re-reads this much before the last run's end, for late events
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ENGAGED = re.compile(r"engag|active|inactive|lapsed|sunset", re.I)


class StageFailed(Exception):
    """A stage can't produce its output. The message is shown to staff, so it says what to do."""


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def compact(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    return {"type": r.get("type"), "id": r.get("id"), "attributes": r.get("attributes") or {},
            "relationships": r.get("relationships") or {}}


def failure(label: str, e: Exception) -> dict:
    return {"label": label, "status": getattr(e, "status", None), "message": str(e)}


def _id(value: Any) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise StageFailed(f"Klaviyo returned an id that isn't safe to use in a path: {value!r}")
    return value


def get_all(client, path: str, params: Optional[dict], max_pages: int) -> dict:
    """Every page up to max_pages: {rows, included, pages, truncated}."""
    target, page_params, pages = path, params, 0
    rows: list = []
    included: list = []
    while target and pages < max_pages:
        page = client.get(target, page_params)
        pages, page_params = pages + 1, None
        data = page.get("data")
        rows.extend(data if isinstance(data, list) else [data] if data else [])
        included.extend(page.get("included") or [])
        target = (page.get("links") or {}).get("next")
    return {"rows": rows, "included": included, "pages": pages, "truncated": bool(target)}


def collection(client, path: str, params: Optional[dict], label: str, max_pages: int) -> dict:
    """Step 24's collection shape: {ok, count, truncated, items} or {ok: false, error}."""
    try:
        res = get_all(client, path, params, max_pages)
    except KlaviyoError as e:
        return {"ok": False, "error": failure(label, e)}
    return {"ok": True, "count": len(res["rows"]), "truncated": res["truncated"],
            "items": [compact(r) for r in res["rows"]]}


# ---------------------------------------------------------------- the stage context

@dataclass
class Collect:
    session: Session
    scope: Scope
    run: m.AuditRun
    client: Any
    now: datetime
    pins: dict = field(default_factory=dict)
    sleep: Callable[[float], None] = time.sleep
    report_limit: int = budget.REPORT_DAILY_LIMIT
    stage: str = ""
    context: dict = field(default_factory=dict)

    # artifacts --------------------------------------------------------------

    def _row(self, name: str) -> Optional[m.AuditArtifact]:
        return self.session.scalar(select(m.AuditArtifact).where(m.AuditArtifact.run_id == self.run.id,
                                                                 m.AuditArtifact.name == name))

    def artifact(self, name: str) -> dict:
        row = self._row(name)
        return copy.deepcopy(row.payload) if row is not None else {}

    def save(self, name: str, payload: dict) -> None:
        row = self._row(name)
        if row is None:
            row = m.AuditArtifact(org_id=self.run.org_id, client_id=self.run.client_id, run_id=self.run.id, name=name)
            self.session.add(row)
        row.payload = payload
        row.captured_at = datetime.now(timezone.utc)
        flag_modified(row, "payload")
        self.session.flush()

    def merge(self, name: str, **parts: Any) -> dict:
        payload = self.artifact(name)
        payload.update(parts)
        self.save(name, payload)
        return payload

    # run state --------------------------------------------------------------

    def state(self, key: Optional[str] = None) -> dict:
        return dict((self.run.stages or {}).get(key or self.stage) or {})

    def checkpoint(self, **values: Any) -> None:
        """Save this stage's progress and commit everything written so far."""
        stages = copy.deepcopy(self.run.stages or {})
        stages.setdefault(self.stage, {}).update(values)
        self.run.stages = stages
        flag_modified(self.run, "stages")
        self.session.commit()

    @property
    def timezone(self) -> str:
        return self.state("verify").get("timezone") or "UTC"

    @property
    def windows(self) -> dict:
        return self.state("verify")["windows"]

    @property
    def placed_order(self) -> Optional[dict]:
        return ((self.artifact("totals").get("metricChoices") or {}).get("placed_order") or {}).get("used")

    # Klaviyo calls ----------------------------------------------------------

    def report(self, kind: str, attributes: dict) -> dict:
        """One reporting call, paid for from today's budget first. Returns Klaviyo's raw response."""
        budget.spend(self.session, self.run.account_id, limit=self.report_limit)
        return self.client.query(f"/api/{kind}s/", {"data": {"type": kind, "attributes": attributes}})

    def safe_report(self, label: str, kind: str, attributes: dict) -> dict:
        try:
            return {"ok": True, "value": self.report(kind, attributes)}
        except (KlaviyoError, budget.BudgetExceeded) as e:
            return {"ok": False, "error": failure(label, e)}

    def values_report(self, kind: str, timeframe: str, metric_id: str) -> dict:
        return self.safe_report(f"{kind} {timeframe}", f"{kind}-values-report", {
            "statistics": STATISTICS, "timeframe": {"key": timeframe}, "conversion_metric_id": metric_id,
            "group_by": GROUP_BY[kind]})

    def aggregate(self, metric_id: str, spec: dict, window: str) -> dict:
        w = self.windows[window]
        utc = window_to_utc(w, self.timezone)
        attrs: dict[str, Any] = {"metric_id": metric_id, "measurements": spec["measurements"], "interval": "month",
                                 "page_size": 500, "timezone": self.timezone,
                                 "filter": [f"greater-or-equal(datetime,{utc['start']})", f"less-than(datetime,{utc['end']})"]}
        if spec.get("by"):
            attrs["by"] = spec["by"]
        res = self.client.query("/api/metric-aggregates/", {"data": {"type": "metric-aggregate", "attributes": attrs}})
        out = (res.get("data") or {}).get("attributes") or {}
        return {"window": w, "utcWindow": utc, "dates": out.get("dates") or [], "rows": out.get("data") or [],
                "links": res.get("links")}

    def metrics(self) -> list[dict]:
        return self.artifact("totals").get("metrics") or []

    @property
    def platform(self) -> Optional[str]:
        return self.context.get("store_platform") or None

    def choose(self, metrics: list[dict], names: list[str], prefer: Optional[str]) -> dict:
        """pick_metric with this run's pins, store platform and measured activity (ecommerce metrics only)."""
        if prefer != "ecommerce":
            return pick_metric(metrics, names, prefer, self.pins)
        activity = self.artifact("totals").get("activity") or None
        return pick_metric(metrics, names, prefer, self.pins, activity=activity, platform=self.platform)


# ---------------------------------------------------------------- stage 0: verify

def verify(c: Collect) -> dict:
    page = c.client.get("/api/accounts/")
    data = page.get("data") or []
    if not data:
        raise StageFailed("Klaviyo returned no account for this key")
    acct = data[0]
    attrs = acct.get("attributes") or {}
    if c.run.account_id and acct.get("id") != c.run.account_id:
        raise StageFailed(f"this key opens Klaviyo account {acct.get('id')}, but the connection is for "
                          f"{c.run.account_id}; reconnect the right key")
    c.run.account_id = acct.get("id") or ""
    tz = attrs.get("timezone") or "UTC"
    boundary_to_utc(month_start(c.now, tz, 0), tz)  # an unknown timezone fails here, before any data is read
    end = utc_iso(c.now)
    windows = {
        "current": {"start": f"{month_start(c.now, tz, -11)}T00:00:00", "end": end},
        "prior": {"start": f"{month_start(c.now, tz, -23)}T00:00:00", "end": f"{month_start(c.now, tz, -11)}T00:00:00"},
    }
    captured = utc_iso(datetime.now(timezone.utc))
    name = (attrs.get("contact_information") or {}).get("organization_name")
    c.merge("api", capturedAt=captured, revision=REVISION, account={"ok": True, "items": [compact(acct)]})
    c.merge("totals", capturedAt=end, revision=REVISION, windows=windows, metricChoices={}, aggregates={},
            campaigns={}, reports={}, errors=[], groupCounts={"lists": [], "segments": []},
            account={"timezone": tz, "currency": attrs.get("preferred_currency"), "organization_name": name})
    c.merge("run", account={"id": c.run.account_id, "timezone": tz}, history=[])
    return {"detail": f"Connected to {name or c.run.account_id}", "timezone": tz, "windows": windows,
            "account_name": name}


# ---------------------------------------------------------------- stage 1: first look

def first_look(c: Collect) -> dict:
    res = get_all(c.client, "/api/metrics/", {"fields[metric]": "name,integration,created,updated"}, 50)
    metrics = [flatten(r) for r in res["rows"]]
    c.merge("api", metrics={"ok": True, "count": len(metrics), "truncated": res["truncated"],
                            "items": [compact(r) for r in res["rows"]]})
    totals = c.artifact("totals")
    totals["metrics"] = metrics
    totals["activity"] = commerce_activity(c, metrics)
    c.save("totals", totals)
    choice = c.choose(metrics, ["Placed Order"], "ecommerce")
    placed = choice["metric"]
    totals = c.artifact("totals")
    totals["metricChoices"]["placed_order"] = describe(choice, totals["activity"])
    c.save("totals", totals)

    flows = get_all(c.client, "/api/flows/", {"page[size]": 50}, 40)
    c.merge("api", flows={"count": len(flows["rows"]), "pages": flows["pages"], "truncated": flows["truncated"],
                          "items": [compact(f) for f in flows["rows"]], "details": []})

    perf = {"capturedAt": utc_iso(datetime.now(timezone.utc)), "revision": REVISION, "conversionMetric": placed,
            "placedOrderCandidates": [x for x in metrics if re.search(r"placed order", x.get("name") or "", re.I)],
            "metrics": metrics, "reports": {}}
    if placed:
        perf["reports"]["flow365"] = c.values_report("flow", "last_365_days", placed["id"])
        perf["reports"]["campaign365"] = c.values_report("campaign", "last_365_days", placed["id"])
        spec = next(s for s in AGGREGATES if s["key"] == "placed_order_by_channel")
        totals = c.artifact("totals")
        agg = {"metricId": placed["id"], "measurements": spec["measurements"], "by": spec["by"]}
        try:
            agg["current"] = c.aggregate(placed["id"], spec, "current")
        except KlaviyoError as e:
            agg["current"] = {"error": failure("placed_order_by_channel current", e)}
        totals["aggregates"]["placed_order_by_channel"] = agg
        c.save("totals", totals)
    c.save("perf", perf)
    summary = first_look_summary(c.artifact("api"), perf, c.artifact("totals"), choice)
    return {"detail": summary["headline"], "summary": summary}


def commerce_activity(c: Collect, metrics: list[dict]) -> dict[str, Optional[int]]:
    """Last-90-day event counts for every ecommerce metric that has a same-named twin from another
    integration (a platform switch leaves the old store's copy behind). One aggregate call each."""
    ids: list[str] = []
    for names in {tuple(s["names"]) for s in AGGREGATES if s.get("prefer") == "ecommerce"}:
        cands = ecommerce_candidates(metrics, list(names))
        if len(cands) > 1:
            ids += [str(x["id"]) for x in cands if str(x["id"]) not in ids]
    return recent_activity(c.client, ids, c.now, tz=c.timezone) if ids else {}


def _sum_series(rows: list[dict], measurement: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in rows:
        dim = (r.get("dimensions") or [""])[0] or ""
        out[dim] = out.get(dim, 0.0) + sum(float(v or 0) for v in (r.get("measurements") or {}).get(measurement) or [])
    return out


def _report_value(perf: dict, key: str) -> Optional[float]:
    rep = (perf.get("reports") or {}).get(key) or {}
    if not rep.get("ok"):
        return None
    results = (((rep.get("value") or {}).get("data") or {}).get("attributes") or {}).get("results") or []
    return sum(float((r.get("statistics") or {}).get("conversion_value") or 0) for r in results)


def first_look_summary(api: dict, perf: dict, totals: dict, choice: dict) -> dict:
    """What the brand can see about two minutes in. Preliminary: the full audit confirms or replaces it."""
    from ..klaviyo_intake import coverage

    flows = [{"id": f.get("id"), "name": (f.get("attributes") or {}).get("name") or "",
              "status": (f.get("attributes") or {}).get("status") or "",
              "trigger_type": (f.get("attributes") or {}).get("trigger_type")}
             for f in (api.get("flows") or {}).get("items") or []]
    live = [f for f in flows if f["status"] == "live"]
    by_trigger: dict[str, int] = {}
    for f in live:
        by_trigger[f["trigger_type"] or "unknown"] = by_trigger.get(f["trigger_type"] or "unknown", 0) + 1
    summary: dict[str, Any] = {
        "preliminary": True,
        "placed_order": describe(choice),
        "flows": {"total": len(flows), "live": len(live), "live_by_trigger": by_trigger,
                  "core_by_name": {k: v["live"] for k, v in coverage(flows).items()}},
        "attributed_365": {"flows": _report_value(perf, "flow365"), "campaigns": _report_value(perf, "campaign365")},
        "revenue_12m": None,
    }
    agg = ((totals.get("aggregates") or {}).get("placed_order_by_channel") or {}).get("current") or {}
    if agg.get("rows") is not None and not agg.get("error"):
        by = _sum_series(agg["rows"], "sum_value")
        total = sum(by.values())
        summary["revenue_12m"] = {"total": round(total, 2), "email": round(by.get("$email_channel", 0.0), 2),
                                  "sms": round(by.get("$sms_channel", 0.0), 2),
                                  "currency": (totals.get("account") or {}).get("currency"),
                                  "window": agg.get("window")}
    if choice["status"] != "selected":
        summary["headline"] = f"Placed Order metric {choice['status']}: {choice['reason']}"
    elif summary["revenue_12m"]:
        r = summary["revenue_12m"]
        share = (r["email"] + r["sms"]) / r["total"] if r["total"] else None
        summary["headline"] = (f"{len(live)} live flows; email and SMS attributed "
                               f"{'unavailable' if share is None else f'{share:.0%}'} of 12-month revenue")
    else:
        summary["headline"] = f"{len(live)} live flows; revenue by channel unavailable"
    return summary


# ---------------------------------------------------------------- stage 2: account structure (step 24)

def structure(c: Collect) -> dict:
    api = c.artifact("api")
    flows = api.get("flows") or {}
    details: list[dict] = flows.get("details") or []
    done = {str((d.get("flow") or {}).get("id")) for d in details}
    for f in flows.get("items") or []:
        if str(f.get("id")) in done:
            continue
        try:
            page = c.client.get(f"/api/flows/{_id(f.get('id'))}/", {"additional-fields[flow]": "definition"})
            data = page.get("data") or {}
            details.append({"flow": f, "detail": compact(data), "included": [],
                            "definition": (data.get("attributes") or {}).get("definition"), "errors": []})
        except KlaviyoError as e:
            details.append({"flow": f, "detail": None, "included": [], "definition": None,
                            "errors": [failure(f"flow {f.get('id')}", e)]})
        if len(details) % CHECKPOINT_EVERY == 0:
            api["flows"] = {**flows, "details": details}
            c.save("api", api)
            c.checkpoint(flows_read=len(details))
    api["flows"] = {**flows, "details": details, "detailed": len(details), "messageDetails": 0, "messageCapHit": False}
    api["campaigns"] = {"ok": True, "source": "account-totals.json campaigns (step 38 reads them with their messages)"}
    api["forms"] = collection(c.client, "/api/forms/", {"page[size]": 10, "sort": "-updated_at"}, "forms", 100)
    api["lists"] = collection(c.client, "/api/lists/", {"page[size]": 10}, "lists", 100)
    api["segments"] = collection(c.client, "/api/segments/", {"page[size]": 10}, "segments", 100)
    api["templates"] = collection(c.client, "/api/templates/",
                                  {"page[size]": 10, "fields[template]": "name,editor_type,created,updated"}, "templates", 100)
    api["capturedAt"] = utc_iso(datetime.now(timezone.utc))
    c.save("api", api)
    errors = sum(1 for d in details if d.get("errors"))
    return {"detail": f"{len(details)} flows read" + (f", {errors} with errors" if errors else ""),
            "flows_read": len(details), "flow_errors": errors}


# ---------------------------------------------------------------- stage 3: performance (step 34)

def performance(c: Collect) -> dict:
    perf = c.artifact("perf")
    placed = perf.get("conversionMetric")
    if not placed:
        raise StageFailed("no Placed Order metric was selected, so flow and campaign revenue can't be measured")
    for key, kind in (("flow90", "flow"), ("campaign90", "campaign")):
        if not ((perf["reports"].get(key) or {}).get("ok")):
            perf["reports"][key] = c.values_report(kind, "last_90_days", placed["id"])
    perf["capturedAt"] = utc_iso(datetime.now(timezone.utc))
    c.save("perf", perf)
    failed = [k for k, r in perf["reports"].items() if not r.get("ok")]
    return {"detail": "4 performance reports read" if not failed else f"reports unavailable: {', '.join(failed)}",
            "failed_reports": failed}


# ---------------------------------------------------------------- stage 4: template weight (step 37)

def _walk(value: Any, visit: Callable[[dict], None]) -> None:
    if isinstance(value, dict):
        visit(value)
        for child in value.values():
            _walk(child, visit)
    elif isinstance(value, list):
        for child in value:
            _walk(child, visit)


def discover_templates(api: dict) -> list[dict]:
    found: dict[str, dict] = {}
    for d in (api.get("flows") or {}).get("details") or []:
        flow = d.get("flow") or {}
        if (flow.get("attributes") or {}).get("status") != "live":
            continue
        fid, fname = flow.get("id"), (flow.get("attributes") or {}).get("name") or ""

        def visit(node: dict) -> None:
            tid = node.get("template_id")
            if not tid:
                return
            entry = found.setdefault(str(tid), {"templateId": str(tid), "messages": []})
            msg = {"flowId": fid, "flowName": fname, "messageId": node.get("id"), "messageName": node.get("name"),
                   "messageStatus": node.get("status"), "subject": node.get("subject_line") or node.get("subject"),
                   "previewText": node.get("preview_text")}
            if msg not in entry["messages"]:
                entry["messages"].append(msg)
        _walk(d.get("definition"), visit)
    return list(found.values())


def _strip_html(html: str) -> str:
    s = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s).replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


MAX_LINKS = 60
_HREF = re.compile(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"']", re.I)
_IMG = re.compile(r"<img\b[^>]*>", re.I)
_ALT = re.compile(r"\balt\s*=\s*[\"']\s*[^\"'\s][^\"']*[\"']", re.I)


def measure_template(entry: dict, resource: dict) -> dict:
    attrs = resource.get("attributes") or {}
    html = str(attrs.get("html") or attrs.get("content") or attrs.get("body") or attrs.get("editor_html") or "")
    text = _strip_html(html)
    size = len(html.encode("utf-8"))
    codes = re.findall(r"(?:\{\{|\{%)[\s\S]{0,180}?(?:\}\}|%\})", html)
    imgs = _IMG.findall(html)
    links = [h.strip() for h in _HREF.findall(html) if not h.strip().lower().startswith(("mailto:", "tel:", "#"))]
    return {**entry, "templateName": attrs.get("name"), "htmlBytes": size, "textChars": len(text),
            "imageCount": len(imgs), "linkCount": len(re.findall(r"<a\b", html, re.I)),
            "imagesWithoutAlt": sum(1 for i in imgs if not _ALT.search(i)),
            "hasUnsubscribe": bool(re.search(r"unsubscribe|manage_preferences|opt[\s-]?out", html, re.I)),
            "hasAddress": bool(re.search(r"organization\.(full_)?address|organization\.address", html, re.I)),
            "links": list(dict.fromkeys(links))[:MAX_LINKS],
            "dynamicCodeCount": len(re.findall(r"\{\{|\{%", html)), "overGmailClipThreshold": size > GMAIL_CLIP_BYTES,
            "text": text[:20_000], "textSample": text[:500],
            "dynamicCodeSamples": list(dict.fromkeys(re.sub(r"\s+", " ", x).strip() for x in codes))[:20]}


def templates(c: Collect) -> dict:
    out = c.artifact("templates")
    measured = {t["templateId"]: t for t in out.get("templates") or []}
    for i, entry in enumerate(discover_templates(c.artifact("api"))):
        if entry["templateId"] in measured:
            continue
        try:
            page = c.client.get(f"/api/templates/{_id(entry['templateId'])}/")
            resource = page.get("data") or {}
            measured[entry["templateId"]] = measure_template(entry, resource)
            attrs = resource.get("attributes") or {}
            html = str(attrs.get("html") or attrs.get("content") or attrs.get("body") or attrs.get("editor_html") or "")
            measured[entry["templateId"]]["htmlHash"] = email_preview.store(
                c.session, c.run.org_id, c.run.client_id, entry["templateId"], html)
        except KlaviyoError as e:
            measured[entry["templateId"]] = {**entry, "error": failure(f"template {entry['templateId']}", e)}
        if (i + 1) % CHECKPOINT_EVERY == 0:
            c.save("templates", {"templates": list(measured.values())})
            c.checkpoint(templates_read=len(measured))
    rows = list(measured.values())
    over = sum(1 for t in rows if t.get("overGmailClipThreshold"))
    errors = sum(1 for t in rows if t.get("error"))
    c.save("templates", {"capturedAt": utc_iso(datetime.now(timezone.utc)), "revision": REVISION,
                         "uniqueLiveTemplates": len(rows), "overGmailClipThreshold": over, "errors": errors,
                         "templates": rows})
    return {"detail": f"{len(rows)} live templates measured, {over} over Gmail's clipping size",
            "templates": len(rows), "over_clip": over, "errors": errors}


# ---------------------------------------------------------------- stage 5: aggregates and campaigns (step 38)

def _specs(metrics: list[dict]) -> list[dict]:
    subs = [x for x in metrics if re.search(r"subscription", x.get("name") or "", re.I)
            and not re.search(r"klaviyo", (x.get("integration") or {}).get("name") or "", re.I)][:SUBSCRIPTION_METRICS]
    return AGGREGATES + [{"key": f"subscription_{x['id']}", "names": [x.get("name")], "fixed_id": x["id"],
                          "measurements": ["count", "unique"], "windows": ["current"]} for x in subs]


def totals(c: Collect) -> dict:
    metrics = c.metrics()
    t = c.artifact("totals")
    for n, spec in enumerate(_specs(metrics)):
        key = spec["key"]
        agg = t["aggregates"].get(key)
        if agg is not None and (agg.get("notFound") or agg.get("ambiguous")
                                or all(w in agg for w in spec["windows"])):
            continue
        if spec.get("fixed_id"):
            hit = [x for x in metrics if x["id"] == spec["fixed_id"]]
            choice = {"metric": hit[0] if hit else None, "candidates": hit, "status": "selected" if hit else "missing",
                      "reason": "Explicit metric ID"}
        else:
            try:
                choice = c.choose(metrics, spec["names"], spec.get("prefer"))
            except PinError as e:
                t["aggregates"][key] = {"error": str(e)}
                t["errors"].append({"ok": False, "label": key, "error": {"message": str(e)}})
                continue
        t["metricChoices"][key] = describe(choice, t.get("activity") if spec.get("prefer") == "ecommerce" else None)
        metric = choice["metric"]
        if metric is None:
            t["aggregates"][key] = ({"ambiguous": True, "error": choice["reason"]} if choice["status"] == "ambiguous"
                                    else {"notFound": spec["names"]})
            continue
        agg = agg or {"metricId": metric["id"], "measurements": spec["measurements"], "by": spec.get("by")}
        for window in spec["windows"]:
            if window in agg and not (agg[window] or {}).get("error"):
                continue
            try:
                agg[window] = c.aggregate(metric["id"], spec, window)
            except KlaviyoError as e:
                agg[window] = {"error": failure(f"{key} {window}", e)}
                t["errors"].append({"ok": False, "label": f"{key} {window}", "error": failure(key, e)})
        t["aggregates"][key] = agg
        if (n + 1) % 5 == 0:
            c.save("totals", t)
            c.checkpoint(aggregates=len(t["aggregates"]))
    c.save("totals", t)

    for channel in ("email", "sms"):
        if (t["campaigns"].get(channel) or {}).get("items") is not None:
            continue
        try:
            res = get_all(c.client, "/api/campaigns/", {"filter": f"equals(messages.channel,'{channel}')",
                                                         "page[size]": 100, "sort": "-created_at",
                                                         "include": "campaign-messages"}, MAX_CAMPAIGN_PAGES)
            t["campaigns"][channel] = {"count": len(res["rows"]), "pages": res["pages"], "truncated": res["truncated"],
                                       "items": [compact(r) for r in res["rows"]],
                                       "messages": [compact(r) for r in res["included"] if r.get("type") == "campaign-message"]}
        except KlaviyoError as e:
            t["campaigns"][channel] = {"error": failure(f"campaigns {channel}", e)}
            t["errors"].append({"ok": False, "label": f"campaigns {channel}", "error": failure(channel, e)})
        c.save("totals", t)
        c.checkpoint(campaigns=channel)

    if "formValues365" not in t["reports"]:
        rep = c.safe_report("form values 365d", "form-values-report", {
            "statistics": FORM_STATISTICS, "timeframe": {"key": "last_365_days"}, "group_by": ["form_id", "form_version_id"]})
        t["reports"]["formValues365"] = rep["value"] if rep["ok"] else {"error": rep["error"]}
        if not rep["ok"]:
            t["errors"].append({"ok": False, "label": "form values 365d", "error": rep["error"]})
    c.save("totals", t)
    missing = sorted(k for k, a in t["aggregates"].items() if a.get("notFound"))
    return {"detail": f"{len(t['aggregates'])} monthly aggregates and "
                      f"{sum(len((t['campaigns'].get(ch) or {}).get('items') or []) for ch in ('email', 'sms'))} campaigns read",
            "aggregates": len(t["aggregates"]), "metrics_not_in_account": missing, "errors": len(t["errors"])}


# ---------------------------------------------------------------- stage 6: orders, 24 months (step 38 section 4)

def _discount(p: dict) -> Optional[list]:
    d = p.get("Discount Codes") if p.get("Discount Codes") is not None else p.get("DiscountCodes")
    if d is None or d == "":
        return None
    return [str(x) for x in d][:10] if isinstance(d, list) else [str(d)]


def _value(p: dict) -> float:
    try:
        return float(p.get("$value") if p.get("$value") is not None else p.get("value") or 0)
    except (TypeError, ValueError):
        return 0.0


def store_orders(c: Collect, metric_id: str, events: list[dict]) -> int:
    """Insert events not stored yet for this client. Returns how many were new."""
    ids = [str(e.get("id")) for e in events if e.get("id")]
    if not ids:
        return 0
    have = set(c.session.scalars(select(m.AuditOrder.event_id).where(m.AuditOrder.client_id == c.run.client_id,
                                                                     m.AuditOrder.event_id.in_(ids))))
    added = 0
    for e in events:
        eid = str(e.get("id") or "")
        attrs = e.get("attributes") or {}
        try:
            placed = datetime.fromisoformat(str(attrs.get("datetime") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if not eid or eid in have or placed.tzinfo is None:
            continue
        placed = placed.astimezone(timezone.utc)
        p = attrs.get("event_properties") or {}
        oid = p.get("$event_id") or p.get("OrderId")
        source = p.get("Source Name") or p.get("SourceName")
        profile = (((e.get("relationships") or {}).get("profile") or {}).get("data") or {}).get("id")
        c.session.add(m.AuditOrder(org_id=c.run.org_id, client_id=c.run.client_id, metric_id=metric_id, event_id=eid[:64],
                                   order_id=str(oid)[:120] if oid else None, profile_id=str(profile)[:64] if profile else None,
                                   placed_at=placed, value=_value(p), items=item_names(p)[:8], discount=_discount(p),
                                   source=str(source)[:120] if source else None))
        have.add(eid)
        added += 1
    c.session.flush()
    return added


def _resume_point(c: Collect, metric_id: str, start: str) -> str:
    """Where to start reading: this window's start, or just before the last finished run's end when that run
    already covered the window with the same metric (a re-audit then reads only new orders)."""
    prior = c.session.scalars(select(m.AuditRun).where(
        m.AuditRun.client_id == c.run.client_id, m.AuditRun.id != c.run.id,
        m.AuditRun.status.in_(("ready", "partial"))).order_by(m.AuditRun.created_at.desc()).limit(5))
    for run in prior:
        st = (run.stages or {}).get("orders") or {}
        if (st.get("status") == "done" and st.get("metric_id") == metric_id and not st.get("truncated")
                and not st.get("error") and st.get("start") and st["start"] <= start and st.get("end")):
            since = datetime.fromisoformat(st["end"].replace("Z", "+00:00")) - ORDER_OVERLAP
            return max(start, utc_iso(since))
    return start


def _reserve_pages(c: Collect, want: int) -> int:
    """Reserve up to the next checkpoint's worth of event pages. Spending commits, so it happens only right
    after a checkpoint, never with orders stored since the last one."""
    left = budget.LIMITS["events"] - budget.used(c.session, c.run.account_id, family="events")
    n = max(1, min(want, left))
    budget.spend(c.session, c.run.account_id, n, family="events")
    return n


def orders(c: Collect) -> dict:
    placed = c.placed_order
    if not placed:
        raise StageFailed("no Placed Order metric was selected, so order history can't be read")
    st = c.state()
    start = st.get("start") or boundary_to_utc(f"{month_start(c.now, c.timezone, -23)}T00:00:00", c.timezone)
    end = st.get("end") or c.windows["current"]["end"]
    full = (c.run.context or {}).get("refresh") == "full"  # the monthly re-audit re-reads the whole window
    since = st.get("since") or (start if full else _resume_point(c, placed["id"], start))
    page_size = st.get("page_size") or 1000
    pages, stored = st.get("pages") or 0, st.get("stored") or 0
    target = st.get("next") or ("/api/events/" if not pages else None)
    c.checkpoint(start=start, end=end, since=since, metric_id=placed["id"])
    truncated, error, reserved = False, None, 0
    while target:
        if pages >= MAX_EVENT_PAGES:
            truncated = True
            break
        if not reserved:
            try:
                reserved = _reserve_pages(c, CHECKPOINT_EVERY - pages % CHECKPOINT_EVERY)
            except budget.BudgetExceeded as e:
                error = failure("placed order events", e)
                break
        reserved -= 1
        params = None if target != "/api/events/" else {
            "filter": f'equals(metric_id,"{_id(placed["id"])}"),greater-or-equal(datetime,{since}),less-than(datetime,{end})',
            "fields[event]": "datetime,event_properties", "page[size]": page_size, "sort": "datetime"}
        try:
            page = c.client.get(target, params)
        except KlaviyoError as e:
            if pages == 0 and page_size == 1000 and e.status == 400:  # the docs disagree on the max page size
                page_size = 200
                continue
            error = failure("placed order events", e)
            break
        pages += 1
        stored += store_orders(c, placed["id"], page.get("data") or [])
        target = (page.get("links") or {}).get("next")
        if pages % CHECKPOINT_EVERY == 0:
            c.checkpoint(next=target, pages=pages, stored=stored, page_size=page_size)
    t = c.artifact("totals")
    summary = {"metric": placed, "start": start, "end": end, "since": since, "pageSize": page_size, "pages": pages,
               "truncated": truncated, "error": error, "fetched": stored}
    t["orders"] = summary
    if error:
        t["errors"].append({"ok": False, "label": "placed order events", "error": error})
    c.save("totals", t)
    c.checkpoint(next=None, pages=pages, stored=stored, page_size=page_size, truncated=truncated, error=error)
    word = "new orders read" if since != start else "orders read"
    return {"detail": f"{stored:,} {word}" + (" (stopped at the page cap)" if truncated else "")
                      + (f"; stopped early: {error['message']}" if error else ""),
            "truncated": truncated, "error": error}


def orders_file(session: Session, run: m.AuditRun) -> Optional[dict]:
    """placed-orders-24mo.json for this run, rebuilt from audit_orders over the run's window."""
    st = (run.stages or {}).get("orders") or {}
    if not st.get("start") or not st.get("end"):
        return None
    start = datetime.fromisoformat(st["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(st["end"].replace("Z", "+00:00"))
    rows = session.scalars(select(m.AuditOrder).where(
        m.AuditOrder.client_id == run.client_id, m.AuditOrder.metric_id == st.get("metric_id"),
        m.AuditOrder.placed_at >= start, m.AuditOrder.placed_at < end).order_by(m.AuditOrder.placed_at))
    return {"metric": {"id": st.get("metric_id")}, "start": st["start"], "end": st["end"],
            "pageSize": st.get("page_size"), "pages": st.get("pages"), "truncated": bool(st.get("truncated")),
            "error": st.get("error"),
            "orders": [_order_row(r) for r in rows]}


def order_key(source: Optional[str], order_number: Optional[str], placed_iso: str, value: float) -> str:
    """One real order: (source, order number, UTC instant, value). Order numbers alone repeat on some
    stores (WooCommerce reuses them), and a re-sent event keeps all four, so it still collapses."""
    raw = f"{source or ''}|{order_number or ''}|{placed_iso}|{value:.2f}"
    return f"{order_number or 'order'}~{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def _order_row(r: m.AuditOrder) -> dict:
    placed = utc_iso(r.placed_at if r.placed_at.tzinfo else r.placed_at.replace(tzinfo=timezone.utc))
    return {"p": r.profile_id, "t": placed, "v": r.value, "i": r.items or [], "d": r.discount, "s": r.source,
            "o": order_key(r.source, r.order_id, placed, r.value or 0.0), "n": r.order_id}


# ---------------------------------------------------------------- stage 7: sizing pools

def groups(c: Collect) -> dict:
    segs = [s for s in (c.artifact("api").get("segments") or {}).get("items") or []
            if _ENGAGED.search((s.get("attributes") or {}).get("name") or "")]
    segs.sort(key=lambda s: str((s.get("attributes") or {}).get("updated") or ""), reverse=True)
    segs.sort(key=lambda s: not (s.get("attributes") or {}).get("is_active", True))  # active first, newest first
    t = c.artifact("totals")
    done = {g["id"] for g in t["groupCounts"]["segments"]}
    for s in segs[:ENGAGEMENT_SEGMENTS]:
        if s.get("id") in done:
            continue
        attrs = s.get("attributes") or {}
        try:
            page = c.client.get(f"/api/segments/{_id(s.get('id'))}/", {"additional-fields[segment]": "profile_count"})
            count, err = ((page.get("data") or {}).get("attributes") or {}).get("profile_count"), None
        except KlaviyoError as e:
            count, err = None, failure(f"segment {s.get('id')} count", e)
        t["groupCounts"]["segments"].append({"id": s.get("id"), "name": attrs.get("name"), "profileCount": count,
                                             "isActive": attrs.get("is_active"), "optInProcess": None, "error": err})
        c.save("totals", t)
        c.sleep(PROFILE_COUNT_PACE_S)
    t["groupCounts"]["selection"] = (f"segments whose names mention engagement or activity, at most {ENGAGEMENT_SEGMENTS}; "
                                     "lists are not counted")
    c.save("totals", t)
    counted = [g for g in t["groupCounts"]["segments"] if g.get("profileCount") is not None]
    return {"detail": f"{len(counted)} engagement segments counted" if segs else "no engagement segments found to count",
            "segments_counted": len(counted)}
