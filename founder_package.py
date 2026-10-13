"""Versioned founder output and idempotent ledger projection. No E3 inference."""
import json
import uuid
from datetime import datetime, timedelta, timezone

PACKAGE_VERSION = "founder-readiness-v1"
LEDGER_TABS = ("Sessions", "Evidence Delta", "Founder Delivery", "Event Log", "Human Gates")


def _texts(value, limit=16):
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(x).strip()[:3000] for x in value if isinstance(x, str) and x.strip()))[:limit]


GAP_DEFAULTS = [
    ("Спрос и оплаты", "Заявленные клиенты и выручка не проверены.", "Обезличенные договоры, счета и банковские поступления", "Сверить каждого заявленного платящего клиента с договором и поступлением", "Каждая заявленная оплата сопоставлена с первичным документом"),
    ("Повторное использование", "Удержание и продления не доказаны.", "Когортная выгрузка использования и продлений", "Отделить активных пользователей от зарегистрированных и сверить продления", "Определены период, знаменатель и результаты по каждой когорте"),
    ("Проблема и покупатель", "Критичность проблемы и путь покупки требуют проверки.", "Протоколы интервью с пользователем, покупателем и плательщиком", "Проверить реальный рабочий процесс и критерии покупки", "Зафиксированы роли, альтернативы и основания оплаты"),
    ("Экономика", "Стоимость привлечения, внедрения и поддержки не подтверждена.", "Реестр затрат, тарифов и трудозатрат", "Рассчитать экономику одной оплаченной подписки с учётом ручной работы", "Расчёт воспроизводим по первичным затратам и оплатам"),
    ("Назначение и данные", "Границы назначения, права на данные и меры защиты требуют проверки.", "Описание назначения, перечень данных, договорные основания и схема доступа", "Проверить функции и доступы; получить профильную оценку применимости требований", "Проверены назначение, права и применимые требования без автоматического правового вывода"),
    ("Команда и права", "Права на разработки и ответственность за выполнение плана не проверены.", "Документы о правах на код и разработки, структура владения и распределение ролей", "Сопоставить ключевые разработки с правообладателями и ответственными", "Для каждого ключевого актива указаны документы и владелец"),
    ("Следующий рубеж", "Связь ресурсов со следующим проверяемым результатом требует проверки.", "Бюджет, план работ и заранее определённые критерии результата", "Проверить бюджет и критерии следующего пилота или исследования", "У каждого результата есть критерий, срок, владелец и способ проверки"),
]


