from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from wavyos.connectors import klaviyo
from wavyos.connectors.klaviyo import KlaviyoClient, KlaviyoError, ReadOnlyViolation, verify_account

KEY = "pk_test_0123456789abcdef0123456789abcdef"


class FakeTime:
    """Injected sleep + clock: sleeping advances the clock, nothing really waits."""

    def __init__(self):
        self.now, self.sleeps = 1000.0, []

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.now += s

    def clock(self):
        return self.now


def make(handler, **kw):
    t = FakeTime()
    seen = []

    def wrapped(request: httpx.Request):
        seen.append(request)
        return handler(request)

    c = KlaviyoClient(KEY, transport=httpx.MockTransport(wrapped), sleep=t.sleep, clock=t.clock, **kw)
    return c, seen, t


def account_body(acct_id="AbC123"):
    return {"data": [{"type": "account", "id": acct_id, "attributes": {
        "contact_information": {"organization_name": "Brand A", "website_url": "https://brand-a.test"},
        "timezone": "America/Los_Angeles", "preferred_currency": "USD"}}]}


# ---------------------------------------------------------------- requests and parsing

def test_account_parsing_and_headers():
    c, seen, _ = make(lambda r: httpx.Response(200, json=account_body()))
    assert c.account() == {"id": "AbC123", "name": "Brand A", "timezone": "America/Los_Angeles", "currency": "USD"}
    req = seen[0]
    assert req.method == "GET" and req.url.path == "/api/accounts/" and req.url.host == "a.klaviyo.com"
    assert req.headers["revision"] == klaviyo.REVISION == "2026-04-15"
    assert req.headers["authorization"] == f"Klaviyo-API-Key {KEY}"
    assert req.headers["accept"] == "application/vnd.api+json"


def test_verify_account():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=account_body("XyZ789")))
    assert verify_account(KEY, transport=transport) == {"id": "XyZ789", "name": "Brand A", "timezone": "America/Los_Angeles"}
    empty = httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []}))
    with pytest.raises(KlaviyoError):
        verify_account(KEY, transport=empty)


def test_pagination_follows_links_next():
    def handler(r: httpx.Request):
        if "page[cursor]" not in r.url.params:
            assert r.url.params["page[size]"] == "50"
            return httpx.Response(200, json={"data": [{"type": "flow", "id": "F1"}, {"type": "flow", "id": "F2"}],
                                             "links": {"next": "https://a.klaviyo.com/api/flows/?page%5Bcursor%5D=abc"}})
        assert r.url.params["page[cursor]"] == "abc" and "page[size]" not in r.url.params  # next link stands alone
        return httpx.Response(200, json={"data": [{"type": "flow", "id": "F3"}], "links": {"next": None}})

    c, seen, t = make(handler)
    assert [f["id"] for f in c.flows()] == ["F1", "F2", "F3"]
    assert len(seen) == 2
    assert t.sleeps == [1.0]  # paced to flows' 60/min steady rate between pages


def test_pagination_refuses_next_link_to_another_host():
    c, seen, _ = make(lambda r: httpx.Response(200, json={"data": [], "links": {"next": "https://evil.test/api/flows/"}}))
    with pytest.raises(ReadOnlyViolation):
        c.flows()
    assert len(seen) == 1


def test_campaigns_need_channel_and_events_filter():
    def handler(r):
        return httpx.Response(200, json={"data": [{"type": r.url.path.split("/")[2], "id": "X"}]})

    c, seen, _ = make(handler)
    c.campaigns("email")
    assert seen[-1].url.params["filter"] == "equals(messages.channel,'email')"
    with pytest.raises(ValueError):
        c.campaigns("fax")
    since = datetime(2026, 9, 1, 7, tzinfo=timezone.utc)
    until = datetime(2026, 10, 1, 7, tzinfo=timezone.utc)
    assert [e["id"] for e in c.events("UNGBCi", since, until)] == ["X"]
    assert seen[-1].url.params["filter"] == ('and(equals(metric_id,"UNGBCi"),greater-or-equal(datetime,2026-09-01T07:00:00Z),'
                                            'less-than(datetime,2026-10-01T07:00:00Z))')
    with pytest.raises(ValueError):  # naive datetimes are ambiguous
        list(c.events("UNGBCi", datetime(2026, 9, 1), until))
    with pytest.raises(ValueError):  # ids can't smuggle a path
        c.flow_actions("../profiles")


