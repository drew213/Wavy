"""conventions.flow_classifier from the check library, applied to step 24 flow definitions.

A flow is a LIVE flow of type X when (a) it's live and not archived, (b) its first trigger maps to
X, (c) it has at least one live send action. Step 34's 90-day recipients (d) are attached so each
check can apply its own rule about sending. Custom API metrics are classified by exact name only
and flagged `needs_review`; name-only (fuzzy) matches are never counted.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .snapshot import MissingData, Snapshot, ref

# Trigger metric name (lower case) -> flow type. conventions.flow_classifier (b).
METRIC_TYPES: dict[str, str] = {
    "subscribed to email marketing": "welcome",
    "subscribed to list": "welcome",
    "form submitted by profile": "welcome",
    "submitted form": "welcome",
    "started checkout": "abandoned_checkout",
    "checkout started": "abandoned_checkout",
    "added to cart": "abandoned_cart",
    "viewed product": "browse",
    "placed order": "post_purchase",
    "fulfilled order": "post_purchase",
    "ordered product": "post_purchase",
    "subscribed to back in stock": "back_in_stock",
}
ORDER_TRIGGER_NAMES = frozenset({"placed order", "fulfilled order", "ordered product"})
CHECKOUT_NAMES = frozenset({"started checkout", "checkout started"})
CART_NAMES = frozenset({"added to cart"})
ORDER_NAMES = frozenset({"placed order"})
ENGAGEMENT_NAMES = frozenset({"opened email", "clicked email", "active on site", "clicked sms"})

# conventions.flow_classifier: an order-triggered flow is replenishment when its first send waits
# >= 21 days and winback when it waits >= 45 days (or its name says so).
REPLENISHMENT_MIN_DAYS = 21
WINBACK_MIN_DAYS = 45
# FLW-07 logic: a sunset segment has no open/click/active-on-site in >= 90 days.
SUNSET_MIN_DAYS = 90
WINBACK_NAME = re.compile(r"win[\s-]?back|re-?activation|lapsed", re.I)
REPLENISH_NAME = re.compile(r"replenish|refill|reorder", re.I)

CORE_TYPES = ("welcome", "abandoned_checkout", "abandoned_cart", "browse", "post_purchase", "winback", "replenishment", "sunset")
INTENT_TYPES = ("welcome", "browse", "abandoned_cart", "abandoned_checkout")
SEND_TYPES = ("send-email", "send-sms")

_UNIT_DAYS = {"days": 1.0, "day": 1.0, "hours": 1 / 24, "hour": 1 / 24, "minutes": 1 / 1440, "minute": 1 / 1440,
              "weeks": 7.0, "week": 7.0}


@dataclass
class Send:
    action_id: str
    index: int                 # position in definition.actions
    channel: str               # email | sms
    live: bool
    status: Optional[str]
    message: dict
    via_ab_test: bool = False
    offset_days: Optional[float] = None   # min cumulative delay from entry


@dataclass
class FlowInfo:
    index: int                 # position in flows.details
    id: str
    name: str
    status: Optional[str]
    archived: bool
    trigger_type: Optional[str]
    trigger_id: Optional[str]
    trigger_metric: Optional[str]
    trigger_integration: Optional[str]
    kind: Optional[str]
    needs_review: list[str] = field(default_factory=list)
    sends: list[Send] = field(default_factory=list)
    main_path_emails: int = 0
    main_path_sms: int = 0
    first_send_days: Optional[float] = None
    recipients90: Optional[int] = None      # max recipients of any one message (≈ people reached)
    recipients90_by_message: dict[str, int] = field(default_factory=dict)
    definition: dict = field(default_factory=dict)

    @property
    def live(self) -> bool:
        return self.status == "live" and not self.archived

    @property
    def live_sends(self) -> list[Send]:
        return [s for s in self.sends if s.live]

    @property
    def classified_live(self) -> bool:
        """conventions.flow_classifier (a) and (c)."""
        return self.live and bool(self.live_sends)

    @property
    def sending(self) -> bool:
        return bool(self.recipients90)

    def ref(self, *path: Any) -> str:
        return ref("api", "flows", "details", self.index, *path)

    def first_email(self) -> Optional[Send]:
        emails = [s for s in self.live_sends if s.channel == "email" and s.offset_days is not None]
        return min(emails, key=lambda s: (s.offset_days, s.index)) if emails else None


def _actions(defn: dict) -> list[dict]:
    return defn.get("actions") or []


def _next_ids(action: dict) -> list[str]:
    links = action.get("links") or {}
    out = [links.get(k) for k in ("next", "next_if_true", "next_if_false")]
    main = (action.get("data") or {}).get("main_action") or {}
    out.append((main.get("links") or {}).get("next"))
    return [str(x) for x in out if x]


def delay_days(action: dict) -> float:
    if action.get("type") != "time-delay":
        return 0.0
    data = action.get("data") or {}
    try:
        value = float(data.get("value") or 0)
    except (TypeError, ValueError):
        return 0.0
    return value * _UNIT_DAYS.get(str(data.get("unit") or "days").lower(), 1.0)


def _send_of(action: dict, index: int) -> Optional[Send]:
    t = action.get("type")
    data = action.get("data") or {}
    if t in SEND_TYPES:
        st = data.get("status")
        return Send(str(action.get("id")), index, "sms" if t == "send-sms" else "email", st == "live", st,
                    data.get("message") or {})
    if t == "ab-test":
        main = data.get("main_action") or {}
        if main.get("type") in SEND_TYPES:
            mdata = main.get("data") or {}
            st = mdata.get("status")
            return Send(str(action.get("id")), index, "sms" if main.get("type") == "send-sms" else "email",
                        st == "live", st, mdata.get("message") or {}, via_ab_test=True)
    return None


def _walk(defn: dict, sends: dict[str, Send]) -> tuple[int, int, dict[str, float]]:
    """(max live emails on one path, max live SMS on one path, min offset per send id)."""
    acts = {str(a.get("id")): a for a in _actions(defn)}
    entry = defn.get("entry_action_id")
    entry = str(entry) if entry is not None else (next(iter(acts)) if acts else None)
    if entry not in acts:
        return 0, 0, {}
    memo: dict[str, tuple[int, int]] = {}

    def longest(node: str, stack: frozenset) -> tuple[int, int]:
        if node in memo:
            return memo[node]
        if node in stack or node not in acts:
            return 0, 0
        s = sends.get(node)
        here = (1 if s and s.live and s.channel == "email" else 0, 1 if s and s.live and s.channel == "sms" else 0)
        kids = [longest(k, stack | {node}) for k in _next_ids(acts[node])]
        best = (max((k[0] for k in kids), default=0), max((k[1] for k in kids), default=0))
        memo[node] = (here[0] + best[0], here[1] + best[1])
        return memo[node]

    offsets: dict[str, float] = {}
    best_at: dict[str, float] = {}
    todo = [(entry, 0.0)]
    while todo:  # DAG relaxation; delays are non-negative so revisits only ever shorten
        node, acc = todo.pop()
        if node not in acts or best_at.get(node, math.inf) <= acc:
            continue
        best_at[node] = acc
        here = acc + delay_days(acts[node])
        if node in sends:
            offsets[node] = min(offsets.get(node, math.inf), here)
        for k in _next_ids(acts[node]):
            todo.append((k, here))
    emails, sms = longest(entry, frozenset())
    return emails, sms, offsets


def _segment_kind(seg: Optional[dict], metric_names: dict[str, str]) -> Optional[str]:
    if not seg:
        return None
    return segment_kind((seg.get("attributes") or {}).get("definition") or {}, metric_names)


def _conditions(defn: dict) -> list[dict]:
    out = []
    for g in (defn or {}).get("condition_groups") or []:
        out.extend(g.get("conditions") or [])
    return out


def _days(tf: dict) -> Optional[float]:
    if (tf or {}).get("operator") != "in-the-last":
        return None
    try:
        return float(tf.get("quantity") or 0) * _UNIT_DAYS.get(str(tf.get("unit") or "day").lower(), 1.0)
    except (TypeError, ValueError):
        return None


def segment_kind(definition: dict, metric_names: dict[str, str]) -> Optional[str]:
    """'winback' when it selects past buyers with no order in >= 45 days; 'sunset' when it selects
    profiles with no open/click/site activity in >= 90 days; 'engaged' when it requires recent
    engagement; else None."""
    conds = _conditions(definition)
    bought_before = no_recent_order = unengaged = engaged = False
    for c in conds:
        if c.get("type") != "profile-metric":
            continue
        name = metric_names.get(str(c.get("metric_id")), "").lower()
        mf = c.get("measurement_filter") or {}
        op, val = mf.get("operator"), mf.get("value")
        days = _days(c.get("timeframe_filter") or {})
        zero = op == "equals" and val == 0
        some = (op in ("greater-than",) and (val or 0) >= 0) or (op == "greater-than-or-equal" and (val or 0) >= 1)
        if name in ORDER_TRIGGER_NAMES:
            if some:
                bought_before = True
            if zero and days is not None and days >= WINBACK_MIN_DAYS:
                no_recent_order = True
        if name in ENGAGEMENT_NAMES:
            if zero and days is not None and days >= SUNSET_MIN_DAYS:
                unengaged = True
            if some and days is not None:
                engaged = True
    if bought_before and no_recent_order:
        return "winback"
    if unengaged:
        return "sunset"
    if engaged:
        return "engaged"
    return None


def metric_names(snap: Snapshot) -> dict[str, str]:
    items = snap.at("api", "metrics", "items", default=[]) or []
    return {str(m.get("id")): ((m.get("attributes") or {}).get("name") or "") for m in items}


def metric_integrations(snap: Snapshot) -> dict[str, str]:
    items = snap.at("api", "metrics", "items", default=[]) or []
    return {str(m.get("id")): (((m.get("attributes") or {}).get("integration") or {}).get("name") or "") for m in items}


def metric_ids_named(snap: Snapshot, names: frozenset[str] | set[str]) -> set[str]:
    return {mid for mid, n in metric_names(snap).items() if n.strip().lower() in names}


def subscription_metric_ids(snap: Snapshot) -> set[str]:
    if not snap.has("totals"):
        return set()
    choices = snap.at("totals", "metricChoices", default={}) or {}
    out = set()
    for k, v in choices.items():
        if k.startswith("subscription_") and isinstance(v, dict) and v.get("used"):
            out.add(str(v["used"].get("id")))
    return out


def classify_flows(snap: Snapshot) -> list[FlowInfo]:
    snap.require("api")
    details = snap.at("api", "flows", "details")
    items = {str(f.get("id")): f.get("attributes") or {} for f in snap.at("api", "flows", "items", default=[]) or []}
    names, integ = metric_names(snap), metric_integrations(snap)
    segments = {str(s.get("id")): s for s in snap.at("api", "segments", "items", default=[]) or []}
    subs = subscription_metric_ids(snap)
    recips: dict[str, dict[str, int]] = {}
    if snap.has("perf"):
        try:
            for r in snap.report_results("flow90"):
                g, st = r.get("groupings") or {}, r.get("statistics") or {}
                fid, mid = str(g.get("flow_id")), str(g.get("flow_message_id"))
                recips.setdefault(fid, {})[mid] = recips.get(fid, {}).get(mid, 0) + int(st.get("recipients") or 0)
        except MissingData:
            recips = {}
    have_perf = snap.has("perf") and bool(recips or _report_ok(snap))
    out = []
    for i, fd in enumerate(details):
        flow = fd.get("flow") or {}
        fid = str(flow.get("id") or (fd.get("detail") or {}).get("id"))
        attrs = items.get(fid) or flow.get("attributes") or {}
        defn = fd.get("definition") or ((fd.get("detail") or {}).get("attributes") or {}).get("definition") or {}
        trig = (defn.get("triggers") or [{}])[0] or {}
        ttype = trig.get("type")
        tid = str(trig["id"]) if trig.get("id") is not None else None
        mname = names.get(tid) if ttype == "metric" else None
        minteg = integ.get(tid) if ttype == "metric" else None
        info = FlowInfo(index=i, id=fid, name=attrs.get("name") or "", status=attrs.get("status"),
                        archived=bool(attrs.get("archived")), trigger_type=ttype, trigger_id=tid,
                        trigger_metric=mname, trigger_integration=minteg, kind=None, definition=defn)
        sends = {}
        for j, a in enumerate(_actions(defn)):
            s = _send_of(a, j)
            if s:
                sends[s.action_id] = s
        info.main_path_emails, info.main_path_sms, offsets = _walk(defn, sends)
        for s in sends.values():
            s.offset_days = offsets.get(s.action_id)
        info.sends = sorted(sends.values(), key=lambda s: s.index)
        live_offsets = [s.offset_days for s in info.sends if s.live and s.offset_days is not None]
        info.first_send_days = min(live_offsets) if live_offsets else None
        if have_perf:
            by_msg = recips.get(fid, {})
            info.recipients90_by_message = by_msg
            info.recipients90 = max(by_msg.values()) if by_msg else 0
        info.kind = _kind(info, ttype, tid, mname, minteg, segments, names, subs)
        out.append(info)
    return out


def _report_ok(snap: Snapshot) -> bool:
    try:
        snap.report_results("flow90")
        return True
    except MissingData:
        return False


def _kind(info: FlowInfo, ttype, tid, mname, minteg, segments, names, subs) -> Optional[str]:
    if ttype == "list":
        return "welcome"
    if ttype == "segment":
        k = _segment_kind(segments.get(tid or ""), names)
        if k in ("winback", "sunset"):
            return k
        info.needs_review.append("segment-triggered flow whose segment isn't recognisably winback or sunset")
        return None
    if ttype != "metric" or not mname:
        return None
    if tid in subs:
        return "subscription"
    kind = METRIC_TYPES.get(mname.strip().lower())
    if kind is None:
        return None
    if (minteg or "").lower() == "api":
        info.needs_review.append(f"trigger metric '{mname}' comes from the API integration; confirm its source")
    if kind == "post_purchase":
        if WINBACK_NAME.search(info.name):
            return "winback"
        if REPLENISH_NAME.search(info.name):
            return "replenishment"
        first = info.first_send_days
        if first is not None and first >= WINBACK_MIN_DAYS:
            return "winback"
        if first is not None and first >= REPLENISHMENT_MIN_DAYS:
            return "replenishment"
    return kind


def live_of(snap: Snapshot, kind: str) -> list[FlowInfo]:
    return [f for f in snap.flows() if f.kind == kind and f.classified_live]


# ---------------------------------------------------------------- condition helpers used by checks

def iter_filter_conditions(flow: FlowInfo) -> list[tuple[str, dict, Optional[int]]]:
    """Every condition that gates people in a flow: ('flow', cond, None) for the flow filter,
    ('split', cond, action_index) for conditional splits, ('message', cond, action_index) for a
    send's additional_filters."""
    out: list[tuple[str, dict, Optional[int]]] = []
    for c in _conditions(flow.definition.get("profile_filter") or {}):
        out.append(("flow", c, None))
    for j, a in enumerate(_actions(flow.definition)):
        data = a.get("data") or {}
        if a.get("type") == "conditional-split":
            for c in _conditions(data.get("profile_filter") or {}):
                out.append(("split", c, j))
        msgs = [data.get("message") or {}]
        if a.get("type") == "ab-test":
            msgs.append(((data.get("main_action") or {}).get("data") or {}).get("message") or {})
        for msg in msgs:
            for c in _conditions(msg.get("additional_filters") or {}):
                out.append(("message", c, j))
    return out


