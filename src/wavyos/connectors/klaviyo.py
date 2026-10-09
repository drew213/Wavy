"""Read-only Klaviyo client.

Ports what the audit collector (klaviyo-audit/24-, 38-, deep-dive-api.js, v2/fetch-events-full.py)
does: JSON:API pagination through `links.next`, retries on 429 (honouring Retry-After) and 5xx with
capped exponential backoff, client-side pacing per endpoint family, and the same pinned revision.

Read-only is enforced here, before any request is built:
- GET is always allowed;
- POST is allowed only to READ_ONLY_POST_PATHS, Klaviyo's query endpoints (aggregates, reports,
  template render) that read data through a request body and change nothing;
- every other method, any other POST path, a path outside /api/ and a pagination link to another
  host raise ReadOnlyViolation without touching the network.

The API key lives only inside the client. It is never logged, never part of repr() and is scrubbed
from every exception message.
"""
from __future__ import annotations

import posixpath
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional
from urllib.parse import urlsplit

import httpx

API_HOST = "a.klaviyo.com"
BASE_URL = f"https://{API_HOST}"
# The revision every audit collector pins (24-, 38-, deep-dive-api.js and v2/fetch-events-full.py).
REVISION = "2026-04-15"

# POST endpoints that only read. Klaviyo has no campaign-series-reports endpoint
# (see klaviyo-audit/README.md), so it isn't listed.
READ_ONLY_POST_PATHS = frozenset({
    "/api/metric-aggregates",
    "/api/flow-values-reports",
    "/api/flow-series-reports",
    "/api/campaign-values-reports",
    "/api/form-values-reports",
    "/api/form-series-reports",
    "/api/segment-values-reports",
    "/api/segment-series-reports",
    "/api/template-render",
})

RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
CAMPAIGN_CHANNELS = ("email", "sms", "mobile_push")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_KEYLIKE = re.compile(r"pk_[A-Za-z0-9_]+")
_AUTH_HEADER = re.compile(r"Klaviyo-API-Key\s+\S+", re.I)


# ---------------------------------------------------------------- errors

class ReadOnlyViolation(PermissionError):
    """A request that could change the Klaviyo account was refused before it was sent."""


class KlaviyoError(Exception):
    """Klaviyo refused or failed a read. Messages never contain the API key."""

    def __init__(self, message: str, *, status: Optional[int] = None, path: str = ""):
        super().__init__(message)
        self.status, self.path = status, path


# ---------------------------------------------------------------- rate notes

@dataclass(frozen=True)
class RateNote:
    """Klaviyo's published per-account limits for one endpoint family (burst per second, steady per minute).

    The client paces itself to the steady rate (min_interval) so long pulls don't live on 429s.
    """
    family: str
    burst_per_s: int
    steady_per_min: int
    note: str = ""

    @property
    def min_interval(self) -> float:
        return 60.0 / self.steady_per_min


_RATES: list[tuple[re.Pattern, RateNote]] = [
    (re.compile(r"^/api/(flow|campaign|form|segment)-(values|series)-reports$"),
     RateNote("reporting", 1, 2, "also capped at 225 a day; a report call waits ~30 s after the last")),
    (re.compile(r"^/api/metric-aggregates$"), RateNote("metric_aggregates", 3, 60)),
    (re.compile(r"^/api/template-render$"), RateNote("template_render", 3, 60)),
    (re.compile(r"^/api/events(/|$)"), RateNote("events", 350, 3500)),
    (re.compile(r"^/api/(flows|flow-actions|flow-messages)(/|$)"), RateNote("flows", 3, 60)),
    (re.compile(r"^/api/campaigns(/|$)"), RateNote("campaigns", 10, 150)),
    (re.compile(r"^/api/(lists|segments)(/|$)"), RateNote("groups", 75, 700,
                                                         "profile_count additional field drops this to 15/min")),
    (re.compile(r"^/api/metrics(/|$)"), RateNote("metrics", 10, 150)),
    (re.compile(r"^/api/accounts(/|$)"), RateNote("accounts", 1, 15)),
]
_DEFAULT_RATE = RateNote("default", 3, 60)


def rate_note(path: str) -> RateNote:
    p = path.rstrip("/") or "/"
    for pattern, note in _RATES:
        if pattern.search(p):
            return note
    return _DEFAULT_RATE


# ---------------------------------------------------------------- the client

