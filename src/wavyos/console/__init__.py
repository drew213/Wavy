"""The ops console: a live view of every client's chain, every execution trace, jobs, approvals and the event log."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from .ops import make_ops_router

STATIC = Path(__file__).parent / "static"


def mount_console(app: FastAPI, get_factory) -> None:
    app.include_router(make_ops_router(get_factory))

    @app.get("/console", include_in_schema=False)
    def console() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html",
                            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                     "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                     "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
                                     "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
                                     "Referrer-Policy": "no-referrer"})

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/console")
