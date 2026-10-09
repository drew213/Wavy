"""Check registry. A check is a plain function registered by its library ID:

    @check("FLW-01")
    def welcome_live(snap: Snapshot, spec: dict) -> CheckOutcome: ...

It reads the snapshot, applies the library's logic and thresholds, and returns a verdict with the
values it measured and evidence refs (`file#/json/pointer`). It raises MissingData when an input
isn't in the snapshot and NotApplicable when the check's precondition is absent (library na_rule);
both become `na` with the reason. Any other exception becomes `error`; it never stops the audit.
Unregistered checks report `not_implemented`, never a verdict.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .. import models as m
from .snapshot import MissingData, Snapshot

CheckFn = Callable[[Snapshot, dict], "CheckOutcome"]
REGISTRY: dict[str, CheckFn] = {}
_IMPL_MODULES = ("checks_data", "checks_capture", "checks_flows", "checks_campaigns", "checks_deliverability",
                 "checks_retention", "checks_creative", "checks_sms")
_loaded = False


class NotApplicable(Exception):
    """The check's precondition is absent (no SMS program, no subscriptions...): scored n/a."""


@dataclass
class CheckOutcome:
    verdict: str
    value: dict = field(default_factory=dict)
    evidence: list[dict] = field(default_factory=list)
    message: str = ""

    def __post_init__(self):
        if self.verdict not in m.VERDICTS:
            raise ValueError(f"verdict must be one of {m.VERDICTS}, not {self.verdict!r}")
        for e in self.evidence:
            if not isinstance(e, dict) or not e.get("ref"):
                raise ValueError("evidence items need a ref")


def ev(ref: str, detail: str = "", kind: str = "snapshot") -> dict:
    return {"kind": kind, "ref": ref, "detail": detail}


def outcome(verdict: str, message: str, value: Optional[dict] = None, evidence: Optional[list[dict]] = None) -> CheckOutcome:
    return CheckOutcome(verdict, value or {}, evidence or [], message)


def worst(*verdicts: str) -> str:
    """fail beats warn beats pass."""
    for v in ("fail", "warn"):
        if v in verdicts:
            return v
    return "pass"


def check(check_id: str) -> Callable[[CheckFn], CheckFn]:
    def deco(fn: CheckFn) -> CheckFn:
        if check_id in REGISTRY and REGISTRY[check_id] is not fn:
            raise ValueError(f"{check_id} is registered twice")
        REGISTRY[check_id] = fn
        fn.check_id = check_id  # type: ignore[attr-defined]
        return fn
    return deco


def load_all() -> dict[str, CheckFn]:
    global _loaded
    if not _loaded:
        for mod in _IMPL_MODULES:
            importlib.import_module(f"{__package__}.{mod}")
        _loaded = True
    return REGISTRY


def run_check(check_id: str, snap: Snapshot, spec: dict) -> CheckOutcome:
    fn = load_all().get(check_id)
    if fn is None:
        return outcome("not_implemented", "no implementation registered for this check yet")
    try:
        result = fn(snap, spec)
    except MissingData as exc:
        return outcome("na", f"data not in snapshot: {exc.reason}", {"missing": exc.ref} if exc.ref else {})
    except NotApplicable as exc:
        return outcome("na", str(exc))
    except Exception as exc:  # noqa: BLE001  a broken check is reported, never fatal
        return outcome("error", f"{type(exc).__name__}: {exc}")
    if not isinstance(result, CheckOutcome):
        return outcome("error", f"{check_id} returned {type(result).__name__}, not a CheckOutcome")
    return result


def pct(x: Optional[float], digits: int = 2) -> str:
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def ratio(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den is None or den == 0:
        return None
    return num / den


def as_dict(obj: Any) -> Any:
    """Round floats so stored values stay readable."""
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {k: as_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [as_dict(v) for v in obj]
    return obj
