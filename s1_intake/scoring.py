"""Детерминированный слой: предохранители поверх LLM, скоринг готовности и вердикт.

Принцип: LLM извлекает и формулирует, код — решает. Уровень доказательности,
категория, readiness и вердикт пересчитываются здесь по правилам из
config/methodology.yaml, поэтому их нельзя «уговорить» текстом в питч-деке.
"""
from __future__ import annotations

from .models import (
    Argument,
    BlockScore,
    Category,
    Claim,
    ClassificationResult,
    ItemScore,
    Scores,
)


def gap_id(item_key: str) -> str:
    return f"gap:{item_key}"


# ------------------------------------------------------------ guardrails ----
def enforce_rules(cls: ClassificationResult, m: dict, checklist: dict[str, dict],
                  available_sources: set[str] | None = None,
                  cap_overrides: dict[str, int] | None = None) -> ClassificationResult:
    """available_sources — источники, которые реально были загружены и разобраны.
    Модель не может сослаться на «документ третьей стороны», которого не было.
    cap_overrides — понижение потолка по фактам о файлах (напр. финмодель без формул → E1)."""
    caps = {k: int(v) for k, v in m["source_level_cap"].items()}
    for k, v in (cap_overrides or {}).items():
        caps[k] = min(caps.get(k, v), int(v))
    available = (available_sources or set(caps)) | {"form"}
    fact_min = int(m["fact_min_level"])
    block_keys = {b["key"] for b in m["blocks"]}

    # 1) уникальные id; слот только из чек-листа маршрута; блок слота задаёт код
    seen, claims = set(), []
    for i, c in enumerate(cls.claims, 1):
        if c.id in seen or not c.id:
            c.id = f"C{i:02d}x"
        seen.add(c.id)
        if c.slot and c.slot not in checklist:
            c.machine_adjusted.append(f"слот '{c.slot}' вне чек-листа → null")
            c.slot = None
        if c.slot:
            c.block = checklist[c.slot]["block"]
        elif c.block not in block_keys:
            c.block = "risks_legal"
        claims.append(c)
    ids = {c.id for c in claims}

    for c in claims:
        # 2a) ссылки только на реально загруженные источники
        phantom = [x for x in c.sources if x not in available]
        if phantom:
            c.machine_adjusted.append(f"удалены несуществующие источники {phantom}")
            c.sources = [x for x in c.sources if x in available]
        # 2b) потолок уровня по источнику: самоотчёт фаундера не становится фактом
        cap = max((caps[s] for s in c.sources), default=0)
        if c.evidence_level > cap:
            c.machine_adjusted.append(f"E{c.evidence_level}→E{cap}: потолок по источнику {c.sources or ['нет']}")
            c.evidence_level = cap
        # 3) ссылки конфликта только на существующие claims
        valid = [x for x in c.conflicts_with if x in ids and x != c.id]
        if len(valid) != len(c.conflicts_with):
            c.machine_adjusted.append("удалены несуществующие ссылки конфликта")
        c.conflicts_with = valid
        # 4) категория — из уровня и конфликтов, не из мнения модели
        if c.category == Category.UNKNOWN:
            c.evidence_level = 0
            continue
        if c.conflicts_with:
            new = Category.CONFLICT
        elif c.evidence_level >= fact_min:
            new = Category.FACT
        else:
            new = Category.FOUNDER_CLAIM
        if new != c.category:
            c.machine_adjusted.append(f"категория {c.category.value}→{new.value}")
            c.category = new

    # 5) конфликт симметричен
    by_id = {c.id: c for c in claims}
    for c in claims:
        for other in c.conflicts_with:
            o = by_id[other]
            if c.id not in o.conflicts_with:
                o.conflicts_with.append(c.id)
            if o.category != Category.CONFLICT:
                o.machine_adjusted.append(f"категория {o.category.value}→CONFLICT (симметрия с {c.id})")
                o.category = Category.CONFLICT

    cls.claims = claims
    return cls


