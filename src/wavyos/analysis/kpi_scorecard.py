"""The brand's own scorecard: the numbers it judges Wavy on, month by month, against last year.

Opens the workspace and every monthly report. Which KPIs lead comes from the questionnaire
(context['kpis']); every KPI is still computed so a later change of mind needs no re-run.
"""
from __future__ import annotations

from datetime import timedelta

from ..audit.snapshot import MissingData, Snapshot, ref
from .base import Analysis, pct, ratio, usd
from .series import BURN_IN_DAYS, first_orders, monthly, orders_rows, try_monthly, year_ago

MODULE = "kpi_scorecard"
# questionnaire label -> kpi key
KPI_KEYS = {"Gross revenue": "gross_revenue", "Net revenue": "net_revenue",
            "Email revenue (campaigns + flows)": "email_revenue", "SMS revenue": "sms_revenue",
            "Total buyers": "buyers", "New customers": "new_customers", "Average order value": "aov",
            "Repeat purchase rate": "repeat_rate", "Email list size": "net_subscribers", "Pop-up sign-ups": "signups"}
LABELS = {"gross_revenue": ("Gross revenue", "USD"), "net_revenue": ("Net revenue (after refunds)", "USD"),
          "email_revenue": ("Email-attributed revenue", "USD"), "sms_revenue": ("SMS-attributed revenue", "USD"),
          "orders": ("Orders", "orders"), "buyers": ("Buyers", "profiles"),
          "new_customers": ("New customers (first observed)", "profiles"), "aov": ("Average order value", "USD"),
          "net_subscribers": ("Net new email subscribers", "profiles"), "signups": ("Form sign-ups", "submits")}
DEFAULT_LEAD = ["gross_revenue", "email_revenue", "buyers", "new_customers", "net_subscribers"]


def _series(snap: Snapshot, window: str) -> dict[str, dict[str, float]]:
    s: dict[str, dict[str, float]] = {}
    for key, agg, meas, dim in (("gross_revenue", "placed_order", "sum_value", None), ("orders", "placed_order", "count", None),
                                ("buyers", "placed_order", "unique", None),
                                ("email_revenue", "placed_order_by_channel", "sum_value", "$email_channel"),
                                ("sms_revenue", "placed_order_by_channel", "sum_value", "$sms_channel"),
                                ("refunds", "refunded_order", "sum_value", None),
                                ("subscribed", "subscribed_email", "unique", None),
                                ("unsubscribed", "unsubscribed_email", "unique", None)):
        got = try_monthly(snap, agg, meas, window, dim)
        if got is not None:
            s[key] = got
    if "gross_revenue" in s and "refunds" in s:
        s["net_revenue"] = {m: v - s["refunds"].get(m, 0.0) for m, v in s["gross_revenue"].items()}
    if "gross_revenue" in s and "orders" in s:
        s["aov"] = {m: v / s["orders"][m] for m, v in s["gross_revenue"].items() if s["orders"].get(m)}
    if "subscribed" in s and "unsubscribed" in s:
        s["net_subscribers"] = {m: v - s["unsubscribed"].get(m, 0.0) for m, v in s["subscribed"].items()}
    if window == "current":
        try:
            totals: dict[str, float] = {}
            for r in snap.rows("form_submits"):
                for label, v in monthly(snap, "form_submits", "count", dimension=(r.get("dimensions") or [None])[0]).items():
                    totals[label] = totals.get(label, 0.0) + v
            if totals:
                s["signups"] = totals
        except MissingData:
            pass
    return s


def _new_customers(snap: Snapshot) -> dict[str, float]:
    rows, end, start = orders_rows(snap)
    origin = (start or (rows[0]["_t"] if rows else end)) + timedelta(days=BURN_IN_DAYS)
    out: dict[str, float] = {}
    tz = snap.tz
    for r in first_orders(rows).values():
        if r["_t"] >= origin:
            label = r["_t"].astimezone(tz).strftime("%Y-%m")
            out[label] = out.get(label, 0.0) + 1
    return out


