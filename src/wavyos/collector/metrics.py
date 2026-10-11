"""Which Klaviyo metric is the real Placed Order (or Received SMS, ...). Port of metric-selection.js
and the pin half of source-contract.js.

Stores that switched platforms keep dormant metrics with the same name, so selection never takes the
first match: it prefers the canonical source, and a tie that remains is `ambiguous`, recorded with
every candidate, and left for staff to pin.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("Started Checkout", "Checkout Started"),
    ("Added to Cart", "Add to Cart"),
    ("Received SMS", "Received Text Message"),
    ("Clicked SMS", "Clicked Text Message"),
    ("Failed to Deliver SMS", "Failed to Deliver Text Message"),
    ("Subscribed to SMS Marketing", "Subscribed to Text Messaging Marketing"),
    ("Unsubscribed from SMS Marketing", "Unsubscribed from SMS", "Unsubscribed from Text Messaging Marketing"),
)
_SHOPIFY = re.compile(r"\bshopify\b", re.I)
_ECOMMERCE = re.compile(r"shopify|woocommerce|bigcommerce|magento|salesforce commerce|wix|squarespace|ecommerce", re.I)
_KLAVIYO = re.compile(r"\bklaviyo\b", re.I)


class PinError(ValueError):
    """A staff pin no longer matches the account. The audit stops rather than guess."""


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def integration_name(metric: dict) -> Optional[str]:
    integ = metric.get("integration")
    return integ if isinstance(integ, str) else (integ or {}).get("name")


def _integration_text(metric: dict) -> str:
    integ = metric.get("integration")
    if isinstance(integ, str):
        return integ
    integ = integ or {}
    return f"{integ.get('name') or ''} {integ.get('key') or ''} {integ.get('category') or ''}"


def aliases(name: str) -> list[str]:
    for group in ALIAS_GROUPS:
        if any(_norm(a) == _norm(name) for a in group):
            return list(group)
    return [name]


def flatten(resource: dict) -> dict:
    """A JSON:API metric resource -> {id, name, integration, created, updated} (the collector's metric shape)."""
    a = resource.get("attributes") or {}
    return {"id": resource.get("id"), "name": a.get("name"), "integration": a.get("integration") or None,
            "created": a.get("created"), "updated": a.get("updated")}


def resolve_pinned(metrics: list[dict], names: Iterable[str], pins: Optional[dict[str, dict]]) -> Optional[dict]:
    """The pinned metric for these names, checked against the account; None when nothing is pinned.

    pins: {event name: {"id": ..., "integration": ... or None}}, from the metric_pins table.
    """
    if not pins:
        return None
    wanted = {_norm(a) for n in names for a in aliases(n)}
    hits = {p["id"]: (key, p) for key, p in pins.items() if _norm(key) in wanted}
    if not hits:
        return None
    if len(hits) > 1:
        raise PinError(f"different metrics are pinned for {' / '.join(names)}")
    key, pin = next(iter(hits.values()))
    metric = next((m for m in metrics if m.get("id") == pin["id"]), None)
    if metric is None:
        raise PinError(f"{key} is pinned to {pin['id']}, which is no longer in this account's metrics")
    if _norm(metric.get("name")) not in wanted:
        raise PinError(f"pinned metric {pin['id']} is now called {metric.get('name')!r}, not {key!r}")
    if pin.get("integration") and _norm(integration_name(metric)) != _norm(pin["integration"]):
        raise PinError(f"pinned metric {pin['id']} comes from {integration_name(metric)!r}, "
                       f"not {pin['integration']!r}")
    return metric


def pick_metric(metrics: list[dict], names: str | list[str], prefer: Optional[str] = None,
                pins: Optional[dict[str, dict]] = None) -> dict:
    """{metric, candidates, status: selected|missing|ambiguous, reason}. prefer: 'ecommerce' | 'klaviyo' | None."""
    names = [names] if isinstance(names, str) else list(names)
    pinned = resolve_pinned(metrics, names, pins)
    if pinned is not None:
        wanted = {_norm(a) for n in names for a in aliases(n)}
        return {"metric": pinned, "candidates": [m for m in metrics if _norm(m.get("name")) in wanted],
                "status": "selected", "reason": f"Pinned by staff ({pinned['id']})"}
    priority: dict[str, int] = {}
    for i, name in enumerate(names):
        for alias in aliases(name):
            priority.setdefault(_norm(alias), i)
    candidates = [m for m in metrics if _norm(m.get("name")) in priority]
    pool, reason = candidates, "Only matching metric"
    if prefer == "ecommerce":
        shopify = [m for m in candidates if _SHOPIFY.search(_integration_text(m))]
        ecommerce = [m for m in candidates if _ECOMMERCE.search(_integration_text(m))]
        if shopify:
            pool, reason = shopify, "Canonical Shopify commerce source"
        elif ecommerce:
            pool, reason = ecommerce, "Only matching ecommerce metric; no Shopify candidate"
    elif prefer == "klaviyo":
        klaviyo = [m for m in candidates if _KLAVIYO.search(_integration_text(m))]
        if klaviyo:
            pool, reason = klaviyo, "Klaviyo channel metric"
    if not pool:
        return {"metric": None, "candidates": candidates, "status": "missing", "reason": "No matching metric name or alias"}
    # Name order only breaks ties inside ONE integration; it never chooses between integrations.
    if len(pool) > 1 and len({_norm(integration_name(m)) for m in pool}) == 1:
        best = min(priority[_norm(m.get("name"))] for m in pool)
        preferred = [m for m in pool if priority[_norm(m.get("name"))] == best]
        if len(preferred) < len(pool):
            reason += "; preferred requested metric name within the same integration"
        pool = preferred
    if len(pool) > 1:
        return {"metric": None, "candidates": candidates, "status": "ambiguous",
                "reason": f"Multiple eligible metrics remain ({', '.join(str(m.get('id')) for m in pool)}); "
                          "explicit source/metric selection required"}
    return {"metric": pool[0], "candidates": candidates, "status": "selected", "reason": reason}


def describe(choice: dict) -> dict:
    """The metricChoices entry step 38 writes: {used, candidates, otherCandidates, status, reason}."""
    def summary(m: dict) -> dict:
        return {"id": m.get("id"), "name": m.get("name"), "integration": integration_name(m)}
    used = choice.get("metric")
    cands = choice.get("candidates") or []
    return {"used": summary(used) if used else None, "candidates": [summary(m) for m in cands],
            "otherCandidates": [summary(m) for m in cands if not used or m.get("id") != used.get("id")],
            "status": choice.get("status"), "reason": choice.get("reason")}
