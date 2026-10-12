"""Writers: one prompt in, text out, with what it cost. Narrative drafting needs no tools, so it talks to
the providers directly with the same credentials and prices as the execution engine.

  anthropic  Claude through the Anthropic SDK (the org's vaulted key, or the SDK's own credential chain)
  codex      ChatGPT through the Codex CLI and the worker's ChatGPT sign-in (no per-token price; cost 0)
  fake       scripted replies, for tests and dry runs
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from sqlalchemy.orm import Session, sessionmaker

from ..execution.adapters import CodexAdapter, _cost
from ..vault import Vault

DRAFT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 8000


@dataclass
class Completion:
    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class Writer(Protocol):
    name: str
    def complete(self, system: str, prompt: str) -> Completion: ...


class AnthropicWriter:
    name = "anthropic"

    def __init__(self, api_key: Optional[str] = None, model: str = DRAFT_MODEL, client=None):
        self._api_key, self.model, self._client = api_key, model, client

    def complete(self, system: str, prompt: str) -> Completion:
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key) if self._api_key else anthropic.Anthropic()
        resp = self._client.messages.create(model=self.model, max_tokens=MAX_TOKENS, system=system,
                                            messages=[{"role": "user", "content": prompt}])
        if resp.stop_reason == "refusal":
            raise RuntimeError("the model declined to write this section")
        text = "".join(b.text for b in resp.content if b.type == "text")
        i, o = resp.usage.input_tokens, resp.usage.output_tokens
        return Completion(text, resp.model, i, o, _cost(self.model, i, o))


class CodexWriter:
    name = "codex"

    def __init__(self, adapter: Optional[CodexAdapter] = None):
        self._adapter = adapter or CodexAdapter()

    def complete(self, system: str, prompt: str) -> Completion:
        return Completion(self._adapter._exec(f"{system}\n\n{prompt}"), "codex")


@dataclass
class FakeWriter:
    """reply(system, prompt) -> text; cost_usd is charged per call so spend caps can be tested."""
    reply: Callable[[str, str], str]
    name: str = "fake"
    cost_usd: float = 0.0
    calls: list = field(default_factory=list)

    def complete(self, system: str, prompt: str) -> Completion:
        self.calls.append((system, prompt))
        return Completion(self.reply(system, prompt), "fake", len(prompt) // 4, 0, self.cost_usd)


def writer_factory(factory: sessionmaker[Session], vault: Optional[Vault]) -> Callable[[str], dict[str, Writer]]:
    """org_id -> {provider: Writer}, each org on its own key."""
    from ..execution.providers import _key
    cache: dict[str, dict[str, Writer]] = {}

    def for_org(org_id: str) -> dict[str, Writer]:
        if org_id not in cache:
            cache[org_id] = {"anthropic": AnthropicWriter(api_key=_key(factory, vault, org_id, "anthropic")),
                             "codex": CodexWriter()}
        return cache[org_id]
    return for_org
