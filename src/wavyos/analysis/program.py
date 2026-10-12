"""Send history: every sent campaign with its results, weekly cadence, the peak-season calendar and
send fatigue (does sending more each week earn less per send?)."""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from ..audit.snapshot import MissingData, Snapshot, parse_dt, ref
from .base import Analysis, pct, ratio, usd
from .flow_history import roll

MODULE = "program"
WEEKS = 52
LIGHT, HEAVY = (1, 2), 4
FATIGUE = 0.6
MIN_WEEKS = 4


def _subject(attrs: dict) -> str:
    content = attrs.get("content") or ((attrs.get("definition") or {}).get("content")) or {}
    return str(content.get("subject") or attrs.get("label") or "")


def campaign_rows(snap: Snapshot) -> list[dict]:
    """Sent campaigns (email and SMS) with subject and 365-day results, oldest first."""
    snap.require("totals")
    results: dict[str, dict] = {}
    try:
        results = roll(snap.report_results("campaign365"), "campaign_id", "campaign_message_name")
    except MissingData:
        pass
    out = []
    for channel in ("email", "sms"):
        block = snap.at("totals", "campaigns", channel, default={}) or {}
        subjects: dict[str, str] = {}
        for msg in block.get("messages") or []:
            cid = (((msg.get("relationships") or {}).get("campaign") or {}).get("data") or {}).get("id")
            if cid:
                subjects.setdefault(cid, _subject(msg.get("attributes") or {}))
        for c in block.get("items") or []:
            a = c.get("attributes") or {}
            t = parse_dt(a.get("send_time"))
            if a.get("status") != "Sent" or t is None:
                continue
            r = results.get(c.get("id")) or {}
            out.append({"id": c.get("id"), "channel": channel, "name": a.get("name") or "", "subject": subjects.get(c.get("id"), ""),
                        "sent_at": t, "recipients": r.get("recipients"), "revenue": r.get("conversion_value"),
                        "rpr": r.get("rpr"), "unsubscribe_rate": r.get("unsubscribe_rate"), "spam_rate": r.get("spam_rate")})
    if not out and not any((snap.at("totals", "campaigns", ch, default={}) or {}).get("items") is not None for ch in ("email", "sms")):
        raise MissingData("no campaign list in the snapshot", ref("totals", "campaigns"))
    return sorted(out, key=lambda r: r["sent_at"])


def last_peak(end: datetime) -> tuple[datetime, datetime]:
    """The most recent finished Black Friday / Cyber Monday window (Nov 15 to Dec 2)."""
    year = end.year if end >= end.replace(month=12, day=3, hour=0, minute=0, second=0, microsecond=0) else end.year - 1
    start = end.replace(year=year, month=11, day=15, hour=0, minute=0, second=0, microsecond=0)
    return start, start.replace(month=12, day=3)


