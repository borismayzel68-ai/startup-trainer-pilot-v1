import re
from copy import deepcopy

ALLOWED_STATUS = {"fact", "hypothesis", "missing", "weak"}
QUICK_TRIAGE_MIN_TURNS = 8
QUICK_TRIAGE_MAX_TURNS = 12

def _slot(slot, question, why, required=True, source_refs=None):
    return {
        "slot": slot,
        "question": question,
        "why": why,
        "required": required,
        "source_refs": list(source_refs or ["S1V2", "Router"]),
    }

SLOT_CATALOG = {
    "problem_unmet_need": [
        _slot("target_problem", "Какую конкретную проблему вы решаете, для кого и в каком рабочем контексте?", "Фиксирует проблему, пользователя и границы применения."),
        _slot("pain_evidence", "Какие первичные данные подтверждают частоту и критичность этой проблемы?", "Проверяет, что проблема подтверждена наблюдениями, а не только мнением."),
    ],
    "product_intended_use": [
        _slot("intended_use", "Сформулируйте назначение продукта одним предложением: пользователь, действие и ожидаемый результат.", "Назначение определяет продуктовые и регуляторные границы."),
        _slot("workflow_boundary", "Что продукт делает и чего принципиально не делает в текущей версии?", "Граница функций снижает риск неверной классификации."),
    ],
    "customer_buyer_payer": [
        _slot("roles", "Кто пользователь, покупатель и плательщик, и где эти роли расходятся?", "Разделяет применение продукта и экономическое решение."),
        _slot("purchase_process", "Кто и по каким критериям утверждает покупку или внедрение?", "Проверяет реальный путь к сделке."),
    ],
    "market": [
        _slot("beachhead", "Какой первый узкий сегмент рынка выбран и почему он доступен сейчас?", "Проверяет фокус выхода на рынок."),
        _slot("market_method", "Как рассчитан достижимый рынок снизу вверх и на каких источниках основан расчёт?", "Проверяет воспроизводимость оценки рынка."),
    ],
    "business_model": [
        _slot("pricing", "За что именно платит клиент и как устроена цена?", "Фиксирует единицу ценности и модель выручки."),
        _slot("unit_economics", "Какие подтверждённые данные есть по выручке, валовой марже и стоимости обслуживания клиента?", "Проверяет экономическую жизнеспособность."),
    ],
    "traction": [
        _slot("traction_metrics", "Какие измеримые результаты получены у реальных пользователей или клиентов?", "Отделяет фактический спрос от интереса."),
        _slot("retention", "Какие данные показывают повторное использование, продление или удержание?", "Проверяет устойчивость ценности."),
    ],
    "team_governance": [
        _slot("critical_roles", "Какие критические компетенции уже закрыты командой, а какие роли остаются незакрытыми?", "Проверяет способность выполнить ближайший план."),
        _slot("ownership", "Как распределены доли, права на разработки и полномочия по ключевым решениям?", "Выявляет риски владения и управления."),
    ],
    "financing_milestone": [
        _slot("funding_need", "Какой объём финансирования нужен и на какой срок?", "Связывает запрос капитала с планом."),
        _slot("milestone_plan", "Какой проверяемый milestone будет достигнут на эти средства и чем он будет подтверждён?", "Проверяет преобразование капитала в снижение риска."),
    ],
    "competition_moat": [
        _slot("alternatives", "Какими альтернативами клиент решает задачу сегодня и почему сменит их?", "Определяет реальную конкуренцию."),
        _slot("defensibility", "Какой элемент преимущества трудно воспроизвести и чем это подтверждается?", "Проверяет устойчивость преимущества."),
    ],
    "science_mechanism": [
        _slot("mechanism", "Какой механизм действия лежит в основе продукта и на каких данных он основан?", "Проверяет научную правдоподобность."),
        _slot("translational_evidence", "Какие данные связывают механизм с ожидаемым результатом у целевой популяции?", "Проверяет переносимость научной гипотезы."),
    ],
    "clinical": [
        _slot("clinical_plan", "Какой клинический план проверяет заявленный эффект и безопасность?", "Проверяет путь получения клинического доказательства."),
        _slot("endpoints", "Какие конечные точки, популяция и критерии успеха определены заранее?", "Проверяет интерпретируемость исследования."),
    ],
    "regulatory": [
        _slot("classification_route", "Какой регуляторный статус и маршрут применимы в заявленной юрисдикции и почему?", "Проверяет классификацию и применимый маршрут."),
        _slot("submission_evidence", "Какой пакет доказательств нужен для следующего регуляторного шага?", "Связывает разработку с требованиями регулятора."),
    ],
    "ip_fto": [
        _slot("ip_position", "Какие права интеллектуальной собственности принадлежат компании и чем это оформлено?", "Проверяет контроль над ключевыми активами."),
        _slot("fto", "Проводился ли анализ свободы действий и какие существенные риски выявлены?", "Проверяет риск нарушения чужих прав."),
    ],
    "cmc_manufacturing": [
        _slot("process_control", "Как определены критические параметры производства и контроль качества?", "Проверяет воспроизводимость продукта."),
        _slot("scale_quality", "Что изменится при масштабировании производства и как будет сохранено качество?", "Проверяет масштабируемость процесса."),
    ],
    "pilots_workflow": [
        _slot("pilot_design", "Как устроен пилот: цель, выборка, длительность и критерии успеха?", "Проверяет, может ли пилот дать решение, а не впечатление."),
        _slot("workflow_integration", "Как продукт встраивается в текущий процесс пользователя и кто отвечает за внедрение?", "Проверяет операционную применимость."),
    ],
    "market_access_procurement_reimbursement": [
        _slot("procurement", "Каков путь закупки у целевого покупателя и какие барьеры уже проверены?", "Проверяет доступ к бюджету."),
        _slot("reimbursement", "Кто оплачивает применение и нужен ли отдельный механизм возмещения?", "Проверяет путь к оплате."),
    ],
    "ai_data_cybersecurity": [
        _slot("data_provenance", "Каково происхождение данных, права на их использование и качество разметки?", "Проверяет основу цифрового или ИИ-продукта."),
        _slot("validation_monitoring", "Как проведена внешняя валидация и как контролируются ошибки и дрейф после запуска?", "Проверяет переносимость и контроль качества модели."),
        _slot("privacy_security", "Какие меры защиты, разграничения доступа и интеграции применяются к данным?", "Проверяет приватность, безопасность и внедрение."),
    ],
    "diagnostics_validity": [
        _slot("analytical_validity", "Какие данные подтверждают аналитическую точность, воспроизводимость и пределы метода?", "Проверяет аналитическую валидность."),
        _slot("clinical_validity", "Какие данные подтверждают связь результата теста с целевым клиническим состоянием?", "Проверяет клиническую валидность."),
    ],
}

