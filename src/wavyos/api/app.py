"""The HTTP API: a thin layer over the services.

create_app(factory, services) builds the app. Routes resolve the caller (bearer token), build a
Scope, call a service and serialize through explicit response models. Each request is one
transaction: committed when the route returns, rolled back when anything raises.

Errors map to {"error": message}: NotPermitted 403, NotFound 404, InvalidState 409,
ValueError 422 (request validation too, without echoing the submitted values), and an optional
module that isn't installed 503.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException

from ..config import Settings, load_settings
from ..db import make_engine, make_sessionmaker
from ..scope import InvalidState, NotFound, NotPermitted
from . import routes_client, routes_org
from .deps import ServiceUnavailable


def _error(status: int, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def _install_error_handlers(app: FastAPI) -> None:
    mapping = [(NotPermitted, 403), (NotFound, 404), (InvalidState, 409), (ServiceUnavailable, 503)]
    for exc_type, status in mapping:
        def handler(request: Request, exc: Exception, status: int = status) -> JSONResponse:
            return _error(status, str(exc))
        app.add_exception_handler(exc_type, handler)

    @app.exception_handler(ValueError)
    def _value_error(request: Request, exc: ValueError) -> JSONResponse:
        return _error(422, str(exc))

    @app.exception_handler(IntegrityError)
    def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        return _error(409, "that conflicts with existing data")

    @app.exception_handler(HTTPException)
    def _http(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Drop "input" and "ctx": a bad request must not echo what was sent (it may hold a key).
        problems = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
                    for e in exc.errors()]
        first = problems[0] if problems else {"loc": [], "msg": "invalid request"}
        where = ".".join(str(p) for p in first["loc"] if p != "body")
        return _error(422, f"{where}: {first['msg']}" if where else first["msg"], problems=problems)


def create_app(factory: Optional[sessionmaker[Session]] = None, services: Optional[dict] = None) -> FastAPI:
    """factory: a SQLAlchemy sessionmaker (default: from WAVYOS_DATABASE_URL).
    services: injectable dependencies; keys used: settings, vault, klaviyo_verify, run_audit,
    compare_audits, audit_output_root. Anything missing is built lazily from settings."""
    services = dict(services or {})
    settings: Settings = services.setdefault("settings", load_settings())
    if factory is None:
        factory = make_sessionmaker(make_engine(settings.database_url))
    app = FastAPI(title="Wavy OS", version="0.2.0",
                  description="Lifecycle Client Operating System: agency standards, client knowledge and the "
                              "insight -> decision -> task -> result chain.")
    app.state.factory = factory
    app.state.services = services
    _install_error_handlers(app)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    app.include_router(routes_org.router)
    app.include_router(routes_client.router)

    from ..console import mount_console
    mount_console(app, lambda: app.state.factory)
    return app
