"""T-304 (#114, FEAT-003): тесты POST /api/start. AC-001, AC-004, AC-005. Только синтетика (@example.test, VC-99x).
ИИ не вызывается на /api/start (маршрутизация и next_question детерминированы); на всякий случай сеть закрыта моком.
Код сервера не менялся (T-305 заменил тесты кода доступа)."""
import io
from unittest import mock

import pytest

from test_auth import BASE, appmod, env, make_client  # noqa: F401  (env — фикстура)


def form(project_id="VC-991", **over):
    d = {"project_id": project_id, "project_name": "Synth", "founder_name": "F", "founder_email": "f@example.test",
         "jurisdiction": "RF", "mode": "B", "consent": "true",
         "intended_use": "storage and visualization of laboratory history", "product_class": "WELLNESS_INFORMATION"}
    d.update(over)
    return {k: v for k, v in d.items() if v is not None}


@pytest.fixture(autouse=True)
def no_network():
    with mock.patch("urllib.request.urlopen", side_effect=AssertionError("сеть закрыта в тесте")):
        yield


def post(c, data, csrf=True, **kw):
    h = {"X-CSRF-Token": c.csrf} if csrf else {}
    return c.post("/api/start", data=data, base_url=BASE, headers=h, **kw)


@pytest.mark.auth_enforced
class TestStartUnderSession:
    def test_founder_success_200_with_id(self, env):  # AC-001
        c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
        r = post(c, form())
        assert r.status_code == 200, r.get_json()
        j = r.get_json()
        assert j["id"] and j["project_id"] == "VC-991"

    def test_investor_403(self, env):  # AC-004
        c, _ = make_client(env, "i@example.test", [("investor", "*")])
        r = post(c, form())
        assert r.status_code == 403 and r.get_json()["error"]

    def test_foreign_case_403(self, env):  # AC-004
        c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
        assert post(c, form("VC-992")).status_code == 403

    def test_no_consent_400(self, env):  # AC-005
        c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
        for v in (None, "false", ""):
            r = post(c, form(consent=v))
            assert r.status_code == 400 and r.get_json()["code"] == "consent_required"

    def test_non_pdf_400(self, env):  # AC-005
        c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
        d = form()
        d["deck"] = (io.BytesIO(b"not-pdf"), "deck.txt", "text/plain")
        r = post(c, d, content_type="multipart/form-data")
        assert r.status_code == 400 and r.get_json()["code"] == "deck_mime_invalid"

    def test_no_csrf_403(self, env):  # AC-004
        c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
        r = post(c, form(), csrf=False)
        assert r.status_code == 403
        assert post(c, form()).status_code == 200  # с токеном тот же запрос проходит

    def test_error_bodies_are_json_with_text(self, env):  # «молчание»: любой отказ несёт error
        c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
        for data in (form(consent=None), form(project_name=None), form(mode="Z")):
            j = post(c, data).get_json()
            assert j and j["error"] and j["code"]


class TestNoInviteCodeT305:
    """T-305 (AC-007): код доступа не требуется и не проверяется; защита — вход, роль, scope."""

    @pytest.mark.auth_enforced
    def test_anonymous_401_even_with_any_code(self, env):  # права не ослаблены
        c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
        anon = appmod.app.test_client()
        for extra in ({}, {"invite_code": "PILOT-S1-EXT-001"}, {"invite_code": "wrong"}):
            r = anon.post("/api/start", data=form(**extra), base_url=BASE)
            assert r.status_code in (401, 403), r.status_code

    @pytest.mark.auth_enforced
    def test_disallowed_role_still_403(self, env):  # права не ослаблены
        for role in ("investor", "fund_staff", "expert"):
            c, _ = make_client(env, "%s@example.test" % role, [(role, "*")])
            assert post(c, form(invite_code="PILOT-S1-EXT-001")).status_code == 403

    @pytest.mark.auth_enforced
    def test_founder_without_code_200_and_code_ignored(self, env):
        c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
        assert post(c, form()).status_code == 200
        assert post(c, form(invite_code="wrong")).status_code == 200

    def test_no_invite_code_check_in_source(self):
        import inspect
        assert "invite_code" not in inspect.getsource(appmod.start)
