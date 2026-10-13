"""HQ-016 T6: стадия LS из ответов анкеты и зоны LS-барьеров З1–З7 (LS_CHECKLIST_S1_v0.2 §7).

Зоны НЕ влияют на балл: считаются после score() и только выводятся в отчёт.
Нет сигнала в анкете (орфанный/детский, биоматериалы, ЛП в стационаре, фаза КИ, стадия) →
статус «не оценено, нужен ответ» и вопрос для диалога раздела 1 (HQ-020).
"""
from __future__ import annotations

import re

from .models import Category, Scores, Submission

DRUG = {"THERAPEUTIC"}
SOFTWARE_DEVICE = {"SOFTWARE_POTENTIAL_MD"}
DEVICE = {"MEDICAL_DEVICE", "SOFTWARE_POTENTIAL_MD", "IVD"}

STATUS = {"веха": "будущая веха", "сейчас": "сейчас", "сейчас_критично": "сейчас",
          "не_применимо": "не применимо", "не_разобрано": "не оценено (в v0.2 не разобрано)"}
NEED_ANSWER = "не оценено, нужен ответ"
K7_MARK = "рабочее, до пилота"


_CLAUSE_SPLIT = re.compile(r"[.;!?\n,]|(?<!\w)но(?!\w)|(?<!\w)а(?!\w)", re.IGNORECASE)


def _clauses(text: str, d: dict) -> list[str]:
    """HQ-059/12.3, 12.10: клаузы с отрицанием / будущим / «до РУ» стадию не задают."""
    skip = [re.compile(p, re.IGNORECASE) for p in d.get("clause_skip", [])]
    return [c for c in _CLAUSE_SPLIT.split(text) if c and c.strip() and not any(p.search(c) for p in skip)]


def stage_hits(text: str, d: dict) -> tuple[list[str], str | None]:
    """→ (классы стадии, фаза КИ). Слова ищутся по границе слова, по клаузам, без отрицаний и будущего."""
    clauses = _clauses(text, d)
    non_ru = [re.compile(p, re.IGNORECASE) for p in d.get("non_ru_registration", [])]
    non_ru_classes = set(d.get("non_ru_classes", []))
    cs = {k: [re.compile(p) for p in v] for k, v in (d.get("keywords_case_sensitive") or {}).items()}
    hits = []
    for stage, words in d.get("keywords", {}).items():
        pats = [re.compile(w, re.IGNORECASE) for w in words] + cs.get(stage, [])
        for c in clauses:
            if stage in non_ru_classes and any(p.search(c) for p in non_ru):
                continue
            if any(p.search(c) for p in pats):
                hits.append(stage)
                break
    for stage, words in cs.items():  # классы, у которых есть только регистрозависимые слова
        if stage not in d.get("keywords", {}) and any(p.search(c) for c in clauses for p in words):
            hits.append(stage)
    phase = None
    for p, words in d.get("phase_keywords", {}).items():
        if any(re.search(w, c, re.IGNORECASE) for w in words for c in clauses):
            phase = p
            break
    return hits, phase


def derive_stage(sub: Submission, m: dict) -> dict:
    """Явное поле ls_stage (если когда-нибудь появится в Tilda) важнее; иначе — ключевые слова из ответов."""
    d = m.get("ls_stage_derivation") or {}
    if sub.ls_stage:
        return {"stage": sub.ls_stage, "phase": None, "uncertain": False, "source": "поле формы ls_stage"}
    by_field = {a.field: a.value for a in sub.answers}
    text = " ".join([by_field.get(f, "") for f in d.get("fields", [])] + [sub.intended_use])
    hits, phase = stage_hits(text, d)
    if len(hits) == 1:
        return {"stage": hits[0], "phase": phase, "uncertain": False, "source": "ответы анкеты"}
    return {"stage": None, "phase": phase, "uncertain": True, "source": "ответы анкеты",
            "candidates": hits}


