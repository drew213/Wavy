"""The lifecycle onboarding questionnaire: eight sections a brand fills in before its audit.

The same questions as the Wavy onboarding artifact. Answers save as the brand types (one row per
client), so they can stop and come back. On submit, the answers that drive the build (voice, words
to avoid, hero products, discount stance, margins, repurchase timing) are mapped onto the
onboarding payload; every answer is kept in the dossier for the team.
"""
from __future__ import annotations

import re
from typing import Any, Optional

T, C, M = "text", "choice", "multi"
LONG, SHORT = "long", "short"
STORE_PLATFORMS = ["Shopify", "WooCommerce", "BigCommerce", "Magento", "Other"]
KPI_OPTIONS = ["Gross revenue", "Net revenue", "Email revenue (campaigns + flows)", "SMS revenue", "Total buyers",
               "New customers", "Average order value", "Repeat purchase rate", "Email list size", "Pop-up sign-ups"]
REGULATED = ["No", "CBD / hemp / THC", "Supplements", "Alcohol", "Tobacco / nicotine", "Other regulated"]


def _q(key, label, kind=T, *, required=False, help="", options=(), size=LONG, placeholder=""):
    return {"key": key, "label": label, "kind": kind, "required": required, "help": help,
            "options": list(options), "size": size, "placeholder": placeholder}


