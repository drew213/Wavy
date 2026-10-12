"""Where emails go: Postmark, Resend, or the outbox (rendered and stored, never sent).

A provider gets a finished message and returns the provider's message id. Errors carry the provider's
status and message, never the API key.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

PROVIDERS = ("outbox", "postmark", "resend")
TIMEOUT = 20.0


class SendFailed(Exception):
    def __init__(self, message: str, retry: bool = True):
        super().__init__(message)
        self.retry = retry


@dataclass
class Message:
    to: str
    subject: str
    html: str
    text: str
    sender: str
    reply_to: Optional[str] = None
    headers: dict = field(default_factory=dict)
    tag: str = ""


class Outbox:
    name = "outbox"

    def send(self, msg: Message, ref: str) -> str:
        return "outbox-" + ref


class _Http:
    name = ""
    url = ""

    def __init__(self, api_key: str, transport: Optional[httpx.BaseTransport] = None):
        self._key = api_key
        self._transport = transport

    def __repr__(self) -> str:
        return f"{type(self).__name__}(key=***)"

    def _post(self, headers: dict, body: dict) -> dict:
        try:
            with httpx.Client(timeout=TIMEOUT, transport=self._transport) as c:
                r = c.post(self.url, headers=headers, json=body)
        except httpx.HTTPError as e:
            raise SendFailed(f"{self.name} unreachable: {type(e).__name__}") from None
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            detail = str(data.get("Message") or data.get("message") or data.get("error") or r.reason_phrase)[:300]
            raise SendFailed(f"{self.name} {r.status_code}: {detail}", retry=r.status_code == 429 or r.status_code >= 500)
        return data


class Postmark(_Http):
    name = "postmark"
    url = "https://api.postmarkapp.com/email"

    def send(self, msg: Message, ref: str) -> str:
        body = {"From": msg.sender, "To": msg.to, "Subject": msg.subject, "HtmlBody": msg.html, "TextBody": msg.text,
                "MessageStream": "outbound", "Tag": msg.tag[:1000] or None, "Metadata": {"ref": ref},
                "Headers": [{"Name": k, "Value": v} for k, v in msg.headers.items()]}
        if msg.reply_to:
            body["ReplyTo"] = msg.reply_to
        data = self._post({"X-Postmark-Server-Token": self._key, "Accept": "application/json"}, body)
        return str(data.get("MessageID") or "")


class Resend(_Http):
    name = "resend"
    url = "https://api.resend.com/emails"

    def send(self, msg: Message, ref: str) -> str:
        body = {"from": msg.sender, "to": [msg.to], "subject": msg.subject, "html": msg.html, "text": msg.text,
                "headers": msg.headers,
                "tags": [{"name": "email", "value": re.sub(r"[^A-Za-z0-9_-]", "_", msg.tag or "onboarding")[:256]}]}
        if msg.reply_to:
            body["reply_to"] = msg.reply_to
        data = self._post({"Authorization": "Bearer " + self._key, "Idempotency-Key": ref}, body)
        return str(data.get("id") or "")


def make(name: str, api_key: Optional[str], transport: Optional[httpx.BaseTransport] = None):
    """The provider to send through, or None when it needs a key nobody has stored yet."""
    if name == "outbox":
        return Outbox()
    if name not in PROVIDERS:
        raise ValueError(f"WAVYOS_EMAIL_PROVIDER must be one of {PROVIDERS}")
    if not api_key:
        return None
    return (Postmark if name == "postmark" else Resend)(api_key, transport)
