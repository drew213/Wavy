"""Live email templates against the message rubric: Gmail clipping, image-only, alt text, unsubscribe and
postal address, claims-risk language (stricter for regulated categories), and links (see links.py)."""
from __future__ import annotations

import re
from typing import Optional

from ..audit.snapshot import MissingData, Snapshot, ref
from . import links as linkcheck
from .base import Analysis

MODULE = "messages"
CLAIMS = ("cure", "cures", "fda approved", "clinically proven", "guaranteed results", "miracle", "risk-free",
          "no side effects", "100% safe")
REGULATED_EXTRA = ("treats", "heal", "heals", "prevents", "diagnose", "anxiety", "insomnia", "depression", "pain relief",
                   "relieves pain", "get high", "get you high", "stoned", "medical", "medicine", "prescription")
IMAGE_ONLY_CHARS = 200


def _terms(context: dict) -> list[str]:
    extra = [w.strip().lower() for w in re.split(r"[,;\n]", context.get("avoid_claims") or "") if 2 < len(w.strip()) <= 40]
    regulated = context.get("regulated_category") not in (None, "No")
    return list(dict.fromkeys(list(CLAIMS) + extra + (list(REGULATED_EXTRA) if regulated else [])))


def _hits(text: str, terms: list[str]) -> list[str]:
    low = text.lower()
    return [t for t in terms if re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", low)]


def run(snap: Snapshot, a: Analysis, context: dict, fetch: Optional[linkcheck.Fetch] = None) -> None:
    snap.require("templates")
    rows = [t for t in snap.at("templates", "templates", default=[]) or [] if not t.get("error")]
    if not rows:
        raise MissingData("no live templates were measured", ref("templates", "templates"))
    terms = _terms(context)
    graded, all_links = [], []
    for t in rows:
        where = "; ".join(f"{m.get('flowName')} / {m.get('messageName')}" for m in (t.get("messages") or [])[:3])
        copy = " ".join(str(x or "") for x in [t.get("text") or t.get("textSample")] +
                        [m.get("subject") for m in t.get("messages") or []] + [m.get("previewText") for m in t.get("messages") or []])
        g = {"template_id": t.get("templateId"), "name": t.get("templateName"), "used_in": where,
             "kb": round((t.get("htmlBytes") or 0) / 1024, 1), "clipped": bool(t.get("overGmailClipThreshold")),
             "image_only": (t.get("imageCount") or 0) > 0 and (t.get("textChars") or 0) < IMAGE_ONLY_CHARS,
             "images_without_alt": t.get("imagesWithoutAlt"), "has_unsubscribe": t.get("hasUnsubscribe"),
             "has_address": t.get("hasAddress"), "claims": _hits(copy, terms), "links": len(t.get("links") or [])}
        g["issues"] = [k for k, bad in (("clipped", g["clipped"]), ("image_only", g["image_only"]),
                                        ("missing_alt", (g["images_without_alt"] or 0) > 0),
                                        ("no_unsubscribe", g["has_unsubscribe"] is False),
                                        ("claims", bool(g["claims"]))) if bad]
        graded.append(g)
        all_links += t.get("links") or []
    a.table("templates", graded)
    a.fact("live_templates", len(graded), "templates", "Live flow templates measured", source=ref("templates", "templates"))
    for key, label in (("clipped", "over Gmail's clipping size"), ("image_only", "image-only"),
                       ("missing_alt", "with images missing alt text"), ("claims", "with claims-risk wording"),
                       ("no_unsubscribe", "without a detectable unsubscribe link")):
        a.fact(f"templates_{key}", sum(1 for g in graded if key in g["issues"]), "templates", f"Live templates {label}")

    claims = [g for g in graded if g["claims"]]
    if claims:
        regulated = context.get("regulated_category") not in (None, "No")
        words = sorted({w for g in claims for w in g["claims"]})
        a.find("claims_risk", "Live emails use claims-risk wording",
               f"{len(claims)} live template(s) contain words the brand or its category should avoid.",
               f"Terms found: {', '.join(words[:12])}. In: " + "; ".join(g["used_in"] or g["name"] or "" for g in claims[:4]) + ".",
               "Have the brand's approver review these lines and replace them with compliant wording; add the terms to "
               "the brand's never-say list so drafts are checked against it.",
               severity="high" if regulated else "medium", category="fix_first", effort_days=1,
               facts=[f"{MODULE}.templates_claims"], readout={"metric": "live templates with claims-risk wording", "days": 14})
    no_alt = [g for g in graded if "missing_alt" in g["issues"]]
    if no_alt:
        a.find("missing_alt_text", "Images without alt text",
               f"{len(no_alt)} live template(s) have images with no alt text, so they read as blank when images are off.",
               f"{sum(g['images_without_alt'] or 0 for g in no_alt)} images across: "
               + "; ".join(g["used_in"] or g["name"] or "" for g in no_alt[:4]) + ".",
               "Add short, descriptive alt text to every image, starting with the hero image and buttons.",
               severity="low", category="creative", effort_days=1, facts=[f"{MODULE}.templates_missing_alt"],
               readout={"metric": "live templates missing alt text", "days": 14})
    no_unsub = [g for g in graded if "no_unsubscribe" in g["issues"]]
    if no_unsub:
        a.find("no_unsubscribe", "Templates without a visible unsubscribe link",
               f"{len(no_unsub)} live template(s) have no unsubscribe or preference link in their HTML.",
               "; ".join(g["used_in"] or g["name"] or "" for g in no_unsub[:4]) + ".",
               "Confirm Klaviyo adds its footer to these; if not, add the unsubscribe tag. Gmail and Yahoo require "
               "one-click unsubscribe for bulk senders.", severity="high", category="fix_first", effort_days=1,
               evidence_class="inferred", facts=[f"{MODULE}.templates_no_unsubscribe"],
               readout={"metric": "live templates without unsubscribe", "days": 7})

    result = linkcheck.check(all_links, fetch)
    a.table("link_issues", result["issues"])
    a.fact("store_domain", result["store_domain"], "domain", "Most-linked store domain in live templates")
    a.fact("links_checked", result["checked"], "links", "Distinct links in live templates")
    a.fact("link_issues", len(result["issues"]), "links", "Link problems found" + (" (links fetched)" if result["fetched"] else
                                                                                   " (offline checks only)"))
    if result["issues"]:
        kinds = sorted({i["issue"] for i in result["issues"]})
        a.find("link_problems", "Links in live emails that don't land where they should",
               f"{len(result['issues'])} link problem(s) in live templates: {', '.join(kinds)}.",
               "; ".join(f"{i['url'][:80]} ({i['detail']})" for i in result["issues"][:5]) + ".",
               f"Point every link at the live store ({result['store_domain'] or 'the current domain'}) over https and "
               "re-test each fixed template.", severity="high" if {"broken", "tls_failed"} & set(kinds) else "medium",
               category="fix_first", effort_days=1, facts=[f"{MODULE}.link_issues"],
               readout={"metric": "link problems in live templates", "days": 7})
