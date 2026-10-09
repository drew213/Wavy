"""Rendering and tracking checks (CRV-*). Step 37 covers live flow templates only; campaign HTML
needs the deep track's step 40 archive."""
from __future__ import annotations

from .checks import as_dict, check, ev, outcome, pct, ratio
from .checks_campaigns import sent_campaigns
from .flows import CORE_TYPES
from .library import th, th_range
from .snapshot import MissingData, Snapshot, ref

# CRV-02 logic: a template is image-heavy with textChars < 300 and imageCount >= 3.
IMAGE_HEAVY_MAX_TEXT = 300
IMAGE_HEAVY_MIN_IMAGES = 3
# CRV-04 logic: campaigns from the last 90 days.
TRACKING_CAMPAIGN_DAYS = 90


def _flow_kinds(snap: Snapshot) -> dict[str, str | None]:
    return {f.id: f.kind for f in snap.flows() if f.classified_live}


def _measured(t: dict) -> bool:
    return not t.get("error") and t.get("htmlBytes") is not None


@check("CRV-01")
def under_gmail_clip(snap: Snapshot, spec: dict):
    """'Low-volume messages' = templates used only by live flows outside the core types.
    Templates step 37 couldn't fetch are unmeasured: a clip seen in the others still fails, but
    the check can't pass (or settle on warn) while any template is unmeasured."""
    snap.require("templates", "api")
    limit = th(spec, "pass", r"> {n} bytes")
    near = th(spec, "warn", r"templates {n}-\d+ KB") * 1024
    templates = snap.at("templates", "templates")
    kinds = _flow_kinds(snap)
    clip_core, clip_other, near_rows, unmeasured = [], [], [], []
    for i, t in enumerate(templates):
        size = t.get("htmlBytes")
        if not _measured(t):
            unmeasured.append(i)
            continue
        flows = {str(m.get("flowId")) for m in t.get("messages") or []}
        core = any(kinds.get(f) in CORE_TYPES for f in flows)
        if size > limit:
            (clip_core if core else clip_other).append(i)
        elif size >= near:
            near_rows.append(i)
    value = {"templates": len(templates), "unmeasured": len(unmeasured), "clipping_core": len(clip_core),
             "clipping_other": len(clip_other), "near_limit": len(near_rows),
             "step37_flagged": snap.at("templates", "overGmailClipThreshold", default=None), "campaigns_checked": False}
    evidence = [ev(ref("templates", "templates", i, "htmlBytes"), f"template HTML over {int(limit):,} bytes") for i in clip_core + clip_other][:15] \
        + [ev(ref("templates", "templates", i, "htmlBytes"), "template HTML near the clip limit") for i in near_rows][:10] \
        + [ev(ref("templates", "templates", i, "error"), "step 37 couldn't fetch this template") for i in unmeasured][:5] \
        or [ev(ref("templates", "templates"), f"{len(templates)} live flow templates measured")]
    if clip_core:
        return outcome("fail", f"{len(clip_core)} core-flow template(s) exceed {int(limit):,} bytes, so Gmail clips them", value, evidence)
    if unmeasured:
        seen = f"; of the rest, {len(clip_other)} non-core clip and {len(near_rows)} are near the limit"
        return outcome("na", f"step 37 couldn't fetch {len(unmeasured)} of {len(templates)} live templates, so clipping "
                             f"can't be ruled out (DAT-05){seen}", value, evidence)
    if clip_other or near_rows:
        return outcome("warn", f"{len(clip_other)} non-core template(s) clip and {len(near_rows)} are within "
                               f"{int(limit - near) // 1024} KB of the limit (campaigns need the deep track)", value, evidence)
    return outcome("pass", f"none of {len(templates)} live flow templates clip (campaign HTML is deep-track only)", value, evidence)