def run(snap: Snapshot, a: Analysis, context: dict) -> None:
    rows = campaign_rows(snap)
    end = snap.as_of
    if end is None:
        raise MissingData("the snapshot has no capture time", ref("totals", "capturedAt"))
    tz = snap.tz
    a.table("calendar", [{**r, "sent_at": r["sent_at"].astimezone(tz).isoformat(), "date": r["sent_at"].astimezone(tz).date().isoformat()}
                         for r in rows])
    local_end = end.astimezone(tz)
    monday = (local_end - timedelta(days=local_end.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    weeks: dict[str, list[dict]] = {}
    for k in range(WEEKS, 0, -1):
        weeks[(monday - timedelta(weeks=k)).date().isoformat()] = []
    for r in rows:
        wk = (r["sent_at"].astimezone(tz) - timedelta(days=r["sent_at"].astimezone(tz).weekday())).date().isoformat()
        if wk in weeks:
            weeks[wk].append(r)
    weekly = [{"week": w, "email": sum(1 for r in rs if r["channel"] == "email"), "sms": sum(1 for r in rs if r["channel"] == "sms"),
               "revenue": round(sum(r["revenue"] or 0 for r in rs), 2),
               "rpr": ratio(sum(r["revenue"] or 0 for r in rs), sum(r["recipients"] or 0 for r in rs))} for w, rs in weeks.items()]
    a.table("weekly", weekly)
    recent = weekly[-13:]
    a.fact("email_campaigns_per_week_13w", sum(w["email"] for w in recent) / 13, "per week",
           "Email campaigns a week, last 13 full weeks", time_basis="send", source=ref("totals", "campaigns", "email"))
    a.fact("empty_weeks_13w", sum(1 for w in recent if not w["email"] and not w["sms"]), "weeks",
           "Weeks with no campaign, last 13 full weeks", time_basis="send")
    a.fact("campaigns_52w", sum(w["email"] + w["sms"] for w in weekly), "campaigns", "Campaigns sent, last 52 weeks",
           time_basis="send")

    p_start, p_end = last_peak(end)
    peak = [r for r in rows if p_start <= r["sent_at"] < p_end]
    year = p_start.year
    a.fact(f"bfcm_{year}_campaigns", len(peak), "campaigns", f"Campaigns sent Nov 15 to Dec 2, {year}", time_basis="send")
    a.fact(f"bfcm_{year}_revenue", sum(r["revenue"] or 0 for r in peak) if peak else None, "USD",
           f"Attributed revenue of campaigns sent Nov 15 to Dec 2, {year}", time_basis="send")
    history_covers = rows and rows[0]["sent_at"] <= p_start
    if history_covers and len(peak) < 3:
        upcoming = local_end.month in (7, 8, 9, 10) or (local_end.month == 11 and local_end.day < 15)
        a.find("thin_peak_season", "Black Friday and Cyber Monday were nearly empty",
               f"Only {len(peak)} campaign(s) went out between Nov 15 and Dec 2, {year}, the highest-intent weeks of the year.",
               f"Campaigns in that window: {', '.join(r['name'] for r in peak) or 'none'}.",
               ("Lock a peak calendar now: early access, the main offer, a Cyber Monday push and a last-chance send, "
                "with segments agreed 6 weeks out." if upcoming else
                "Build next year's peak calendar from this year's best-performing promotions before October."),
               severity="high", category="campaigns", effort_days=3, facts=[f"{MODULE}.bfcm_{year}_campaigns"],
               readout={"metric": "campaigns and revenue in the peak window", "days": 60})

    light = [w["rpr"] for w in weekly if w["email"] in LIGHT and w["rpr"] is not None]
    heavy = [w["rpr"] for w in weekly if w["email"] >= HEAVY and w["rpr"] is not None]
    if len(light) >= MIN_WEEKS and len(heavy) >= MIN_WEEKS:
        lo, hi = statistics.median(light), statistics.median(heavy)
        a.fact("rpr_light_weeks", lo, "USD", "Median campaign revenue per recipient, weeks with 1-2 email sends", time_basis="send")
        a.fact("rpr_heavy_weeks", hi, "USD", f"Median campaign revenue per recipient, weeks with {HEAVY}+ email sends",
               time_basis="send")
        if lo and hi < FATIGUE * lo:
            a.find("send_fatigue", "Heavier send weeks earn much less per send",
                   f"In weeks with {HEAVY}+ campaigns, each recipient is worth {usd(hi)} against {usd(lo)} in 1-2 send weeks.",
                   f"{len(heavy)} heavy weeks and {len(light)} light weeks over the last 52 weeks.",
                   "Cap regular weeks at the lighter rhythm and save extra sends for real moments (launches, peak), "
                   "sent to engaged segments only.", severity="medium", category="campaigns", effort_days=1,
                   facts=[f"{MODULE}.rpr_light_weeks", f"{MODULE}.rpr_heavy_weeks"],
                   readout={"metric": "campaign revenue per recipient by week", "days": 45})
