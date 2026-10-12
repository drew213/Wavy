"""The ops console: a live view of every client's chain, every execution trace, jobs, approvals and the event log.

Also serves the brand-facing pages: self-serve sign-up and sign-in (/start, /login), onboarding (/onboard), a
brand's saved audit (/audit/<slug>) and the signed unsubscribe link in onboarding email (/email/unsubscribe).
Every page is static; data comes from /v1/ops with the browser's token.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .ops import make_ops_router

STATIC = Path(__file__).parent / "static"
HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "DENY",
           "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
           "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
           "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
           "Referrer-Policy": "no-referrer"}
# Email previews render in sandboxed srcdoc frames, which inherit this page's policy: the brand's images, fonts
# and stylesheets load from their CDNs over https. Scripts and connections stay same-origin.
WORKSPACE_HEADERS = {**HEADERS, "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                     "style-src 'self' 'unsafe-inline' https:; font-src 'self' https: data:; connect-src 'self'; "
                     "img-src 'self' data: https:; frame-src 'self'; frame-ancestors 'none'"}
ASSETS = {"geist-latin.woff2": "font/woff2", "geist-mono-latin.woff2": "font/woff2", "wavy-logo-white.png": "image/png"}


def _page(name: str, headers: dict = HEADERS) -> FileResponse:
    return FileResponse(STATIC / name, media_type="text/html", headers=headers)


def _note(title: str, body: str, form: str = "", status: int = 200) -> HTMLResponse:
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · Wavy OS</title><style>
body{{margin:0;background:#0b0c0d;color:#f2f2f0;font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:440px;margin:14vh auto;padding:0 24px}} img{{width:120px;margin-bottom:32px}}
h1{{font-size:24px;margin:0 0 12px}} p{{color:#b9bcc0;margin:0 0 24px}}
button{{background:#c8ff00;color:#0b0c0d;border:0;border-radius:8px;padding:12px 18px;font:600 15px inherit;cursor:pointer}}
</style></head><body><main><img src="/assets/wavy-logo-white.png" alt="Wavy Studios">
<h1>{escape(title)}</h1><p>{escape(body)}</p>{form}</main></body></html>"""
    return HTMLResponse(page, status_code=status, headers=HEADERS)


def mount_console(app: FastAPI, get_factory) -> None:
    app.include_router(make_ops_router(get_factory))

    @app.get("/console", include_in_schema=False)
    def console() -> FileResponse:
        return _page("index.html")

    @app.get("/onboard", include_in_schema=False)
    def onboard() -> FileResponse:
        return _page("onboard.html")

    @app.get("/audit/{slug}", include_in_schema=False)
    def audit(slug: str) -> FileResponse:
        return _page("workspace.html", WORKSPACE_HEADERS)

    @app.get("/assets/{name}", include_in_schema=False)
    def asset(name: str) -> FileResponse:
        if name not in ASSETS:
            raise HTTPException(404)
        return FileResponse(STATIC / "assets" / name, media_type=ASSETS[name],
                            headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/start", include_in_schema=False)
    def start() -> FileResponse:
        return _page("account.html")

    @app.get("/login", include_in_schema=False)
    def login() -> FileResponse:
        return _page("account.html")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/login")

    # The link works without signing in: it carries the person's id and a signature only the server can make.
    # GET only asks (mail scanners follow links); POST unsubscribes, which is also what one-click
    # List-Unsubscribe sends.

    def _signed(request: Request, u: str, t: str) -> bool:
        from ..api.deps import service
        from ..mail import links
        return links.verify(service(request, "vault"), u, t)

    BAD_LINK = ("This link doesn't work", "It may have been copied incompletely. Use the link in your latest email, "
                "or reply to it and we'll stop the emails for you.")

    @app.get("/email/unsubscribe", include_in_schema=False)
    def unsubscribe_ask(request: Request, u: str = "", t: str = "") -> HTMLResponse:
        if not _signed(request, u, t):
            return _note(*BAD_LINK, status=400)
        form = (f'<form method="post" action="/email/unsubscribe?u={escape(u)}&amp;t={escape(t)}">'
                '<button type="submit">Stop reminders</button></form>')
        return _note("Stop reminder emails?", "You'll still hear from us when something happens on your account, "
                     "like your audit being ready or a fix going live. Your audit and progress stay in the app.", form)

    @app.post("/email/unsubscribe", include_in_schema=False)
    def unsubscribe(request: Request, u: str = "", t: str = "") -> HTMLResponse:
        from .. import models as m
        from ..db import utcnow
        if not _signed(request, u, t):
            return _note(*BAD_LINK, status=400)
        s = get_factory()()
        try:
            user = s.get(m.User, u)
            if user is not None and user.email_opt_out_at is None:
                user.email_opt_out_at = utcnow()
                s.commit()
        finally:
            s.close()
        return _note("Reminders stopped", "We won't send you any more reminders. You'll still hear from us when "
                     "something happens on your account. Your audit and progress are in the app whenever you want them.")
