"""FEAT-002: кабинет показывает только существующие назначения и безопасную навигацию."""
import os
import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("INVITE_CODE", "PILOT-S1-EXT-001")
os.environ.setdefault("SESSION_DATABASE_URL", "sqlite:///:memory:")
import app as appmod  # noqa: E402
from auth_store import AuthStore  # noqa: E402

pytestmark = pytest.mark.auth_enforced
BASE = "https://localhost"


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("AUTH_ALLOWLIST", "")
    previous = appmod.app.extensions["auth_store"]
    fresh = AuthStore("sqlite:///:memory:")
    appmod.app.extensions["auth_store"] = fresh
    appmod.app.testing = True
    yield fresh
    appmod.app.extensions["auth_store"] = previous


def client_with(store, role, scope="*"):
    email = "cabinet-" + role + "@example.test"
    # Исключаем случайные отказы в require_role из-за отсутствия в allowlist.
    os.environ["AUTH_ALLOWLIST"] += ("," if os.environ["AUTH_ALLOWLIST"] else "") + email + ":" + role + ":" + scope
    user = store.upsert_user(email, "Synthetic Cabinet")
    store.ensure_grant(user["id"], role, scope)
    client = appmod.app.test_client()
    with client.session_transaction() as sess:
        sess["uid"] = user["id"]
        sess["csrf"] = "cabinet-csrf"
    return client


def test_cabinet_requires_login(store):
    response = appmod.app.test_client().get("/cabinet", base_url=BASE)
    assert response.status_code == 401


@pytest.mark.parametrize("role", [
    "founder", "investor", "fund_staff", "owner",
    "expert", "signer", "clinic", "syndicator", "buyer",
])
def test_cabinet_accessible_for_current_grant(store, role):
    client = client_with(store, role)
    response = client.get("/cabinet", base_url=BASE)
    assert response.status_code == 200
    assert "Мой рабочий кабинет" in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"


def test_founder_only_sees_trainer_not_fund_or_admin(store):
    response = client_with(store, "founder", "VC-SYNTH").get("/cabinet", base_url=BASE)
    page = response.get_data(as_text=True)
    assert "Тренажёр основателя" in page
    assert 'href="/admin/users"' not in page
    assert 'href="/portfolio"' not in page
    assert "VC-SYNTH" in page


def test_investor_has_read_only_links_and_not_admin(store):
    response = client_with(store, "investor", "VC-SYNTH").get("/cabinet", base_url=BASE)
    page = response.get_data(as_text=True)
    assert 'href="/portfolio"' in page
    assert 'href="/s5/today"' in page
    assert 'href="/admin/users"' not in page
    assert "Тренажёр основателя" not in page


def test_scoped_staff_does_not_offer_global_portfolio(store):
    response = client_with(store, "fund_staff", "CASE-SYNTH").get("/cabinet", base_url=BASE)
    page = response.get_data(as_text=True)
    assert 'href="/portfolio"' not in page
    assert 'href="/s5/today"' in page


def test_unimplemented_role_does_not_claim_working_functions(store):
    response = client_with(store, "expert").get("/cabinet", base_url=BASE)
    page = response.get_data(as_text=True)
    assert "пока не подключены" in page
    assert 'href="/portfolio"' not in page
    assert 'href="/admin/users"' not in page


def test_owner_global_has_admin_link(store):
    response = client_with(store, "owner").get("/cabinet", base_url=BASE)
    assert 'href="/admin/users"' in response.get_data(as_text=True)