@check("CRV-02")
def not_image_only(snap: Snapshot, spec: dict):
    snap.require("templates", "api")
    ok_max = th(spec, "pass", r"< {n}% image-heavy", 0.01)
    _, w_hi = th_range(spec, "warn", r"{n}-{n}%", 0.01)
    templates = snap.at("templates", "templates")
    if not templates:
        raise MissingData("step 37 measured no live templates", ref("templates", "templates"))
    unmeasured = [i for i, t in enumerate(templates) if not _measured(t) or t.get("textChars") is None or t.get("imageCount") is None]
    heavy = [i for i, t in enumerate(templates) if i not in unmeasured
             and t["textChars"] < IMAGE_HEAVY_MAX_TEXT and t["imageCount"] >= IMAGE_HEAVY_MIN_IMAGES]
    firsts = set()
    for f in snap.flows():
        if f.classified_live and f.kind in ("welcome", "abandoned_checkout"):
            s = f.first_email()
            if s and s.message.get("id"):
                firsts.add(str(s.message["id"]))
    def is_first(i: int) -> bool:
        return any(str(m.get("messageId")) in firsts for m in templates[i].get("messages") or [])

    heavy_first = [i for i in heavy if is_first(i)]
    first_unmeasured = any(is_first(i) for i in unmeasured)
    # Unmeasured templates could go either way: the share lies between these bounds.
    low, high = len(heavy) / len(templates), (len(heavy) + len(unmeasured)) / len(templates)
    value = as_dict({"templates": len(templates), "unmeasured": len(unmeasured), "image_heavy": len(heavy),
                     "share": low if not unmeasured else None, "share_bounds": [low, high],
                     "core_first_emails_image_heavy": len(heavy_first)})
    evidence = [ev(ref("templates", "templates", i), "image-heavy template (little text, 3+ images)") for i in heavy][:15] \
        or [ev(ref("templates", "templates"), "textChars and imageCount per template")]
    msg = (f"{pct(low, 0)} of {len(templates)} live templates are image-heavy" if not unmeasured else
           f"{len(heavy)} of {len(templates)} live templates are image-heavy and {len(unmeasured)} couldn't be measured "
           f"(share {pct(low, 0)}-{pct(high, 0)})")
    if low > w_hi or heavy_first:
        return outcome("fail", msg + (f"; {len(heavy_first)} welcome/checkout first email(s) among them" if heavy_first else ""),
                       value, evidence)
    if high < ok_max and not first_unmeasured:
        return outcome("pass", msg, value, evidence)
    if low >= ok_max and high <= w_hi and not first_unmeasured:
        return outcome("warn", msg, value, evidence)
    return outcome("na", msg + "; the unmeasured templates decide the verdict (DAT-05)", value, evidence)


@check("CRV-04")
def clicks_tracked(snap: Snapshot, spec: dict):
    snap.require("api", "totals")
    ok_min = th(spec, "pass", r">= {n}% tagged", 0.01)
    f_lo = th(spec, "fail", r"< {n}%", 0.01)
    flow_sends = [s for f in snap.flows() if f.classified_live for s in f.live_sends if s.channel == "email"]
    flow_share = ratio(sum(1 for s in flow_sends if s.message.get("add_tracking_params")), len(flow_sends))
    camps = sent_campaigns(snap, TRACKING_CAMPAIGN_DAYS)
    camp_share = ratio(sum(1 for _, a, _ in camps if (a.get("tracking_options") or {}).get("add_tracking_params")), len(camps))
    clicks_off = [i for i, a, _ in camps if (a.get("tracking_options") or {}).get("is_tracking_clicks") is False]
    if flow_share is None and camp_share is None:
        raise MissingData("no live flow emails or recent campaigns", ref("totals", "campaigns", "email", "items"))
    value = as_dict({"flow_emails": len(flow_sends), "flow_tagged_share": flow_share, "campaigns_90d": len(camps),
                     "campaign_tagged_share": camp_share, "campaigns_click_tracking_off": len(clicks_off)})
    evidence = [ev(ref("api", "flows", "details"), "live flow send-email add_tracking_params"),
                ev(ref("totals", "campaigns", "email", "items"), "campaign tracking_options, last 90 days")]
    evidence += [ev(ref("totals", "campaigns", "email", "items", i, "attributes", "tracking_options"), "click tracking off")
                 for i in clicks_off[:5]]
    shares = [s for s in (flow_share, camp_share) if s is not None]
    low = min(shares)
    msg = f"UTMs on {pct(flow_share, 0)} of live flow emails and {pct(camp_share, 0)} of recent campaigns"
    if low < f_lo or clicks_off:
        return outcome("fail", msg + (f"; click tracking off on {len(clicks_off)} campaign(s)" if clicks_off else ""), value, evidence)
    if low < ok_min or len(shares) < 2:
        return outcome("warn", msg, value, evidence)
    return outcome("pass", msg, value, evidence)
