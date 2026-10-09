"""Audit engine tests: scorecards, insights, dedupe, permissions, isolation, re-audit comparison,
and an integration run over each real saved snapshot (skipped when the folder isn't on this machine)."""
from __future__ import annotations

import os

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from wavyos import events, tenancy
from wavyos import models as m
from wavyos.audit import compare, load_library, run_audit, score
from wavyos.audit.checks import outcome
from wavyos.audit.engine import WARN_SEVERITY
from wavyos.scope import NotFound, NotPermitted
from wavyos.taxonomy import CHECK_AREA_MAP

FIXTURE = Path(__file__).parent / "fixtures" / "audit" / "synthetic-shop"
# Optional integration run over real collector output: point WAVYOS_TEST_SNAPSHOT_ROOT at a folder of
# snapshot directories. Skipped when unset, so no client data or names live in the repo.
REAL_OUTPUT = Path(os.environ.get("WAVYOS_TEST_SNAPSHOT_ROOT", "var/no-snapshots"))
REAL_SNAPSHOTS = sorted(p.name for p in REAL_OUTPUT.glob("*") if (p / "account-audit-api.json").exists()) if REAL_OUTPUT.exists() else []
LIB = load_library()
SPECS = {c["id"]: c for c in LIB["checks"]}


def _insights(session, client):
    return session.scalars(select(m.Insight).where(m.Insight.client_id == client.id)).all()


def _results(session, sc):
    return {r.check_id: r for r in session.scalars(select(m.CheckResult).where(m.CheckResult.scorecard_id == sc.id))}


def test_run_audit_writes_scorecard_results_and_insights(session, world):
    scope = world.agent("analyst")
    sc = run_audit(session, scope, str(FIXTURE))
    results = _results(session, sc)
    assert set(results) == set(SPECS)                         # every library check has a result
    assert sc.library_version == LIB["version"] and sc.track == "snapshot"
    assert sc.as_of is not None and sc.snapshot_ref == str(FIXTURE)
    s = sc.summary
    assert sum(s["by_verdict"].values()) == len(SPECS) and s["by_verdict"]["error"] == 0
    assert sum(sum(v.values()) for v in s["by_area"].values()) == len(SPECS)
    assert 0 <= s["score"] <= 100
    # The fixture has no cart flow and no confirmed store platform.
    assert results["FLW-03"].verdict == "fail" and results["DAT-01"].verdict == "warn"
    assert results["CAP-03"].verdict == "na" and "deep" in results["CAP-03"].message      # deep-only check
    assert results["DLV-05"].verdict == "na" and "collector" in results["DLV-05"].message  # needs a collector

    flagged = {cid: r for cid, r in results.items() if r.verdict in ("warn", "fail")}
    assert flagged and all(r.insight_id for r in flagged.values())
    assert all(r.insight_id is None for r in results.values() if r.verdict not in ("warn", "fail"))
    insights = {i.id: i for i in _insights(session, world.a)}
    assert len(insights) == len(flagged) == s["insights_created"]
    for cid, r in flagged.items():
        ins = insights[r.insight_id]
        spec = SPECS[cid]
        assert ins.area == CHECK_AREA_MAP[spec["area"]]
        assert ins.severity == (spec["severity"] if r.verdict == "fail" else WARN_SEVERITY[spec["severity"]])
        assert ins.source_kind == "check" and ins.source_ref == f"scorecard:{sc.id}#{cid}"
        assert ins.dedupe_key == f"check:{cid}" and ins.evidence and all(e["ref"] for e in ins.evidence)
    cart = insights[results["FLW-03"].insight_id]
    assert cart.severity == "high" and cart.area == "flow_audit"
    dat01 = insights[results["DAT-01"].insight_id]
    assert dat01.severity == "high"                            # critical check, warn -> one level lower

    ev = session.scalars(select(m.Event).where(m.Event.action == "audit.scored", m.Event.client_id == world.a.id)).all()
    assert len(ev) == 1 and ev[0].object_id == sc.id and ev[0].data["score"] == s["score"]
    assert events.verify_chain(session, world.org.id, world.a.id) == (True, None)


def test_second_run_dedupes_insights(session, world):
    scope = world.agent("analyst")
    first = run_audit(session, scope, str(FIXTURE))
    n = len(_insights(session, world.a))
    second = run_audit(session, scope, str(FIXTURE))
    assert len(_insights(session, world.a)) == n
    assert second.summary["insights_created"] == 0 and second.summary["insights_reused"] == n
    r1, r2 = _results(session, first), _results(session, second)
    assert all(r2[c].insight_id == r1[c].insight_id for c in r1 if r1[c].insight_id)


def test_context_confirms_platform_and_insights_can_be_skipped(session, world):
    sc = run_audit(session, world.agent("analyst"), str(FIXTURE), create_insights=False,
                   context={"store_platform": "Shopify"})
    assert _results(session, sc)["DAT-01"].verdict == "pass"
    assert _insights(session, world.a) == []