def ancestors(flow: FlowInfo) -> dict[str, set[str]]:
    """action id -> every action id that can come before it on some path."""
    parents: dict[str, set[str]] = {}
    for a in _actions(flow.definition):
        for k in _next_ids(a):
            parents.setdefault(k, set()).add(str(a.get("id")))
    memo: dict[str, set[str]] = {}

    def up(node: str, stack: frozenset) -> set[str]:
        if node in memo:
            return memo[node]
        out: set[str] = set()
        for p in parents.get(node, ()):
            if p in stack:
                continue
            out.add(p)
            out |= up(p, stack | {node})
        memo[node] = out
        return out

    return {str(a.get("id")): up(str(a.get("id")), frozenset()) for a in _actions(flow.definition)}


def action_ids(flow: FlowInfo) -> list[str]:
    return [str(a.get("id")) for a in _actions(flow.definition)]


def is_zero_since(cond: dict) -> bool:
    """profile-metric count == 0 (since flow start, in a recent window, or ever)."""
    mf = cond.get("measurement_filter") or {}
    return cond.get("type") == "profile-metric" and mf.get("operator") == "equals" and mf.get("value") == 0


def excludes_metric(flow: FlowInfo, metric_ids: set[str]) -> list[tuple[str, Optional[int]]]:
    """Where the flow excludes people who did one of these metrics (where, action index)."""
    return [(where, j) for where, c, j in iter_filter_conditions(flow)
            if str(c.get("metric_id")) in metric_ids and (is_zero_since(c) or where == "split")]
