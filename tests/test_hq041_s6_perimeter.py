"""HQ-041 S6 (#37): шлюз «Периметр входа». Только синтетика, без сети."""
import copy
import os
import uuid

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SESSION_DATABASE_URL", "sqlite:///:memory:")

from portfolio.access import Actor  # noqa: E402
from portfolio_update import PortfolioUpdates, reconcile  # noqa: E402
from s6_perimeter import blueprint as B  # noqa: E402
from s6_perimeter import engine as E  # noqa: E402
from s6_perimeter.s5_link import entry_passport  # noqa: E402

POLICY = E.load_policy()
GOOD = {
    "legal_entity": {"value": {"name": "Synth Bio", "form": "ООО"}, "evidence": "SYN-DOC-1"},
    "deal_form": {"value": "convertible_loan", "evidence": "SYN-DOC-2"},
    "check_rub": {"value": 8_000_000, "evidence": "SYN-DOC-3"},
    "covenants": {"value": [{"type": "information_rights"}], "evidence": "SYN-DOC-4"},
    "tranches": {"value": [{"amount_rub": 3_000_000}, {"amount_rub": 5_000_000}], "evidence": "SYN-DOC-5"},
}


def ev(**kw):
    return E.evaluate({**GOOD, **kw}, POLICY)


def test_all_fields_present_passes():
    r = E.evaluate(GOOD, POLICY)
    assert r["result"] == E.PASS and r["problems"] == [] and r["policy_status"] == "hypothesis"


@pytest.mark.parametrize("field", E.FIELDS)
def test_missing_field_needs_evidence(field):
    app = copy.deepcopy(GOOD)
    del app[field]
    r = E.evaluate(app, POLICY)
    assert r["result"] == E.NEEDS_EVIDENCE
    assert {"field": field, "reason": "нет данных"} in r["problems"]


@pytest.mark.parametrize("field", E.FIELDS)
@pytest.mark.parametrize("empty", [None, "", [], {}])
def test_empty_value_needs_evidence(field, empty):
    assert ev(**{field: {"value": empty, "evidence": "SYN"}})["result"] == E.NEEDS_EVIDENCE


@pytest.mark.parametrize("field", E.FIELDS)
@pytest.mark.parametrize("evidence", [None, "", "  ", 5])
def test_no_evidence_needs_evidence(field, evidence):
    r = ev(**{field: {**GOOD[field], "evidence": evidence}})
    assert r["result"] == E.NEEDS_EVIDENCE
    assert {"field": field, "reason": "нет доказательства"} in r["problems"]


def test_values_outside_policy_are_blocked():
    bad = [
        {"legal_entity": {"value": {"name": "X", "form": "ИП"}, "evidence": "d"}},
        {"deal_form": {"value": "grant", "evidence": "d"}},
        {"check_rub": {"value": 5_900_000, "evidence": "d"}},
        {"check_rub": {"value": True, "evidence": "d"}},
        {"covenants": {"value": [{"type": "unknown"}], "evidence": "d"}},
        {"tranches": {"value": [{"amount_rub": 8_000_000}] * 6, "evidence": "d"}},
        {"tranches": {"value": [{"amount_rub": 1}], "evidence": "d"}},  # сумма ≠ чек
    ]
    for kw in bad:
        assert ev(**kw)["result"] == E.NEEDS_EVIDENCE, kw


def test_check_boundaries_inclusive():
    both = {"tranches": {"value": [{"amount_rub": 6_000_000}], "evidence": "d"}}
    assert ev(check_rub={"value": 6_000_000, "evidence": "d"}, **both)["result"] == E.PASS


def test_thresholds_come_from_config_not_code(tmp_path):
    p = tmp_path / "p.yaml"
    text = (E.PKG / "perimeter_policy.yaml").read_text(encoding="utf-8").replace("max: 10000000", "max: 7000000")
    p.write_text(text, encoding="utf-8")
    pol = E.load_policy(str(p))
    assert E.evaluate(GOOD, pol)["result"] == E.NEEDS_EVIDENCE
    assert E.evaluate(GOOD, POLICY)["result"] == E.PASS


