"""Which past promotions won: promotional campaigns by revenue per recipient, offer depth against
results, and how the best one compares with Black Friday."""
from __future__ import annotations

import re
import statistics

from ..audit.snapshot import Snapshot
from .base import Analysis, pct, usd
from .program import campaign_rows

MODULE = "promotions"
PROMO = re.compile(r"sale|% ?off|\boff\b|bfcm|black friday|cyber|flash|bogo|free (shipping|gift)|\bcode\b|deal|discount|"
                   r"save|clearance|last chance|ends tonight|early access", re.I)
BLACK_FRIDAY = re.compile(r"black friday|bfcm|\bbf\b|cyber", re.I)
DEPTH = re.compile(r"(\d{1,2})\s?%")
MIN_RECIPIENTS = 1000
DEEP = 30


def run(snap: Snapshot, a: Analysis, context: dict) -> None:
    rows = campaign_rows(snap)
    promos = []
    for r in rows:
        text = f"{r['name']} {r['subject']}"
        if not PROMO.search(text):
            continue
        depths = [int(d) for d in DEPTH.findall(text) if 5 <= int(d) <= 90]
        promos.append({**r, "sent_at": r["sent_at"].isoformat(), "depth": max(depths) if depths else None,
                       "black_friday": bool(BLACK_FRIDAY.search(text))})
    a.table("promotions", sorted(promos, key=lambda p: -(p["revenue"] or 0)))
    measured = [p for p in promos if (p["recipients"] or 0) >= MIN_RECIPIENTS and p["rpr"] is not None]
    campaign_rev = sum(r["revenue"] or 0 for r in rows)
    a.fact("promo_campaigns_365d", len(promos), "campaigns", "Promotional campaigns sent (by name and subject)", time_basis="send")
    a.fact("promo_revenue_share", (sum(p["revenue"] or 0 for p in promos) / campaign_rev) if campaign_rev else None, "ratio",
           "Promotional campaigns' share of campaign-attributed revenue, 365 days", time_basis="send")
    if not measured:
        return
    best = max(measured, key=lambda p: p["rpr"])
    a.fact("best_promo_rpr", best["rpr"], "USD", f"Revenue per recipient of the best promotion ('{best['name']}')",
           time_basis="send")
    bf = [p for p in measured if p["black_friday"]]
    if bf and not best["black_friday"]:
        top_bf = max(bf, key=lambda p: p["rpr"])
        a.fact("best_black_friday_rpr", top_bf["rpr"], "USD", f"Revenue per recipient of '{top_bf['name']}'", time_basis="send")
        if best["rpr"] > 1.2 * top_bf["rpr"]:
            a.find("best_promo_beat_bf", "A smaller promotion beat Black Friday",
                   f"'{best['name']}' earned {usd(best['rpr'])} per recipient, more than the best Black Friday send "
                   f"('{top_bf['name']}', {usd(top_bf['rpr'])}).",
                   f"{int(best['recipients']):,} recipients and {usd(best['revenue'])} attributed for the winner.",
                   f"Repeat the winning format (offer, framing, timing of '{best['name']}') at least once a quarter, and use "
                   "it to shape this year's peak offer.", severity="medium", category="campaigns", effort_days=2,
                   facts=[f"{MODULE}.best_promo_rpr", f"{MODULE}.best_black_friday_rpr"],
                   readout={"metric": "revenue per recipient of the repeated format", "days": 60})
    deep = [p["rpr"] for p in measured if p["depth"] is not None and p["depth"] >= DEEP]
    shallow = [p["rpr"] for p in measured if p["depth"] is not None and p["depth"] < DEEP]
    if len(deep) >= 3 and len(shallow) >= 3:
        d, s = statistics.median(deep), statistics.median(shallow)
        a.fact("rpr_deep_offers", d, "USD", f"Median revenue per recipient, offers of {DEEP}%+", time_basis="send")
        a.fact("rpr_shallow_offers", s, "USD", f"Median revenue per recipient, offers under {DEEP}%", time_basis="send")
        if d <= s:
            a.find("deep_discounts_no_better", "Deeper discounts didn't earn more per send",
                   f"Offers of {DEEP}% or more earned {usd(d)} per recipient (median) against {usd(s)} for smaller offers.",
                   f"{len(deep)} deep and {len(shallow)} shallow promotional campaigns with {MIN_RECIPIENTS:,}+ recipients.",
                   f"Keep regular promotions under {DEEP}% and save deep offers for one peak moment; every point of "
                   "discount not given is margin kept.", severity="medium", category="campaigns", effort_days=1,
                   facts=[f"{MODULE}.rpr_deep_offers", f"{MODULE}.rpr_shallow_offers"],
                   readout={"metric": "margin per promotional send", "days": 60})
