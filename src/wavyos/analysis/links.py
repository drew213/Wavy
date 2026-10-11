"""The link checker: what live templates link to, offline first, optionally fetched.

Offline it finds the store's domain (the most-linked non-social domain), then flags plain-http links and
links to domains that aren't the store, a social network or a known service (often an old domain). With a
fetcher it also follows each URL once, with a timeout, and flags errors, TLS failures and redirects to
another domain. The fetcher refuses private, loopback and link-local addresses, because the URLs come
from client accounts.
"""
from __future__ import annotations

import ipaddress
import socket
from collections import Counter
from typing import Callable, Optional
from urllib.parse import urlsplit

KNOWN = ("instagram.com", "facebook.com", "fb.com", "tiktok.com", "twitter.com", "x.com", "youtube.com", "youtu.be",
         "pinterest.com", "linkedin.com", "klaviyo.com", "klaviyomail.com", "manage.kmail-lists.com", "apple.com",
         "google.com", "trustpilot.com", "yotpo.com", "okendo.io", "judge.me", "gorgias.com", "rechargeapps.com",
         "spotify.com", "threads.net", "snapchat.com")
Fetch = Callable[[str], dict]  # url -> {status, final_url, error}


def host(url: str) -> str:
    h = (urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _known(h: str) -> bool:
    return any(h == k or h.endswith("." + k) for k in KNOWN)


def is_checkable(url: str) -> bool:
    return url.lower().startswith(("http://", "https://")) and "{{" not in url and "{%" not in url


def store_domain(urls: list[str]) -> Optional[str]:
    counts = Counter(host(u) for u in urls if is_checkable(u) and host(u) and not _known(host(u)))
    return counts.most_common(1)[0][0] if counts else None


def _same_site(h: str, domain: str) -> bool:
    return h == domain or h.endswith("." + domain) or domain.endswith("." + h)


def check(urls: list[str], fetch: Optional[Fetch] = None, store: Optional[str] = None, limit: int = 80) -> dict:
    """{store_domain, issues: [{url, issue, detail}], checked, fetched}."""
    urls = [u for u in dict.fromkeys(urls) if is_checkable(u)]
    store = store or store_domain(urls)
    issues = []
    for u in urls:
        h = host(u)
        if u.lower().startswith("http://"):
            issues.append({"url": u, "issue": "not_https", "detail": "plain http link"})
        if store and h and not _known(h) and not _same_site(h, store):
            issues.append({"url": u, "issue": "off_domain", "detail": f"links to {h}, not {store}"})
    fetched = 0
    if fetch is not None:
        for u in urls[:limit]:
            res = fetch(u)
            fetched += 1
            if res.get("error"):
                kind = "tls_failed" if "ssl" in res["error"].lower() or "certificate" in res["error"].lower() else "unreachable"
                issues.append({"url": u, "issue": kind, "detail": res["error"][:200]})
            elif (res.get("status") or 0) >= 400:
                issues.append({"url": u, "issue": "broken", "detail": f"HTTP {res['status']}"})
            elif res.get("final_url") and store and not _same_site(host(res["final_url"]), store) \
                    and _same_site(host(u), store):
                issues.append({"url": u, "issue": "redirects_away", "detail": f"lands on {host(res['final_url'])}"})
    return {"store_domain": store, "issues": issues, "checked": len(urls), "fetched": fetched}


def _public(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def safe_fetch(url: str, timeout: float = 5.0) -> dict:
    """Follow one URL (up to 5 redirects), public addresses only, no cookies, nothing sent but a GET."""
    import httpx

    target = url
    for _ in range(6):
        h = urlsplit(target).hostname or ""
        if not h or not _public(h):
            return {"status": None, "final_url": target, "error": f"refused: {h or 'no host'} isn't a public address"}
        try:
            r = httpx.get(target, timeout=timeout, follow_redirects=False,
                          headers={"User-Agent": "WavyOS-LinkCheck/1.0"})
        except httpx.HTTPError as e:
            return {"status": None, "final_url": target, "error": f"{type(e).__name__}: {e}"}
        if r.is_redirect and r.headers.get("location"):
            target = str(r.next_request.url) if r.next_request else r.headers["location"]
            continue
        return {"status": r.status_code, "final_url": target, "error": None}
    return {"status": None, "final_url": target, "error": "too many redirects"}