def test_config_values_marked_hypothesis():
    assert POLICY["status"] == "hypothesis"
    for f in E.FIELDS:
        assert POLICY[f]["status"] == "hypothesis", f


def test_no_policy_literals_in_code():
    src = (E.PKG / "engine.py").read_text(encoding="utf-8")
    for lit in ("6000000", "10000000", "convertible_loan", "information_rights", "ООО"):
        assert lit not in src


def test_admit_blocks_and_owner_release_is_bound_to_data():
    rec = {"company_id": "SYN-1", "input": {}, **E.evaluate({}, POLICY)}
    digest = E.sha256(rec)
    with pytest.raises(E.GateBlocked):
        E.admit("g1", rec, None)
    rel = {"by": "owner@synthetic.test", "sha256": digest}
    with pytest.raises(E.GateBlocked):  # JSON release сам по себе не допуск
        E.admit("g1", rec, rel)
    decision = {"by": "owner@synthetic.test", "sha256": digest, "source": "audit_log"}
    E.admit("g1", rec, None, human_decision=decision)
    with pytest.raises(E.GateBlocked):  # данные изменились — решение не действует
        E.admit("g1", {**rec, "company_id": "SYN-2"}, None, human_decision=decision)
    with pytest.raises(E.GateBlocked):  # нет именного актора
        E.admit("g1", rec, None, human_decision={"sha256": digest, "source": "audit_log"})
    with pytest.raises(E.GateBlocked):  # admin не человек
        E.admit("g1", rec, None, human_decision={"by": "admin", "sha256": digest, "source": "audit_log"})


def test_forged_result_field_does_not_admit():
    rec = {"company_id": "SYN-1", "input": {}, "result": E.PASS, "problems": []}
    with pytest.raises(E.GateBlocked):
        E.admit("g1", rec, None)


def test_s5_link_only_after_perimeter():
    rec = {"company_id": "SYN-1", "input": GOOD, **E.evaluate(GOOD, POLICY)}
    p = entry_passport("g1", rec, None)
    assert p.company_id == "SYN-1" and p.fields["deal_form"] == "convertible_loan"
    assert reconcile(p, dict(p.fields), actor=Actor("fund_staff")) == []
    assert isinstance(PortfolioUpdates().flags, (list, dict))
    with pytest.raises(E.GateBlocked):
        entry_passport("g2", {"company_id": "SYN-1", "input": {}}, None)
    blocked = {"company_id": "SYN-1", "input": {**GOOD, "covenants": {"value": [], "evidence": "SYN"}},
               **E.evaluate({**GOOD, "covenants": {"value": [], "evidence": "SYN"}}, POLICY)}
    forged = {"by": "owner@synthetic.test", "sha256": E.sha256(blocked)}
    with pytest.raises(E.GateBlocked):
        entry_passport("g3", blocked, forged)
    decision = {"by": "owner@synthetic.test", "sha256": E.sha256(blocked), "source": "audit_log"}
    assert entry_passport("g3", blocked, None, human_decision=decision).company_id == "SYN-1"


# ----------------------------------------------------------------- HTTP / RBAC
def _client():
    import app as appmod
    return appmod.app.test_client()