class _Secret:
    __slots__ = ("_v",)

    def __init__(self, value: str):
        self._v = value

    def reveal(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__


def _utc_z(value: datetime | str) -> str:
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        raise ValueError("pass timezone-aware datetimes; naive ones are ambiguous across account timezones")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise ValueError(f"{what} must be a Klaviyo ID (letters, digits, - and _)")
    return value


class KlaviyoClient:
    """Read-only access to one Klaviyo account.

    `transport` lets tests use httpx.MockTransport; `sleep` and `clock` let them skip real waits.
    List methods return JSON:API resources ({type, id, attributes, relationships, ...}).
    """

    def __init__(self, api_key: str, *, transport: Optional[httpx.BaseTransport] = None, revision: str = REVISION,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 max_attempts: int = 6, backoff_base: float = 1.0, max_backoff: float = 60.0,
                 max_retry_after: float = 120.0, timeout: float = 45.0, pace: bool = True):
        if not api_key or not isinstance(api_key, str):
            raise ValueError("a Klaviyo private API key is required")
        self._key = _Secret(api_key.strip())
        self.revision = revision
        self._sleep, self._clock = sleep, clock
        self.max_attempts, self.backoff_base, self.max_backoff = max_attempts, backoff_base, max_backoff
        self.max_retry_after, self._pace = max_retry_after, pace
        self._last_call: dict[str, float] = {}
        self.stats = {"requests": 0, "retries": 0, "rate_limited": 0}
        # The key is added per request, not as a default header, so nothing on the httpx client holds it.
        self._http = httpx.Client(base_url=BASE_URL, transport=transport, timeout=timeout, follow_redirects=False,
                                  headers={"accept": "application/vnd.api+json", "revision": revision})

    def __repr__(self) -> str:
        return f"KlaviyoClient(revision={self.revision!r}, key=<redacted>)"

    __str__ = __repr__

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "KlaviyoClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------ guard and transport

    def _redact(self, text: Any) -> str:
        s = str(text)
        key = self._key.reveal()
        if key:
            s = s.replace(key, "[REDACTED]")
        return _AUTH_HEADER.sub("Klaviyo-API-Key [REDACTED]", _KEYLIKE.sub("[REDACTED]", s))

    @staticmethod
    def check_read_only(method: str, path_or_url: str) -> tuple[str, str]:
        """Return (method, url-or-path) if the request only reads; raise ReadOnlyViolation otherwise."""
        method = method.upper()
        parts = urlsplit(path_or_url)
        if parts.scheme or parts.netloc:
            if parts.scheme != "https" or parts.netloc != API_HOST:
                raise ReadOnlyViolation(f"refusing a request to {parts.scheme}://{parts.netloc}: only {BASE_URL}")
        path = parts.path
        segments = path.split("/")
        if ".." in segments or "." in segments or "//" in path or "\\" in path:
            raise ReadOnlyViolation(f"refusing an unnormalized path {path!r}")
        norm = posixpath.normpath(path) if path else ""
        if not norm.startswith("/api/") and norm != "/api":
            raise ReadOnlyViolation(f"refusing {path!r}: only /api/ paths")
        if method == "GET":
            return method, path_or_url
        if method == "POST" and norm.rstrip("/") in READ_ONLY_POST_PATHS:
            return method, path_or_url
        raise ReadOnlyViolation(f"{method} {norm} isn't a read-only Klaviyo call; this client never writes")

    def _wait_for_rate(self, path: str) -> None:
        if not self._pace:
            return
        note = rate_note(path)
        last = self._last_call.get(note.family)
        if last is not None:
            wait = last + note.min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_call[note.family] = self._clock()

    def _backoff(self, attempt: int, retry_after: Optional[str]) -> float:
        if retry_after:
            try:
                return min(self.max_retry_after, max(0.0, float(retry_after)))
            except ValueError:
                pass  # an HTTP-date Retry-After: fall back to exponential
        return min(self.max_backoff, self.backoff_base * (2 ** attempt))

    def request(self, method: str, path: str, *, params: Optional[dict] = None, body: Optional[dict] = None) -> dict:
        """One read. `path` is an /api/ path or a `links.next` URL. Raises ReadOnlyViolation before sending a write."""
        method, target = self.check_read_only(method, path)
        if method == "GET" and body is not None:
            raise ReadOnlyViolation("GET requests don't carry a body")
        api_path = urlsplit(target).path
        headers = {"Authorization": f"Klaviyo-API-Key {self._key.reveal()}"}
        if body is not None:
            headers["content-type"] = "application/vnd.api+json"
        last_problem = "no attempt made"
        for attempt in range(self.max_attempts):
            self._wait_for_rate(api_path)
            self.stats["requests"] += 1
            try:
                resp = self._http.request(method, target, params=params, json=body, headers=headers)
            except httpx.TransportError as e:
                last_problem = f"network error ({type(e).__name__}: {self._redact(e)})"
                if attempt + 1 < self.max_attempts:
                    self.stats["retries"] += 1
                    self._sleep(self._backoff(attempt, None))
                    continue
                raise KlaviyoError(f"Klaviyo {method} {api_path} failed: {last_problem}", path=api_path) from None
            if resp.is_success:
                try:
                    return resp.json() if resp.content else {}
                except ValueError:
                    raise KlaviyoError(f"Klaviyo {method} {api_path} returned a non-JSON body",
                                       status=resp.status_code, path=api_path) from None
            last_problem = f"HTTP {resp.status_code}: {self._detail(resp)}"
            if resp.status_code in RETRY_STATUSES and attempt + 1 < self.max_attempts:
                self.stats["retries"] += 1
                self.stats["rate_limited"] += resp.status_code == 429
                self._sleep(self._backoff(attempt, resp.headers.get("retry-after")))
                continue
            raise KlaviyoError(f"Klaviyo {method} {api_path} failed: {last_problem}",
                               status=resp.status_code, path=api_path)
        raise KlaviyoError(f"Klaviyo {method} {api_path} failed: {last_problem}", path=api_path)  # pragma: no cover

    def _detail(self, resp: httpx.Response) -> str:
        try:
            errors = resp.json().get("errors") or []
            text = "; ".join(f"{e.get('code') or ''}: {e.get('detail') or e.get('title') or ''}".strip(": ")
                             for e in errors if isinstance(e, dict)) or "request rejected"
        except Exception:
            text = "request rejected (non-JSON body)"
        return self._redact(text)[:500]

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        return self.request("GET", path, params=params)

    def query(self, path: str, body: dict) -> dict:
        """POST to a read-only query endpoint (aggregates, reports, template render)."""
        return self.request("POST", path, body=body)

    def iter_pages(self, path: str, params: Optional[dict] = None, max_pages: int = 1000) -> Iterator[dict]:
        """Yield every page, following links.next. The first request carries `params`; next links carry their own."""
        target, page_params, pages = path, params, 0
        while target and pages < max_pages:
            page = self.get(target, page_params)
            pages += 1
            yield page
            target, page_params = (page.get("links") or {}).get("next"), None

    def collect(self, path: str, params: Optional[dict] = None, max_pages: int = 1000) -> list[dict]:
        rows: list[dict] = []
        for page in self.iter_pages(path, params, max_pages):
            data = page.get("data")
            rows.extend(data if isinstance(data, list) else [data] if data else [])
        return rows

    # ------------------------------------------------------------ endpoints

    def account(self) -> dict:
        """The account this key belongs to: {id, name, timezone, currency}."""
        data = self.get("/api/accounts/").get("data") or []
        if not data:
            raise KlaviyoError("Klaviyo returned no account for this key", path="/api/accounts/")
        a = data[0].get("attributes") or {}
        contact = a.get("contact_information") or {}
        return {"id": data[0].get("id"), "name": contact.get("organization_name"),
                "timezone": a.get("timezone"), "currency": a.get("preferred_currency")}

    def flows(self) -> list[dict]:
        return self.collect("/api/flows/", {"page[size]": 50})

    def flow_actions(self, flow_id: str) -> list[dict]:
        return self.collect(f"/api/flows/{_safe_id(flow_id, 'flow_id')}/flow-actions/", {"page[size]": 50})

    def campaigns(self, channel: str, *, include_messages: bool = False) -> list[dict]:
        """Campaigns for one channel. Klaviyo requires the channel filter on this revision."""
        if channel not in CAMPAIGN_CHANNELS:
            raise ValueError(f"channel must be one of {CAMPAIGN_CHANNELS}")
        params = {"filter": f"equals(messages.channel,'{channel}')", "sort": "-created_at"}
        if include_messages:
            params["include"] = "campaign-messages"
        return self.collect("/api/campaigns/", params)

    def lists(self) -> list[dict]:
        return self.collect("/api/lists/")

    def segments(self) -> list[dict]:
        return self.collect("/api/segments/")

    def metrics(self) -> list[dict]:
        return self.collect("/api/metrics/", {"fields[metric]": "name,integration,created,updated"})

    def metric_aggregate(self, metric_id: str, *, measurements: list[str], start: datetime | str,
                         end: datetime | str, interval: str = "month", by: Optional[list[str]] = None,
                         timezone_name: str = "UTC", page_size: int = 500) -> dict:
        """Aggregate one metric over [start, end). Returns {dates, data}. Pass account-local boundaries as UTC instants."""
        attrs: dict[str, Any] = {
            "metric_id": _safe_id(metric_id, "metric_id"), "measurements": list(measurements), "interval": interval,
            "page_size": page_size, "timezone": timezone_name,
            "filter": [f"greater-or-equal(datetime,{_utc_z(start)})", f"less-than(datetime,{_utc_z(end)})"],
        }
        if by:
            attrs["by"] = list(by)
        res = self.query("/api/metric-aggregates/", {"data": {"type": "metric-aggregate", "attributes": attrs}})
        out = (res.get("data") or {}).get("attributes") or {}
        return {"dates": out.get("dates") or [], "data": out.get("data") or []}

    def _report(self, kind: str, attributes: dict) -> dict:
        res = self.query(f"/api/{kind}s/", {"data": {"type": kind, "attributes": attributes}})
        return (res.get("data") or {}).get("attributes") or {}

    def flow_values_report(self, *, statistics: list[str], timeframe: dict, conversion_metric_id: str,
                           group_by: Optional[list[str]] = None, filter: Optional[str] = None) -> dict:
        """Flow performance totals. timeframe = {"key": "last_30_days"} or {"start": ..., "end": ...}."""
        attrs = {"statistics": list(statistics), "timeframe": timeframe,
                 "conversion_metric_id": _safe_id(conversion_metric_id, "conversion_metric_id")}
        if group_by:
            attrs["group_by"] = list(group_by)
        if filter:
            attrs["filter"] = filter
        return self._report("flow-values-report", attrs)

    def flow_series_report(self, *, statistics: list[str], timeframe: dict, interval: str,
                           conversion_metric_id: str, group_by: Optional[list[str]] = None) -> dict:
        attrs = {"statistics": list(statistics), "timeframe": timeframe, "interval": interval,
                 "conversion_metric_id": _safe_id(conversion_metric_id, "conversion_metric_id")}
        if group_by:
            attrs["group_by"] = list(group_by)
        return self._report("flow-series-report", attrs)

    def campaign_values_report(self, *, statistics: list[str], timeframe: dict, conversion_metric_id: str,
                               group_by: Optional[list[str]] = None, filter: Optional[str] = None) -> dict:
        attrs = {"statistics": list(statistics), "timeframe": timeframe,
                 "conversion_metric_id": _safe_id(conversion_metric_id, "conversion_metric_id")}
        if group_by:
            attrs["group_by"] = list(group_by)
        if filter:
            attrs["filter"] = filter
        return self._report("campaign-values-report", attrs)

    def events(self, metric_id: str, since: datetime | str, until: datetime | str, *, page_size: int = 200,
               include_profile: bool = False, max_pages: int = 100_000) -> Iterator[dict]:
        """Every event for one metric in [since, until), oldest first. A generator: pulls can be large."""
        flt = (f'and(equals(metric_id,"{_safe_id(metric_id, "metric_id")}"),'
               f"greater-or-equal(datetime,{_utc_z(since)}),less-than(datetime,{_utc_z(until)}))")
        params: dict[str, Any] = {"filter": flt, "page[size]": page_size, "sort": "datetime"}
        if include_profile:
            params.update({"include": "profile", "fields[profile]": "id"})
        for page in self.iter_pages("/api/events/", params, max_pages):
            yield from page.get("data") or []


def verify_account(api_key: str, transport: Optional[httpx.BaseTransport] = None, **kw) -> dict:
    """Connection setup: confirm the key works and say which account it opens. Returns {id, name, timezone}."""
    with KlaviyoClient(api_key, transport=transport, **kw) as c:
        a = c.account()
    if not a.get("id"):
        raise KlaviyoError("Klaviyo returned an account without an ID", path="/api/accounts/")
    return {"id": a["id"], "name": a.get("name"), "timezone": a.get("timezone")}
