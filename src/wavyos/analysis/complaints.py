"""Where spam complaints and unsubscribes come from, across flows and campaigns (365 days).

A single source causing most complaints for little revenue is the most fixable deliverability risk there
is, and third-party identity tools (Retention.com and similar) are a frequent cause: they add people who
never signed up directly."""
from __future__ import annotations

import re

from ..audit.snapshot import MissingData, Snapshot, ref
from .base import Analysis, pct, usd
from .flow_history import names_from_api, roll

MODULE = "complaints"
MIN_COMPLAINTS = 10
CONCENTRATED = 0.4
THIRD_PARTY = re.compile(r"retention\.?com|reveal|identity|resolution|wunderkind|ad[\s-]?sync", re.I)


def _sources(snap: Snapshot) -> list[dict]:
    names = names_from_api(snap)
    out = []
    for report, key, name_key, kind in (("flow365", "flow_id", "flow_name", "flow"),
                                        ("campaign365", "campaign_id", "campaign_message_name", "campaign")):
        try:
            rows = roll(snap.report_results(report), key, name_key)
        except MissingData:
            continue
        for r in rows.values():
            name = names.get(r["id"], r["name"]) if kind == "flow" else r["name"]
            out.append({"kind": kind, "id": r["id"], "name": name, "complaints": r["spam_complaints"],
                        "unsubscribes": r["unsubscribes"], "revenue": r["conversion_value"], "recipients": r["recipients"]})
    if not out:
        raise MissingData("no flow or campaign performance report", ref("perf", "reports"))
    return out


def run(snap: Snapshot, a: Analysis, context: dict) -> None:
    src = _sources(snap)
    complaints = sum(s["complaints"] for s in src)
    unsubs = sum(s["unsubscribes"] for s in src)
    revenue = sum(s["revenue"] for s in src)
    a.fact("spam_complaints_365d", complaints, "complaints", "Spam complaints across flows and campaigns, 365 days",
           time_basis="send", source=ref("perf", "reports"))
    a.fact("unsubscribes_365d", unsubs, "profiles", "Unsubscribes across flows and campaigns, 365 days", time_basis="send")
    for s in src:
        s["complaint_share"] = s["complaints"] / complaints if complaints else None
        s["unsubscribe_share"] = s["unsubscribes"] / unsubs if unsubs else None
        s["revenue_share"] = s["revenue"] / revenue if revenue else None
    a.table("sources", sorted(src, key=lambda s: (-s["complaints"], -s["unsubscribes"]))[:25])

    if complaints >= MIN_COMPLAINTS:
        top = max(src, key=lambda s: s["complaints"])
        share = a.fact("top_complaint_source_share", top["complaint_share"], "ratio",
                       f"Share of spam complaints from {top['kind']} '{top['name']}'", time_basis="send")
        a.fact("top_complaint_source_revenue", top["revenue"], "USD", f"Attributed revenue of {top['kind']} '{top['name']}'",
               time_basis="send")
        if share >= CONCENTRATED:
            third = bool(THIRD_PARTY.search(top["name"] or ""))
            a.find("complaint_concentration", "One send source causes most spam complaints",
                   f"The {top['kind']} '{top['name']}' causes {pct(share, 0)} of all spam complaints while earning "
                   f"{usd(top['revenue'])} ({pct(top['revenue_share'], 1)} of email revenue).",
                   f"{int(top['complaints']):,} of {int(complaints):,} complaints over 365 days.",
                   ("Pause or gate the third-party identity source feeding it, and only send to people who opted in "
                    "on the brand's own forms. " if third else "Tighten who enters it and how often it sends; ")
                   + "Re-check complaint share two weeks after the change.",
                   severity="critical" if share >= 0.6 else "high", category="deliverability",
                   facts=[f"{MODULE}.top_complaint_source_share", f"{MODULE}.top_complaint_source_revenue"],
                   readout={"metric": "spam complaint rate", "days": 30})
    if unsubs >= 100:
        top_u = max(src, key=lambda s: s["unsubscribes"])
        share_u = a.fact("top_unsubscribe_source_share", top_u["unsubscribe_share"], "ratio",
                         f"Share of unsubscribes from {top_u['kind']} '{top_u['name']}'", time_basis="send")
        if share_u >= CONCENTRATED and (top_u["revenue_share"] or 0) < share_u / 2:
            a.find("unsubscribe_concentration", "One send source drives a large share of unsubscribes",
                   f"The {top_u['kind']} '{top_u['name']}' causes {pct(share_u, 0)} of unsubscribes but earns "
                   f"{pct(top_u['revenue_share'], 1)} of email revenue.",
                   f"{int(top_u['unsubscribes']):,} of {int(unsubs):,} unsubscribes over 365 days.",
                   "Cut its frequency or audience first; if it's a flow, shorten it and move the hard sell later.",
                   severity="medium", category="deliverability", facts=[f"{MODULE}.top_unsubscribe_source_share"],
                   readout={"metric": "unsubscribe rate", "days": 30})
