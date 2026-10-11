"""Product titles from a Placed Order event. Port of order-items.js.

Only titles are kept. Whole line items can carry customer or custom properties, so they are never stored.
"""
from __future__ import annotations

import re
from typing import Any

_INDEX = re.compile(r"^(0|[1-9]\d*)$")
_TITLE_FIELDS = ("ProductName", "Name", "name", "title", "Title", "product_title")


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict) or not value or not all(isinstance(k, str) and _INDEX.match(k) for k in value):
        return []
    return [value[k] for k in sorted(value, key=int)]


def _title(item: Any):
    def clean(v):
        return v.strip() if isinstance(v, str) and v.strip() else None
    if isinstance(item, str):
        return clean(item)
    if not isinstance(item, dict):
        return None
    for f in _TITLE_FIELDS:
        t = clean(item.get(f))
        if t:
            return t
    return None


def item_names(properties: Any) -> list[str]:
    if not isinstance(properties, dict):
        return []
    extra = properties.get("$extra") if isinstance(properties.get("$extra"), dict) else {}
    for source in (properties.get("ItemNames"), properties.get("Items"), extra.get("line_items"),
                   properties.get("line_items")):
        titles = [t for t in (_title(x) for x in _as_list(source)) if t]
        if titles:
            return titles
    return []