def _login(store, email, role):
    user = store.upsert_user(email, "Synthetic " + email)
    store.ensure_grant(user["id"], role, "*")
    os.environ["AUTH_ALLOWLIST"] = ",".join(filter(None, [os.environ.get("AUTH_ALLOWLIST", ""), f"{email}:{role}"]))
    c = _client()
    with c.session_transaction() as sess:
        sess["uid"] = user["id"]
        sess["csrf"] = "csrf-" + email
    c.csrf = {"X-CSRF-Token": "csrf-" + email}
    return c


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setenv("AUTH_ALLOWLIST", os.environ.get("AUTH_ALLOWLIST", ""))
    import app as appmod
    from auth_store import AuthStore
    store = AuthStore("sqlite:///:memory:")
    monkeypatch.setitem(appmod.app.extensions, "auth_store", store)
    appmod.app.testing = True
    tag = uuid.uuid4().hex[:8]
    out = {r: _login(store, f"{r}-{tag}@synthetic.test", r) for r in ("owner", "fund_staff", "investor")}
    out["anon"] = _client()
    return out


def _create(c, fields):
    r = c.post("/s6/gates", headers=c.csrf, json={"company_id": "SYN-CO-1", "fields": fields})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()


ROUTES = [("post", "/s6/gates"), ("get", "/s6/gates/x"), ("post", "/s6/gates/x/forward-to-s2"),
          ("post", "/s6/gates/x/release")]


@pytest.mark.auth_enforced
@pytest.mark.parametrize("method,url", ROUTES)
def test_anon_401_investor_403(clients, method, url):
    assert getattr(clients["anon"], method)(url, json={}).status_code == 401
    inv = clients["investor"]
    r = getattr(inv, method)(url, headers=inv.csrf, json={})
    assert r.status_code == 403 and r.get_json()["code"] == "forbidden"


@pytest.mark.auth_enforced
def test_release_is_owner_only_and_all_routes_use_require_role():
    import app as appmod
    for ep, view in appmod.app.view_functions.items():
        if ep.startswith("s6_perimeter."):
            roles = view._rbac["roles"]
            assert roles == ({"owner"} if ep.endswith("release_gate") else {"owner", "fund_staff"}), ep


@pytest.mark.auth_enforced
def test_fund_staff_can_create_but_not_release(clients):
    staff = clients["fund_staff"]
    g = _create(staff, {})
    assert g["result"] == E.NEEDS_EVIDENCE and g["admitted_to_s2"] is False
    r = staff.post(f"/s6/gates/{g['id']}/release", headers=staff.csrf, json={"reason": "x"})
    assert r.status_code == 403


@pytest.mark.auth_enforced
@pytest.mark.parametrize("role", ["owner", "fund_staff"])
def test_pass_goes_to_s2(clients, role):
    c = clients[role]
    g = _create(c, GOOD)
    assert g["result"] == E.PASS and g["admitted_to_s2"] is True
    r = c.post(f"/s6/gates/{g['id']}/forward-to-s2", headers=c.csrf, json={"sector": "medtech", "innovative": True})
    assert r.status_code == 201 and r.get_json()["result"] == "FIT"
    again = c.post(f"/s6/gates/{g['id']}/forward-to-s2", headers=c.csrf, json={"sector": "medtech", "innovative": True})
    assert again.status_code == 409  # повтор — 409, как у POST /s2/checks (один захват шлюза на оба пути)


@pytest.mark.auth_enforced
@pytest.mark.parametrize("field", E.FIELDS)
def test_http_missing_field_blocks_s2(clients, field):
    c = clients["owner"]
    fields = {k: v for k, v in GOOD.items() if k != field}
    g = _create(c, fields)
    assert g["result"] == E.NEEDS_EVIDENCE
    r = c.post(f"/s6/gates/{g['id']}/forward-to-s2", headers=c.csrf, json={"sector": "medtech", "innovative": True})
    assert r.status_code == 409 and r.get_json()["gate_status"] == E.NEEDS_EVIDENCE
    assert r.get_json()["problems"][0]["field"] == field
    assert c.get(f"/s6/gates/{g['id']}").get_json()["s2_check_id"] is None


