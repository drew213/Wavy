"""The ops console: a live view of every client's chain, every execution trace, jobs, approvals and the event log.

Also serves the brand-facing pages: self-serve sign-up and sign-in (/start, /login), onboarding (/onboard) and a
brand's saved audit (/audit/<slug>). Every page is static; data comes from /v1/ops with the browser's token.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

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
        return RedirectResponse("/console")