def run(snap: Snapshot, a: Analysis, context: dict) -> None:
    cur, prior = _series(snap, "current"), _series(snap, "prior")
    if "gross_revenue" not in cur:
        raise MissingData("no Placed Order revenue by month", ref("totals", "aggregates", "placed_order"))
    try:
        new = _new_customers(snap)
        cur["new_customers"] = {m: new.get(m, 0.0) for m in cur["gross_revenue"]}
        prior["new_customers"] = {year_ago(m): new[year_ago(m)] for m in cur["gross_revenue"] if year_ago(m) in new}
    except MissingData as e:
        a.notes.append(f"new customers unavailable: {e.reason}")
    months = sorted(cur["gross_revenue"])
    lead = [KPI_KEYS[k] for k in context.get("kpis") or [] if KPI_KEYS.get(k) in LABELS] or DEFAULT_LEAD
    a.table("selected", [{"kpi": k, "label": LABELS[k][0], "chosen_by_brand": bool(context.get("kpis"))} for k in lead])
    a.table("monthly", [{"month": mo, **{k: round(v[mo], 2) for k, v in cur.items() if mo in v},
                         "gross_revenue_last_year": prior.get("gross_revenue", {}).get(year_ago(mo))} for mo in months])

    last = months[-1]
    recent = months[-3:]
    for key, (label, unit) in LABELS.items():
        series = cur.get(key)
        if series is None:
            a.fact(f"{key}.last_month", None, unit, f"{label}, {last}", window=last)
            continue
        a.fact(f"{key}.last_month", series.get(last), unit, f"{label}, {last}", window=last,
               source=ref("totals", "aggregates"))
        if key != "aov":
            a.fact(f"{key}.12m", sum(series.get(m, 0.0) for m in months), unit, f"{label}, {months[0]} to {last}",
                   window=f"{months[0]}..{last}", source=ref("totals", "aggregates"))
        then = [prior.get(key, {}).get(year_ago(m)) for m in recent]
        if all(v is not None for v in then) and sum(then):
            now_v = sum(series.get(m, 0.0) for m in recent)
            a.fact(f"{key}.yoy_3m", now_v / sum(then) - 1, "ratio", f"{label}, {recent[0]} to {last} vs a year earlier",
                   window=f"{recent[0]}..{last}")

    gross_12 = sum(cur["gross_revenue"].values())
    attributed = sum(cur.get("email_revenue", {}).values()) + sum(cur.get("sms_revenue", {}).values())
    share = a.fact("owned_share_12m", ratio(attributed, gross_12) if "email_revenue" in cur else None, "ratio",
                   "Email + SMS attributed share of store revenue", window=f"{months[0]}..{last}")

    yoy = next((f.value for f in a.facts if f.id == f"{MODULE}.gross_revenue.yoy_3m"), None)
    if yoy is not None and yoy <= -0.10:
        new_yoy = next((f.value for f in a.facts if f.id == f"{MODULE}.new_customers.yoy_3m"), None)
        side = ("new customers fell " + pct(-new_yoy, 0)) if new_yoy is not None and new_yoy < 0 else "returning buyers carried the drop"
        a.find("revenue_down", "Store revenue is down year over year",
               f"Gross revenue for {recent[0]} to {last} is {pct(-yoy, 0)} below the same months last year; {side}.",
               f"Gross revenue {usd(sum(cur['gross_revenue'].get(m, 0) for m in recent))} vs "
               f"{usd(sum(prior['gross_revenue'].get(year_ago(m), 0) for m in recent))} a year earlier.",
               "Point the next 30 days at the side that dropped: capture and welcome if new customers fell, "
               "post-purchase and winback if returning buyers did. Track both monthly on the scorecard.",
               severity="high", category="growth", facts=[f"{MODULE}.gross_revenue.yoy_3m", f"{MODULE}.new_customers.yoy_3m"],
               readout={"metric": "gross revenue vs same month last year", "days": 60})
    if share is not None and share < 0.15:
        a.find("owned_share_low", "Email and SMS earn a small share of store revenue",
               f"Email and SMS are credited with {pct(share)} of store revenue over the last 12 months.",
               f"{usd(attributed)} attributed of {usd(gross_12)} store revenue (Klaviyo attribution; attributed is not incremental).",
               "Fix the core flows first (they earn without new sends), then raise campaign cadence to the agreed weekly rhythm.",
               severity="high", category="growth", facts=[f"{MODULE}.owned_share_12m"],
               readout={"metric": "email + SMS attributed share of store revenue", "days": 90})
    elif share is not None and share > 0.5:
        a.find("owned_share_high", "Attributed share looks too high to take at face value",
               f"Klaviyo credits email and SMS with {pct(share)} of store revenue.",
               f"{usd(attributed)} attributed of {usd(gross_12)}; Klaviyo counts any order within the attribution window.",
               "Report this share with its attribution window, and use holdout tests before claiming lift from it.",
               severity="low", category="measurement", facts=[f"{MODULE}.owned_share_12m"], evidence_class="inferred")
    new_yoy = next((f.value for f in a.facts if f.id == f"{MODULE}.new_customers.yoy_3m"), None)
    if new_yoy is not None and new_yoy <= -0.20:
        a.find("new_customers_down", "Fewer new customers than last year",
               f"New customers for {recent[0]} to {last} are {pct(-new_yoy, 0)} below the same months last year.",
               "First observed buyers per month from the stored orders, after a 90-day burn-in.",
               "Strengthen capture: check the main popup's offer and submit rate, and make sure the welcome series "
               "reaches every new subscriber.", severity="medium", category="capture",
               facts=[f"{MODULE}.new_customers.yoy_3m"], readout={"metric": "new customers a month", "days": 60})
