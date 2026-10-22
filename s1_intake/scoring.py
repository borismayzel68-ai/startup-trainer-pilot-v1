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
    # P0 #36: пустой набор — «файлов нет», а не «источники не заданы»; различаем только None
    available = (set(caps) if available_sources is None else set(available_sources)) | {"form"}
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
        c.verified_by = None  # модель не может «подписать» факт
        if c.category == Category.UNKNOWN:
            c.evidence_level = 0
            continue
        if c.conflicts_with:
            new = Category.CONFLICT
        elif c.evidence_level >= fact_min:
            # HQ-035 / канон Р-5: код ставит максимум FACT_CANDIDATE; FACT — только подпись человека
            new = Category.FACT_CANDIDATE
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


# ------------------------------------------------------ HQ-016: LS-стадия ----
def ls_stage_context(routing: dict, ls_stage, m: dict) -> dict:
    """Условия 1–3 HQ-016 §3 (условие 4 — заявленная выручка — проверяется после классификации).
    mark: размечать применимость слотов (SCORED / N_A_BY_STAGE / MISSING);
    eligible: IT-слоты можно ставить N_A_BY_STAGE; milestones: добавить К1–К6."""
    rule = m.get("ls_stage_rule") or {}
    derived = ls_stage if isinstance(ls_stage, dict) else {"stage": ls_stage or None, "uncertain": False}
    stage = (derived.get("stage") or "").strip().lower()
    uncertain = bool(derived.get("uncertain"))
    gate = bool(routing.get("routing_complete"))
    asset = routing.get("product_class")
    is_ls = gate and asset in rule.get("asset_classes", [])
    pre = rule.get("pre_registration_stages", {})
    unc_label = (m.get("ls_stage_derivation") or {}).get("uncertain_label", "уточнить в диалоге")
    ctx = {"gate": gate, "asset_class": asset, "stage": stage or None,
           "stage_label": pre.get(stage, stage or None) if not uncertain else unc_label,
           "stage_uncertain": uncertain, "stage_source": derived.get("source"), "phase": derived.get("phase"),
           # не-LS с гейтом не размечается вовсе (T3: вывод побайтно прежний)
           "mark": bool(rule) and (is_ls or (bool(stage) and not gate)), "eligible": False, "milestones": False, "reason": ""}
    if not rule:
        return ctx
    if not gate:
        ctx["reason"] = "Входной гейт (юрисдикция × тип актива) не заполнен — N/A по стадии запрещено"
    elif not is_ls:
        ctx["reason"] = "Тип актива не требует регистрации — правило не действует"
    elif stage in pre:
        ctx.update(eligible=True, milestones=True, reason="LS до РУ / пилота / продаж")
    elif uncertain:  # T6(3): поле стадии отсутствует, ответы неоднозначны — правило работает, стадия «уточнить»
        ctx.update(eligible=True, milestones=True,
                   reason="Стадия из анкеты не определена однозначно — уточнить в диалоге; считается как до РУ")
    elif stage in rule.get("post_registration_stages", []):
        ctx["reason"] = "Есть РУ, пилот или продажи — IT-слоты оцениваются по обычным правилам"
    else:
        ctx["reason"] = "Стадия не указана — N/A по стадии не ставится"
    return ctx


def cap_device_pilots(ctx: dict, cls: ClassificationResult, m: dict) -> ClassificationResult:
    """T6(3): у МИ пилот до регистрации — веха со статусом «заявление» (не выше E1), не признак продаж."""
    d = m.get("ls_stage_derivation") or {}
    if not (ctx.get("eligible") and ctx.get("asset_class") in ("MEDICAL_DEVICE", "SOFTWARE_POTENTIAL_MD", "IVD")):
        return cls
    cap = int(d.get("device_pilot_max_level", 1))
    for c in cls.claims:
        if c.slot in d.get("device_pilot_slots", []) and c.evidence_level > cap:
            c.machine_adjusted.append(f"E{c.evidence_level}→E{cap}: пилот МИ до регистрации — «заявление»")
            c.evidence_level = cap
            if c.category in (Category.FACT, Category.FACT_CANDIDATE):
                c.category = Category.FOUNDER_CLAIM
    return cls