def build_package(session, report, origin, completed_at):
    sid = session["id"]
    claims = _texts(report.get("claims"))
    # This input path has no independently verified primary documents.
    # Preserve any model misclassification, never grant E3.
    claims = list(dict.fromkeys(claims + _texts(report.get("facts"))))
    evidence = []

    def add(slot, domain, statement, status, source_ref, source_type):
        statement = str(statement or "").strip()[:3000]
        if not statement:
            return
        status = status if status in {"hypothesis", "missing", "weak"} else "claim"
        evidence.append({
            "evidence_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{sid}:{slot}")),
            "slot_id": slot, "domain": domain, "statement": statement,
            "status": status, "evidence_level": "E0" if status == "missing" else "E1",
            "source_type": source_type, "source_ref": source_ref,
            "source_date": completed_at, "verification_status": "DATA_GAP" if status == "missing" else "NOT_VERIFIED",
            "visibility": "FOUNDER_SHAREABLE", "decision_impact": "Требует первичного доказательства до изменения вывода",
            "accepted_evidence": "Первичный документ или заранее описанный проверяемый тест",
            "owner": session["founder_name"],
        })

    add("intake.description", "intake", session.get("description"), "claim", f"session:{sid}:intake.description", "FOUNDER_INPUT")
    for i, qa in enumerate(session.get("qa") or []):
        analysis = qa.get("analysis") or {}
        add(f"answer.{i+1}", qa.get("module") or "profile", qa.get("answer"), analysis.get("status"), f"session:{sid}:qa:{i+1}", "FOUNDER_ANSWER")
    for i, statement in enumerate(claims):
        add(f"claim.{i+1}", "report_claim", statement, "claim", f"session:{sid}:report:claims:{i+1}", "FOUNDER_REPORT_SUMMARY")
    for i, statement in enumerate(_texts(report.get("missing"))):
        add(f"missing.{i+1}", "evidence_gap", statement, "missing", f"session:{sid}:report:missing:{i+1}", "TRAINER_GAP")

    gaps = []
    raw_gaps = report.get("critical_gaps")
    if isinstance(raw_gaps, list):
        for row in raw_gaps[:7]:
            if isinstance(row, dict) and all(isinstance(row.get(k), str) and row[k].strip() for k in ("gap", "next_evidence", "next_test", "success_criterion")):
                gaps.append({k: row[k][:2000] for k in ("gap", "next_evidence", "next_test", "success_criterion")})
    if not 5 <= len(gaps) <= 7:
        gaps = [{"gap": f"{title}: {gap}", "next_evidence": doc, "next_test": test, "success_criterion": criterion} for title, gap, doc, test, criterion in GAP_DEFAULTS]
    start = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    for i, gap in enumerate(gaps):
        gap.update({"gap_id": f"G{i+1}", "owner": session["founder_name"], "due_day": 30 if i < 3 else 60 if i < 5 else 90})
    plan = []
    for day in (30, 60, 90):
        selected = [g for g in gaps if g["due_day"] == day]
        plan.append({"day": day, "deadline": (start + timedelta(days=day)).date().isoformat(), "owner": session["founder_name"], "actions": [{"gap_id": g["gap_id"], "action": g["next_test"], "accepted_evidence": g["next_evidence"], "success_criterion": g["success_criterion"]} for g in selected]})
    coverage = report.get("coverage") or {}
    required = int(coverage.get("required") or 0)
    assessed = int(coverage.get("required_assessed") or 0)
    snapshot = {k: session.get(k, "") for k in ("project_id", "project_name", "jurisdiction", "product_class", "intended_use", "stage", "user", "buyer", "payer", "mode")}
    result = {
        "package_version": PACKAGE_VERSION, "report_version": f"{sid}:v1", "session_id": sid,
        "generated_at": completed_at, "project_snapshot": snapshot,
        "thesis": str(report.get("thesis") or ""), "supported_facts": [], "labelled_claims": [{"statement": x, "evidence_level": "E1", "verification_status": "NOT_VERIFIED"} for x in claims],
        "hypotheses": _texts(report.get("hypotheses")), "weak_evidence": _texts(report.get("weak")), "missing_documents": _texts(report.get("missing")),
        "critical_gaps": gaps, "plan_30_60_90": plan,
        "readiness_score": {"value": 0, "maximum": 100, "method": "Доля обязательных слотов с независимо проверенным первичным доказательством E3", "verified_required_slots": 0, "required_slots": required, "assessment_coverage_percent": round(100 * assessed / required) if required else 0, "meaning": "Оценка доказательности пакета, а не качества стартапа или инвестиционное решение"},
        "recommendation": report.get("recommendation") if report.get("recommendation") in {"iterate", "not_ready"} else "iterate",
        "evidence_delta": evidence,
        "internal_delta": {"changed_evidence_ids": [e["evidence_id"] for e in evidence], "workbook_write": "WAITING_VERIFICATION_AND_APPROVAL", "next_meeting_questions": [g["next_test"] for g in gaps]},
        "founder_delivery": {"delivery_id": f"FD-{sid}-v1", "status": "WAITING_APPROVAL", "approved_by": "", "approved_at": "", "sent_status": "NOT_SENT"},
        "human_gate": {"required_approver": "Борис", "status": "WAITING_APPROVAL", "approved_by": "", "date": ""},
        "report_url": f"{origin}/api/session/{sid}/report.md",
        "raw_json_url": f"{origin}/api/session/{sid}/report.json",
        "access_policy": "OWNER_SESSION_OR_INTERNAL_RECEIVER_AUTH", "disclaimer": report.get("disclaimer", "Это подготовка к разговору с инвестором, не решение фонда и не обещание инвестиций."),
        "stop_reason": session.get("stop_reason"), "question_count": len(session.get("qa") or []),
    }
    validate_package(result)
    return result


