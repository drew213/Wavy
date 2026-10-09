from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from wavyos import models as m
from wavyos import skills
from wavyos.execution import engine, packet
from wavyos.taxonomy import AREAS

ROOT = skills.DEFAULT_ROOT


def test_repo_skills_are_complete_and_valid():
    errs = skills.validate(ROOT)
    assert errs == [], "\n".join(errs)


def test_every_area_has_all_ten_components_and_a_checklist():
    loaded, _ = skills.load(ROOT)
    for area in AREAS:
        sk = loaded[f"area.{area}"]
        assert [h for h, _ in skills.AREA_SECTIONS] == list(sk.sections)
        assert 5 <= len(sk.checklist) <= 12
        assert sk.approvals


def test_validator_catches_problems(tmp_path):
    (tmp_path / "roles").mkdir()
    (tmp_path / "areas").mkdir()
    good = (ROOT / "areas" / "onboarding.md").read_text()
    (tmp_path / "areas" / "onboarding.md").write_text(good.replace("## Maintenance", "## Upkeep"))
    bad_action = good.replace("| `", "| `campaign.yolo` ", 1) if "| `" in good else good + "\n| campaign.yolo | autonomous | anyone |\n"
    (tmp_path / "areas" / "offers.md").write_text(bad_action.replace("area: onboarding", "area: offers").replace("key: area.onboarding", "key: area.offers"))
    errs = skills.validate(tmp_path, require_complete=False)
    assert any("Upkeep" in e or "sections must be exactly" in e for e in errs)
    assert any("unregistered action" in e or "level" in e for e in errs)


def test_publish_is_idempotent_and_feeds_packets(session, world):
    w = world
    org_scope = w.user(w.admin, None)
    first = skills.publish(session, org_scope, ROOT)
    assert len(first) == len(skills.ROLES) + 10 * len(AREAS) and all(changed for _, _, changed in first)
    again = skills.publish(session, org_scope, ROOT)
    assert not any(changed for _, _, changed in again)
    assert "## You never" in skills.role_skill(session, w.org.id, "creative")
    assert len(skills.checklist_for(session, w.org.id, "voice_copy")) >= 5
    # a creative packet carries the role skill and the area skill's components
    from wavyos import approvals, operations
    w.a.settings = {"allowed_providers": ["fake"]}
    ins, _ = operations.create_insight(session, w.agent("analyst"), area="flow_audit", title="x",
                                       evidence=[{"kind": "check", "ref": "r", "detail": "d"}])
    d, ap = operations.propose_decision(session, w.agent("strategist"), title="x", insight_ids=[ins.id], rationale="r",
                                        measurement={"metric": "m", "window_days": 7})
    approvals.decide(session, w.user(w.approver), ap.id, True)
    t = operations.create_task(session, w.agent("strategist"), decision_id=d.id, title="Copy", area="voice_copy",
                               owner_role="creative", done_when="saved",
                               checklist=skills.checklist_for(session, w.org.id, "voice_copy"))
    ex = engine.dispatch(session, w.agent("strategist"), t.id)
    assert ex.packet["role_skill"].startswith("## Mission")
    comps = {s["component"] for s in ex.packet["knowledge"]["standards"]}
    assert {"sop", "decision_rules", "quality_checklist"} <= comps