def apply_fact_signoffs(cls: ClassificationResult, signoffs: dict[str, dict], fact_min: int) -> ClassificationResult:
    """HQ-035: подпись человека — пакетом на слот. FACT_CANDIDATE слота → FACT с verified_by = роль подписанта."""
    for c in cls.claims:
        so = signoffs.get(c.slot or "")
        if so and c.category == Category.FACT_CANDIDATE and c.evidence_level >= fact_min:
            c.category = Category.FACT
            c.verified_by = so["role"]
            c.machine_adjusted.append(f"FACT_CANDIDATE→FACT: подпись роли {so['role']} ({so['at']})")
    return cls


def apply_revenue_claims(ctx: dict, cls: ClassificationResult, m: dict) -> dict:
    """Условие 4: основатель заявил выручку / продажи / платящих клиентов → N/A не ставится (T4)."""
    it = set((m.get("ls_stage_rule") or {}).get("it_slots", {}))
    if ctx.get("eligible") and any(c.slot in it and c.category != Category.UNKNOWN for c in cls.claims):
        ctx = {**ctx, "eligible": False,
               "reason": "Основатель заявил выручку / продажи — IT-слоты оцениваются (SCORED), не N/A"}
    return ctx


def _ls_summary(ctx: dict, m: dict, blocks_out: list[BlockScore], na: dict[str, str],
                excluded: list[str], applicable_weight: float, checklist: dict[str, dict], readiness: int) -> dict:
    rule = m["ls_stage_rule"]
    items = [it for b in blocks_out for it in b.items]
    scored = [it for it in items if it.applicability == "SCORED"]
    applicable = [it for it in items if it.applicability in ("SCORED", "MISSING")]
    na_rows = [{"key": it.key, "label": it.label, "na_reason": it.na_reason,
                "future_milestone": rule["future_milestone"], "risk": rule["it_slots"][it.key]["risk"]}
               for it in items if it.applicability == "N_A_BY_STAGE"]
    ms = sorted((it for it in items if checklist.get(it.key, {}).get("ls_k")), key=lambda it: checklist[it.key]["ls_k"])
    order = rule.get("barrier_order", [])
    milestones = [{"k": checklist[it.key]["ls_k"], "key": it.key, "label": it.label, "level": it.best_level,
                   "status": it.status.value, "applicability": it.applicability} for it in ms]
    barriers, next_artifact = [], None
    if ms:
        # порядок v0.2: регуляторный трек, клинические данные, IP, производство, возмещение.
        # LSM v1 (16.10): сначала слабые (нет данных / E0–E2), потом подтверждённые документом (E3+), потом FACT;
        # внутри группы — порядок v0.2. Иначе E3-вехи вытесняли E0 «кто платит» за лимит barriers_max.
        def _evidenced(it):
            return (it.best_level or 0) >= 3
        ranked = sorted(ms, key=lambda it: (it.status == Category.FACT, _evidenced(it),
                                             order.index(checklist[it.key]["ls_k"])))
        # 22.10 (замечание рецензента, ДА Бориса): документ не снимает риск сам. Пункты с документом E3+
        # (не FACT) остаются в барьерах после слабых, с пометкой «достаточность проверяет человек».
        open_items = [it for it in ranked if it.status != Category.FACT]
        n = min(max(len(open_items), int(rule["barriers_min"])), int(rule["barriers_max"]), len(ranked))
        doc_note = rule.get("barrier_doc_note", "Документ есть; достаточность доказательства проверяет человек")
        barriers = [{"k": checklist[it.key]["ls_k"], "key": it.key, "label": it.label,
                     "why": checklist[it.key]["why"], "next_step": checklist[it.key]["next_step"],
                     "status": it.status.value, "level": it.best_level,
                     "note": doc_note if (_evidenced(it) and it.status != Category.FACT) else None}
                    for it in ranked[:n]]
        # Отбор — по слабости доказательства; показ — в каноническом порядке v0.2. К7 — от самого слабого пункта.
        weakest = barriers[0]
        barriers.sort(key=lambda b: order.index(b["k"]))
        next_artifact = {"k7": "К7", "from": weakest["k"], "artifact": weakest["next_step"],
                         "mark": "рабочее, до пилота"}
    na_list = ", ".join(r["label"] for r in na_rows) or "—"
    return {
        "applied": bool(na), "reason": ctx["reason"], "gate": ctx["gate"], "asset_class": ctx["asset_class"],
        "stage": ctx["stage"], "stage_label": ctx["stage_label"],
        "stage_uncertain": ctx.get("stage_uncertain", False), "stage_source": ctx.get("stage_source"),
        "applicable_weight": round(applicable_weight, 4), "excluded_blocks": excluded,
        "slots_scored": len(scored), "slots_applicable": len(applicable),
        "na_slots": na_rows, "milestones": milestones, "barriers": barriers, "next_artifact": next_artifact,
        "summary_line": f"Readiness {readiness}/100 · {len(scored)} из {len(applicable)} применимых слотов"
                        f" · не применимо по стадии: {na_list}",
    }


