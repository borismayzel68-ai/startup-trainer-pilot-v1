"""T-001 (#114, FEAT-001): «Начать» без кода доступа под сессией; ошибки текстом. AC-005, AC-013, AC-017. Только синтетика."""
import os

import pytest

from test_auth import BASE, appmod, env, make_client  # noqa: F401  (env — фикстура)

pytestmark = pytest.mark.auth_enforced


def form(project_id):
    return {"project_id": project_id, "project_name": "Synth", "founder_name": "F", "founder_email": "f@example.test",
            "jurisdiction": "RF", "mode": "B", "consent": "true",
            "intended_use": "storage and visualization of laboratory history", "product_class": "WELLNESS_INFORMATION"}


def post(c, project_id):
    return c.post("/api/start", data=form(project_id), base_url=BASE, headers={"X-CSRF-Token": c.csrf})


def test_founder_starts_without_invite_code(env):
    founder, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
    r = post(founder, "VC-991")
    assert r.status_code == 200, r.get_json()  # контракт /api/start — 200 (не 201)
    assert r.get_json()["project_id"] == "VC-991"
    grants = founder.get("/auth/me", base_url=BASE).get_json()["grants"]
    assert {"role": "founder", "scope": "VC-991"} in grants  # из этого гранта форма подставляет номер


def test_investor_gets_403_with_text(env):
    inv, _ = make_client(env, "i@example.test", [("investor", "*")])
    r = post(inv, "VC-991")
    assert r.status_code == 403
    assert r.get_json()["error"]


def test_foreign_project_403_and_anonymous_401(env):
    founder, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
    assert post(founder, "VC-992").status_code == 403
    anon = appmod.app.test_client()
    assert anon.post("/api/start", data=form("VC-991"), base_url=BASE).status_code == 401


def test_form_has_no_code_field_and_error_above_button():
    path = os.path.join(os.path.dirname(__file__), "..", "templates", "index.html")
    html = open(path, encoding="utf-8").read()
    assert "Код доступа" not in html and "invite_code" not in html and 'id="code"' not in html
    assert html.index('id="startErr"') < html.index('onclick="startSession()"')