def validate_package(p):
    if p.get("package_version") != PACKAGE_VERSION or p.get("supported_facts") != []:
        raise ValueError("Package evidence policy violated")
    if not 5 <= len(p.get("critical_gaps", [])) <= 7 or {x.get("day") for x in p.get("plan_30_60_90", [])} != {30, 60, 90}:
        raise ValueError("Incomplete gaps or action plan")
    if not p.get("project_snapshot") or not p.get("readiness_score") or not p.get("evidence_delta"):
        raise ValueError("Incomplete package")
    if any(x.get("evidence_level") == "E3" or x.get("status") == "fact" for x in p["evidence_delta"]):
        raise ValueError("Automatic E1 to E3 upgrade blocked")
    if p["founder_delivery"]["status"] != "WAITING_APPROVAL":
        raise ValueError("Human gate bypass blocked")
    return True


def render_markdown(p):
    lines = ["# Отчёт готовности основателя", f"Версия: {p['report_version']}", f"Дата: {p['generated_at']}", "", "## Карточка проекта"]
    for k, label in [("project_id", "Проект"), ("project_name", "Название"), ("jurisdiction", "Юрисдикция"), ("product_class", "Категория"), ("intended_use", "Заявленное назначение"), ("stage", "Заявленная стадия"), ("user", "Пользователь"), ("buyer", "Покупатель"), ("payer", "Плательщик")]:
        lines.append(f"- {label}: {p['project_snapshot'].get(k) or 'не указано'}")
    lines += ["", "## Тезис", p["thesis"], "", "## Подтверждено первичными документами — E3", "Первичные документы не проверены; подтверждённых фактов нет.", "", "## Заявления основателя — E1, не проверено"]
    lines += ["- " + x["statement"] for x in p["labelled_claims"]]
    for key, label in [("hypotheses", "Гипотезы"), ("weak_evidence", "Слабые доказательства"), ("missing_documents", "Недостающие документы")]:
        lines += ["", "## " + label] + ["- " + x for x in p[key]]
    lines += ["", "## Критические пробелы и следующие проверки"]
    for gap in p["critical_gaps"]:
        lines += [f"### {gap['gap_id']}. {gap['gap']}", f"Документ: {gap['next_evidence']}", f"Следующий тест: {gap['next_test']}", f"Критерий: {gap['success_criterion']}", f"Ответственный: {gap['owner']}; срок: день {gap['due_day']}."]
    lines += ["", "## План на 30 / 60 / 90 дней"]
    for phase in p["plan_30_60_90"]:
        lines += [f"### День {phase['day']} — {phase['deadline']}"] + [f"- {a['gap_id']}: {a['action']}; доказательство: {a['accepted_evidence']}; критерий: {a['success_criterion']}" for a in phase["actions"]]
    score = p["readiness_score"]
    lines += ["", "## Оценка готовности доказательств", f"{score['value']}/{score['maximum']}. {score['method']}.", f"Покрытие вопросов: {score['assessment_coverage_percent']}%. {score['meaning']}.", "", "## Выдача основателю", "WAITING_APPROVAL. Не отправлено. Требуется одобрение Бориса с датой.", "", "## Итог", p["recommendation"], "", p["disclaimer"]]
    return "\n".join(lines) + "\n"


