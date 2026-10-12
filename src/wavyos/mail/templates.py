"""The onboarding emails' words. Each function takes the brand's state (lifecycle.Brand) and one recipient and
returns blocks. Every number comes from the brand's own stored audit (scorecard summary, ledger facts) or its
impact readings: these templates never estimate anything themselves, and no model writes them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .render import Email, money, money_range

if TYPE_CHECKING:
    from .lifecycle import Brand
    from ..models import User

GOAL_LINE = "Goal: $10K of measured lift in your first 30 days."
KEY_STEPS = ["In Klaviyo, open Settings, then API keys.",
             "Choose Create Private API Key and give it Read-only access.",
             "Paste it into Wavy. It starts with pk_."]


def _first(u: "User") -> str:
    return (u.name or "").split(" ")[0] or "there"


def _named(u: "User") -> str:
    """", Maya" when we know a first name, else nothing."""
    first = (u.name or "").split(" ")[0]
    return f", {first}" if first else ""


def _sized(c: dict) -> bool:
    return (c.get("sizing") or {}).get("status") == "sized"


def _top(b: "Brand", n: int = 3) -> list[dict]:
    """The highest-ranked sized levers, topped up with the highest-ranked other cards when fewer are sized."""
    cards = sorted(b.summary.get("opportunities") or [], key=lambda c: c.get("rank") or 999)
    return ([c for c in cards if _sized(c)] + [c for c in cards if not _sized(c)])[:n]


def _items(cards: list[dict]) -> list[dict]:
    return [{"title": c["title"], "detail": c.get("outcome") or c.get("observation") or "",
             "value": money_range(c["sizing"].get("annual")) if _sized(c) else ""} for c in cards]


def _total(b: "Brand") -> dict:
    return ((b.summary.get("total") or {}).get("annual")) or {}


# ---------------------------------------------------------------- getting connected

def welcome(b: "Brand", u: "User") -> Email:
    return Email(f"Welcome to Wavy{_named(u)}. Here's what happens next",
                 "Connect Klaviyo and your audit starts on its own.",
                 [{"p": f"Hi {_first(u)},"},
                  {"p": f"Your {b.client.name} workspace is ready. Here's how the next 30 days work:"},
                  {"steps": ["Connect Klaviyo with a read-only key. It can't change anything in your account.",
                             "Wavy audits the account: every flow and email, 24 months of orders, deliverability, and what each part earns.",
                             "You approve the fixes worth making. Wavy ships them and measures what each one earns."]},
                  {"p": GOAL_LINE + " Only revenue a fix measurably adds counts toward it."},
                  {"button": ("Connect Klaviyo", b.links.connect)},
                  {"note": "Reply to this email if anything gets in the way."}])


def connect_nudge(b: "Brand", u: "User") -> Email:
    return Email(f"{b.client.name}'s audit is waiting on one key",
                 "One read-only key in Klaviyo, and the audit runs itself.",
                 [{"p": f"Hi {_first(u)},"},
                  {"p": "Your audit starts the moment Wavy can read your Klaviyo account. Here's the whole job:"},
                  {"steps": KEY_STEPS},
                  {"button": ("Paste your key", b.links.connect)},
                  {"note": "A read-only key can see your flows, campaigns and orders. It can't send, edit or delete anything."}])


def connect_help(b: "Brand", u: "User") -> Email:
    return Email("What the audit reads, and what it can't touch",
                 "Read-only, encrypted, and you can revoke it from Klaviyo any time.",
                 [{"p": f"Hi {_first(u)},"},
                  {"p": "Before you hand over a key, here's exactly what happens with it:"},
                  {"items": [{"title": "It's read-only", "detail": "Wavy's Klaviyo client refuses to send any request that would change your account."},
                             {"title": "It's encrypted", "detail": "The key is sealed in Wavy's vault and only opened for the moment a read needs it."},
                             {"title": "You stay in control", "detail": "Delete the key in Klaviyo and Wavy's access ends right away."}]},
                  {"p": "What you get back: every flow and email mapped with what it earns, the fixes ranked by what they're worth, "
                        "and a plan to the first $10K of measured lift."},
                  {"button": ("Connect Klaviyo", b.links.connect)},
                  {"note": "Stuck on the key? Reply and we'll walk you through it."}])


def connect_last(b: "Brand", u: "User") -> Email:
    return Email("Should we keep your workspace open?", "The last reminder about connecting Klaviyo.",
                 [{"p": f"Hi {_first(u)},"},
                  {"p": f"The {b.client.name} workspace is still waiting on a Klaviyo key. This is the last reminder we'll send."},
                  {"p": "If now isn't the right time, no problem: the workspace stays here, and the audit starts whenever you connect."},
                  {"button": ("Connect Klaviyo", b.links.connect)},
                  {"note": "If something about the key or the product put you off, reply and tell us. It changes what we build."}])


# ---------------------------------------------------------------- the audit

def audit_started(b: "Brand", u: "User") -> Email:
    return Email(f"Your {b.client.name} audit is running", "We'll email you the moment it's ready.",
                 [{"p": f"Hi {_first(u)},"},
                  {"p": "Wavy is reading your Klaviyo account now:"},
                  {"steps": ["Every flow, split and email, with what each one earned over 90 and 365 days.",
                             "24 months of orders and account totals, so the opportunities are sized from your own numbers.",
                             "Templates, deliverability, forms, lists and segments."]},
                  {"p": "You can close the tab. We'll email you as soon as it's ready. "
                        "The bigger the order history, the longer it takes."},
                  {"button": ("Watch it run", b.links.connect)}])


def audit_problem(b: "Brand", u: "User") -> Email:
    p = b.problem or {}
    if p.get("key"):
        return Email(f"Your {b.client.name} audit needs a new key", "Klaviyo turned the key down. A new read-only key fixes it.",
                     [{"p": f"Hi {_first(u)},"},
                      {"p": "Klaviyo refused the key Wavy was given, so the audit stopped at: " + p.get("stage", "connecting") + "."},
                      {"p": "This usually means the key was deleted, or it doesn't have read access. A fresh read-only key fixes it:"},
                      {"steps": KEY_STEPS},
                      {"button": ("Paste a new key", b.links.connect)}])
    return Email(f"Your {b.client.name} audit hit a snag", "It's on our side. We're on it.",
                 [{"p": f"Hi {_first(u)},"},
                  {"p": "The audit stopped at: " + p.get("stage", "a collection step") + ". Your key is fine; nothing for you to do."},
                  {"p": "Wavy's team has been alerted and will restart it. We'll email you when it's ready."},
                  {"note": "Reply if you'd like an update."}])


def audit_ready(b: "Brand", u: "User") -> Email:
    total, top = _total(b), _top(b)
    stats = []
    if b.summary.get("score") is not None:
        stats.append(("Audit score", f"{b.summary['score']:.0f}/100" if isinstance(b.summary["score"], (int, float)) else str(b.summary["score"])))
    if total.get("base") is not None:
        stats.append(("Sized opportunity a year", money(total["base"])))
    if b.facts.get("journey_map.live_emails") is not None:
        stats.append(("Live flow emails mapped", f"{b.facts['journey_map.live_emails']:,.0f}"))
    blocks = [{"p": f"Hi {_first(u)},"},
              {"p": f"The {b.client.name} audit is done. Here's the short version:"}]
    if stats:
        blocks.append({"stats": stats[:3]})
    if top:
        blocks += [{"h": "Where the money is" if any(_sized(c) for c in top) else "Fix these first"}, {"items": _items(top)}]
    if total.get("base") is not None:
        blocks.append({"note": f"Sized from your account's own numbers: {money(total.get('low'))} to {money(total.get('high'))} a year "
                               "across the levers, counting each customer group once. These are estimates; "
                               "the lift Wavy reports later is measured."})
    if b.facts.get("journey_map.messages_with_suggestions"):
        blocks.append({"p": f"Your journey map is in there too: every live flow email with its numbers, and "
                            f"{b.facts['journey_map.messages_with_suggestions']:,.0f} with a specific improvement."})
    blocks += [{"button": ("Open your audit", b.links.workspace)},
               {"p": "Next: approve the fixes you want. Wavy ships them and measures each one. " + GOAL_LINE}]
    return Email(f"Your {b.client.name} audit is ready", "The fixes worth making, ranked by what they're worth.", blocks)


def questions_nudge(b: "Brand", u: "User") -> Email:
    return Email("The answers that sharpen your plan", "Your answers change what Wavy recommends.",
                 [{"p": f"Hi {_first(u)},"},
                  {"p": "The audit can read what your account does, but not what you want it to do. A few answers change the plan:"},
                  {"steps": ["Anything regulated about what you sell, so no email makes a claim it shouldn't.",
                             "How you feel about discounting, so flows don't train customers to wait for one.",
                             "Your top goals for the next 90 days, so the fixes are ranked toward them."]},
                  {"button": ("Answer the questions", b.links.questions)},
                  {"note": "Answers save as you go, so you can do a section at a time."}])


def review_nudge(b: "Brand", u: "User") -> Email:
    top = _top(b, 1)
    blocks = [{"p": f"Hi {_first(u)},"}, {"p": f"Your {b.client.name} audit is ready and hasn't been opened yet."}]
    if top:
        c = top[0]
        blocks += [{"h": "The biggest fix on the list"}, {"items": _items([c])}]
        if c.get("steps"):
            blocks += [{"p": "What it takes:"}, {"steps": c["steps"][:4]}]
    blocks += [{"button": ("See the full list", b.links.opportunities)}]
    return Email(f"The biggest fix in your {b.client.name} audit", "One fix, what it's worth, and what it takes.", blocks)


def story_ready(b: "Brand", u: "User") -> Email:
    return Email(f"Your strategist's read on {b.client.name} is in", "The written analysis is now in your audit.",
                 [{"p": f"Hi {_first(u)},"},
                  {"p": "A Wavy strategist has reviewed your audit. The written parts are in your workspace: what the numbers "
                        "mean for your brand, the copy for each recommended email, and rewrites of your weakest live emails."},
                  {"button": ("Read it", b.links.workspace)}])


def approve_fixes(b: "Brand", u: "User") -> Email:
    top = _top(b)
    blocks = [{"p": f"Hi {_first(u)},"},
              {"p": "Results are measured from the day a fix goes live, so the sooner one is approved, the more of your first 30 days it counts for. These are at the top of your list:"}]
    if top:
        blocks.append({"items": _items(top)})
    blocks += [{"p": "Approve one and Wavy builds it, then measures what it earns."},
               {"button": ("Approve your first fixes", b.links.opportunities)},
               {"note": f"Day {b.day} of 30. {GOAL_LINE}"}]
    return Email("Pick your first three fixes", "The fastest path to your first measured $10K.", blocks)


# ---------------------------------------------------------------- shipping and measuring

def write_access(b: "Brand", u: "User") -> Email:
    return Email("Let Wavy make the changes for you", "One more key, so approved fixes ship themselves.",
                 [{"p": f"Hi {_first(u)},"},
                  {"p": "You've approved fixes. To build them in Klaviyo, Wavy needs a key that can write. "
                        "Every change still waits for your approval before anything goes live."},
                  {"button": ("Add the key", b.links.connect)}])


def fix_live(b: "Brand", u: "User") -> Email:
    fx = [x for x in b.progress["fixes"] if x["shipped_at"]]
    first = fx[0] if fx else {}
    return Email("Your first fix is live", "Wavy is measuring it from today.",
                 [{"p": f"Hi {_first(u)},"},
                  {"p": f"\"{first.get('title', 'Your first fix')}\" is live in Klaviyo."},
                  {"p": "Wavy compares what the changed emails earn per recipient before and after, adjusted for the "
                        "trend in the flows it didn't touch. The first reading lands after 7 days of sends."},
                  {"button": ("See progress", b.links.workspace)},
                  {"note": GOAL_LINE}])


def _progress_blocks(b: "Brand") -> list[dict]:
    p = b.progress
    return [{"progress": {"value": p["measured"], "goal": p["goal"],
                          "label": f"{money(p['measured'])} measured of {money(p['goal'])}"}},
            {"stats": [("Measured lift", money(p["measured"])), ("Attributed (not counted)", money(p["attributed"])),
                       ("Fixes live", f"{p['live']}")]}]


def _fix_items(b: "Brand") -> list[dict]:
    label = {"measured": None, "too_small": "Too few sends to read yet", "pending": "Lift pending: new flow",
             "waiting": "First reading after 7 days live", "live": "Live", "approved": "Approved, not live yet",
             "no_targets": "Live; not tied to a flow"}
    return [{"title": x["title"], "detail": x["reason"] if x["state"] in ("too_small", "pending") else (label.get(x["state"]) or ""),
             "value": money(x["lift"]) if x["lift"] is not None else ""} for x in b.progress["fixes"][:6]]


def first_lift(b: "Brand", u: "User") -> Email:
    return Email("Your first measured result", "What your first live fix has earned so far.",
                 [{"p": f"Hi {_first(u)},"}, {"p": "The first reading is in:"}, *_progress_blocks(b),
                  {"items": _fix_items(b)},
                  {"note": "Measured lift: revenue per recipient before vs since the change, adjusted for the trend in "
                           "the flows it didn't touch. Attributed revenue is Klaviyo's credit, shown for context and never counted."},
                  {"button": ("See the details", b.links.workspace)}])


def weekly_impact(b: "Brand", u: "User") -> Email:
    p = b.progress
    blocks = [{"p": f"Hi {_first(u)},"}, {"p": f"Day {p['day']} of 30. Here's where {b.client.name} stands:"},
              *_progress_blocks(b), {"items": _fix_items(b)}]
    if b.next_step and b.next_step["owner"] == "you":
        blocks += [{"h": "Your next move"}, {"p": b.next_step["detail"]}, {"button": (b.next_step["cta"], b.next_step["url"])}]
    else:
        blocks.append({"button": ("Open your workspace", b.links.workspace)})
    return Email(f"{b.client.name}: {money(p['measured'])} measured so far", f"Week {max(1, (p['day'] - 1) // 7)} of your first 30 days.", blocks)


def milestone_10k(b: "Brand", u: "User") -> Email:
    return Email(f"{b.client.name} passed $10K in measured lift", "Measured, not attributed.",
                 [{"p": f"Hi {_first(u)},"},
                  {"p": f"The fixes you approved have now added {money(b.progress['measured'])} in measured lift."},
                  *_progress_blocks(b), {"items": _fix_items(b)},
                  {"p": "The next fixes on your list are already ranked. Keep going."},
                  {"button": ("See what's next", b.links.opportunities)}])


def day30_summary(b: "Brand", u: "User") -> Email:
    p = b.progress
    blocks = [{"p": f"Hi {_first(u)},"}, {"p": f"Your first 30 days with Wavy are done. Here's the full picture for {b.client.name}:"},
              *_progress_blocks(b)]
    if p["fixes"]:
        blocks.append({"items": _fix_items(b)})
    blocks += [{"p": "Measurement keeps running on every live fix, and the weekly refresh keeps re-ranking what's next."},
               {"button": ("Plan the next 30 days", b.links.opportunities)}]
    return Email(f"Your first 30 days: {money(p['measured'])} measured", "What shipped, what it earned, and what's next.", blocks)


def stalled(b: "Brand", u: "User") -> Email:
    step = b.next_step or {}
    return Email(f"{b.client.name} is halfway through its first 30 days", "One thing is holding up your results.",
                 [{"p": f"Hi {_first(u)},"},
                  {"p": f"Day {b.day} of 30, and nothing has gone live yet. The one thing in the way:"},
                  {"items": [{"title": step.get("title", "Approve your first fix"), "detail": step.get("detail", "")}]},
                  {"button": (step.get("cta", "Open your workspace"), step.get("url", b.links.workspace))},
                  {"note": "Reply if something's blocking you. We'll help clear it."}])


# ---------------------------------------------------------------- staff alerts

def staff_signup(b: "Brand", u: "User") -> Email:
    return Email(f"New sign-up: {b.client.name}", "A brand signed up for Wavy OS.",
                 [{"p": f"{b.client.name} signed up" + (f" ({b.client.website})" if b.client.website else "") + "."},
                  {"button": ("Open in the console", b.links.console)}])


def staff_review_needed(b: "Brand", u: "User") -> Email:
    return Email(f"Review needed: {b.client.name}'s audit", "The written parts wait for a strategist. Target: 24 hours.",
                 [{"p": f"{b.client.name}'s audit is published with numbers only. The written sections wait for your review."},
                  {"button": ("Review drafts", b.links.console)}])


def staff_fixes_approved(b: "Brand", u: "User") -> Email:
    waiting = [x for x in b.progress["fixes"] if not x["shipped_at"]]
    return Email(f"Ship it: {b.client.name} approved {len(waiting)} fix(es)", "Approved fixes are waiting to go live.",
                 [{"p": f"{b.client.name} approved fixes. Each day before they're live is a day off their 30-day clock."},
                  {"items": [{"title": x["title"], "detail": f"approved {x['approved_at'][:10]}"} for x in waiting[:8]]},
                  {"button": ("Open in the console", b.links.console)}])


def staff_stalled(b: "Brand", u: "User") -> Email:
    step = b.next_step or {}
    return Email(f"Stalled: {b.client.name} (day {b.day})", "Nothing live at day 14.",
                 [{"p": f"{b.client.name} has nothing live at day {b.day}."},
                  {"items": [{"title": "Blocker: " + step.get("title", "unknown"), "detail": step.get("detail", "")}]},
                  {"button": ("Open in the console", b.links.console)}])


def staff_audit_failed(b: "Brand", u: "User") -> Email:
    p = b.problem or {}
    return Email(f"Audit failed: {b.client.name}", "A collection step failed and the brand was told we're on it.",
                 [{"p": f"{b.client.name}'s audit stopped at {p.get('stage', 'a collection step')}."},
                  {"note": (p.get("error") or "")[:400]},
                  {"button": ("Open in the console", b.links.console)}])