def test_client_viewer_may_not_run_an_audit(session, world):
    admin = world.user(world.admin, None)
    viewer = tenancy.add_user(session, admin, email="viewer@brand-a.test", name="Viewer", kind="client")
    tenancy.grant(session, admin, user_id=viewer.id, role="client_viewer", client_id=world.a.id)
    session.flush()
    with pytest.raises(NotPermitted):
        run_audit(session, world.user(viewer), str(FIXTURE))
    assert session.scalars(select(m.Scorecard)).all() == []


def test_audit_needs_a_client_and_an_existing_folder(session, world, tmp_path):
    with pytest.raises(NotPermitted):
        run_audit(session, world.agent("analyst", None), str(FIXTURE))
    with pytest.raises(FileNotFoundError):
        run_audit(session, world.agent("analyst"), str(tmp_path / "nope"))


def test_cross_client_isolation(session, world):
    sc_a = run_audit(session, world.agent("analyst", world.a), str(FIXTURE))
    scope_b = world.agent("analyst", world.b)
    assert _insights(session, world.b) == []
    with pytest.raises(NotFound):
        compare(session, scope_b, sc_a.id, sc_a.id)
    sc_b = run_audit(session, scope_b, str(FIXTURE))
    # B gets its own insights: dedupe never reaches across clients.
    assert sc_b.summary["insights_created"] == sc_a.summary["insights_created"]
    ids_a = {i.id for i in _insights(session, world.a)}
    ids_b = {i.id for i in _insights(session, world.b)}
    assert ids_a and ids_b and not ids_a & ids_b
    assert all(i.client_id == world.b.id for i in _insights(session, world.b))
    with pytest.raises(NotFound):
        compare(session, world.agent("analyst", world.a), sc_a.id, sc_b.id)


def test_compare_reports_deltas_between_audits(session, world, tmp_path):
    scope = world.agent("analyst")
    before = run_audit(session, scope, str(FIXTURE))
    fixed = tmp_path / "after"
    shutil.copytree(FIXTURE, fixed)
    api = json.loads((fixed / "account-audit-api.json").read_text())
    chk = next(d for d in api["flows"]["details"] if d["flow"]["id"] == "F_CHK")
    cart = json.loads(json.dumps(chk).replace("F_CHK", "F_CART"))   # add a cart flow shaped like the checkout one
    cart["definition"]["triggers"][0]["id"] = "ATC1"
    cart["definition"]["profile_filter"]["condition_groups"][0]["conditions"].append(
        {"type": "profile-metric", "metric_id": "SC1", "measurement": "count",
         "measurement_filter": {"type": "numeric", "operator": "equals", "value": 0},
         "timeframe_filter": {"type": "date", "operator": "flow-start"}})
    api["flows"]["details"].append(cart)
    api["flows"]["items"].append({"id": "F_CART", "type": "flow", "attributes": cart["flow"]["attributes"]})
    (fixed / "account-audit-api.json").write_text(json.dumps(api))
    perf = json.loads((fixed / "performance-cross-review.json").read_text())
    rows = perf["reports"]["flow90"]["value"]["data"]["attributes"]["results"]
    rows += [dict(r, groupings=dict(r["groupings"], flow_id="F_CART")) for r in rows if r["groupings"]["flow_id"] == "F_CHK"]
    (fixed / "performance-cross-review.json").write_text(json.dumps(perf))
    after = run_audit(session, scope, str(fixed))
    diff = compare(session, scope, before.id, after.id)
    assert diff["checks"]["FLW-03"] == {"before": "fail", "after": "pass", "direction": "improved", "value_delta": {}}
    assert diff["checks"]["CAP-01"]["direction"] == "same"
    assert diff["tally"]["improved"] >= 1 and diff["tally"]["regressed"] == 0
    assert diff["score"]["delta"] > 0
    assert set(diff["checks"]) == set(SPECS)


def test_score_formula():
    specs = {"A": {"severity": "critical"}, "B": {"severity": "low"}, "C": {"severity": "high"}, "D": {"severity": "high"}}
    res = {"A": outcome("pass", ""), "B": outcome("fail", ""), "C": outcome("warn", ""), "D": outcome("na", "")}
    # (4*1 + 1*0 + 3*0.5) / (4 + 1 + 3) = 5.5 / 8
    assert score(res, specs) == pytest.approx(68.8)
    assert score({"D": outcome("na", "")}, specs) is None


@pytest.mark.parametrize("slug", REAL_SNAPSHOTS)
def test_real_snapshot_audits_cleanly(session, world, slug):
    folder = REAL_OUTPUT / slug
    if not folder.is_dir():
        pytest.skip(f"{slug} snapshot isn't on this machine")
    sc = run_audit(session, world.agent("analyst"), str(folder))
    results = _results(session, sc)
    assert set(results) == set(SPECS)
    errors = sorted(cid for cid, r in results.items() if r.verdict == "error")
    assert not errors, f"checks errored: {errors}"
    assert all(r.verdict != "not_implemented" for cid, r in results.items()
               if "snapshot" in SPECS[cid]["tracks"] and SPECS[cid]["status"] == "data-available")
    assert all(r.insight_id for r in results.values() if r.verdict in ("warn", "fail"))
