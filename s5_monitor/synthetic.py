"""Шесть синтетических компаний для светофора HQ-031. Совпадения с реальными компаниями случайны.

Дата среза у всех — 15.10.2026, как в спецификации S5. Суммы — в млн условных единиц.
"""
from __future__ import annotations

from datetime import date

AS_OF = date(2026, 10, 15)
CASH = date(2026, 9, 30)


def _promise(pid, status, *, thesis=False, missing=False, evidence="SYN-DOC"):
    return {
        "promise_id": pid,
        "text": f"синтетическое обещание {pid}",
        "promised_on": "2026-01-15",
        "owner": "основатель",
        "due": "2026-09-30",
        "status": status,
        "evidence": evidence,
        "last_reviewed": "2026-10-05",
        "thesis_bearing": thesis,
        "missing_from_update": missing,
    }


def _case(cid, cash, burn, promises, *, update=date(2026, 10, 5), events=None, claims=None, extra=None):
    row = {
        "company_id": cid,
        "synthetic": True,
        "vertical": "medtech",
        "as_of": AS_OF,
        "thesis": "синтетический тезис регистрации",
        "promises": promises,
        "metrics": {
            "cash": cash,
            "cash_date": CASH,
            "regular_burn": burn,
            "obligations": 0,
            "last_update": update,
        },
        "material_events": events or [],
        "claims": claims or [],
    }
    if extra:
        row.update(extra)
    return row


# Ожидаемые цвета при порогах по умолчанию (красный < 6, жёлтый < 9), с учётом 15 дней от даты кассы.
COMPANIES = [
    # 40 / 2 − 0,5 = 19,5 → зелёный; обещания HIT
    _case("SYN-S5-01", 40, 2, [_promise("P1", "HIT", evidence="SYN-протокол"), _promise("P2", "HIT")]),
    # повторный MISS по тезису → красный (деньги сами по себе были бы жёлтыми: 14 / 1.5 − 0,5 ≈ 8,8)
    _case("SYN-S5-02", 14, "1.5", [
        _promise("P1", "MISS", thesis=True),
        _promise("P2", "MISS", thesis=True),
    ]),
    # конфликт не по тезису + пропавшее обещание → жёлтый
    _case("SYN-S5-03", 12, "1.5", [
        _promise("P1", "HIT"),
        _promise("P2", "OPEN", missing=True),
    ], claims=[{"label": "hypothesis", "conflict": True, "statement": "грант получен", "source": "SYN-выписка"}]),
    # 8 / 2 − 0,5 = 3,5 < 6 → красный только из-за порога
    _case("SYN-S5-04", 8, 2, [_promise("P1", "HIT")]),
    # 16 / 2 − 0,5 = 7,5 → жёлтый только из-за порога
    _case("SYN-S5-05", 16, 2, [_promise("P1", "HIT")]),
    # 19 / 2 − 0,5 = 9,0 (жёлтый — строго меньше 9) → зелёный
    _case("SYN-S5-06", 19, 2, [_promise("P1", "HIT")]),
]

# SYN-S5-02: контрольная точка транша без доказательства — второй красный драйвер.
COMPANIES[1]["metrics"]["tranche_checkpoint"] = {"passed": True, "evidence": ""}
