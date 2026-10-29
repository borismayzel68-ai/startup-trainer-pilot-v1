"""HQ-031 (#16): светофор по форме HQ-030. Только синтетика.

Уже в master (в этом файле не переписываются):
- светофор паспорта MOS-4, tests/test_mos4_portfolio.py;
- отсутствие таблицы Promise Ledger в db/migrations;
- сверка и триггер частичного выхода, tests/test_hq042_s5_reconcile.py.

Здесь — то, что дописывает этот PR: (c) схема без ledger, (e) FACT, (f) цвета, (g) Human Gate.
"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio.access import Actor
from portfolio_update import NEEDS_EVIDENCE, EntryPassport, PortfolioUpdates, reconcile
from s5_monitor.engine import GREEN, RED, YELLOW, InvalidThresholds, Stop, evaluate, runway_months, screen_facts
from s5_monitor.schema import PROMISE_FIELDS
from s5_monitor.synthetic import AS_OF, COMPANIES

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = {
    "SYN-S5-01": GREEN,
    "SYN-S5-02": RED,
    "SYN-S5-03": YELLOW,
    "SYN-S5-04": RED,
    "SYN-S5-05": YELLOW,
    "SYN-S5-06": GREEN,
}


def _bare(**kw):
    row = {
        "company_id": "SYN-UNIT",
        "synthetic": True,
        "vertical": "medtech",
        "as_of": AS_OF,
        "thesis": "синтетический тезис",
        "promises": [{"promise_id": "P1", "text": "t", "status": "HIT", "evidence": "SYN"}],
        "metrics": {"cash": 20, "cash_date": AS_OF, "regular_burn": 1, "obligations": 0,
                    "last_update": AS_OF},
        "claims": [],
    }
    row.update(kw)
    return row


def test_c_no_new_ledger_tables():
    names = []
    for path in (ROOT / "db" / "migrations").glob("*.sql"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "CREATE TABLE" in line.upper():
                names.append(line)
    blob = "\n".join(names).lower()
    assert "ledger" not in blob and "promise" not in blob
    for path in (ROOT / "s5_monitor").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "CREATE TABLE" not in text.upper()
        assert "promise_ledger" not in text
    assert "promise_id" in PROMISE_FIELDS
    assert not list((ROOT / "s5_monitor").glob("*.sql"))
    assert not list((ROOT / "db" / "migrations").glob("*ledger*"))


def test_e_fact_without_verified_by_rejected():
    accepted, rejected = screen_facts([
        {"label": "FACT", "statement": "касса 40"},
        {"label": "FACT", "statement": "касса 40", "verified_by": ""},
        {"label": "FACT", "statement": "касса 40", "verified_by": "admin"},
        {"label": "FACT", "statement": "касса 40", "verified_by": "analyst"},
        {"label": "FACT", "statement": "касса 40", "verified_by": "owner"},
        {"label": "hypothesis", "statement": "заявление"},
    ])
    assert len(rejected) == 3
    assert {c.get("verified_by") for c in accepted if c.get("label") == "FACT"} == {"analyst", "owner"}
    out = evaluate(_bare(claims=[{"label": "FACT", "statement": "без подписи"}]))
    assert out["facts_rejected"] and not any(c.get("label") == "FACT" for c in out["facts_accepted"])


def test_f_six_companies_red_yellow_green():
    got = {c["company_id"]: evaluate(c) for c in COMPANIES}
    assert {cid: row["color"] for cid, row in got.items()} == EXPECTED
    assert {row["color_label"] for row in got.values()} == {"красный", "жёлтый", "зелёный"}
    for row in got.values():
        assert row["thresholds"] == {"runway_red_below": "6", "runway_yellow_below": "9"}
        assert "6" in row["threshold_note"] and "9" in row["threshold_note"]
        assert row["synthetic"] is True
    assert got["SYN-S5-01"]["runway_months"] == "19.5"
    assert got["SYN-S5-06"]["runway_months"] == "9.0"
    assert any(d["code"] == "repeat_miss" for d in got["SYN-S5-02"]["drivers"])
    assert any(d["code"] == "promise_missing" for d in got["SYN-S5-03"]["drivers"])


@pytest.mark.parametrize("runway,color", [("5.9", RED), ("6", YELLOW), ("8.9", YELLOW), ("9", GREEN), ("9.1", GREEN)])
def test_f_default_thresholds(runway, color):
    out = evaluate(_bare(metrics={"cash": runway, "cash_date": AS_OF, "regular_burn": 1, "obligations": 0,
                                  "last_update": AS_OF}))
    assert out["color"] == color


def test_f_investor_threshold_and_range():
    custom = {"runway_red_below": 8, "runway_yellow_below": 14}
    assert evaluate(_bare(thresholds=custom, metrics={"cash": 7, "cash_date": AS_OF, "regular_burn": 1,
                                                      "obligations": 0, "last_update": AS_OF}))["color"] == RED
    mid = evaluate(_bare(thresholds=custom, metrics={"cash": 10, "cash_date": AS_OF, "regular_burn": 1,
                                                     "obligations": 0, "last_update": AS_OF}))
    assert mid["color"] == YELLOW and mid["thresholds"]["runway_red_below"] == "8"
    assert evaluate(_bare(thresholds=custom, metrics={"cash": 15, "cash_date": AS_OF, "regular_burn": 1,
                                                      "obligations": 0, "last_update": AS_OF}))["color"] == GREEN
    for bad in ({"runway_red_below": 5, "runway_yellow_below": 9},
                {"runway_red_below": 6, "runway_yellow_below": 19},
                {"runway_red_below": 12, "runway_yellow_below": 12}):
        with pytest.raises(InvalidThresholds):
            evaluate(_bare(thresholds=bad))


def test_promised_inflow_does_not_change_runway():
    metrics = {"cash": 4, "cash_date": AS_OF, "regular_burn": 2, "obligations": 0, "last_update": AS_OF,
               "promised_inflow": 100, "grant_not_received": 20}
    months = runway_months(metrics, AS_OF)
    assert months == Decimal(2)
    assert evaluate(_bare(metrics=metrics))["color"] == RED


def test_g_no_autosend():
    import re
    for path in (ROOT / "s5_monitor").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"import (httpx|requests|smtplib|urllib)|send_email|openai", text), path.name
    for case in COMPANIES:
        out = evaluate(case)
        assert out["sent"] is False and out["dispatched"] is False
        if out["color"] == RED:
            assert out["human_gate"] is True and out["draft_actions"]
            assert all("не отправлен" in line for line in out["draft_actions"])


def test_stop_hold_and_unknown_are_not_green():
    with pytest.raises(Stop):
        evaluate({"company_id": "SYN", "as_of": AS_OF, "vertical": "fintech"})
    with pytest.raises(Stop):
        evaluate({"vertical": "medtech", "as_of": AS_OF})
    hold = evaluate({"company_id": "SYN", "vertical": "biotech", "as_of": AS_OF, "promises": [], "thesis": ""})
    assert hold["state"] == "HOLD" and hold["color"] is None and hold["sent"] is False
    unknown = evaluate(_bare(metrics={"last_update": AS_OF}, promises=[{"promise_id": "P", "status": "HIT", "text": "t"}]))
    assert unknown["state"] == "UNEVALUATED" and unknown["color"] is None


def test_hq042_reconcile_and_new_round_still_work_inside_monitor():
    passport = {"field_a": "x"}
    out = evaluate(_bare(new_round="R2", entry_passport=passport, update={"field_a": "y", "field_c": 1}))
    assert out["partial_exit_check"]["status"] == "OPEN"
    assert out["partial_exit_check"]["rules"] == NEEDS_EVIDENCE
    assert out["partial_exit_check"]["sent"] is False
    kinds = {d["field"]: d["kind"] for d in out["passport_diffs"]}
    assert kinds == {"field_a": "changed", "field_c": "not_in_passport"}
    # тот же контракт напрямую, как в закрытом #41
    actor = Actor("fund_staff")
    assert reconcile(EntryPassport("SYN-UNIT", passport), {"field_a": "x"}, actor=actor) == []
    flag = PortfolioUpdates().mark_new_round("SYN-UNIT", "R2", actor=actor)
    assert flag.status == "OPEN" and flag.rules == NEEDS_EVIDENCE


def test_same_input_same_color():
    a = evaluate(COMPANIES[1])
    b = evaluate(COMPANIES[1])
    assert a["color"] == b["color"] == RED
    assert a["drivers"] == b["drivers"]


def test_today_endpoint_is_read_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SESSION_DATABASE_URL", "sqlite:///:memory:")
    import app as appmod
    appmod.app.testing = True
    previous = appmod.app.config.get("AUTH_ENFORCED", True)
    appmod.app.config["AUTH_ENFORCED"] = False
    try:
        client = appmod.app.test_client()
        response = client.get("/s5/today")
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert body["sent"] is False and len(body["companies"]) == 6
        colors = [row["color"] for row in body["companies"]]
        assert colors[0] == RED and GREEN in colors and YELLOW in colors
        assert client.post("/s5/today", json={"send": True}).status_code == 405
        assert "sent" in json.dumps(body)
    finally:
        appmod.app.config["AUTH_ENFORCED"] = previous
