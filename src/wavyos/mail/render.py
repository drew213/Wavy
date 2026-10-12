"""Email layout: a few block types rendered to HTML (inline styles, table layout, 600px) and to plain text.

Templates hand over blocks, never HTML, so every value is escaped once, here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import Any, Optional

INK, MUTED, LINE, PAPER, BAND, LIME = "#16181a", "#5d6468", "#e4e6e3", "#f4f4f1", "#0b0c0d", "#c8ff00"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"


@dataclass
class Email:
    subject: str
    preheader: str
    blocks: list[dict] = field(default_factory=list)


def money(v: Optional[float]) -> str:
    if v is None:
        return "-"
    v = float(v)
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if abs(v) >= 10_000:
        return f"${v / 1000:.0f}K"
    return f"${v:,.0f}"


def _compact(v: Optional[float]) -> str:
    if v is None:
        return "-"
    v = float(v)
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if abs(v) >= 1000:
        k = v / 1000
        return f"${k:.0f}K" if abs(k) >= 10 else f"${k:.1f}K".replace(".0K", "K")
    return f"${v:,.0f}"


def money_range(annual: Optional[dict]) -> str:
    if not annual:
        return "not sized"
    return f"{_compact(annual.get('low'))}–{_compact(annual.get('high'))} a year"


def _e(v: Any) -> str:
    return escape(str(v), quote=True)


def _block_html(b: dict) -> str:
    if "h" in b:
        return f'<h2 style="margin:28px 0 8px;font:600 17px/1.35 {FONT};color:{INK}">{_e(b["h"])}</h2>'
    if "p" in b:
        return f'<p style="margin:0 0 14px;font:400 15px/1.6 {FONT};color:{INK}">{_e(b["p"])}</p>'
    if "note" in b:
        return f'<p style="margin:0 0 14px;font:400 13px/1.55 {FONT};color:{MUTED}">{_e(b["note"])}</p>'
    if "stats" in b:
        cells = "".join(
            f'<td style="padding:14px 12px;border:1px solid {LINE};border-radius:8px;vertical-align:top" width="{100 // len(b["stats"])}%">'
            f'<div style="font:600 22px/1.2 {FONT};color:{INK}">{_e(v)}</div>'
            f'<div style="margin-top:4px;font:400 12px/1.4 {FONT};color:{MUTED}">{_e(label)}</div></td>'
            for label, v in b["stats"])
        return (f'<table role="presentation" width="100%" cellspacing="6" cellpadding="0" style="margin:6px -6px 14px">'
                f'<tr>{cells}</tr></table>')
    if "items" in b:
        rows = "".join(
            f'<tr><td style="padding:12px 0;border-top:1px solid {LINE};vertical-align:top">'
            f'<div style="font:600 15px/1.4 {FONT};color:{INK}">{_e(i["title"])}</div>'
            + (f'<div style="margin-top:3px;font:400 13px/1.5 {FONT};color:{MUTED}">{_e(i["detail"])}</div>' if i.get("detail") else "")
            + '</td>'
            + (f'<td style="padding:12px 0 12px 12px;border-top:1px solid {LINE};vertical-align:top;text-align:right;'
               f'white-space:nowrap;font:600 14px/1.4 {FONT};color:{INK}">{_e(i["value"])}</td>' if i.get("value") else "<td></td>")
            + '</tr>' for i in b["items"])
        return f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:4px 0 14px">{rows}</table>'
    if "steps" in b:
        lis = "".join(f'<li style="margin:0 0 8px">{_e(s)}</li>' for s in b["steps"])
        return f'<ol style="margin:0 0 14px;padding-left:22px;font:400 15px/1.55 {FONT};color:{INK}">{lis}</ol>'
    if "progress" in b:
        pr = b["progress"]
        share = max(0.0, min(1.0, (pr["value"] or 0) / pr["goal"])) if pr.get("goal") else 0.0
        fill = f'<td width="{max(1, round(share * 100))}%" style="background:{LIME};height:10px;font-size:0;line-height:0">&nbsp;</td>' if share > 0 else ""
        return (f'<div style="margin:6px 0 4px;font:600 15px/1.4 {FONT};color:{INK}">{_e(pr["label"])}</div>'
                f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 14px;background:{LINE};border-radius:5px">'
                f'<tr>{fill}<td style="height:10px;font-size:0;line-height:0">&nbsp;</td></tr></table>')
    if "button" in b:
        label, url = b["button"]
        return (f'<table role="presentation" cellspacing="0" cellpadding="0" style="margin:22px 0 18px"><tr>'
                f'<td style="background:{LIME};border-radius:8px"><a href="{_e(url)}" style="display:inline-block;padding:13px 22px;'
                f'font:600 15px/1 {FONT};color:{BAND};text-decoration:none">{_e(label)}</a></td></tr></table>')
    raise ValueError(f"unknown email block {sorted(b)}")


def _block_text(b: dict) -> str:
    if "h" in b:
        return b["h"].upper()
    if "p" in b or "note" in b:
        return b.get("p") or b["note"]
    if "stats" in b:
        return "\n".join(f"{label}: {v}" for label, v in b["stats"])
    if "items" in b:
        return "\n".join(f"- {i['title']}" + (f" ({i['value']})" if i.get("value") else "")
                         + (f"\n  {i['detail']}" if i.get("detail") else "") for i in b["items"])
    if "steps" in b:
        return "\n".join(f"{n}. {s}" for n, s in enumerate(b["steps"], 1))
    if "progress" in b:
        return b["progress"]["label"]
    if "button" in b:
        label, url = b["button"]
        return f"{label}: {url}"
    raise ValueError(f"unknown email block {sorted(b)}")


def _shown(email: Email) -> list[dict]:
    return [b for b in email.blocks if not any(k in b and not b[k] for k in ("items", "steps", "stats"))]


def html(email: Email, *, app_url: str, footer: str, unsubscribe_url: Optional[str]) -> str:
    body = "".join(_block_html(b) for b in _shown(email))
    unsub = (f' &middot; <a href="{_e(unsubscribe_url)}" style="color:{MUTED}">Stop reminder emails</a>' if unsubscribe_url else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{_e(email.subject)}</title></head>
<body style="margin:0;padding:0;background:{PAPER}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{_e(email.preheader)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{PAPER}"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px">
<tr><td style="background:{BAND};border-radius:12px 12px 0 0;padding:22px 32px">
<img src="{_e(app_url)}/assets/wavy-logo-white.png" width="112" height="25" alt="Wavy Studios" style="display:block;border:0"></td></tr>
<tr><td style="background:#ffffff;border-radius:0 0 12px 12px;padding:30px 32px 18px">{body}</td></tr>
<tr><td style="padding:18px 32px;font:400 12px/1.6 {FONT};color:{MUTED};text-align:center">{_e(footer)}{unsub}</td></tr>
</table></td></tr></table></body></html>"""


def text(email: Email, *, footer: str, unsubscribe_url: Optional[str]) -> str:
    parts = [_block_text(b) for b in _shown(email)]
    tail = footer + (f"\nStop reminder emails: {unsubscribe_url}" if unsubscribe_url else "")
    return "\n\n".join(parts + ["--", tail]) + "\n"
