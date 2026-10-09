"""Unit tests for the audit checks against small synthetic snapshots (no client data).

base_files() builds one healthy synthetic shop in which every implemented snapshot check passes
(DAT-01 given the confirmed store platform). Each test breaks one thing and asserts the verdict.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import pytest

from wavyos.audit import Snapshot, load_all, run_check
from wavyos.audit.checks import CheckOutcome, check, outcome
from wavyos.audit.flows import classify_flows, segment_kind
from wavyos.audit.library import LibraryError, specs, th, th_range
from wavyos.audit.snapshot import MissingData

CAPTURE = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
CUR = [f"{y}-{m:02d}" for y, m in [(2025, 11), (2025, 12)] + [(2026, k) for k in range(1, 11)]]   # 12 buckets, last partial
PRI = [f"{y}-{m:02d}" for y, m in [(2024, 11), (2024, 12)] + [(2025, k) for k in range(1, 11)]]
SPECS = specs()
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "audit"


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def dates(months):
    return [f"{m}-01T00:00:00+00:00" for m in months]


def window(prior=False):
    return ({"start": "2024-11-01T00:00:00", "end": "2025-11-01T00:00:00"} if prior
            else {"start": "2025-11-01T00:00:00", "end": iso(CAPTURE)})


def agg(measurements: dict, prior: dict | None = None, dims: dict | None = None):
    """measurements: {name: value or [12 values]}; dims: {dimension: {name: values}} for grouped aggregates."""
    def rows(meas):
        return [{"dimensions": [], "measurements": {k: (v if isinstance(v, list) else [v] * 12) for k, v in meas.items()}}]

    def dim_rows(d):
        return [{"dimensions": [k], "measurements": {n: (v if isinstance(v, list) else [v] * 12) for n, v in meas.items()}}
                for k, meas in d.items()]

    out = {"metricId": "X", "current": {"window": window(), "dates": dates(CUR), "rows": dim_rows(dims) if dims else rows(measurements)}}
    if prior is not None:
        out["prior"] = {"window": window(True), "dates": dates(PRI), "rows": rows(prior)}
    return out


# ---------------------------------------------------------------- flows

def email(aid, nxt=None, *, status="live", smart=True, transactional=False, tracking=True, filters=None):
    msg = {"id": f"{aid}-msg", "smart_sending_enabled": smart, "transactional": transactional, "add_tracking_params": tracking}
    if filters:
        msg["additional_filters"] = {"condition_groups": [{"conditions": filters}]}
    return {"id": aid, "type": "send-email", "links": {"next": nxt}, "data": {"status": status, "message": msg}}


def sms(aid, nxt=None, *, opt_out=True, prefix=True, quiet=True):
    return {"id": aid, "type": "send-sms", "links": {"next": nxt},
            "data": {"status": "live", "message": {"id": f"{aid}-msg", "add_opt_out_language": opt_out, "add_org_prefix": prefix,
                                                   "sms_quiet_hours_enabled": quiet, "body": "Hi there"}}}


def delay(aid, days, nxt):
    return {"id": aid, "type": "time-delay", "links": {"next": nxt}, "data": {"unit": "days", "value": days}}


def split(aid, conds, yes, no):
    return {"id": aid, "type": "conditional-split", "links": {"next_if_true": yes, "next_if_false": no},
            "data": {"profile_filter": {"condition_groups": [{"conditions": conds}]}}}


def metric_cond(mid, op="equals", value=0, timeframe="flow-start", days=None):
    tf = {"type": "date", "operator": timeframe}
    if days:
        tf.update(unit="day", quantity=days)
    return {"type": "profile-metric", "metric_id": mid, "measurement": "count",
            "measurement_filter": {"type": "numeric", "operator": op, "value": value}, "timeframe_filter": tf}


def flow(fid, name, trigger, actions, *, status="live", filters=None, reentry=None):
    ttype, tid = trigger
    defn = {"triggers": [{"type": ttype, "id": tid}], "actions": actions, "entry_action_id": actions[0]["id"] if actions else None}
    if filters:
        defn["profile_filter"] = {"condition_groups": [{"conditions": filters}]}
    if reentry:
        defn["reentry_criteria"] = reentry
    attrs = {"name": name, "status": status, "archived": False}
    return {"id": fid, "type": "flow", "attributes": attrs}, {"flow": {"id": fid, "attributes": attrs}, "definition": defn,
                                                               "errors": [], "actionFetch": {"ok": True}}


EXIT = metric_cond("PO1")


def base_flows():
    return [
        flow("F_WEL", "Welcome", ("list", "L1"), [email("w1", "wd1"), delay("wd1", 1, "w2"), email("w2", "wd2"),
                                                  delay("wd2", 2, "w3"), email("w3")], filters=[EXIT]),
        flow("F_CHK", "Checkout", ("metric", "SC1"), [email("c1", "cd1"), delay("cd1", 1, "c2"), email("c2")], filters=[EXIT],
             reentry={"duration": 7, "unit": "day"}),
        flow("F_CART", "Cart", ("metric", "ATC1"), [email("a1", "ad1"), delay("ad1", 1, "a2"), email("a2")],
             filters=[EXIT, metric_cond("SC1")]),
        flow("F_BRW", "Browse", ("metric", "VP1"), [email("b1")], filters=[EXIT, metric_cond("ATC1")],
             reentry={"duration": 14, "unit": "day"}),
        flow("F_PP", "Post purchase", ("metric", "PO1"),
             [split("ps", [metric_cond("PO1", "equals", 1, "alltime")], "p1", "p3"), email("p1", "pd1"),
              delay("pd1", 35, "p2"), email("p2"), email("p3")]),
        flow("F_WB", "Winback", ("metric", "PO1"), [delay("wbd", 70, "wb1"), email("wb1", "wbd2"), delay("wbd2", 7, "wb2"),
                                                    email("wb2")]),
        flow("F_SUN", "Sunset", ("segment", "SEG_UNENG"),
             [email("s1", "sup"), {"id": "sup", "type": "update-profile", "links": {}, "data": {"status": "live"}}]),
    ]


def flow_ids_messages(details):
    for d in details:
        for a in d["definition"]["actions"]:
            if a["type"] in ("send-email", "send-sms"):
                yield d["flow"]["id"], a["data"]["message"]["id"]


# ---------------------------------------------------------------- orders

def base_orders():
    """40 cohort buyers (first order Jan-Feb 2025): 12 come back with these gaps; 20 recent one-off buyers."""
    gaps = [30, 30, 30, 35, 40, 40, 40, 45, 50, 50, 55, 60]
    rows, n = [], 0
    start = datetime(2025, 1, 15, 12, tzinfo=timezone.utc)
    for k in range(40):
        p = f"syn{k:03d}"
        t = start + timedelta(days=k)
        rows.append({"o": f"o{n}", "p": p, "t": t.isoformat(), "v": 50.0, "i": ["Synthetic item"]})
        n += 1
        if k < len(gaps):
            rows.append({"o": f"o{n}", "p": p, "t": (t + timedelta(days=gaps[k])).isoformat(), "v": 50.0, "i": ["Synthetic item"]})
            n += 1
    for k in range(20):
        rows.append({"o": f"o{n}", "p": f"new{k:03d}", "t": datetime(2026, 6, 1 + k, tzinfo=timezone.utc).isoformat(),
                     "v": 40.0, "i": ["Synthetic item"]})
        n += 1
    return {"start": "2024-11-01T00:00:00Z", "end": iso(CAPTURE), "truncated": False, "error": None, "orders": rows}


# ---------------------------------------------------------------- campaigns

def base_campaigns():
    items, c90, c365 = [], [], []
    for k in range(40):   # two a week, Monday and Thursday, for 20 weeks before the capture week
        monday = datetime(2026, 9, 28, 15, tzinfo=timezone.utc) - timedelta(weeks=20 - k // 2)
        t = monday + timedelta(days=0 if k % 2 == 0 else 3)
        cid = f"CMP{k:02d}"
        items.append({"id": cid, "attributes": {
            "name": f"Synthetic campaign {k}", "status": "Sent", "archived": False, "send_time": t.isoformat(),
            "audiences": {"included": ["SEG_ENG"], "excluded": ["SEG_UNENG"]},
            "send_options": {"use_smart_sending": True, "ignore_unsubscribes": False},
            "tracking_options": {"add_tracking_params": True, "is_tracking_clicks": True, "is_tracking_opens": True}}})
        row = {"groupings": {"campaign_id": cid, "campaign_message_id": f"{cid}-m", "send_channel": "email"},
               "statistics": {"recipients": 1000, "delivered": 990, "conversion_value": 250.0, "spam_complaint_rate": 0.0001,
                              "bounce_rate": 0.003, "unsubscribe_rate": 0.001}}
        c365.append(row)
        if t >= CAPTURE - timedelta(days=90):
            c90.append(copy.deepcopy(row))
    # 365-day report: 40 campaigns at 250 each = 10,000 over 40,000 recipients (RPR 0.25); the 90-day subset matches.
    return items, c90, c365


def report(rows):
    return {"ok": True, "value": {"data": {"attributes": {"results": rows}}, "links": {"next": None}}}


# ---------------------------------------------------------------- the whole shop

def _build():
    items, details = zip(*base_flows())
    metrics = [("PO1", "Placed Order", "Shopify"), ("SC1", "Started Checkout", "Shopify"), ("ATC1", "Added to Cart", "Shopify"),
               ("VP1", "Viewed Product", "Shopify"), ("OE1", "Opened Email", "Klaviyo"), ("CE1", "Clicked Email", "Klaviyo"),
               ("AOS1", "Active on Site", "Shopify")]
    segs = {
        "SEG_ENG": [metric_cond("OE1", "greater-than", 0, "in-the-last", 90)],
        "SEG_UNENG": [metric_cond("OE1", "equals", 0, "in-the-last", 180), metric_cond("CE1", "equals", 0, "in-the-last", 180)],
        "SEG_LAPSED": [metric_cond("PO1", "greater-than", 0, "alltime"), metric_cond("PO1", "equals", 0, "in-the-last", 90)],
    }
    camp_items, c90, c365 = base_campaigns()
    f90 = [{"groupings": {"flow_id": fid, "flow_message_id": mid, "send_channel": "email"},
            "statistics": {"recipients": 400, "conversion_value": 8000 / 16}} for fid, mid in flow_ids_messages(details)]
    f365 = [{"groupings": r["groupings"], "statistics": {"recipients": 1600, "conversion_value": 13000 / 16}} for r in f90]
    api = {
        "capturedAt": iso(CAPTURE),
        "account": {"ok": True, "items": [{"attributes": {"timezone": "UTC"}}]},
        "flows": {"truncated": False, "messageCapHit": False, "items": list(items), "details": list(details)},
        "campaigns": {"truncated": False},
        "forms": {"ok": True, "truncated": False, "items": [{"id": "FORM1", "attributes": {"status": "live"}},
                                                            {"id": "FORM2", "attributes": {"status": "draft"}}]},
        "lists": {"ok": True, "truncated": False, "items": [{"id": "L1", "attributes": {}}]},
        "segments": {"ok": True, "truncated": False, "items": [
            {"id": k, "attributes": {"definition": {"condition_groups": [{"conditions": v}]}}} for k, v in segs.items()]},
        "metrics": {"ok": True, "truncated": False, "items": [
            {"id": i, "attributes": {"name": n, "integration": {"name": g}}} for i, n, g in metrics]},
        "templates": {"ok": True, "truncated": False, "items": []},
        "profilesSample": {"ok": True, "truncated": True, "items": []},
    }
    perf = {"capturedAt": iso(CAPTURE), "conversionMetric": {"id": "PO1"}, "placedOrderCandidates": [{"id": "PO1"}],
            "reports": {"flow90": report(f90), "flow365": report(f365), "campaign90": report(c90), "campaign365": report(c365)}}
    used = lambda i, n: {"used": {"id": i, "name": n, "integration": "Shopify"}, "otherCandidates": []}  # noqa: E731
    totals = {
        "capturedAt": iso(CAPTURE),
        "windows": {"current": window(), "prior": window(True)},
        "account": {"timezone": "UTC", "currency": "USD"},
        "metricChoices": {"placed_order": used("PO1", "Placed Order"), "started_checkout": used("SC1", "Started Checkout"),
                          "added_to_cart": used("ATC1", "Added to Cart"), "viewed_product": used("VP1", "Viewed Product"),
                          "received_sms": {"used": None, "otherCandidates": []}},
        "aggregates": {
            "placed_order": agg({"count": 100, "sum_value": 10000.0, "unique": 90}, prior={"count": 90, "sum_value": 9000.0}),
            "placed_order_by_channel": agg({}, dims={"$email_channel": {"count": 30, "sum_value": 3000.0}}),
            "placed_order_by_flow": agg({}, dims={"F_CHK": {"count": 10, "sum_value": 1000.0}}),
            "started_checkout": agg({"count": 500, "unique": 400}),
            "added_to_cart": agg({"count": 900, "unique": 800}),
            "viewed_product": agg({"count": 5000, "unique": 3000}),
            "received_email": agg({"count": 100000, "unique": 50000}),
            "marked_spam": agg({"count": 20}),
            "bounced_email": agg({"count": 300, "unique": 300}),
            "bounced_email_by_type": agg({}, dims={"Hard": {"count": 100}, "Soft": {"count": 200}}),
            "dropped_email": agg({"count": 50}),
            "unsubscribed_email": agg({"count": 150, "unique": 140}),
            "subscribed_email": agg({"count": 1000, "unique": 1000}, prior={"count": 900, "unique": 900}),
            "received_email_by_inbox": agg({}, dims={"Gmail": {"count": 60000}, "Hotmail / Outlook": {"count": 20000},
                                                     "Apple": {"count": 15000}, "Yahoo": {"count": 5000}}),
            "opened_email_by_inbox": agg({}, dims={"Gmail": {"unique": 12000}, "Hotmail / Outlook": {"unique": 4000},
                                                   "Apple": {"unique": 9000}, "Yahoo": {"unique": 1000}}),
            "received_sms": {"notFound": True},
        },
        "campaigns": {"email": {"truncated": False, "items": camp_items}, "sms": {"truncated": False, "count": 0, "items": []}},
        "orders": {"truncated": False, "error": None},
        "reports": {"formValues365": {"data": {"attributes": {"results": [
            {"groupings": {"form_id": "FORM1", "form_version_id": "1"}, "statistics": {"viewed_form_uniques": 6000, "submits": 300}},
            {"groupings": {"form_id": "FORM1", "form_version_id": "2"}, "statistics": {"viewed_form_uniques": 4000, "submits": 200}},
            {"groupings": {"form_id": "FORM2", "form_version_id": "3"}, "statistics": {"viewed_form_uniques": 9000, "submits": 9}}]}}}},
        "errors": [], "catalog": {"truncated": False}, "coupons": {"truncated": False},
    }
    templates = {"capturedAt": iso(CAPTURE), "errors": 0, "overGmailClipThreshold": 0, "templates": [
        {"templateId": f"T{fid}", "htmlBytes": 50000, "textChars": 1200, "imageCount": 2, "linkCount": 5,
         "messages": [{"flowId": fid, "messageId": mid}]} for fid, mid in flow_ids_messages(details)]}
    run = {"account": {"timezone": "UTC"}, "history": [{"ranAt": iso(CAPTURE), "steps": [
        {"file": f, "ok": True, "code": 0} for f in ("24-fetch-account-audit-api.js", "34-fetch-performance-cross-review.js",
                                                     "37-audit-live-template-weight.js", "38-fetch-account-totals.js")]}]}
    return {"run.json": run, "account-audit-api.json": api, "performance-cross-review.json": perf,
            "live-template-weight-audit.json": templates, "account-totals.json": totals, "placed-orders-24mo.json": base_orders()}


@lru_cache(maxsize=1)
def _base_json() -> str:
    return json.dumps(_build())


def base_files() -> dict:
    return json.loads(_base_json())


CONTEXT = {"store_platform": "Shopify"}


def snap_of(files: dict, context: dict | None = None) -> Snapshot:
    return Snapshot.from_dicts(files, context=CONTEXT if context is None else context)


def run(check_id: str, files: dict | None = None, context: dict | None = None) -> CheckOutcome:
    return run_check(check_id, snap_of(base_files() if files is None else files, context), SPECS[check_id])


def detail(files, fid):
    return next(d for d in files["account-audit-api.json"]["flows"]["details"] if d["flow"]["id"] == fid)


def set_actions(files, fid, actions):
    d = detail(files, fid)["definition"]
    d["actions"], d["entry_action_id"] = actions, actions[0]["id"]
    return files


def drop_flow(files, fid):
    fl = files["account-audit-api.json"]["flows"]
    fl["details"] = [d for d in fl["details"] if d["flow"]["id"] != fid]
    fl["items"] = [i for i in fl["items"] if i["id"] != fid]
    return files


def set_series(files, key, measurement, values, window_="current", dim=None):
    w = files["account-totals.json"]["aggregates"][key][window_]
    row = w["rows"][0] if dim is None else next(r for r in w["rows"] if r["dimensions"][0] == dim)
    row["measurements"][measurement] = values if isinstance(values, list) else [values] * 12
    return files


def set_recipients(files, fid, n, report_="flow90"):
    for r in files["performance-cross-review.json"]["reports"][report_]["value"]["data"]["attributes"]["results"]:
        if r["groupings"]["flow_id"] == fid:
            r["statistics"]["recipients"] = n
    return files


IMPLEMENTED = sorted(c for c, s in SPECS.items() if "snapshot" in s["tracks"] and s["status"] == "data-available")


# ================================================================= the baseline itself

@pytest.mark.parametrize("check_id", IMPLEMENTED)
def test_every_snapshot_check_passes_on_the_healthy_shop(check_id):
    out = run(check_id)
    expected = {"RET-03": "na", "RET-04": "na", "SMS-02": "na", "SMS-03": "na", "SMS-04": "na"}.get(check_id, "pass")
    assert out.verdict == expected, f"{check_id}: {out.verdict}: {out.message}"
    if out.verdict == "pass":
        assert out.evidence and all(e["ref"] for e in out.evidence)


def test_all_snapshot_data_checks_are_registered():
    reg = load_all()
    assert set(IMPLEMENTED) <= set(reg)


def test_static_fixture_folder_matches_builder():
    """tests/fixtures/audit/synthetic-shop is the builder's shop without a cart flow (engine tests use it)."""
    folder = FIXTURE_DIR / "synthetic-shop"
    want = drop_flow(base_files(), "F_CART")
    for name, data in want.items():
        assert json.loads((folder / name).read_text()) == data, f"regenerate {name}"