def question_key(value):
    return re.sub(r"[^a-zа-яё0-9]+", " ", str(value or "").lower()).strip()

def slot_definitions(session):
    rows = []
    for module in session.get("selected_modules", []):
        for spec in SLOT_CATALOG.get(module, []):
            row = deepcopy(spec)
            row["module"] = module
            rows.append(row)
    return rows

def ensure_coverage(session):
    coverage = session.setdefault("coverage", {})
    for spec in slot_definitions(session):
        key = f"{spec['module']}.{spec['slot']}"
        coverage.setdefault(key, {
            "module": spec["module"],
            "slot": spec["slot"],
            "required": bool(spec["required"]),
            "assessed": False,
            "status": None,
            "applicability": "applicable",
            "na_reason": "",
            "evidence_origin": None,
            "quote": "",
            "contradiction": "",
            "follow_up_count": 0,
        })
    return coverage
def seed_source_coverage(session, items, corpus):
    coverage = ensure_coverage(session)
    corpus_lower = str(corpus or "").lower()
    accepted = 0
    for item in items or []:
        module = str(item.get("module") or "")
        slot = str(item.get("slot") or "")
        key = f"{module}.{slot}"
        status = str(item.get("status") or "")
        quote = str(item.get("quote") or "").strip()
        if key not in coverage or status not in ALLOWED_STATUS or not quote:
            continue
        if quote.lower() not in corpus_lower:
            continue
        if status == "fact":
            status = "weak"
        row = coverage[key]
        row.update({
            "assessed": True,
            "status": status,
            "evidence_origin": "source",
            "quote": quote[:500],
        })
        accepted += 1
    session["source_coverage_scanned"] = True
    return accepted

_NO_DATA = re.compile(r"^(нет данных|данных нет|не зна(?:ю|ем)|не измеряли|не проверяли|пока нет)\b", re.I)
_NOT_APPLICABLE = re.compile(r"^(не применимо|не относится|n/?a)\b[\s:,-]*(.*)$", re.I)

def explicit_no_data(answer):
    return bool(_NO_DATA.search(str(answer or "").strip()))

def explicit_not_applicable(answer):
    match = _NOT_APPLICABLE.search(str(answer or "").strip())
    if not match:
        return None
    reason = match.group(1).strip()
    return reason if len(reason) >= 4 else None

def record_answer(session, question, answer, analysis):
    module = str((question or {}).get("module") or "")
    slot = str((question or {}).get("slot") or "")
    if not module or not slot:
        return
    coverage = ensure_coverage(session)
    row = coverage.get(f"{module}.{slot}")
    if not row:
        return
    status = str((analysis or {}).get("status") or "weak")
    if status not in ALLOWED_STATUS:
        status = "weak"
    if status == "fact":
        status = "weak"
    na_reason = explicit_not_applicable(answer)
    row["assessed"] = True
    row["status"] = "missing" if na_reason else status
    row["evidence_origin"] = "founder"
    row["quote"] = str((analysis or {}).get("quote") or answer or "")[:500]
    row["contradiction"] = str((analysis or {}).get("contradiction") or "")[:800]
    if na_reason:
        row["applicability"] = "not_applicable"
        row["na_reason"] = na_reason
    if (question or {}).get("kind") == "follow_up":
        row["follow_up_count"] = int(row.get("follow_up_count") or 0) + 1