SECTIONS = [
    {"key": "business", "title": "Your business",
     "intro": "The big picture: what you sell, where revenue comes from, and what the next 90 days need to deliver.",
     "questions": [
         _q("what_you_sell", "In a sentence or two: what do you sell, who is it for, and why do people choose you over the alternatives?", required=True),
         _q("goals_90d", "What are your top 3 goals for the next 90 days?", required=True,
            placeholder="e.g. grow repeat revenue, launch the new collection, rebuild the welcome series"),
         _q("channel_split", "Roughly how does revenue split across channels, and how much comes from email and SMS today?",
            help="Your site, Amazon, wholesale, retail. Ballpark percentages are fine."),
         _q("economics", "Average order value, gross margin range and repeat purchase rate, if you know them."),
         _q("seasonality", "Which are your busiest and slowest months? Anything seasonal we should plan around?"),
     ]},
    {"key": "customer", "title": "Your customer", "intro": "Who buys, why they hesitate, and why they come back (or don't).",
     "questions": [
         _q("customer", "Describe your main customer, and a second one if you have one.", required=True,
            help="Age, lifestyle, what triggers them to buy."),
         _q("hesitations", "What are the top 3 hesitations people have before their first purchase?"),
         _q("why_return", "Why do customers come back and buy again?"),
         _q("why_stop", "Why do customers stop buying or return items?"),
         _q("support_questions", "What does your support team get asked every day?"),
         _q("customer_links", "Links to reviews, surveys or customer content we can learn from."),
     ]},
    {"key": "products", "title": "Products", "intro": "Where customers start, what they buy next, and what's coming.",
     "questions": [
         _q("hero_products", "What are your hero products, and which ones do most new customers buy first?", required=True),
         _q("second_purchase", "What's the best second purchase, and how do customers usually move between categories?"),
         _q("consumable_days", "For anything consumable: how long does it usually last before a customer runs out?"),
         _q("launches", "Launches, restocks and drops planned for the next 6 months, with rough dates."),
         _q("dont_push", "Any products we shouldn't push (low stock, low margin, being discontinued)?"),
     ]},
    {"key": "offers", "title": "Offers and policies", "intro": "How you think about discounts, and the rules we should never break.",
     "questions": [
         _q("discount_stance", "How do you feel about discounting?", C, required=True,
            options=["We avoid discounts", "Occasional promotions only", "Regular promotions are fine", "Promotions drive most of our sales"]),
         _q("deepest_discount", "What's the deepest discount you're comfortable with, and what's off-limits?", required=True),
         _q("welcome_offer", "What is your current welcome offer?", size=SHORT),
         _q("programs", "Which of these do you run?", M,
            options=["Loyalty / rewards", "Referral program", "Subscriptions", "Bundles", "None of these"]),
         _q("program_apps", "Which apps run them?", size=SHORT),
         _q("shipping_returns", "Free shipping threshold and return policy."),
         _q("sales_calendar", "Your sales calendar: BFCM, anniversary, and any moments specific to your brand."),
     ]},
    {"key": "brand", "title": "Brand and creative", "intro": "How you sound, what you look like, and what we must never say.",
     "questions": [
         _q("voice_words", "Three words that describe your brand voice.", required=True, size=SHORT),
         _q("never_sound_like", "Three words your brand should never sound like.", size=SHORT),
         _q("admired_brands", "2–3 brands whose emails or texts you admire, and why."),
         _q("avoid_claims", "Words, claims or topics we must avoid.", required=True,
            help="Legal, medical and competitor claims matter most, especially for supplements and wellness."),
         _q("brand_guidelines", "Link to brand guidelines, logos, fonts and colors."),
         _q("assets_link", "Where do photos, video and UGC live? Please share a link with access for the Wavy team."),
         _q("founder_appears", "Can the founder or face of the brand sign or appear in some sends?", C,
            options=["Yes, regularly", "Occasionally", "No"]),
     ]},
    {"key": "email_sms", "title": "Your email and SMS today",
     "intro": "Your view of what's running now. We'll confirm the details in our own audit, so best guesses are fine.",
     "questions": [
         _q("whats_working", "What's working, what isn't, and what have you already tried?"),
         _q("live_flows", "Which automated flows do you believe are live, and who built them?"),
         _q("campaign_frequency", "How often do you send campaigns today?", size=SHORT),
         _q("deliverability", "Any past deliverability problems, spam complaints or list cleanups?"),
         _q("platform_switch", "Have you switched email or SMS platforms recently?", size=SHORT),
     ]},
    {"key": "tools", "title": "Tools and access", "intro": "What you use, and what access the Wavy team needs.",
     "questions": [
         _q("store_platform", "Store platform", C, required=True, options=STORE_PLATFORMS,
            help="Where your orders are placed. Klaviyo often keeps old order sources after a platform switch."),
         _q("regulated_category", "Does anything you sell fall in a regulated category?", C, options=REGULATED,
            help="Some categories can't use certain channels or claims (CBD and THC can't send Klaviyo SMS)."),
         _q("email_platform", "Email platform", C, required=True, options=["Klaviyo", "Omnisend", "Mailchimp", "Other"]),
         _q("sms_platform", "SMS platform", C, required=True,
            options=["Klaviyo SMS", "Attentive", "Postscript", "One Text", "Other", "We don't send SMS yet"]),
         _q("other_tools", "Other tools we should know about.", help="Reviews, loyalty, subscriptions, helpdesk, popups and forms, quizzes."),
         _q("access_given", "Which access have you already given the Wavy team?", M,
            options=["Email platform", "SMS platform", "Shopify (collaborator)", "Store admin (WooCommerce or other)",
                     "Reviews app", "Asset library", "None yet"]),
         _q("dns_owner", "Who can make DNS changes for your sending domain?", size=SHORT),
         _q("integrations", "Any custom integrations or events (quiz, app, wholesale portal)?"),
     ]},
    {"key": "together", "title": "Working together", "intro": "Who we work with, who signs off, and what success looks like.",
     "questions": [
         _q("primary_contact", "Primary contact", required=True, size=SHORT),
         _q("final_approver", "Who gives final approval on emails and texts?", required=True, size=SHORT),
         _q("other_reviewers", "Anyone else who needs to see work before it goes live (legal, founder, ops)?"),
         _q("approval_speed", "How quickly can you usually turn around approvals?", C,
            options=["Same day", "Within 24 hours", "Within 48 hours", "Longer"]),
         _q("talk_channel", "Where do you prefer to talk day to day?", C, options=["Slack Connect", "Email", "Either"]),
         _q("meeting_cadence", "How often would you like a meeting?", C, options=["Weekly", "Every two weeks", "Monthly"]),
         _q("success", "What does success look like at 30, 60 and 90 days?", required=True),
         _q("kpis", "Which numbers will you judge us on?", M, options=KPI_OPTIONS,
            help="Your workspace and every monthly report open with these."),
         _q("past_agency", "Anything that went wrong with past agencies that you want us to avoid?"),
     ]},
]
QUESTIONS = {q["key"]: q for s in SECTIONS for q in s["questions"]}
SUBMITTER = "submitted_by_name"
MAX_TEXT = 4000


def clean(answers: dict) -> dict:
    """Keep only known questions with well-formed values; drop empties. Raises ValueError on bad input."""
    out: dict[str, Any] = {}
    for key, value in (answers or {}).items():
        if key == SUBMITTER:
            if value:
                out[key] = str(value).strip()[:200]
            continue
        q = QUESTIONS.get(key)
        if q is None:
            raise ValueError(f"unknown question {key!r}")
        if value in (None, "", []):
            continue
        if q["kind"] == T:
            if not isinstance(value, str):
                raise ValueError(f"{key} must be text")
            if len(value) > MAX_TEXT:
                raise ValueError(f"keep {key} under {MAX_TEXT} characters")
            if value.strip():
                out[key] = value
        elif q["kind"] == C:
            if value not in q["options"]:
                raise ValueError(f"{value!r} isn't an option for {key}")
            out[key] = value
        else:
            if not isinstance(value, list) or any(v not in q["options"] for v in value):
                raise ValueError(f"{key} must be a list of its options")
            out[key] = list(dict.fromkeys(value))
    return out


