"""HQ-032 (#17): входной пакет до term sheet и обход шлюза S6.

(a) живёт в tests/test_s2_mandate.py::test_a_needs_evidence_blocked_for_owner_staff_and_admin.
Здесь: (b) полный пакет, (d) pre-revenue без кратности, (e) FACT, (g) Human Gate.
#37 закрыт — Closes на него не ставится.
"""
import json
import re
from pathlib import Path

from s6_perimeter.entry_pack import build_entry_pack
from s6_perimeter.engine import FIELDS, NEEDS_EVIDENCE, PASS

ROOT = Path(__file__).resolve().parent.parent
M = 1_000_000


def fields(check_rub=8 * M):
    return {
        "legal_entity": {"value": {"name": "Synth Bio", "form": "ООО"}, "evidence": "SYN-DOC-1"},
        "deal_form": {"value": "convertible_loan", "evidence": "SYN-DOC-2"},
        "check_rub": {"value": check_rub, "evidence": "SYN-DOC-3"},
        "covenants": {"value": [{"type": "information_rights"}], "evidence": "SYN-DOC-4"},
        "tranches": {"value": [{"amount_rub": check_rub}], "evidence": "SYN-DOC-5"},
    }


def test_b_full_pack_passes():
    pack = build_entry_pack({"revenue_status": "pre-revenue", "fields": fields(), "claims": [
        {"label": "FACT", "statement": "договор есть", "verified_by": "analyst"},
    ]})
    assert pack["state"] == PASS
    assert pack["gaps"] == []
    assert [row["field"] for row in pack["conditions"]] == list(FIELDS)
    assert all(row["lawyer_required"] and row["status"] == "CLOSED" for row in pack["conditions"])
    assert pack["sent"] is False and pack["facts_accepted"][0]["verified_by"] == "analyst"


def test_b_missing_field_is_needs_evidence():
    incomplete = fields()
    del incomplete["legal_entity"]
    pack = build_entry_pack({"fields": incomplete})
    assert pack["state"] == NEEDS_EVIDENCE
    assert any(row["field"] == "legal_entity" and row["status"] == NEEDS_EVIDENCE for row in pack["gaps"])


def test_d_pre_revenue_has_no_multiple_or_term_sheet():
    pack = build_entry_pack({
        "revenue_status": "pre-revenue",
        "valuation_multiple": "10×",
        "valuation_rub": 900_000_000,
        "term_sheet": "Term sheet: оценка 900 млн",
        "fields": fields(),
        "claims": [{"label": "hypothesis", "statement": "оценка 10× к аналогам — 900 млн"}],
    })
    blob = json.dumps(pack, ensure_ascii=False).lower()
    assert pack["pre_revenue"] is True
    for key in ("valuation_multiple", "valuation", "valuation_rub", "multiple", "term_sheet", "fair_value"):
        assert key not in pack
    assert "10×" not in blob and "10x" not in blob and "900" not in blob and "term sheet" not in blob
    stopped = build_entry_pack({"request": "term_sheet", "term_sheet": "пункт 1. оценка", "fields": fields()})
    assert stopped["state"] == "STOP_FORBIDDEN" and stopped["sent"] is False
    assert "оценка" not in json.dumps(stopped, ensure_ascii=False).lower()


def test_e_fact_without_verified_by_rejected():
    pack = build_entry_pack({"fields": fields(), "claims": [
        {"label": "FACT", "statement": "без подписи"},
        {"label": "FACT", "statement": "админ", "verified_by": "admin"},
        {"label": "FACT", "statement": "владелец", "verified_by": "owner"},
    ]})
    assert pack["facts_rejected"]
    assert [c["verified_by"] for c in pack["facts_accepted"]] == ["owner"]


def test_a_waiver_survives_a_long_audit_log():
    """Решение человека ищется по шлюзу, а не среди последних строк общего журнала."""
    from flask import Flask

    from auth_store import AuthStore
    from s6_perimeter import waiver

    app = Flask("audit-window")
    store = AuthStore("sqlite:///:memory:")
    app.extensions["auth_store"] = store
    digest = "a" * 64
    with app.app_context():
        store.audit("owner@example.test", waiver.WAIVER_ACTION, "gate-1", "allow", "127.0.0.1",
                    f"source=audit_log sha256={digest} reason=синтетика")
        for i in range(600):
            store.audit("other@example.test", "change", f"/n/{i}", "allow", "127.0.0.1", "шум")
        found = waiver.human_decision(None, "gate-1", digest)
    assert found == {"by": "owner@example.test", "sha256": digest, "source": "audit_log"}
    assert store.audit_rows(500)[0]["object"] != "gate-1"


def test_g_no_autosend():
    for path in (ROOT / "s6_perimeter").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"import (httpx|requests|smtplib|urllib)|send_email|openai", text), path.name
    pack = build_entry_pack({"fields": fields()})
    assert pack["sent"] is False and pack["dispatched"] is False
