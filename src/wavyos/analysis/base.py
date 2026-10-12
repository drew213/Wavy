"""What every analysis module produces: facts (the claim ledger), findings (finding / evidence / action)
and evidence tables.

A fact is the only kind of number a section or a draft may show. A finding is only written when the
module can say what to do about it: no action, no finding. Modules read a Snapshot and never call
Klaviyo; a module whose inputs are missing reports itself unavailable with the reason.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

EVIDENCE_CLASSES = ("documented", "inferred", "proposed")
SEVERITIES = ("critical", "high", "medium", "low")


@dataclass
class Fact:
    id: str                    # "<module>.<name>", unique in a run
    value: Any                 # number, string, or None for "unavailable" (never 0 for missing)
    unit: str
    label: str
    window: str = ""
    time_basis: str = "event"  # event (when it happened) or send (when the message went out)
    source: str = ""           # snapshot ref, e.g. account-totals.json#/aggregates/placed_order
    module: str = ""
    evidence_class: str = "documented"


@dataclass
class Finding:
    id: str
    module: str
    title: str
    finding: str               # what is true, in one sentence
    evidence: str              # the numbers behind it, each one a fact
    action: str                # what to do, concretely
    severity: str = "medium"
    category: str = "insight"
    facts: list[str] = field(default_factory=list)
    lever: Optional[str] = None
    effort_days: int = 2
    readout: Optional[dict] = None
    value: Optional[dict] = None   # {"annual": {low, base, high}, "basis": ...} when the module can size it
    evidence_class: str = "documented"

    def as_dict(self) -> dict:
        return asdict(self)


class Analysis:
    """One module's output, built up as it runs."""

    def __init__(self, module: str):
        self.module = module
        self.facts: list[Fact] = []
        self.findings: list[Finding] = []
        self.tables: dict[str, list[dict]] = {}
        self.notes: list[str] = []

    def fact(self, name: str, value: Any, unit: str, label: str, *, window: str = "", time_basis: str = "event",
             source: str = "", evidence_class: str = "documented") -> Any:
        if isinstance(value, float):
            value = round(value, 4) if math.isfinite(value) else None
        self.facts.append(Fact(f"{self.module}.{name}", value, unit, label, window, time_basis, source, self.module,
                               evidence_class))
        return value

    def find(self, name: str, title: str, finding: str, evidence: str, action: str, **kw: Any) -> Finding:
        if not action.strip():
            raise ValueError("a finding needs an action")
        f = Finding(f"{self.module}.{name}", self.module, title, finding, evidence, action, **kw)
        self.findings.append(f)
        return f

    def table(self, name: str, rows: list[dict]) -> None:
        self.tables[name] = rows


# ---------------------------------------------------------------- shared helpers

def wilson(successes: int, n: int, z: float = 1.96) -> tuple[Optional[float], Optional[float]]:
    """95% Wilson score interval for a proportion; (None, None) when n is 0."""
    if n <= 0:
        return None, None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def usd(x: Optional[float]) -> str:
    if x is None:
        return "unavailable"
    return f"${x:,.0f}" if abs(x) >= 100 else f"${x:,.2f}"


def pct(x: Optional[float], digits: int = 1) -> str:
    return "unavailable" if x is None else f"{x * 100:.{digits}f}%"


def ratio(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or not b else a / b


def stats_of(row: dict) -> dict:
    return row.get("statistics") or {}


def num(x: Any) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0