def missing_required(answers: dict) -> list[str]:
    missing = [q["label"] for q in QUESTIONS.values() if q["required"] and not answers.get(q["key"])]
    if not answers.get(SUBMITTER):
        missing.append("Your name")
    return missing


def progress(answers: dict) -> dict:
    return {"answered": sum(1 for k in QUESTIONS if answers.get(k)), "total": len(QUESTIONS)}


# ---------------------------------------------------------------- answers -> onboarding payload

_STANCE = {"We avoid discounts": "NEVER_DISCOUNT", "Occasional promotions only": "VALUE_ADD_ONLY",
           "Regular promotions are fine": "MODERATE", "Promotions drive most of our sales": "AGGRESSIVE"}


def _words(text: Optional[str], limit: int = 12) -> list[str]:
    parts = re.split(r"[,;\n/]|\band\b", text or "")
    return [p.strip(" .-•*\t").lower() for p in parts if p.strip(" .-•*\t")][:limit]


def _first_number(pattern: str, text: str) -> Optional[float]:
    m = re.search(pattern, text or "", re.IGNORECASE)
    return float(m.group(1).replace(",", "")) if m else None


def _days(text: Optional[str]) -> Optional[int]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(day|week|month)", text or "", re.IGNORECASE)
    if not m:
        return None
    return round(float(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2).lower()])


def payload_fields(answers: dict) -> dict:
    """What the questionnaire contributes to OnboardingIntakePayload. Anything not answered is left
    out, so the payload keeps its defaults (and the measured Klaviyo numbers win where they exist)."""
    a = answers
    f: dict[str, Any] = {"intake": dict(a)}
    if a.get("goals_90d"):
        f["primary_goal_90d"] = a["goals_90d"].strip()
    if a.get("store_platform") and a["store_platform"] != "Other":
        f["store_platform"] = a["store_platform"]
    voice = _words(a.get("voice_words"), 5)
    if voice:
        f["tone_descriptors"] = voice
        f["voice_archetype"] = ", ".join(w.capitalize() for w in voice[:3])
    forbidden = [w for w in _words(a.get("never_sound_like"), 6) + _words(a.get("avoid_claims"), 20) if len(w.split()) <= 4]
    if forbidden:
        f["forbidden_words"] = list(dict.fromkeys(forbidden))
    if a.get("avoid_claims"):
        f["regulated_limits"] = a["avoid_claims"].strip()
    heroes = [h for h in re.split(r"[,;\n]", a.get("hero_products") or "") if h.strip(" .-•*")][:4]
    if heroes:
        f["hero_products"] = [{"name": h.strip(" .-•*"), "sku": f"SKU-H{i + 1}"} for i, h in enumerate(heroes)]
    if a.get("discount_stance"):
        f["discount_rules"] = f"{_STANCE[a['discount_stance']]}: {(a.get('deepest_discount') or '').strip()}".strip(": ")
    tools = [t for t in (a.get("email_platform"), a.get("sms_platform")) if t and t != "We don't send SMS yet"]
    if tools:
        f["messaging_tools"] = tools
    if "Subscriptions" in (a.get("programs") or []):
        f["subscription_app"] = (a.get("program_apps") or "subscriptions").strip()
    shipping = _first_number(r"free[^$\d]{0,30}\$?\s*(\d[\d,]*(?:\.\d+)?)", a.get("shipping_returns", "")) \
        or _first_number(r"\$\s*(\d[\d,]*(?:\.\d+)?)", a.get("shipping_returns", ""))
    if shipping:
        f["free_shipping_threshold"] = shipping
    econ = a.get("economics", "")
    margin = _first_number(r"margin[^\d]{0,25}(\d{1,2}(?:\.\d+)?)\s*%", econ) or _first_number(r"(\d{1,2}(?:\.\d+)?)\s*%[^.\n]{0,20}margin", econ)
    if margin:
        f["gross_margin_pct"] = margin
    aov = _first_number(r"(?:aov|average order(?: value)?)[^\d$]{0,20}\$?\s*(\d[\d,]*(?:\.\d+)?)", econ)
    if aov:
        f["target_aov"] = aov
    cycle = _days(a.get("consumable_days"))
    if cycle:
        f["typical_consumption_days"] = cycle
    return f
