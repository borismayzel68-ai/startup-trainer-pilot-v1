"""Список и карточка: последний снапшот + RAG + доступ. Хранилище — Postgres (PgRepo) или словари (тесты)."""
from __future__ import annotations

from datetime import date

from . import rag
from .access import Actor, can_list, can_view

NOMINEE_NOTE = "деньги фонда, договор на партнёра"
PASSPORT_FIELDS = ("cost", "valuation_basis", "valuation_date", "round", "ownership_pct", "runway_months", "burn",
                   "plan_burn", "next_milestone_what", "next_milestone_when", "next_milestone_who", "loan_income",
                   "last_investor_update")


class Forbidden(PermissionError):
    pass


def _view(company: dict, snap: dict | None, today: date) -> dict:
    snap = snap or {}
    res = rag.evaluate(snap.get("runway_months"), snap.get("burn"), snap.get("plan_burn"),
                       snap.get("last_investor_update"), today, company["thresholds"])
    out = {"vc_id": company["vc_id"], "name": company["name"], "holding_vehicle": company["holding_vehicle"],
           "holding_note": NOMINEE_NOTE if company["holding_vehicle"] == "partner_nominee" else None,
           "snapshot_date": snap.get("snapshot_date"),
           "rag": {"color": res.color, "burn_flag": res.burn_flag, "update_overdue": res.update_overdue,
                   "explanation": res.explanation()}}
    out.update({k: snap.get(k) for k in PASSPORT_FIELDS})
    return out


def list_view(repo, actor: Actor | None, today: date) -> list[dict]:
    if not can_list(actor):
        raise Forbidden("нет доступа к портфелю")
    rows = []
    for c in repo.companies():
        if can_view(actor, c["vc_id"]):
            s = repo.snapshots(c["vc_id"])
            v = _view(c, s[0] if s else None, today)
            rows.append({k: v[k] for k in ("vc_id", "name", "holding_vehicle", "holding_note", "runway_months", "rag")})
    return rows


def card_view(repo, actor: Actor | None, vc_id: str, today: date) -> dict:
    if not can_view(actor, vc_id):
        raise Forbidden("нет доступа к компании")
    c = next((x for x in repo.companies() if x["vc_id"] == vc_id), None)
    if c is None:
        raise LookupError(vc_id)
    s = repo.snapshots(vc_id)
    v = _view(c, s[0] if s else None, today)
    v["history"] = [{k: x.get(k) for k in ("snapshot_date", *PASSPORT_FIELDS)} for x in s]
    return v


class PgRepo:
    def __init__(self, conn):
        self.conn = conn

    def companies(self):
        rows = self.conn.execute(
            "SELECT s.vc_id, s.name, p.holding_vehicle::text, p.runway_red_below, p.runway_yellow_upto "
            "FROM portfolio_companies p JOIN startups s ON s.id=p.startup_id ORDER BY s.vc_id").fetchall()
        return [{"vc_id": r[0], "name": r[1], "holding_vehicle": r[2], "thresholds": rag.Thresholds(r[3], r[4])}
                for r in rows]

    def snapshots(self, vc_id):
        cols = ("snapshot_date",) + PASSPORT_FIELDS
        cur = self.conn.execute(
            "SELECT " + ", ".join("n." + c for c in cols) + " FROM portfolio_passport_snapshots n "
            "JOIN portfolio_companies p ON p.id=n.company_id JOIN startups s ON s.id=p.startup_id "
            "WHERE s.vc_id=%s ORDER BY n.snapshot_date DESC", (vc_id,))
        return [dict(zip(cols, r)) for r in cur.fetchall()]