# --------------------------------------------------------------- scoring ----
def score(cls: ClassificationResult, m: dict, checklist: dict[str, dict], ls_ctx: dict | None = None) -> Scores:
    ev = {int(k): float(v) for k, v in m["evidence_value"].items()}
    crit_w = float(m["scoring"]["critical_item_weight"])
    fact_min = int(m["fact_min_level"])

    blocks_out: list[BlockScore] = []
    critical_gaps, critical_conflicts = [], []
    weighted = weight_sum = 0.0
    mark = bool(ls_ctx and ls_ctx.get("mark"))
    na: dict[str, str] = {}
    if ls_ctx and ls_ctx.get("eligible"):
        rule = m["ls_stage_rule"]
        na = {k: rule["na_reason"].format(stage=ls_ctx["stage_label"])
              for k in rule["it_slots"] if k in checklist}
    excluded: list[str] = []

    for b in m["blocks"]:
        keys = [k for k, v in checklist.items() if v["block"] == b["key"]]
        if not keys:
            continue
        items: list[ItemScore] = []
        num = den = 0.0
        for key in keys:
            it = checklist[key]
            related = [c for c in cls.claims if c.slot == key]
            if key in na:  # HQ-016: не в знаменателе, не пробел, не 0
                items.append(ItemScore(key=key, label=it["label"], critical=it["critical"], best_level=None,
                                       status=Category.UNKNOWN, claim_ids=[c.id for c in related],
                                       applicability="N_A_BY_STAGE", na_reason=na[key]))
                continue
            substantive = [c for c in related if c.category != Category.UNKNOWN]
            conflicts = [c for c in substantive if c.category == Category.CONFLICT]
            clean = [c for c in substantive if c.category != Category.CONFLICT]

            if conflicts and not any(c.evidence_level >= fact_min for c in clean):
                status, best, val = Category.CONFLICT, max(c.evidence_level for c in conflicts), 0.0
                if it["critical"]:
                    critical_conflicts.append(key)
            elif clean:
                best = max(c.evidence_level for c in clean)
                if best < fact_min:
                    status = Category.FOUNDER_CLAIM
                elif any(c.category == Category.FACT and c.verified_by for c in clean):
                    status = Category.FACT
                else:
                    status = Category.FACT_CANDIDATE
                val = ev[best]
            else:
                status, best, val = Category.UNKNOWN, None, 0.0
                if it["critical"]:
                    critical_gaps.append(key)

            w = crit_w if it["critical"] else 1.0
            num += w * val
            den += w
            items.append(ItemScore(key=key, label=it["label"], critical=it["critical"],
                                   best_level=best, status=status, claim_ids=[c.id for c in related],
                                   applicability=("MISSING" if status == Category.UNKNOWN else "SCORED") if mark else None))
        bscore = num / den if den else 0.0
        if den or not na:
            weighted += b["weight"] * bscore
            weight_sum += b["weight"]
        else:  # все слоты блока N_A_BY_STAGE → блок выпадает из знаменателя (вес не перераспределяется)
            excluded.append(b["key"])
        blocks_out.append(BlockScore(key=b["key"], label=b["label"], weight=b["weight"],
                                     score=round(bscore, 3), items=items))

    r = int(round(100 * weighted / weight_sum)) if weight_sum else 0
    substantive = sum(1 for c in cls.claims if c.category != Category.UNKNOWN and c.slot)
    verdict, gates = decide(r, cls.thesis_fit.fit, critical_gaps, critical_conflicts, m, substantive)
    ls = _ls_summary(ls_ctx, m, blocks_out, na, excluded, weight_sum, checklist, r) if mark else None
    return Scores(readiness=r, blocks=blocks_out, critical_gaps=critical_gaps,
                  critical_conflicts=critical_conflicts, verdict=verdict, verdict_gates=gates, ls=ls)


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
            if it.applicability == "N_A_BY_STAGE":  # HQ-016: не пробел
                continue
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
