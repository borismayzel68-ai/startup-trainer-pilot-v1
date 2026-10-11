"""Оркестрация БП-01: парсинг → классификация → правила → скоринг → Марк/Анна → синтез."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import Settings, build_checklist, routing_for
from .llm import LLMClient
from .ls_zones import build_zones, derive_stage
from .models import (
    Category,
    BearCase,
    BullCase,
    ClassificationResult,
    EvaluationResult,
    ParsedDoc,
    Submission,
    Synthesis,
)
from .scoring import (
    apply_revenue_claims,
    enforce_rules,
    gap_id,
    ls_stage_context,
    cap_device_pilots,
    priority_gaps,
    score,
    validate_citations,
)

log = logging.getLogger(__name__)


def load_prompt(prompts_dir: Path, name: str) -> tuple[str, str]:
    text = (prompts_dir / name).read_text(encoding="utf-8")
    _, rest = text.split("## SYSTEM", 1)
    system, user = rest.split("## USER", 1)
    return system.strip(), user.strip()


def render(template: str, **kw) -> str:
    for k, v in kw.items():
        template = template.replace("{{" + k + "}}", v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1))
    return template


# ------------------------------------------------------- context builders ----
def _answers_text(sub: Submission) -> str:
    if not sub.answers:
        return "(анкета пустая)"
    return "\n\n".join(f"[{a.field} | блок {a.block}] {a.label}\nОтвет: {a.value}" for a in sub.answers)


def _docs_text(docs: list[ParsedDoc]) -> str:
    parts = []
    for d in docs:
        head = f"--- {d.kind} (источник: {d.source}) · {d.name} ---"
        body = d.text if d.ok and d.text else "(не обработан: " + "; ".join(d.warnings) + ")"
        parts.append(f"{head}\n{body}")
    return "\n\n".join(parts) or "(документы не загружены)"


def _audit_text(cls: ClassificationResult) -> str:
    return "\n".join(
        f"{c.id} | {c.slot or c.block + '.-'} | {c.category.value} | E{c.evidence_level} | {c.statement}"
        + (f" | конфликт с {','.join(c.conflicts_with)}" if c.conflicts_with else "")
        for c in cls.claims
    )


def _gaps_text(scores, idx) -> str:
    rows = [f"{gap_id(k)} — {idx[k]['label']} (КРИТИЧНЫЙ)" for k in scores.critical_gaps]
    for b in scores.blocks:
        for it in b.items:
            if it.status.value == "UNKNOWN" and not it.critical and it.applicability != "N_A_BY_STAGE":
                rows.append(f"{gap_id(it.key)} — {it.label}")
    return "\n".join(rows) or "(пробелов по чек-листу нет)"


def _scale_text(m: dict) -> str:
    return "\n".join(f"{v['code']} — {v['label']}: {v['description']}" for _, v in sorted(m["evidence_scale"].items()))


def _checklist_text(checklist: dict) -> str:
    return "\n".join(
        f"{k} — [{v['block_label']}] {v['label']}. {v['why']}{' [КРИТИЧНЫЙ]' if v['critical'] else ''}"
        for k, v in checklist.items()
    )


def _routing_text(sub: Submission, routing: dict) -> str:
    j = routing.get("jurisdiction") or f"не распознана ('{sub.jurisdiction}')"
    pc = routing.get("product_class") or f"не распознан ('{sub.product_class}')"
    markets = ", ".join(routing.get("active_markets") or []) or "—"
    return (f"Юрисдикция: {j}; рынки: {markets}; класс продукта: {pc}.\n"
            f"Назначение (intended use) со слов фаундера: {sub.intended_use or '—'}")


def model_cap_overrides(docs: list[ParsedDoc], m: dict) -> dict[str, int]:
    """Канон E1: «прогнозы и расчёты без явных формул». Захардкоженная модель → потолок E1."""
    models = [d for d in docs if d.ok and d.source == "model"]
    if models and max(d.formula_cells or 0 for d in models) < int(m.get("model_min_formulas", 10)):
        for d in models:
            msg = "В финмодели почти нет формул (значения захардкожены) — данные модели не выше E1."
            if msg not in d.warnings:
                d.warnings.append(msg)
        return {"model": 1}
    return {}


# --------------------------------------------------------------- pipeline ----
def evaluate(job_id: str, sub: Submission, docs: list[ParsedDoc], llm: LLMClient, settings: Settings, m: dict) -> EvaluationResult:
    pdir = settings.prompts_dir
    routing = routing_for(sub.jurisdiction, sub.target_markets, sub.product_class, sub.intended_use)
    ls_ctx = ls_stage_context(routing, derive_stage(sub, m), m)   # HQ-016 (+T6: стадия из анкеты)
    idx = build_checklist(m, routing["modules"], ls_milestones=ls_ctx["milestones"])

    # 1. Классификация FACT / FOUNDER_CLAIM / UNKNOWN / CONFLICT
    sys_p, usr_p = load_prompt(pdir, "01_classify.md")
    metrics = {d.name: d.metrics for d in docs if d.metrics}
    cls = llm.complete_json(
        "classify", sys_p,
        render(usr_p, thesis=m["thesis"]["description"].strip(), evidence_scale=_scale_text(m),
               checklist=_checklist_text(idx), routing=_routing_text(sub, routing),
               company=f"{sub.project_name} · фаундер: {sub.founder_name}",
               answers=_answers_text(sub), documents=_docs_text(docs), metrics=metrics or "(нет)"),
        ClassificationResult,
    )

    # 2. Детерминированные предохранители и скоринг
    cls = enforce_rules(cls, m, idx, {d.source for d in docs if d.ok}, model_cap_overrides(docs, m))
    cls = cap_device_pilots(ls_ctx, cls, m)
    # HQ-020 / сценарий §3.0, §4.2: устный ответ в диалоге без файла — максимум E1 («не выше 2»)
    lim = int(m["dialogue_questions_s1"]["text_answer_level"])
    for c in cls.claims:
        if "dlg_" in (c.source_ref or "") and c.evidence_level > lim:
            c.machine_adjusted.append(f"E{c.evidence_level}→E{lim}: ответ в диалоге без файла")
            c.evidence_level = lim
            if c.category == Category.FACT:
                c.category = Category.FOUNDER_CLAIM
    ls_ctx = apply_revenue_claims(ls_ctx, cls, m)
    scores = score(cls, m, idx, ls_ctx)
    if scores.ls is not None and (ls_ctx["asset_class"] in m["ls_stage_rule"]["asset_classes"] or not ls_ctx["gate"]):
        z = build_zones(ls_ctx, scores, m)  # T6: зоны З1–З7 после скоринга — на балл не влияют
        if ls_ctx.get("stage_uncertain"):
            q = m["ls_stage_derivation"]["clarify_question"]
            z["dialogue_questions"].insert(0, {"zone": None, "signal": "stage", "question": q, "round": "Р0"})
        scores.ls.update(z)
    if not routing["routing_complete"]:
        scores.verdict_gates.append("Юрисдикция или класс продукта не распознаны — чек-лист только по общим модулям")
    audit, gaps = _audit_text(cls), _gaps_text(scores, idx)
    valid_ids = {c.id for c in cls.claims} | {gap_id(k) for k in idx}
    common = dict(company_summary=cls.company_summary, sector=cls.sector, stage=cls.stage,
                  readiness=str(scores.readiness), audit=audit, gaps=gaps)

    # 3. Марк (Bull)
    sys_p, usr_p = load_prompt(pdir, "02_bull_mark.md")
    bull = llm.complete_json("bull", sys_p, render(usr_p, **common), BullCase)
    bull.arguments = validate_citations(bull.arguments, valid_ids)

    # 4. Анна (Bear) — видит кейс Марка и атакует его
    sys_p, usr_p = load_prompt(pdir, "03_bear_anna.md")
    bear = llm.complete_json("bear", sys_p, render(usr_p, bull=bull.model_dump_json(indent=1), **common), BearCase)
    bear.arguments = validate_citations(bear.arguments, valid_ids)

    # 5. Синтез: барьеры, Proof Points, 30/60/90
    sys_p, usr_p = load_prompt(pdir, "04_synthesis.md")
    sys_p = sys_p.replace("{{verdict}}", scores.verdict)
    syn = llm.complete_json(
        "synthesis", sys_p,
        render(usr_p, company_summary=cls.company_summary, readiness=str(scores.readiness),
               verdict=scores.verdict, gates="; ".join(scores.verdict_gates),
               priority_gaps=priority_gaps(scores), audit=audit,
               bull=bull.model_dump_json(indent=1), bear=bear.model_dump_json(indent=1)),
        Synthesis,
    )
    for pp in syn.proof_points:
        pp.claim_ids = [x for x in pp.claim_ids if x in valid_ids]
        pp.target_level = min(pp.target_level, 4)

    return EvaluationResult(
        job_id=job_id, submission=sub, routing=routing, docs=docs, classification=cls, scores=scores,
        bull=bull, bear=bear, synthesis=syn, model=llm.model_name, methodology_version=str(m["version"]),
    )