# ================================================================= library and registry plumbing

def test_thresholds_are_read_from_the_library():
    assert th(SPECS["FLW-08"], "pass", r">= {n}%", 0.01) == pytest.approx(0.35)
    assert th(SPECS["CRV-01"], "pass", r"> {n} bytes") == 102400
    assert th_range(SPECS["FLW-06"], "pass", r"{n}-{n}x median") == (1.5, 2.0)
    with pytest.raises(LibraryError):
        th(SPECS["FLW-08"], "pass", r"no such {n} text")


def test_changed_library_threshold_changes_the_verdict():
    spec = copy.deepcopy(SPECS["FLW-08"])
    spec["thresholds"]["pass"] = "flow share >= 60%"
    out = run_check("FLW-08", snap_of(base_files()), spec)
    assert out.verdict == "warn"  # the synthetic shop's 56% share now misses the pass bar


def test_missing_file_is_na_with_reason_and_crash_is_error():
    files = base_files()
    del files["performance-cross-review.json"]
    out = run("FLW-08", files)
    assert out.verdict == "na" and "performance-cross-review.json" in out.message

    @check("ZZZ-01")
    def broken(snap, spec):
        raise RuntimeError("boom")
    assert run_check("ZZZ-01", snap_of(base_files()), {}).verdict == "error"
    from wavyos.audit.checks import REGISTRY
    REGISTRY.pop("ZZZ-01")
    assert run_check("ZZZ-99", snap_of(base_files()), {}).verdict == "not_implemented"