@pytest.mark.auth_enforced
def test_bypass_attempts(clients):
    owner, staff = clients["owner"], clients["fund_staff"]
    g = _create(staff, {})
    gid = g["id"]
    # 1) прямой вызов функции передачи
    with clients["owner"].application.test_request_context():
        with pytest.raises(E.GateBlocked):
            B.forward_to_s2(gid, "medtech", True)
    # 2) подделанное тело: result/admitted в запросе игнорируются
    r = staff.post("/s6/gates", headers=staff.csrf, json={
        "company_id": "SYN-CO-1", "result": "PASS", "admitted_to_s2": True, "fields": {}})
    assert r.get_json()["result"] == E.NEEDS_EVIDENCE
    r = staff.post(f"/s6/gates/{gid}/forward-to-s2", headers=staff.csrf,
                   json={"sector": "medtech", "innovative": True, "result": "PASS", "released_by_owner": {"by": "x"}})
    assert r.status_code == 409
    # 3) release от fund_staff / без reason
    assert staff.post(f"/s6/gates/{gid}/release", headers=staff.csrf, json={"reason": "x"}).status_code == 403
    assert owner.post(f"/s6/gates/{gid}/release", headers=owner.csrf, json={}).status_code == 400
    # 4) release без CSRF
    assert owner.post(f"/s6/gates/{gid}/release", json={"reason": "x"}).status_code == 403
    # 5) подмена result в хранилище не открывает шлюз
    store = owner.application.extensions["s1_intake"].store
    data = store.get(gid)["data"]
    data["s6"]["gate"]["result"] = E.PASS
    store.update(gid, data_patch=data)
    assert staff.post(f"/s6/gates/{gid}/forward-to-s2", headers=staff.csrf,
                      json={"sector": "medtech", "innovative": True}).status_code == 409


@pytest.mark.auth_enforced
def test_owner_release_opens_gate_only_for_same_data(clients):
    owner, staff = clients["owner"], clients["fund_staff"]
    g = _create(staff, {k: v for k, v in GOOD.items() if k != "covenants"})
    body = {"sector": "medtech", "innovative": True}
    assert staff.post(f"/s6/gates/{g['id']}/forward-to-s2", headers=staff.csrf, json=body).status_code == 409
    r = owner.post(f"/s6/gates/{g['id']}/release", headers=owner.csrf, json={"reason": "синтетическое решение owner"})
    assert r.status_code == 200 and r.get_json()["released_by_owner"]["by"].startswith("owner-")
    assert owner.post(f"/s6/gates/{g['id']}/release", headers=owner.csrf, json={"reason": "ещё"}).status_code == 409
    assert staff.post(f"/s6/gates/{g['id']}/forward-to-s2", headers=staff.csrf, json=body).status_code == 201
    # новый шлюз с теми же данными решение не наследует
    g2 = _create(staff, {k: v for k, v in GOOD.items() if k != "covenants"})
    assert staff.post(f"/s6/gates/{g2['id']}/forward-to-s2", headers=staff.csrf, json=body).status_code == 409


@pytest.mark.auth_enforced
def test_release_not_needed_when_passed_and_404(clients):
    owner = clients["owner"]
    g = _create(owner, GOOD)
    assert owner.post(f"/s6/gates/{g['id']}/release", headers=owner.csrf, json={"reason": "x"}).status_code == 409
    assert owner.get("/s6/gates/nope").status_code == 404
    assert owner.post("/s6/gates/nope/release", headers=owner.csrf, json={"reason": "x"}).status_code == 404


@pytest.mark.auth_enforced
def test_s2_input_missing_returns_422(clients):
    c = clients["owner"]
    g = _create(c, GOOD)
    r = c.post(f"/s6/gates/{g['id']}/forward-to-s2", headers=c.csrf, json={})
    assert r.status_code == 422 and set(r.get_json()["missing"]) == {"sector", "innovative"}


def test_no_outbound_calls_in_s6():
    import re
    for f in E.PKG.glob("*.py"):
        assert not re.search(r"import (httpx|requests|smtplib|urllib)|openai", f.read_text(encoding="utf-8")), f.name
