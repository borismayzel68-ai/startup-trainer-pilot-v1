"""Связка S6 → S5: паспорт входа для portfolio_update.reconcile собирается из полей пройденного периметра.
S5 не меняется — берём только его публичный EntryPassport. Непройденный периметр паспорта не даёт (GateBlocked)."""
from __future__ import annotations

from portfolio_update import EntryPassport

from . import engine as E


def entry_passport(gate_id: str, rec: dict, release: dict | None, *, human_decision: dict | None = None) -> EntryPassport:
    """Паспорт только после admit(). Поле release само по себе не открывает непройденный периметр:
    нужно то же решение человека из аудит-лога, что и для входа в S2."""
    E.admit(gate_id, rec, release, human_decision=human_decision)
    return EntryPassport(rec["company_id"], {k: rec["input"][k]["value"] for k in E.FIELDS})