def test_metric_aggregate_and_reports_are_paced_posts():
    bodies = []

    def handler(r):
        bodies.append((r.method, r.url.path, json.loads(r.content)))
        if r.url.path == "/api/metric-aggregates/":
            return httpx.Response(200, json={"data": {"attributes": {"dates": ["2026-09-01"], "data": [{"measurements": {"count": [3]}}]}}})
        return httpx.Response(200, json={"data": {"attributes": {"results": [{"groupings": {}, "statistics": {}}]}}})

    c, _, t = make(handler)
    agg = c.metric_aggregate("UNGBCi", measurements=["count", "sum_value"], start=datetime(2026, 9, 1, 7, tzinfo=timezone.utc),
                             end="2026-10-01T07:00:00Z", by=["$attributed_channel"], timezone_name="America/Los_Angeles")
    assert agg["dates"] == ["2026-09-01"]
    method, path, body = bodies[0]
    assert (method, path, body["data"]["type"]) == ("POST", "/api/metric-aggregates/", "metric-aggregate")
    assert body["data"]["attributes"]["filter"] == ["greater-or-equal(datetime,2026-09-01T07:00:00Z)",
                                                    "less-than(datetime,2026-10-01T07:00:00Z)"]
    c.flow_values_report(statistics=["recipients"], timeframe={"key": "last_30_days"}, conversion_metric_id="UNGBCi")
    c.campaign_values_report(statistics=["recipients"], timeframe={"key": "last_30_days"}, conversion_metric_id="UNGBCi")
    assert [b[1] for b in bodies[1:]] == ["/api/flow-values-reports/", "/api/campaign-values-reports/"]
    assert bodies[2][2]["data"]["type"] == "campaign-values-report"
    assert t.sleeps == [30.0]  # reporting endpoints share a 2/min budget
    assert klaviyo.rate_note("/api/flow-series-reports/").steady_per_min == 2


# ---------------------------------------------------------------- retries

def test_429_honours_retry_after_then_succeeds():
    responses = [httpx.Response(429, headers={"Retry-After": "7"}, json={"errors": [{"detail": "throttled"}]}),
                 httpx.Response(200, json=account_body())]
    c, seen, t = make(lambda r: responses.pop(0))
    assert c.account()["id"] == "AbC123"
    assert len(seen) == 2 and 7.0 in t.sleeps
    assert c.stats["rate_limited"] == 1 and c.stats["retries"] == 1


def test_5xx_backoff_is_exponential_and_capped():
    c, seen, t = make(lambda r: httpx.Response(503, json={}), max_attempts=6, backoff_base=1.0, max_backoff=5.0, pace=False)
    with pytest.raises(KlaviyoError) as err:
        c.account()
    assert len(seen) == 6 and t.sleeps == [1.0, 2.0, 4.0, 5.0, 5.0]
    assert err.value.status == 503
    huge = [httpx.Response(429, headers={"Retry-After": "86400"}), httpx.Response(200, json=account_body())]
    c2, _, t2 = make(lambda r: huge.pop(0), max_retry_after=120.0, pace=False)
    c2.account()
    assert t2.sleeps == [120.0]


def test_4xx_is_not_retried():
    c, seen, _ = make(lambda r: httpx.Response(400, json={"errors": [{"code": "invalid", "detail": "bad filter"}]}))
    with pytest.raises(KlaviyoError) as err:
        c.flows()
    assert len(seen) == 1 and "bad filter" in str(err.value) and err.value.status == 400


# ---------------------------------------------------------------- read-only enforcement

@pytest.mark.parametrize("method,path", [
    ("POST", "/api/profiles/"),
    ("POST", "/api/campaign-send-jobs/"),
    ("POST", "/api/campaign-series-reports/"),       # doesn't exist; not on the allowlist
    ("POST", "/api/metric-aggregates/../profiles/"),
    ("PATCH", "/api/flows/F1/"),
    ("PUT", "/api/flows/F1/"),
    ("DELETE", "/api/lists/L1/"),
    ("GET", "/v1/api/lists/"),
    ("GET", "https://evil.test/api/flows/"),
    ("GET", "http://a.klaviyo.com/api/flows/"),
])
def test_read_only_violation_raised_before_any_request(method, path):
    c, seen, _ = make(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ReadOnlyViolation):
        c.request(method, path, body={"data": {}} if method != "GET" else None)
    assert seen == []


def test_allowlisted_posts_go_through():
    c, seen, _ = make(lambda r: httpx.Response(200, json={"data": {}}), pace=False)
    for path in sorted(klaviyo.READ_ONLY_POST_PATHS):
        c.query(path + "/", {"data": {"type": "x", "attributes": {}}})
    assert len(seen) == len(klaviyo.READ_ONLY_POST_PATHS) and all(r.method == "POST" for r in seen)
    assert "/api/template-render" in klaviyo.READ_ONLY_POST_PATHS
    with pytest.raises(ReadOnlyViolation):
        c.request("GET", "/api/flows/", body={"x": 1})


# ---------------------------------------------------------------- the key never leaks

def test_key_never_in_repr_or_errors():
    def echo(r):  # a hostile error body that echoes the credential back
        return httpx.Response(401, json={"errors": [{"code": "not_authenticated",
                                                     "detail": f"bad key {r.headers['authorization']} / {KEY}"}]})

    c, _, _ = make(echo)
    assert KEY not in repr(c) and KEY not in str(c) and "redacted" in repr(c)
    assert KEY not in repr(vars(c))
    with pytest.raises(KlaviyoError) as err:
        c.account()
    assert KEY not in str(err.value) and KEY not in repr(err.value) and "pk_" not in str(err.value)
    assert err.value.status == 401

    def boom(r):
        raise httpx.ConnectError(f"connection reset while sending {KEY}", request=r)

    c2, _, _ = make(boom, max_attempts=2, pace=False)
    with pytest.raises(KlaviyoError) as err2:
        c2.account()
    assert KEY not in str(err2.value) and err2.value.__cause__ is None and err2.value.__suppress_context__


def test_key_required():
    with pytest.raises(ValueError):
        KlaviyoClient("")