def coverage_complete(session):
    coverage = ensure_coverage(session)
    required = [row for row in coverage.values() if row.get("required")]
    return bool(required) and all(
        row.get("assessed") or (
            row.get("applicability") == "not_applicable" and row.get("na_reason")
        )
        for row in required
    )

def pending_follow_up(row):
    if not row.get("assessed") or int(row.get("follow_up_count") or 0) >= 1:
        return False
    if row.get("applicability") == "not_applicable":
        return False
    return row.get("status") == "weak" or bool(str(row.get("contradiction") or "").strip())

def _unasked(question, asked):
    return question_key(question) not in asked

def next_question(session):
    coverage = ensure_coverage(session)
    asked = {question_key(x.get("question")) for x in session.get("qa", [])}
    specs = slot_definitions(session)

    for spec in specs:
        row = coverage[f"{spec['module']}.{spec['slot']}"]
        if row.get("required") and not row.get("assessed") and _unasked(spec["question"], asked):
            return {
                "module": spec["module"],
                "slot": spec["slot"],
                "question": spec["question"],
                "why": spec["why"],
                "required": True,
                "kind": "required",
                "source_refs": list(spec.get("source_refs") or []),
                "jurisdiction_guard": {
                    "active_markets": list(session.get("active_markets") or []),
                    "vocabulary": list(session.get("jurisdiction_vocab") or []),
                    "foreign_route_requires_source_ref": True,
                },
            }

    by_key = {f"{x['module']}.{x['slot']}": x for x in specs}
    for key, row in coverage.items():
        if not pending_follow_up(row):
            continue
        spec = by_key.get(key)
        if not spec:
            continue
        question = "Какое первичное доказательство или конкретный документ может закрыть этот пробел: " + spec["why"].lower()
        if _unasked(question, asked):
            return {
                "module": spec["module"],
                "slot": spec["slot"],
                "question": question,
                "why": "Уточнение слабого или противоречивого ответа.",
                "required": bool(spec["required"]),
                "kind": "follow_up",
                "source_refs": list(spec.get("source_refs") or []),
                "jurisdiction_guard": {
                    "active_markets": list(session.get("active_markets") or []),
                    "vocabulary": list(session.get("jurisdiction_vocab") or []),
                    "foreign_route_requires_source_ref": True,
                },
            }

    for spec in specs:
        row = coverage[f"{spec['module']}.{spec['slot']}"]
        if not row.get("assessed") and _unasked(spec["question"], asked):
            return {
                "module": spec["module"],
                "slot": spec["slot"],
                "question": spec["question"],
                "why": spec["why"],
                "required": False,
                "kind": "optional",
                "source_refs": list(spec.get("source_refs") or []),
                "jurisdiction_guard": {
                    "active_markets": list(session.get("active_markets") or []),
                    "vocabulary": list(session.get("jurisdiction_vocab") or []),
                    "foreign_route_requires_source_ref": True,
                },
            }
    return None
def coverage_summary(session):
    rows = list(ensure_coverage(session).values())
    return {
        "total": len(rows),
        "assessed": sum(bool(x.get("assessed")) for x in rows),
        "required": sum(bool(x.get("required")) for x in rows),
        "required_assessed": sum(bool(x.get("required") and x.get("assessed")) for x in rows),
        "weak_followups_pending": sum(pending_follow_up(x) for x in rows),
    }

def decide_progress(session, founder_stop=False):
    if founder_stop:
        return {"done": True, "stop_reason": "founder_stop"}

    phase = session.setdefault("phase", "quick_triage")
    turn = int(session.get("turn") or 0)
    candidate = next_question(session)
    complete = coverage_complete(session)
    followups = any(pending_follow_up(x) for x in ensure_coverage(session).values())

    if phase == "quick_triage":
        if turn >= QUICK_TRIAGE_MIN_TURNS and complete and not followups:
            return {"done": True, "stop_reason": "required_coverage_complete"}
        if turn >= QUICK_TRIAGE_MAX_TURNS:
            if complete and not followups:
                return {"done": True, "stop_reason": "required_coverage_complete"}
            session["phase"] = "full_readiness"
            phase = "full_readiness"
        elif candidate is None:
            return {"done": True, "stop_reason": "marginal_value_low"}
        else:
            return {"done": False, "stop_reason": None}

    if complete and not followups:
        return {"done": True, "stop_reason": "required_coverage_complete"}
    if candidate is None:
        return {"done": True, "stop_reason": "marginal_value_low"}
    return {"done": False, "stop_reason": None}