# --------------------------------------------------------------- scoring ----
def score(cls: ClassificationResult, m: dict, checklist: dict[str, dict]) -> Scores:
    ev = {int(k): float(v) for k, v in m["evidence_value"].items()}
    crit_w = float(m["scoring"]["critical_item_weight"])
    fact_min = int(m["fact_min_level"])

    blocks_out: list[BlockScore] = []
    critical_gaps, critical_conflicts = [], []
    weighted = weight_sum = 0.0

    for b in m["blocks"]:
        keys = [k for k, v in checklist.items() if v["block"] == b["key"]]
        if not keys:
            continue
        items: list[ItemScore] = []
        num = den = 0.0
        for key in keys:
            it = checklist[key]
            related = [c for c in cls.claims if c.slot == key]
            substantive = [c for c in related if c.category != Category.UNKNOWN]
            conflicts = [c for c in substantive if c.category == Category.CONFLICT]
            clean = [c for c in substantive if c.category != Category.CONFLICT]

            if conflicts and not any(c.evidence_level >= fact_min for c in clean):
                status, best, val = Category.CONFLICT, max(c.evidence_level for c in conflicts), 0.0
                if it["critical"]:
                    critical_conflicts.append(key)
            elif clean:
                best = max(c.evidence_level for c in clean)
                status = Category.FACT if best >= fact_min else Category.FOUNDER_CLAIM
                val = ev[best]
            else:
                status, best, val = Category.UNKNOWN, None, 0.0
                if it["critical"]:
                    critical_gaps.append(key)

            w = crit_w if it["critical"] else 1.0
            num += w * val
            den += w
            items.append(ItemScore(key=key, label=it["label"], critical=it["critical"],
                                   best_level=best, status=status, claim_ids=[c.id for c in related]))
        bscore = num / den if den else 0.0
        weighted += b["weight"] * bscore
        weight_sum += b["weight"]
        blocks_out.append(BlockScore(key=b["key"], label=b["label"], weight=b["weight"],
                                     score=round(bscore, 3), items=items))

    r = int(round(100 * weighted / weight_sum)) if weight_sum else 0
    substantive = sum(1 for c in cls.claims if c.category != Category.UNKNOWN and c.slot)
    verdict, gates = decide(r, cls.thesis_fit.fit, critical_gaps, critical_conflicts, m, substantive)
    return Scores(readiness=r, blocks=blocks_out, critical_gaps=critical_gaps,
                  critical_conflicts=critical_conflicts, verdict=verdict, verdict_gates=gates)


def decide(readiness: int, thesis_fit: bool, crit_gaps: list, crit_conflicts: list, m: dict,
           substantive_claims: int = 99):
    """PASS не зависит от readiness: readiness падает с ростом чек-листа (36 слотов у MEDICAL_DEVICE
    против 23 у общего набора), и порог по нему отклонял бы нормальные заявки сложных классов.
    PASS = вне тезиса ИЛИ пустая заявка (меньше pass_min_substantive_claims содержательных утверждений)."""
    sc = m["scoring"]
    gates = []
    if not thesis_fit and m["thesis"].get("out_of_thesis_is_pass", True):
        gates.append("Вне тезиса фонда (по оценке LLM — проверить вручную)")
        return "PASS", gates
    if substantive_claims < sc["pass_min_substantive_claims"]:
        gates.append(f"Пустая заявка: содержательных утверждений {substantive_claims} "
                     f"< {sc['pass_min_substantive_claims']}")
        return "PASS", gates
    ok_next = True
    if readiness < sc["next_min"]:
        gates.append(f"Readiness {readiness} < порога NEXT {sc['next_min']}")
        ok_next = False
    if crit_conflicts:
        gates.append(f"Неразрешённые конфликты по критичным пунктам: {', '.join(crit_conflicts)}")
        ok_next = False
    if len(crit_gaps) > sc["max_critical_gaps_for_next"]:
        gates.append(f"Критичных пробелов {len(crit_gaps)} > допустимых {sc['max_critical_gaps_for_next']}")
        ok_next = False
    if ok_next:
        gates.append(f"Readiness {readiness} ≥ {sc['next_min']}, критичных конфликтов нет, пробелов в норме")
        return "NEXT", gates
    return "HOLD", gates


def priority_gaps(scores: Scores, top: int = 8) -> list[dict]:
    """Пункты с наибольшим «дефицитом доказательности» × вес блока — вход для Proof Points."""
    rows = []
    for b in scores.blocks:
        for it in b.items:
            level = it.best_level if it.status not in (Category.UNKNOWN, Category.CONFLICT) else -1
            deficit = (4 - max(level, 0)) / 4 + (0.5 if it.status in (Category.UNKNOWN, Category.CONFLICT) else 0)
            weight = b.weight * (2 if it.critical else 1)
            rows.append({"item": it.key, "label": it.label, "status": it.status.value,
                         "level": it.best_level, "critical": it.critical, "priority": round(deficit * weight, 3)})
    rows.sort(key=lambda r: r["priority"], reverse=True)
    return rows[:top]


def validate_citations(args: list[Argument], valid_ids: set[str]) -> list[Argument]:
    """Удаляет выдуманные ссылки; аргумент без валидных ссылок помечается unsupported."""
    for a in args:
        a.claim_ids = [x for x in a.claim_ids if x in valid_ids]
        a.unsupported = not a.claim_ids
    return args