def test_outcome_rejects_bad_verdicts_and_evidence():
    with pytest.raises(ValueError):
        outcome("great", "x")
    with pytest.raises(ValueError):
        CheckOutcome("pass", {}, [{"kind": "x"}], "")


def test_snapshot_months_flag_partial_and_pre_window_buckets():
    files = base_files()
    snap = snap_of(files)
    b = snap.monthly("placed_order")
    assert [x.full for x in b] == [True] * 11 + [False]
    # A bucket that starts before the window (old collector boundary) isn't full either.
    w = files["account-totals.json"]["aggregates"]["placed_order"]["current"]
    w["dates"] = dates(["2025-10"] + CUR[:-1])
    assert [x.full for x in snap_of(files).monthly("placed_order")][0] is False
    with pytest.raises(MissingData):
        snap.monthly("received_sms")


def test_order_history_dedupes_and_counts_null_profiles():
    files = base_files()
    rows = files["placed-orders-24mo.json"]["orders"]
    rows.append(dict(rows[0]))                                  # duplicate order id
    rows.append({"o": "x1", "p": None, "t": rows[0]["t"], "i": []})
    hist = snap_of(files).order_history()
    assert hist.duplicates == 1 and hist.null_profile == 1
    assert len(hist.by_profile) == 60


def test_classifier_types_and_paths():
    flows = {f.id: f for f in classify_flows(snap_of(base_files()))}
    assert {k: f.kind for k, f in flows.items()} == {
        "F_WEL": "welcome", "F_CHK": "abandoned_checkout", "F_CART": "abandoned_cart", "F_BRW": "browse",
        "F_PP": "post_purchase", "F_WB": "winback", "F_SUN": "sunset"}
    assert flows["F_WEL"].main_path_emails == 3
    assert flows["F_PP"].main_path_emails == 2          # the split's longer branch
    assert flows["F_WB"].first_send_days == 70
    assert flows["F_WEL"].recipients90 == 400


