"""HQ-062 (ревью 23.10, п.1): вход на S3 по сессии MOS-1 — sign-screen и фильтр S3 в /s1/jobs. Только синтетика."""
import dataclasses
import uuid

import pytest

from test_auth import env, make_client  # noqa: F401  (фикстура env и клиент с готовой сессией)
import app as appmod

pytestmark = pytest.mark.auth_enforced

BASE = "https://localhost"
ADMIN_TOKEN = "a" * 32
SIGNER_TOKEN = "carol-synthetic-token-0000000003"
ADMIN = {"Authorization": "Bearer " + ADMIN_TOKEN}
SIGNER = {"Authorization": "Bearer " + SIGNER_TOKEN}


@pytest.fixture
def jobs(monkeypatch):
    """(id записи S3, id записи S1) в хранилище приложения; токены — синтетические."""
    state = appmod.app.extensions["s1_intake"]
    monkeypatch.setattr(state, "settings", dataclasses.replace(
        state.settings, admin_token=ADMIN_TOKEN, approvers={"carol": SIGNER_TOKEN}))
    monkeypatch.delenv("S3_OWNERS", raising=False)

    def make(data):
        sid = "synthetic-" + uuid.uuid4().hex
        return state.store.create_or_get(sid, "Synth", "f@example.test", data)[0]
    return make({"s3": {"payer": "fund"}}), make({})


def _ids(resp):
    assert resp.status_code == 200, resp.get_json()
    return {j["id"] for j in resp.get_json()}


def test_owner_session_opens_sign_screen_and_sees_s3(env, jobs):
    s3_id, s1_id = jobs
    owner, _ = make_client(env, "owner-s3@example.test", [("owner", "*")])
    assert owner.get(f"/s3/briefs/{s3_id}/sign-screen", base_url=BASE).status_code == 200
    ids = _ids(owner.get("/s1/jobs?limit=500", base_url=BASE))
    assert s3_id in ids and s1_id in ids  # сессия staff: S1 как раньше + записи S3


def test_fund_staff_session_sees_s3(env, jobs):
    s3_id, _ = jobs
    staff, _ = make_client(env, "staff-s3@example.test", [("fund_staff", "*")])
    assert staff.get(f"/s3/briefs/{s3_id}/sign-screen", base_url=BASE).status_code == 200
    assert s3_id in _ids(staff.get("/s1/jobs?limit=500", base_url=BASE))


def test_investor_session_forbidden_on_sign_screen_and_no_s3_in_list(env, jobs):
    s3_id, _ = jobs
    inv, _ = make_client(env, "inv-s3@example.test", [("investor", "*")])
    assert inv.get(f"/s3/briefs/{s3_id}/sign-screen", base_url=BASE).status_code == 403
    assert s3_id not in _ids(inv.get("/s1/jobs?limit=500", base_url=BASE))  # investor записи S3 не видит


def test_anonymous_gets_401(env, jobs):
    s3_id, _ = jobs
    c = appmod.app.test_client()
    assert c.get(f"/s3/briefs/{s3_id}/sign-screen", base_url=BASE).status_code == 401
    assert c.get("/s1/jobs", base_url=BASE).status_code == 401


def test_bearer_behaviour_unchanged(env, jobs):
    s3_id, s1_id = jobs
    c = appmod.app.test_client()
    url = f"/s3/briefs/{s3_id}/sign-screen"
    assert c.get(url, headers=ADMIN, base_url=BASE).status_code == 403  # admin-Bearer не подписант
    assert c.get(url, headers=SIGNER, base_url=BASE).status_code == 200  # подписант S1 по Bearer
    adm = _ids(c.get("/s1/jobs?limit=500", headers=ADMIN, base_url=BASE))
    assert s1_id in adm and s3_id not in adm  # admin-Bearer: S1 без S3
    sig = _ids(c.get("/s1/jobs?limit=500", headers=SIGNER, base_url=BASE))
    assert s3_id in sig and s1_id not in sig  # подписант: только S3


def _brief_id():
    """Бриф S3 в хранилище приложения (синтетика), ждёт подписи."""
    from s3_expert import engine as E
    state = appmod.app.extensions["s1_intake"]
    brief = E.build_brief({"question": "Синтетический вопрос?", "bounds": "синтетика", "cutoff": "2026-11-01",
                           "materials": [{"text": "синтетический материал"}]})
    return state.store.create_or_get("s3-" + uuid.uuid4().hex, "S3 brief", "", {"s3": {"brief": brief, "payer": "fund"}})[0]


def test_owner_signs_from_sign_screen_with_csrf_from_page(env, jobs):
    """Проверка стенда 30.10: owner вошёл + Bearer подписанта. Экран подписи отдаёт CSRF-токен сессии,
    без него POST /sign — 403 (CSRF), с ним — подпись проходит. Сессия без Bearer подписать не может (G-12)."""
    import re
    bid = _brief_id()
    owner, _ = make_client(env, "owner-sign@example.test", [("owner", "*")])
    page = owner.get(f"/s3/briefs/{bid}/sign-screen", base_url=BASE)
    assert page.status_code == 200 and page.headers["X-Frame-Options"] == "DENY"
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.get_data(as_text=True)).group(1)
    assert token and token != "__CSRF__"
    url = f"/s3/briefs/{bid}/sign"
    assert owner.post(url, json={"checked": True}, headers=SIGNER, base_url=BASE).status_code == 403  # нет CSRF
    no_bearer = owner.post(url, json={"checked": True}, headers={"X-CSRF-Token": token}, base_url=BASE)
    assert no_bearer.status_code == 403  # сессия — не подпись (G-12)
    ok = owner.post(url, json={"checked": True}, headers={**SIGNER, "X-CSRF-Token": token}, base_url=BASE)
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["signed_by"] == "carol"