def ledger_rows(session, p):
    validate_package(p)
    eid = session["event_id"]
    completed = session["completed_at"]
    sid = session["id"]
    next_action = "Проверить пакет и одобрить выдачу основателю; запросить первичные доказательства"
    sessions = [sid, session["project_id"], session["project_name"], session["founder_name"], session["founder_email"], session["jurisdiction"], session["product_class"], session["mode"], session["created_at"], completed, "COMPLETE", session.get("trainer_version", "s1-ext-pilot-v1.6-founder-package"), p["report_url"], p["raw_json_url"], "COMPLETE_WITH_EVIDENCE_GAPS", p["question_count"], next_action, "Борис", f"Полный пакет {p['package_version']}; E1 не повышается автоматически до E3", "EVIDENCE_GAP", completed, "TRAINER_COMPLETED", eid, "WEB_FORM", "WAITING_APPROVAL", ""]
    evidence = [[e["evidence_id"], sid, session["project_id"], e["slot_id"], e["domain"], e["statement"], e["status"], e["evidence_level"], e["source_type"], e["source_ref"], completed, e["verification_status"], e["visibility"], "", e["decision_impact"], e["accepted_evidence"], e["owner"], "", completed, "WEB_FORM", completed] for e in p["evidence_delta"]]
    delivery = [p["founder_delivery"]["delivery_id"], session["project_id"], sid, session["founder_name"], session["founder_email"], "FOUNDER_READINESS_PACKAGE", p["report_url"], "DRAFT_READY", "WAITING_APPROVAL", "", "", "NOT_SENT", "", "WEB_REPORT", "", completed]
    event = [eid, completed, session["project_id"], sid, "TRAINER_COMPLETED", "STARTUP_TRAINER", p["raw_json_url"], "PACKAGE_SAVED_WAITING_APPROVAL", "", 0, session["idempotency_key"], "STARTUP_TRAINER", "Полный пакет; все записи проверяются до подтверждения Render", "WEB_FORM", "EVIDENCE_GAP", next_action, "Борис", "WAITING_APPROVAL"]
    gate = [f"HG-{sid}-v1", session["project_id"], "FOUNDER_OUTBOUND", "FOUNDER", "SEND_FOUNDER_READINESS_PACKAGE", "MEDIUM", "Борис", "WAITING_APPROVAL", "", "", "", "NOT_SENT", p["report_url"]]
    return dict(zip(LEDGER_TABS, [[sessions], evidence, [delivery], [event], [gate]]))


def read_ledger(body):
    result = {}
    for part in (body or {}).get("valueRanges", []):
        name = str(part.get("range", "")).split("!")[0].strip("'")
        result[name] = part.get("values") or []
    if set(LEDGER_TABS) - set(result):
        raise ValueError("All five ledger tabs must be read before writing")
    return result


def plan_ledger_write(session, p, ledger_body):
    actual = read_ledger(ledger_body)
    expected = ledger_rows(session, p)
    data = []
    for tab, rows in expected.items():
        existing = actual[tab]
        index = {str(r[0]): i + 1 for i, r in enumerate(existing) if r}
        append_at = len(existing) + 1
        for row in rows:
            at = index.get(str(row[0]))
            if at is None:
                at = append_at
                append_at += 1
            data.append({"range": f"'{tab}'!A{at}", "majorDimension": "ROWS", "values": [row]})
    return {"valueInputOption": "RAW", "data": data, "includeValuesInResponse": True}


def verify_ledger(session, p, ledger_body):
    actual = read_ledger(ledger_body)
    expected = ledger_rows(session, p)
    counts = {}
    for tab, rows in expected.items():
        for row in rows:
            matches = [r for r in actual[tab] if r and str(r[0]) == str(row[0])]
            if len(matches) != 1:
                raise ValueError(f"Ledger missing or duplicate: {tab}:{row[0]}")
            found = matches[0]
            if [str(x) for x in found] + [""] * max(0, len(row) - len(found)) != [str(x) for x in row]:
                raise ValueError(f"Ledger readback mismatch: {tab}:{row[0]}")
        counts[tab] = len(rows)
    return counts
