"""Campaign checks (CMP-*). Campaign lists come from step 38 (account-totals.json campaigns.email)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .checks import as_dict, check, ev, outcome, pct, ratio
from .flows import metric_names, segment_kind
from .library import th
from .snapshot import MissingData, Snapshot, parse_dt, ref

# CMP-01 logic: count the last 13 full ISO weeks.
CADENCE_WEEKS = 13
# CMP-03 logic: Sent campaigns in the last 90 days. CMP-04 logic: the last 365 days.
TARGETING_DAYS = 90
SETTINGS_DAYS = 365
_STEM_TAIL = re.compile(r"[\s\-_|:/]*(\(.*?\)|\[.*?\]|copy|clone|resend|\bv\d+\b|\bvariant\s*\w*|\b[ab]\b|\d+)\s*$", re.I)


def name_stem(name: str) -> str:
    """'Labor Day Sale (A)' and 'Labor Day Sale - B' share the stem 'labor day sale'."""
    s = (name or "").strip().lower()
    prev = None
    while s and s != prev:
        prev, s = s, _STEM_TAIL.sub("", s).strip()
    return s or (name or "").strip().lower()


def sent_campaigns(snap: Snapshot, days: float | None = None) -> list[tuple[int, dict, datetime]]:
    """(index, attributes, send time) for Sent, unarchived email campaigns, newest last."""
    snap.require("totals")
    items = snap.at("totals", "campaigns", "email", "items")
    end = snap.as_of
    out = []
    for i, c in enumerate(items):
        a = c.get("attributes") or {}
        t = parse_dt(a.get("send_time"))
        if a.get("status") != "Sent" or a.get("archived") or t is None:
            continue
        if days is not None and end is not None and t < end - timedelta(days=days):
            continue
        out.append((i, a, t))
    return sorted(out, key=lambda r: r[2])


@check("CMP-01")
def campaign_cadence(snap: Snapshot, spec: dict):
    end = snap.as_of
    if end is None:
        raise MissingData("the snapshot has no capture time", ref("totals", "capturedAt"))
    p = th(spec, "pass", r">= {n} per week")
    w_empty = int(th(spec, "warn", r">= {n} empty weeks"))
    f_lo = th(spec, "fail", r"< {n} per week")
    tz = snap.tz
    local_end = end.astimezone(tz)
    this_monday = (local_end - timedelta(days=local_end.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = this_monday - timedelta(weeks=CADENCE_WEEKS)
    weeks: dict[str, set] = {}
    for k in range(CADENCE_WEEKS):
        wk = (start + timedelta(weeks=k)).isocalendar()
        weeks[f"{wk.year}-W{wk.week:02d}"] = set()
    used = []
    for i, a, t in sent_campaigns(snap):
        lt = t.astimezone(tz)
        if not (start <= lt < this_monday):
            continue
        wk = lt.isocalendar()
        weeks[f"{wk.year}-W{wk.week:02d}"].add((lt.date().isoformat(), name_stem(a.get("name") or "")))
        used.append(i)
    counts = {k: len(v) for k, v in weeks.items()}
    avg = sum(counts.values()) / CADENCE_WEEKS
    empty = [k for k, n in counts.items() if n == 0]
    value = as_dict({"weeks": counts, "per_week": avg, "empty_weeks": empty, "campaign_rows": len(used)})
    evidence = [ev(ref("totals", "campaigns", "email", "items"), f"Sent campaigns, {CADENCE_WEEKS} full ISO weeks to "
                                                                f"{(this_monday - timedelta(days=1)).date()}, deduplicated by send date and name stem")]
    msg = f"{avg:.1f} campaigns a week over the last {CADENCE_WEEKS} full weeks; {len(empty)} empty week(s)"
    if avg < f_lo:
        return outcome("fail", msg, value, evidence)
    if avg >= p and not empty:
        return outcome("pass", msg, value, evidence)
    return outcome("warn", msg + (f" (>= {w_empty} empty weeks)" if len(empty) >= w_empty else ""), value, evidence)


@check("CMP-02")
def campaign_rpr_holding(snap: Snapshot, spec: dict):
    snap.require("perf")
    p = th(spec, "pass", r">= {n}% of RPR365", 0.01)
    f_lo = th(spec, "fail", r"< {n}%", 0.01)
    rpr = {}
    for d in (90, 365):
        rows = snap.report_results(f"campaign{d}")
        val = sum(float((r.get("statistics") or {}).get("conversion_value") or 0) for r in rows)
        rec = sum(float((r.get("statistics") or {}).get("recipients") or 0) for r in rows)
        rpr[d] = ratio(val, rec)
    if rpr[90] is None or rpr[365] is None or rpr[365] == 0:
        raise MissingData("no campaign recipients or revenue in the 90/365-day reports", ref("perf", "reports"))
    r = rpr[90] / rpr[365]
    end = snap.as_of
    holiday = end is not None and any((end - timedelta(days=k)).month in (11, 12) for k in range(0, 91, 5))
    value = as_dict({"rpr90": rpr[90], "rpr365": rpr[365], "ratio": r, "holiday_in_90d": holiday})
    evidence = [ev(ref("perf", "reports", "campaign90"), "campaign conversion value and recipients, 90 days"),
                ev(ref("perf", "reports", "campaign365"), "campaign conversion value and recipients, 365 days")]
    season = (" (the 90 days include November-December)" if holiday
              else " (the 365 days include November-December; the 90 days don't)")
    msg = f"90-day campaign revenue per recipient is {pct(r, 0)} of the 365-day figure" + season
    verdict = "pass" if r >= p else ("fail" if r < f_lo else "warn")
    return outcome(verdict, msg, value, evidence)


def _segment_kinds(snap: Snapshot) -> dict[str, str | None]:
    names = metric_names(snap)
    return {str(s.get("id")): segment_kind((s.get("attributes") or {}).get("definition") or {}, names)
            for s in snap.at("api", "segments", "items", default=[]) or []}


@check("CMP-03")
def campaigns_target_engaged(snap: Snapshot, spec: dict):
    snap.require("api", "totals")
    p = th(spec, "pass", r">= {n}%", 0.01)
    f_lo = th(spec, "fail", r"< {n}%", 0.01)
    kinds = _segment_kinds(snap)
    lists = {str(x.get("id")) for x in snap.at("api", "lists", "items", default=[]) or []}
    recent = sent_campaigns(snap, TARGETING_DAYS)
    if not recent:
        raise MissingData(f"no Sent campaigns in the last {TARGETING_DAYS} days", ref("totals", "campaigns", "email", "items"))
    targeted, whole_list = 0, 0
    for _, a, _ in recent:
        aud = a.get("audiences") or {}
        inc = [str(x) for x in aud.get("included") or []]
        exc = [str(x) for x in aud.get("excluded") or []]
        if any(kinds.get(x) == "engaged" for x in inc) or any(kinds.get(x) == "sunset" for x in exc):
            targeted += 1
        elif inc and all(x in lists for x in inc):
            whole_list += 1
    share = targeted / len(recent)
    value = as_dict({"campaigns": len(recent), "engaged_targeted": targeted, "list_only_sends": whole_list, "share": share})
    evidence = [ev(ref("totals", "campaigns", "email", "items"), f"audiences of {len(recent)} campaigns sent in {TARGETING_DAYS} days"),
                ev(ref("api", "segments", "items"), "segment definitions (engagement conditions)")]
    msg = (f"{pct(share, 0)} of {len(recent)} recent campaigns include an engaged segment or exclude an unengaged one"
           f"; {whole_list} went to lists only")
    verdict = "pass" if share >= p else ("fail" if share < f_lo else "warn")
    return outcome(verdict, msg, value, evidence)


@check("CMP-04")
def campaign_settings_safe(snap: Snapshot, spec: dict):
    p = th(spec, "pass", r"smart sending >= {n}%", 0.01)
    recent = sent_campaigns(snap, SETTINGS_DAYS)
    if not recent:
        raise MissingData(f"no Sent campaigns in the last {SETTINGS_DAYS} days", ref("totals", "campaigns", "email", "items"))
    ignored = [i for i, a, _ in recent if (a.get("send_options") or {}).get("ignore_unsubscribes")]
    smart = sum(1 for _, a, _ in recent if (a.get("send_options") or {}).get("use_smart_sending"))
    share = smart / len(recent)
    value = as_dict({"campaigns": len(recent), "ignore_unsubscribes": len(ignored), "smart_sending_share": share})
    evidence = [ev(ref("totals", "campaigns", "email", "items", i, "attributes", "send_options"), "ignores unsubscribes")
                for i in ignored[:10]] or [ev(ref("totals", "campaigns", "email", "items"), f"send options of {len(recent)} campaigns")]
    if ignored:
        return outcome("fail", f"{len(ignored)} campaign(s) in the last year were sent ignoring unsubscribes", value, evidence)
    msg = f"no campaign ignored unsubscribes; smart sending on {pct(share, 0)} of {len(recent)}"
    return outcome("pass" if share >= p else "warn", msg, value, evidence)