def v02_stage(asset: str | None, stage: str | None, phase: str | None) -> str | None:
    if not stage:
        return None
    if asset in DRUG:
        if stage in ("idea", "poc", "preclinical"):
            return "Д"
        if stage == "clinical":
            return phase or "Ф?"
        return "Рег"
    if stage in ("idea", "poc"):
        return "П"
    if stage == "preclinical":
        return "ТИ"
    if stage == "clinical":
        return "КИ-МИ"
    return "Рег-МИ"


def _rule_for(zone: dict, asset: str) -> dict:
    r = zone["rules"]
    if asset in SOFTWARE_DEVICE and "software_device" in r:
        return r["software_device"]
    return r["drug"] if asset in DRUG else r["device"]


def _evaluate(rule: dict, stage: str | None, signals: dict) -> tuple[str, str | None, str]:
    """→ (код статуса, сигнал-вопрос или None, причина)."""
    if "signal" in rule:
        return "need", rule["signal"], "в анкете нет ответа"
    if "all" in rule:
        return rule["all"], None, rule.get("reason", "")
    if stage is None:
        return "need", "stage", "стадия не определена — уточнить в диалоге"
    if stage == "Ф?":
        vals = {rule[p] for p in ("Ф1", "Ф2", "Ф3") if p in rule}
        if len(vals) == 1:
            return vals.pop(), None, "фаза КИ не указана; статус одинаков для Ф1–Ф3"
        if {v.split("_")[0] for v in vals} == {"сейчас"}:
            return "сейчас", None, "фаза КИ не указана"
        return "need", rule.get("unknown_phase", "drug_phase"), "фаза КИ не указана"
    return rule.get(stage, "веха"), None, f"стадия {stage}"


def build_zones(ctx: dict, scores: Scores, m: dict) -> dict:
    zones_cfg, signals = m.get("ls_zones") or [], m.get("ls_zone_signals") or {}
    if not ctx.get("gate"):
        return {"zones": [], "gate_request": "Заполните входной гейт: юрисдикция × тип актива — без него "
                                             "зоны LS-барьеров не строятся и N/A по стадии не ставится",
                "dialogue_questions": []}
    asset = ctx.get("asset_class")
    stage = v02_stage(asset, ctx.get("stage"), ctx.get("phase"))
    k_status = {}
    for b in scores.blocks:
        for it in b.items:
            k_status[it.key.split(".")[-1]] = it
    k_slot = {"К1": "k1_clinical_stage", "К2": "k2_regulatory_path", "К3": "k3_ip",
              "К4": "k4_science_poc", "К5": "k5_manufacturing", "К6": "k6_reimbursement"}
    zones, questions = [], []
    for z in zones_cfg:
        code, signal, why = _evaluate(_rule_for(z, asset), stage, signals)
        if z["id"] == "З5" and code == "не_применимо":  # v0.2 §8 п. 3: IP никогда не N/A
            code = "сейчас_критично"
        status = NEED_ANSWER if code == "need" else STATUS[code]
        critical = code == "сейчас_критично" or (z["id"] == "З6" and code.startswith("сейчас"))
        item = k_status.get(k_slot.get(z["k"] or "", ""))
        evidence = (f"документ основателя ({', '.join(item.claim_ids)})"
                    if item is not None and item.status == Category.FACT else "НЕ ПОДТВЕРЖДЕНО")
        row = {"id": z["id"], "title": z["title"], "status": status, "critical": critical,
               "applies": code != "не_применимо", "stage": stage or "уточнить в диалоге", "reason": why,
               "k": z["k"], "basis": list(z["basis"]), "evidence": evidence,
               "next_step": z["next_step"] if critical else None}
        if code == "need":
            q = signals.get(signal) if signal != "stage" else (m.get("ls_stage_derivation") or {}).get("clarify_question")
            row["question"] = q
            if q and q not in [x["question"] for x in questions]:
                questions.append({"zone": z["id"], "signal": signal, "question": q, "round": "Р0/Р1"})
        zones.append(row)
    return {"zones": zones, "gate_request": None, "dialogue_questions": questions}