def test_order_trigger_becomes_replenishment_by_delay_and_custom_api_is_flagged():
    files = base_files()
    set_actions(files, "F_PP", [delay("rd", 25, "r1"), email("r1")])
    files["account-audit-api.json"]["metrics"]["items"][3]["attributes"]["integration"]["name"] = "API"
    flows = {f.id: f for f in classify_flows(snap_of(files))}
    assert flows["F_PP"].kind == "replenishment"
    assert flows["F_BRW"].needs_review


def test_segment_kinds():
    names = {"PO1": "Placed Order", "OE1": "Opened Email"}
    cg = lambda *c: {"condition_groups": [{"conditions": list(c)}]}  # noqa: E731
    assert segment_kind(cg(metric_cond("OE1", "equals", 0, "in-the-last", 120)), names) == "sunset"
    assert segment_kind(cg(metric_cond("OE1", "equals", 0, "in-the-last", 30)), names) is None
    assert segment_kind(cg(metric_cond("PO1", "greater-than", 0, "alltime"),
                           metric_cond("PO1", "equals", 0, "in-the-last", 60)), names) == "winback"
    assert segment_kind(cg(metric_cond("OE1", "greater-than", 0, "in-the-last", 30)), names) == "engaged"


# ================================================================= data (DAT)

def test_dat01_order_source():
    assert run("DAT-01", context={}).verdict == "warn"                       # platform not confirmed
    files = base_files()
    files["account-totals.json"]["metricChoices"]["placed_order"]["otherCandidates"] = [{"id": "PO2", "integration": "Magento 2"}]
    assert run("DAT-01", files).verdict == "warn"
    assert run("DAT-01", context={"store_platform": "WooCommerce"}).verdict == "fail"
    files = base_files()
    files["performance-cross-review.json"]["conversionMetric"]["id"] = "PO2"
    assert run("DAT-01", files).verdict == "fail"
    files = base_files()
    set_series(files, "placed_order", "count", [100] * 10 + [0, 5])
    out = run("DAT-01", files)
    assert out.verdict == "fail" and "2026-09" in out.message


