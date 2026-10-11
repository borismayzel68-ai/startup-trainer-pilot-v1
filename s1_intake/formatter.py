"""Readiness Pack: JSON + HTML + PDF.

Founder Readiness Report строится ТОЛЬКО из whitelist-представления
(build_founder_view) — в него физически не попадают вердикт, Bull/Bear-кейсы,
внутренние гейты и аудит-след. Это гарантирует тест test_founder_report_has_no_internal_data.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import Category, EvaluationResult
from .scoring import priority_gaps

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))

LEVEL_CODE = {None: "—", 0: "E0", 1: "E1", 2: "E2", 3: "E3", 4: "E4"}


def readiness_band(r: int) -> str:
    if r >= 70:
        return "Высокая"
    if r >= 50:
        return "Средняя"
    if r >= 30:
        return "Начальная"
    return "Низкая"


def build_founder_view(res: EvaluationResult, m: dict, fund_name: str) -> dict:
    scale = {int(k): v for k, v in m["evidence_scale"].items()}
    ls = res.scores.ls
    excluded = set((ls or {}).get("excluded_blocks", []))
    evidence_map = []
    for b in res.scores.blocks:
        row = {
            "label": b.label,
            "score_pct": int(round(b.score * 100)),
            "items": [_founder_item(it) for it in b.items],
        }
        if b.key in excluded:
            row["na_by_stage"] = True
        evidence_map.append(row)
    s = res.synthesis
    view = {
        "fund_name": fund_name,
        "company": res.submission.project_name,
        "founder_name": res.submission.founder_name,
        "date": res.created_at.strftime("%d.%m.%Y"),
        "readiness": res.scores.readiness,
        "readiness_band": readiness_band(res.scores.readiness),
        "scale": [{"code": v["code"], "label": v["label"], "description": v["description"]} for _, v in sorted(scale.items())],
        "evidence_map": evidence_map,
        "barriers": [b.model_dump() for b in s.critical_barriers],
        "proof_points": [{
            "proof_point": p.proof_point, "how_to_obtain": p.how_to_obtain,
            "from": LEVEL_CODE[p.current_level], "to": LEVEL_CODE[p.target_level],
            "why": p.valuation_impact,
        } for p in s.proof_points],
        "plan": {"30": s.plan_30, "60": s.plan_60, "90": s.plan_90},
        "objections": s.founder_objections_to_preempt,
        "file_warnings": [w for d in res.docs for w in d.warnings],
    }
    if ls:  # HQ-016 §4: только для LS-разметки; не-LS вывод не меняется
        view["ls"] = {k: ls.get(k) for k in ("applied", "reason", "stage_label", "summary_line", "na_slots",
                                             "milestones", "barriers", "next_artifact", "slots_scored",
                                             "slots_applicable", "zones", "gate_request", "stage_uncertain")}
    return view


def _founder_item(it) -> dict:
    d = {
        "label": it.label,
        "critical": it.critical,
        "level": LEVEL_CODE[it.best_level] if it.status != Category.UNKNOWN else "нет данных",
        "level_num": it.best_level if it.status not in (Category.UNKNOWN, Category.CONFLICT) else -1,
        "conflict": it.status == Category.CONFLICT,
    }
    if it.applicability == "N_A_BY_STAGE":
        d.update(level="не применимо по стадии", na_by_stage=True, na_reason=it.na_reason)
    return d


def build_internal_view(res: EvaluationResult, m: dict, fund_name: str) -> dict:
    by_id = {c.id: c for c in res.classification.claims}
    return {
        "fund_name": fund_name,
        "r": res,
        "by_id": by_id,
        "level_code": LEVEL_CODE,
        "counts": {cat.value: sum(1 for c in res.classification.claims if c.category == cat) for cat in Category},
        "founder_view": build_founder_view(res, m, fund_name),
    }


def html_to_pdf(html: str) -> bytes | None:
    """PDF из сохранённого HTML. None — если WeasyPrint/системные библиотеки (pango) недоступны."""
    try:
        from weasyprint import HTML  # тяжёлая зависимость: грузим только здесь
    except Exception as e:  # noqa: BLE001
        log.error("WeasyPrint unavailable (%s) — PDF skipped, HTML kept", e)
        return None
    return HTML(string=html, base_url=str(TEMPLATES)).write_pdf()


def build_free_part(res: EvaluationResult, m: dict) -> dict:
    """HQ-020 (решение PM): бесплатная часть S1 — балл, три пробела, риск по каждому, метка-оговорка.
    Уходит автоматически, без approve. Внутренние метки вердикта сюда не попадают."""
    from .config import build_checklist
    cfg = m["free_part_s1"]
    ls = res.scores.ls or {}
    idx = build_checklist(m, res.routing.get("modules", []), bool(ls.get("milestones")))
    gaps = []
    for g in priority_gaps(res.scores, top=50):
        why = (idx.get(g["item"]) or {}).get("why") or ""
        if not why:
            continue  # риск не задан методологией — не выдумываем
        gaps.append({"gap": g["label"], "risk": why.strip()})
        if len(gaps) == cfg["gaps"]:
            break
    stage = ls.get("stage_label") if ls else None
    if ls.get("stage_uncertain"):
        stage = cfg["stage_unconfirmed"]
    part = {"label": cfg["label"], "score": res.scores.readiness, "scale": "0–100", "stage": stage, "gaps": gaps}
    rows = "".join(f"<li><b>{_e(g['gap'])}</b><br>Риск: {_e(g['risk'])}</li>" for g in gaps)
    part["html"] = (f"<div class=\"s1-free\"><p><b>{_e(cfg['label'])}</b></p>"
                    f"<p>Оценка готовности: {res.scores.readiness} / 100</p>"
                    + (f"<p>Стадия: {_e(stage)}</p>" if stage else "") + f"<ol>{rows}</ol></div>")
    return part


def _e(x) -> str:
    import html as _h
    return _h.escape(str(x))


def build_pack_blocks(res: EvaluationResult, m: dict) -> list[dict]:
    """HQ-020 §2.7 / S1_DIALOG_SCENARIO_v0.1 §5.2: блоки 1–11 окончательного Readiness Pack.
    Блоки 10 (не дослано) и 11 («проверил, дата») заполняет main.py из сессии и журнала подписи.
    Где источник не даёт значения, блок помечен status=STOP с причиной — значения не выдумываются."""
    ls = res.scores.ls or {}
    claims = [{"id": c.id, "statement": c.statement, "class": c.category.value, "level": f"E{c.evidence_level}",
               "source": c.source_ref or ", ".join(c.sources) or "НЕ ПОДТВЕРЖДЕНО"} for c in res.classification.claims]
    k7 = ls.get("next_artifact")
    w = m["working"]
    na = w["pack_block8"]["not_specified"]
    blocks = [
        {"n": 1, "title": "Статус", "value": w["pack_block1"]["map"][res.scores.verdict],
         "basis": w["pack_block1"]["label"], "status": "рабочее"},
        {"n": 2, "title": "Строка итога", "value": ls.get("summary_line"),
         "note": "шкала 0–100 по решению PM; формат «X,X / 5» сценария §5.2 п. 2 считается в пилоте вручную"},
        {"n": 3, "title": "Что доказано", "claims": claims},
        {"n": 4, "title": "К1–К6 и К7", "milestones": ls.get("milestones"), "k7": k7},
        {"n": 5, "title": "Слоты N_A_BY_STAGE", "na_slots": ls.get("na_slots")},
        {"n": 6, "title": "LS-барьеры З1–З7", "zones": ls.get("zones")},
        {"n": 7, "title": "Критичные барьеры (2–5)", "barriers": ls.get("barriers")},
        {"n": 8, "title": "Следующие шаги", "first_step_k7": k7, "status": "рабочее",
         "steps": [{"horizon": hz, "step": st, "owner": na, "procedure_rf": na}
                   for hz, plan in zip(w["pack_block8"]["horizons"],
                                       (res.synthesis.plan_30, res.synthesis.plan_60, res.synthesis.plan_90))
                   for st in plan]},
        {"n": 9, "title": "Оговорки", "value": m["dialogue_questions_s1"]["pack_disclaimer"]},
        {"n": 10, "title": "Не дослано и почему", "items": []},
        {"n": 11, "title": "Проверил, дата", "signed": None},
    ]
    if not ls:  # путь IT: LS-блоки не строятся (HQ016 T3)
        for b in blocks:
            if b["n"] in (4, 5, 6, 7):
                b.update(status="N/A", reason="путь IT: LS-блок не строится")
    return blocks


def generate_pack(res: EvaluationResult, m: dict, fund_name: str) -> dict:
    """Readiness Pack: JSON-структура + два HTML. Хранится в БД; PDF — по запросу из HTML."""
    founder = build_founder_view(res, m, fund_name)
    internal = build_internal_view(res, m, fund_name)
    return {
        "readiness_pack": {
            "founder_readiness_report": json.loads(json.dumps(founder, ensure_ascii=False, default=str)),
            "internal_analyst_brief": json.loads(res.model_dump_json()),
        },
        "free_part": build_free_part(res, m),
        "pack_blocks": build_pack_blocks(res, m),
        "founder_report_html": _env.get_template("founder_report.html").render(**founder),
        "analyst_brief_html": _env.get_template("analyst_brief.html").render(**internal),
    }