def test_dat02_trigger_metrics():
    files = base_files()
    set_series(files, "started_checkout", "count", [500] * 9 + [0, 0, 0])
    assert run("DAT-02", files).verdict == "fail"
    files = base_files()
    files["account-totals.json"]["metricChoices"]["started_checkout"]["used"]["id"] = "SC_OTHER"
    assert run("DAT-02", files).verdict == "warn"
    files = base_files()
    files["account-audit-api.json"]["metrics"]["items"].append({"id": "CUST", "attributes": {"name": "revamp_checkout",
                                                                                              "integration": {"name": "API"}}})
    detail(files, "F_CHK")["definition"]["triggers"][0]["id"] = "CUST"
    assert run("DAT-02", files).verdict == "warn"


def test_dat03_line_items():
    files = base_files()
    rows = files["placed-orders-24mo.json"]["orders"]
    recent = [r for r in rows if r["t"] >= "2025-10-02"]
    for r in recent[: len(recent) // 5]:
        r["i"] = []
    assert run("DAT-03", files).verdict == "warn"                              # ~80% with items
    for r in recent:
        r["i"] = []
    assert run("DAT-03", files).verdict == "fail"


def test_dat04_timezone_and_buckets():
    files = base_files()
    files["account-audit-api.json"]["account"]["items"][0]["attributes"]["timezone"] = "US/Eastern"
    assert run("DAT-04", files).verdict == "fail"
    files = base_files()
    files["account-totals.json"]["aggregates"]["marked_spam"]["current"]["dates"][3] = "2026-02-01T05:00:00+00:00"
    assert run("DAT-04", files).verdict == "fail"
    out = run("DAT-04")
    assert out.value["partial_buckets"] == ["2026-10"]


def test_dat05_completeness():
    files = base_files()
    files["account-totals.json"]["errors"] = [{"label": "flow series weekly 365d"}]
    assert run("DAT-05", files).verdict == "warn"
    files["account-audit-api.json"]["flows"]["truncated"] = True
    out = run("DAT-05", files)
    assert out.verdict == "fail" and "flows truncated" in out.message
    files = base_files()
    del files["placed-orders-24mo.json"]
    assert run("DAT-05", files).verdict == "fail"
    assert run("DAT-05", {}).verdict == "na"


def test_dat06_reconciliation():
    files = base_files()
    set_series(files, "placed_order_by_channel", "sum_value", 5500.0, dim="$email_channel")
    assert run("DAT-06", files).verdict == "warn"                              # share 55%
    set_series(files, "placed_order_by_channel", "sum_value", [3000.0] * 5 + [12000.0] + [3000.0] * 6, dim="$email_channel")
    assert run("DAT-06", files).verdict == "fail"                              # one month above total
    files = base_files()
    for r in files["performance-cross-review.json"]["reports"]["flow365"]["value"]["data"]["attributes"]["results"]:
        r["statistics"]["conversion_value"] *= 2
    assert run("DAT-06", files).verdict == "fail"                              # reports disagree > 30%


# ================================================================= capture (CAP)

def test_cap01_forms():
    files = base_files()
    res = files["account-totals.json"]["reports"]["formValues365"]["data"]["attributes"]["results"]
    res[0]["statistics"]["submits"], res[1]["statistics"]["submits"] = 120, 80   # 2%
    assert run("CAP-01", files).verdict == "warn"
    res[0]["statistics"]["submits"], res[1]["statistics"]["submits"] = 60, 40    # 1%
    assert run("CAP-01", files).verdict == "fail"
    files = base_files()
    files["account-audit-api.json"]["forms"]["items"][0]["attributes"]["status"] = "draft"
    assert run("CAP-01", files).verdict == "fail"


def test_cap02_list_growth():
    files = base_files()
    set_series(files, "unsubscribed_email", "unique", [140] * 9 + [1200, 140, 140])
    assert run("CAP-02", files).verdict == "warn"                              # one negative month
    files = base_files()
    set_series(files, "subscribed_email", "unique", 2000, window_="prior")
    assert run("CAP-02", files).verdict == "warn"                              # gross down exactly 50%
    set_series(files, "subscribed_email", "unique", 2500, window_="prior")
    assert run("CAP-02", files).verdict == "fail"
    files = base_files()
    set_series(files, "unsubscribed_email", "unique", 1500)
    assert run("CAP-02", files).verdict == "fail"                              # net negative


# ================================================================= flows (FLW)

def test_flw01_welcome():
    files = set_recipients(base_files(), "F_WEL", 0)
    assert run("FLW-01", files).verdict == "warn"
    files = base_files()
    set_actions(files, "F_WEL", [email("w1")])
    assert run("FLW-01", files).verdict == "warn"
    assert run("FLW-01", drop_flow(base_files(), "F_WEL")).verdict == "fail"
    files = base_files()
    detail(files, "F_WEL")["flow"]["attributes"]["status"] = "draft"
    files["account-audit-api.json"]["flows"]["items"][0]["attributes"]["status"] = "draft"
    out = run("FLW-01", files)
    assert out.verdict == "fail" and "non-live" in out.message


def test_flw02_checkout():
    files = set_recipients(base_files(), "F_CHK", 200)                         # 200 / 1200 = 17%
    assert run("FLW-02", files).verdict == "warn"
    files = set_recipients(base_files(), "F_CHK", 60)                          # 5%
    assert run("FLW-02", files).verdict == "fail"
    files = base_files()
    set_actions(files, "F_CHK", [email("c1")])
    assert run("FLW-02", files).verdict == "warn"
    assert run("FLW-02", drop_flow(base_files(), "F_CHK")).verdict == "fail"


def test_flw03_cart():
    files = base_files()
    detail(files, "F_CART")["definition"]["profile_filter"] = {"condition_groups": [{"conditions": [EXIT]}]}
    assert run("FLW-03", files).verdict == "warn"
    files = base_files()
    set_actions(files, "F_CART", [email("a1", filters=[metric_cond("SC1")])])
    assert run("FLW-03", files).verdict == "warn"                              # message-level exclusion, 1 email
    assert run("FLW-03", drop_flow(base_files(), "F_CART")).verdict == "fail"


def test_flw04_browse():
    files = base_files()
    detail(files, "F_BRW")["definition"]["reentry_criteria"] = {"duration": 1, "unit": "day"}
    assert run("FLW-04", files).verdict == "warn"
    files = base_files()
    detail(files, "F_BRW")["definition"]["reentry_criteria"] = {"duration": 0, "unit": "alltime"}
    assert run("FLW-04", files).verdict == "pass"
    files = base_files()
    detail(files, "F_BRW")["definition"]["profile_filter"] = {"condition_groups": [{"conditions": [EXIT]}]}
    assert run("FLW-04", files).verdict == "warn"
    assert run("FLW-04", drop_flow(base_files(), "F_BRW")).verdict == "fail"


def test_flw05_post_purchase():
    files = base_files()
    set_actions(files, "F_PP", [email("p1", "pd1"), delay("pd1", 3, "p2"), email("p2")])
    assert run("FLW-05", files).verdict == "warn"                              # one path for everyone
    files = base_files()
    set_actions(files, "F_PP", [split("ps", [metric_cond("PO1", "equals", 1, "alltime")], "p1", None), email("p1")])
    assert run("FLW-05", files).verdict == "warn"                              # one email
    assert run("FLW-05", drop_flow(base_files(), "F_PP")).verdict == "fail"


def test_flw06_winback():
    files = base_files()
    d = detail(files, "F_WB")
    d["definition"]["actions"][0]["data"]["value"] = 20                       # name says winback; 20 days is too early
    assert run("FLW-06", files).verdict == "warn"
    files = set_recipients(base_files(), "F_WB", 0)
    assert run("FLW-06", files).verdict == "warn"
    assert run("FLW-06", drop_flow(base_files(), "F_WB")).verdict == "fail"
    files = base_files()
    del files["placed-orders-24mo.json"]
    assert run("FLW-06", files).verdict == "na"                                # presence known, timing not


def test_flw07_sunset():
    files = base_files()
    set_actions(files, "F_SUN", [email("s1")])
    assert run("FLW-07", files).verdict == "warn"                              # no suppression step
    files = drop_flow(base_files(), "F_SUN")
    assert run("FLW-07", files).verdict == "warn"                              # campaigns exclude the unengaged segment
    for c in files["account-totals.json"]["campaigns"]["email"]["items"]:
        c["attributes"]["audiences"]["excluded"] = []
    assert run("FLW-07", files).verdict == "fail"


def _scale_report(files, rep, factor):
    for r in files["performance-cross-review.json"]["reports"][rep]["value"]["data"]["attributes"]["results"]:
        r["statistics"]["conversion_value"] *= factor


def test_flw08_flow_share():
    files = base_files()
    _scale_report(files, "flow365", 0.5)                                      # 6.5k vs 10k campaigns: 39%; 90d share 55%
    assert run("FLW-08", files).verdict == "pass"
    _scale_report(files, "flow90", 0.2)                                       # 90d share drops to ~19%
    assert run("FLW-08", files).verdict == "warn"
    files = base_files()
    _scale_report(files, "flow365", 0.15)                                     # ~16%
    assert run("FLW-08", files).verdict == "fail"


def test_flw09_purchase_exit():
    files = base_files()
    detail(files, "F_CHK")["definition"].pop("profile_filter")
    assert run("FLW-09", files).verdict == "fail"
    files = base_files()
    d = detail(files, "F_CHK")
    d["definition"].pop("profile_filter")
    set_actions(files, "F_CHK", [email("c1", "cd1", filters=[EXIT]), delay("cd1", 1, "c2"), email("c2")])
    assert run("FLW-09", files).verdict == "warn"                              # only the first send is guarded
    files = base_files()
    files["account-audit-api.json"]["metrics"]["items"].append({"id": "PO2", "attributes": {"name": "Placed Order",
                                                                                            "integration": {"name": "Magento 2"}}})
    detail(files, "F_CHK")["definition"]["profile_filter"] = {"condition_groups": [{"conditions": [metric_cond("PO2")]}]}
    assert run("FLW-09", files).verdict == "warn"                              # non-canonical order metric
    files = base_files()
    d = detail(files, "F_CHK")
    d["definition"].pop("profile_filter")
    set_actions(files, "F_CHK", [split("g", [metric_cond("PO1", "greater-than", 0)], None, "c1"), email("c1")])
    assert run("FLW-09", files).verdict == "pass"                              # split before every send


def test_flw10_guardrails():
    files = base_files()
    d = detail(files, "F_WEL")
    d["definition"]["actions"][2]["data"]["message"]["transactional"] = True
    assert run("FLW-10", files).verdict == "fail"
    files = base_files()
    for fid in ("F_BRW", "F_SUN"):
        for a in detail(files, fid)["definition"]["actions"]:
            if a["type"] == "send-email":
                a["data"]["message"]["smart_sending_enabled"] = False
    out = run("FLW-10", files)
    assert out.verdict == "warn", out.message                                  # 10 of 12 -> 83%
    for a in detail(files, "F_WEL")["definition"]["actions"]:
        if a["type"] == "send-email":
            a["data"]["message"]["smart_sending_enabled"] = False
    assert run("FLW-10", files).verdict == "fail"                              # 7 of 12 -> 58%
    files = base_files()
    detail(files, "F_WEL")["definition"]["actions"].append(email("w4", status="draft"))
    assert run("FLW-10", files).verdict == "warn"


# ================================================================= campaigns (CMP)

def _campaigns(files):
    return files["account-totals.json"]["campaigns"]["email"]["items"]


def test_cmp01_cadence():
    files = base_files()
    items = _campaigns(files)
    for c in items[-26::2]:                                                    # keep one a week for the last 13 weeks
        c["attributes"]["status"] = "Draft"
    assert run("CMP-01", files).verdict == "warn"
    for c in items[-26:]:
        c["attributes"]["status"] = "Draft"
    for c in items[-26::4]:
        c["attributes"]["status"] = "Sent"                                     # ~0.5 a week
    assert run("CMP-01", files).verdict == "fail"
    files = base_files()
    for c in list(_campaigns(files)):                                          # A/B copies on the same day don't count twice
        c2 = copy.deepcopy(c)
        c2["attributes"]["name"] += " (B)"
        _campaigns(files).append(c2)
    assert run("CMP-01", files).value["per_week"] == pytest.approx(2.0)


def test_cmp02_rpr():
    files = base_files()
    _scale_report(files, "campaign90", 0.7)
    assert run("CMP-02", files).verdict == "warn"
    _scale_report(files, "campaign90", 0.5)
    assert run("CMP-02", files).verdict == "fail"


def test_cmp03_targeting():
    files = base_files()
    recent = [c for c in _campaigns(files) if c["attributes"]["send_time"] >= "2026-07-04"]
    for c in recent[: len(recent) * 3 // 10]:
        c["attributes"]["audiences"] = {"included": ["L1"], "excluded": []}
    assert run("CMP-03", files).verdict == "warn"
    for c in recent:
        c["attributes"]["audiences"] = {"included": ["L1"], "excluded": []}
    assert run("CMP-03", files).verdict == "fail"


def test_cmp04_settings():
    files = base_files()
    _campaigns(files)[5]["attributes"]["send_options"]["ignore_unsubscribes"] = True
    assert run("CMP-04", files).verdict == "fail"
    files = base_files()
    for c in _campaigns(files)[:20]:
        c["attributes"]["send_options"]["use_smart_sending"] = False
    assert run("CMP-04", files).verdict == "warn"


# ================================================================= deliverability (DLV)

def _set_campaign_stat(files, stat, value, first_n=1):
    rows = files["performance-cross-review.json"]["reports"]["campaign90"]["value"]["data"]["attributes"]["results"]
    for r in rows[:first_n]:
        r["statistics"][stat] = value


def test_dlv01_spam():
    files = set_series(base_files(), "marked_spam", "count", [20] * 4 + [150] + [20] * 7)   # 0.15% one month
    assert run("DLV-01", files).verdict == "warn"
    files = set_series(base_files(), "marked_spam", "count", [20] * 4 + [400] + [20] * 7)   # 0.4%
    assert run("DLV-01", files).verdict == "fail"
    files = base_files()
    _set_campaign_stat(files, "spam_complaint_rate", 0.004)                   # one campaign at 0.4%
    assert run("DLV-01", files).verdict == "warn"


def test_dlv02_bounces():
    f = set_series(base_files(), "bounced_email", "count", [300] * 3 + [1500] + [300] * 8)    # ~1.5%
    assert run("DLV-02", f).verdict == "warn"
    f = set_series(base_files(), "bounced_email", "count", [300] * 3 + [2500] + [300] * 8)    # ~2.4%
    assert run("DLV-02", f).verdict == "fail"
    f = set_series(base_files(), "bounced_email_by_type", "count", [100] * 3 + [600] + [100] * 8, dim="Hard")
    assert run("DLV-02", f).verdict == "fail"                                                 # hard ~0.6%
    f = base_files()
    _set_campaign_stat(f, "bounce_rate", 0.05)
    assert run("DLV-02", f).verdict == "warn"


def test_dlv03_unsubscribes():
    f = set_series(base_files(), "unsubscribed_email", "count", [150] * 6 + [400] + [150] * 5)   # 0.4%
    assert run("DLV-03", f).verdict == "warn"
    f = set_series(base_files(), "unsubscribed_email", "count", [150] * 6 + [700] + [150] * 5)   # 0.7%
    assert run("DLV-03", f).verdict == "fail"
    f = base_files()
    n = len(f["performance-cross-review.json"]["reports"]["campaign90"]["value"]["data"]["attributes"]["results"])
    _set_campaign_stat(f, "unsubscribe_rate", 0.008, first_n=max(1, n // 5))
    assert run("DLV-03", f).verdict == "warn"


def test_dlv04_inbox_providers():
    f = set_series(base_files(), "opened_email_by_inbox", "unique", 2400, dim="Hotmail / Outlook")   # 12% vs ~18% avg
    assert run("DLV-04", f).verdict == "warn"
    f = set_series(base_files(), "opened_email_by_inbox", "unique", 1000, dim="Hotmail / Outlook")   # 5%
    assert run("DLV-04", f).verdict == "fail"
    f = set_series(base_files(), "opened_email_by_inbox", "unique", [4000] * 10 + [2000, 2000], dim="Hotmail / Outlook")
    assert run("DLV-04", f).verdict == "fail"                                  # 50% month-over-month drop
    f = set_series(base_files(), "opened_email_by_inbox", "unique", 100, dim="Apple")
    assert run("DLV-04", f).verdict == "pass"                                  # Apple isn't compared


# ================================================================= retention (RET)

def _orders(files):
    return files["placed-orders-24mo.json"]["orders"]


def _keep_repeaters(files, n):
    """The cohort is 40 first buyers, 12 of whom order again; keep only the first n second orders."""
    firsts, keep = set(), []
    for r in _orders(files):
        if r["p"].startswith("syn") and r["p"] in firsts:
            if int(r["p"][3:]) >= n:
                continue
        firsts.add(r["p"])
        keep.append(r)
    files["placed-orders-24mo.json"]["orders"] = keep
    return files


def test_ret01_repeat_rate():
    assert run("RET-01").value["repeat_rate"] == pytest.approx(12 / 40)
    assert run("RET-01", _keep_repeaters(base_files(), 8)).verdict == "warn"     # 20%
    assert run("RET-01", _keep_repeaters(base_files(), 4)).verdict == "fail"     # 10%
    files = base_files()
    files["placed-orders-24mo.json"]["orders"] = [r for r in _orders(files) if r["p"].startswith("new")]
    assert run("RET-01", files).verdict == "na"                                # nobody has 12 months of follow-up


def test_ret02_timing():
    files = base_files()
    detail(files, "F_PP")["definition"]["actions"][2]["data"]["value"] = 55   # second email at day 55 (median 40)
    assert run("RET-02", files).verdict == "warn"
    detail(files, "F_PP")["definition"]["actions"][2]["data"]["value"] = 90
    assert run("RET-02", files).verdict == "fail"


def _with_subscriptions(files, names_counts):
    t = files["account-totals.json"]
    for i, (name, counts) in enumerate(names_counts):
        key = f"subscription_SUB{i}"
        t["metricChoices"][key] = {"used": {"id": f"SUB{i}", "name": name, "integration": "Synthetic Subs"}}
        t["aggregates"][key] = agg({"count": counts, "unique": counts})
        files["account-audit-api.json"]["metrics"]["items"].append({"id": f"SUB{i}", "attributes": {
            "name": name, "integration": {"name": "Synthetic Subs"}}})
    return files


def test_ret03_subscription_events():
    full = [("Subscription Started", 50), ("Subscription Cancelled", 20), ("Subscription Renewed", 300)]
    assert run("RET-03", _with_subscriptions(base_files(), full)).verdict == "pass"
    assert run("RET-03", _with_subscriptions(base_files(), full[:2])).verdict == "warn"
    zero = [(n, 0) for n, _ in full]
    assert run("RET-03", _with_subscriptions(base_files(), zero)).verdict == "na"
    assert run("RET-03", _with_subscriptions(base_files(), zero), {"sells_subscriptions": True}).verdict == "fail"
    assert run("RET-03", _with_subscriptions(base_files(), full), {"sells_subscriptions": False}).verdict == "na"


def test_ret04_subscription_flows():
    full = [("Subscription Started", 50), ("Subscription Cancelled", 20), ("Subscription Renewed", 300)]
    files = _with_subscriptions(base_files(), full)
    assert run("RET-04", files).verdict == "fail"
    items, details = zip(*[flow("F_SC", "Cancel save", ("metric", "SUB1"), [email("sc1")])])
    files["account-audit-api.json"]["flows"]["items"] += list(items)
    files["account-audit-api.json"]["flows"]["details"] += list(details)
    assert run("RET-04", files).verdict == "warn"
    assert run("RET-04", files, {"subscription_native_messages": ["upcoming"]}).verdict == "pass"


# ================================================================= creative (CRV)

def _templates(files):
    return files["live-template-weight-audit.json"]["templates"]


def test_crv01_clipping():
    files = base_files()
    next(t for t in _templates(files) if t["messages"][0]["flowId"] == "F_CHK")["htmlBytes"] = 110000
    assert run("CRV-01", files).verdict == "fail"
    files = base_files()
    _templates(files)[0]["htmlBytes"] = 95000
    assert run("CRV-01", files).verdict == "warn"
    files = base_files()
    _templates(files)[0].update(htmlBytes=None, error={"message": "template failed (422)"})
    assert run("CRV-01", files).verdict == "na"


def test_crv02_image_heavy():
    files = base_files()
    heavy = [t for t in _templates(files) if t["messages"][0]["flowId"] in ("F_WB", "F_SUN")][:2]
    for t in heavy:
        t.update(textChars=50, imageCount=6)
    assert run("CRV-02", files).verdict == "warn"                              # 2 of 16 = 12.5%
    files = base_files()
    next(t for t in _templates(files) if t["messages"][0]["messageId"] == "w1-msg").update(textChars=50, imageCount=6)
    assert run("CRV-02", files).verdict == "fail"                              # the welcome first email
    files = base_files()
    for t in _templates(files)[:6]:
        t.update(textChars=50, imageCount=6)
    assert run("CRV-02", files).verdict == "fail"


def test_crv04_utms():
    files = base_files()
    for c in _campaigns(files)[-6:]:
        c["attributes"]["tracking_options"]["add_tracking_params"] = False
    assert run("CRV-04", files).verdict == "warn"
    files = base_files()
    _campaigns(files)[-1]["attributes"]["tracking_options"]["is_tracking_clicks"] = False
    assert run("CRV-04", files).verdict == "fail"


# ================================================================= SMS

def test_sms02_flow_sms_compliance():
    assert run("SMS-02").verdict == "na"
    files = base_files()
    d = detail(files, "F_WEL")
    d["definition"]["actions"][-1]["links"]["next"] = "t1"
    d["definition"]["actions"].append(sms("t1"))
    assert run("SMS-02", files).verdict == "pass"
    d["definition"]["actions"][-1] = sms("t1", prefix=False)
    assert run("SMS-02", files).verdict == "warn"
    d["definition"]["actions"][-1] = sms("t1", quiet=False)
    assert run("SMS-02", files).verdict == "fail"


def _with_klaviyo_sms(files, received=1000, unsub=10, failed=10, sms_revenue=500.0):
    t = files["account-totals.json"]
    t["metricChoices"]["received_sms"] = {"used": {"id": "RS1", "name": "Received SMS", "integration": "Klaviyo"}}
    t["aggregates"]["received_sms"] = agg({"count": received})
    t["aggregates"]["unsubscribed_sms"] = agg({"count": unsub})
    t["aggregates"]["failed_sms"] = agg({"count": failed})
    rows = t["aggregates"]["placed_order_by_channel"]["current"]["rows"]
    if sms_revenue is not None:
        rows.append({"dimensions": ["$sms_channel"], "measurements": {"count": [5] * 12, "sum_value": (
            sms_revenue if isinstance(sms_revenue, list) else [sms_revenue] * 12)}})
    return files


def test_sms03_optout_and_failure():
    assert run("SMS-03").verdict == "na"
    assert run("SMS-03", _with_klaviyo_sms(base_files())).verdict == "pass"
    assert run("SMS-03", _with_klaviyo_sms(base_files(), unsub=20)).verdict == "warn"    # 2%
    assert run("SMS-03", _with_klaviyo_sms(base_files(), unsub=40)).verdict == "fail"    # 4%
    assert run("SMS-03", _with_klaviyo_sms(base_files(), failed=70)).verdict == "fail"   # ~6.5%
    assert run("SMS-03", _with_klaviyo_sms(base_files(), received=0)).verdict == "na"


def test_sms04_sms_revenue():
    assert run("SMS-04").verdict == "na"
    assert run("SMS-04", _with_klaviyo_sms(base_files())).verdict == "pass"
    assert run("SMS-04", _with_klaviyo_sms(base_files(), sms_revenue=[500.0] * 9 + [0.0, 500.0, 500.0])).verdict == "warn"
    assert run("SMS-04", _with_klaviyo_sms(base_files(), sms_revenue=None)).verdict == "fail"


def write_fixture_folder(folder: Path = FIXTURE_DIR / "synthetic-shop") -> None:
    """Regenerate the static engine fixture: python -c 'import test_audit_checks as t; t.write_fixture_folder()'."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in drop_flow(base_files(), "F_CART").items():
        (folder / name).write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
