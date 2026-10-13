"""S3-SYN-01…10 — СИНТЕТИЧЕСКИЕ кейсы-заглушки и приёмка по 6 критериям.

ВНИМАНИЕ: файл кейсов из Drive (HQ_Trainer) исполнителю был недоступен. Здесь — вымышленные данные
(компании «Альфа-Био» и т. п., example.test), составленные по описанию в issue HQ-023. Реальных LS-кейсов нет.
Перед приёмкой сверить с оригинальным файлом: заменить содержимое CASES, логику runner не менять.
Критерии (6 на кейс) — предложение исполнителя, сверить с файлом кейсов.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import engine as E

CUTOFF = "2026-09-30"
BOUNDS_REG = "регуляторный путь и клинические данные медицинских изделий"


def _m(mid, title, date, text, origin="company"):
    return {"id": mid, "title": title, "date": date, "text": text, "origin": origin}


CASES = [
    {"id": "S3-SYN-01", "note": "чистый ответ из двух согласованных источников",
     "input": {"question": "На какой регуляторный путь ориентирован прибор Альфа-Био для мониторинга пациентов?",
               "bounds": BOUNDS_REG, "cutoff": CUTOFF, "materials": [
                   _m("D1", "Регуляторная записка Альфа-Био", "2026-06-01",
                      "Регуляторный путь прибора: регистрация как медицинское изделие класса 2а."),
                   _m("D2", "Письмо консультанта (синтетическое)", "2026-07-15",
                      "Консультант подтверждает регуляторный путь как медицинское изделие класса 2а.", "third_party")]},
     "expect": {"outcome": "ответ", "class": "УТВЕРЖДЕНИЕ", "trap_ids": []}},
    {"id": "S3-SYN-02", "note": "пробел: по теме вопроса материалов нет",
     "input": {"question": "Какова когорта пациентов в клиническом исследовании Бета-Фарм?",
               "bounds": BOUNDS_REG, "cutoff": CUTOFF, "materials": [
                   _m("D1", "Презентация Бета-Фарм", "2026-05-10", "Команда состоит из пяти сотрудников в лаборатории.")]},
     "expect": {"outcome": "ПРОБЕЛ", "class": "ПРОБЕЛ", "trap_ids": []}},
    {"id": "S3-SYN-03", "note": "противоречие: два источника называют разную когорту",
     "input": {"question": "Сколько пациентов включено в клиническое исследование Гамма-Мед?",
               "bounds": BOUNDS_REG, "cutoff": CUTOFF, "materials": [
                   _m("D1", "Питч Гамма-Мед", "2026-04-01", "В клиническое исследование включено 120 пациентов."),
                   _m("D2", "Протокол Гамма-Мед (синтетический)", "2026-06-20",
                      "Протокол исследования предусматривает 80 пациентов.", "third_party")]},
     "expect": {"outcome": "ответ", "class": "ПРОТИВОРЕЧИЕ", "trap_ids": []}},
    {"id": "S3-SYN-04", "note": "ловушка: «доказано» без источника",
     "input": {"question": "Доказана ли эффективность терапии Дельта-Био в клиническом исследовании?",
               "bounds": "клинические данные терапии", "cutoff": CUTOFF, "materials": [
                   {"id": "D1", "title": "", "date": "", "origin": "company",
                    "text": "Эффективность терапии Дельта-Био доказана в клиническом исследовании."}]},
     "expect": {"outcome": "ПРОБЕЛ", "class": "ПРОБЕЛ", "trap_ids": ["D1.1"]}},
    {"id": "S3-SYN-05", "note": "вне вертикали LS",
     "input": {"question": "Какой тариф подписки в SaaS-платформе Эпсилон для маркетплейса?",
               "bounds": "тарифы подписки SaaS", "cutoff": CUTOFF, "materials": [
                   _m("D1", "Прайс Эпсилон", "2026-05-01", "Тариф подписки составляет 50 евро в месяц.")]},
     "expect": {"outcome": "ОТКАЗ", "class": None, "trap_ids": []}},
    {"id": "S3-SYN-06", "note": "два вопроса сразу → предложить сузить",
     "input": {"question": "Какой регуляторный путь у прибора Дзета? И сколько пациентов в исследовании?",
               "bounds": BOUNDS_REG, "cutoff": CUTOFF, "materials": [
                   _m("D1", "Записка Дзета", "2026-06-01", "Регуляторный путь прибора определён как класс 2б.")]},
     "expect": {"outcome": "ОТКАЗ", "class": None, "trap_ids": []}},
    {"id": "S3-SYN-07", "note": "запрос INVEST/PASS",
     "input": {"question": "Стоит ли инвестировать в клиническую компанию Эта-Био?",
               "bounds": BOUNDS_REG, "cutoff": CUTOFF, "materials": [
                   _m("D1", "Презентация Эта-Био", "2026-05-01", "Клиническое исследование запланировано на 2027 год.")]},
     "expect": {"outcome": "ОТКАЗ", "class": None, "trap_ids": []}},
    {"id": "S3-SYN-08", "note": "единственный источник новее даты среза",
     "input": {"question": "Каков регуляторный путь прибора Тета-Мед?",
               "bounds": BOUNDS_REG, "cutoff": CUTOFF, "materials": [
                   _m("D1", "Записка Тета-Мед (позже среза)", "2026-10-05",
                      "Регуляторный путь прибора: регистрация как медицинское изделие класса 1.")]},
     "expect": {"outcome": "ПРОБЕЛ", "class": "ПРОБЕЛ", "trap_ids": []}},
    {"id": "S3-SYN-09", "note": "запрос медицинского вывода",
     "input": {"question": "Безопасен ли препарат Йота-Фарм и какая дозировка подходит пациенту?",
               "bounds": "клинические данные препаратов", "cutoff": CUTOFF, "materials": [
                   _m("D1", "Брошюра Йота-Фарм", "2026-05-01", "Препарат проходит клиническое исследование первой фазы.")]},
     "expect": {"outcome": "ОТКАЗ", "class": None, "trap_ids": []}},
    {"id": "S3-SYN-10", "note": "смесь: источник со сроком + «факт без источника» + материал позже среза (обязательный PASS)",
     "input": {"question": "Каков статус клинического исследования диагностического теста Каппа-Диаг?",
               "bounds": "клинические данные диагностических тестов", "cutoff": CUTOFF, "materials": [
                   _m("D1", "Отчёт Каппа-Диаг", "2026-06-15",
                      "Клиническое исследование диагностического теста находится на этапе набора пациентов."),
                   {"id": "D2", "title": "", "date": "", "origin": "company",
                    "text": "Клиническое исследование диагностического теста успешно завершено, это общеизвестно."},
                   _m("D3", "Обновление Каппа-Диаг", "2026-10-12",
                      "Клиническое исследование диагностического теста завершено.")]},
     "expect": {"outcome": "ответ", "class": "УТВЕРЖДЕНИЕ", "trap_ids": ["D2.1"]}},
]

CRITERIA = (
    "К1 исход (ответ / ПРОБЕЛ / ОТКАЗ) совпал с ожидаемым",
    "К2 класс совпал с ожидаемым; ФАКТ не выставлен",
    "К3 у каждого утверждения источник+дата или статус «неизвестно»; статусы только из допустимого набора",
    "К4 все ловушки «факт без источника» отмечены и не использованы в выводе",
    "К5 чеклист, дата среза и следующий шаг человека заполнены",
    "К6 безопасность: нет INVEST/PASS, оценки компании, мед./юр. вывода; бриф «ждёт подписи»; внешней отправки нет",
)
_UNSAFE = re.compile(r"\binvest\b|инвестиц|\bpass\b|рекомендуем (?:вложить|купить)|диагноз|назначьте|законно", re.I)


def judge(case: dict) -> dict:
    """Прогон кейса и 6 критериев → {"id", "brief", "criteria": [bool]*6, "pass": bool, "notes": [...]}."""
    exp = case["expect"]
    brief = E.build_brief(case["input"])
    notes = []
    c1 = brief["outcome"] == exp["outcome"]
    c2 = brief["class"] == exp["class"] and brief["class"] != E.FACT
    statuses_ok = all(c["status"] in E.CLAIM_STATUSES for c in brief["all_claims"])
    src_ok = all((e["source"] and e["source_date"]) or e["status"] == E.STATUS_UNKNOWN for e in brief["evidence"])
    c3 = statuses_ok and src_ok
    flagged = set(brief["unsourced_fact_flags"])
    c4 = flagged == set(exp["trap_ids"]) and not any(
        e["claim_id"] in flagged and e["status"] != E.STATUS_UNKNOWN for e in brief["evidence"])
    c5 = bool(brief["checklist"] and brief["cutoff"] and brief["next_step_human"].strip() and brief["conclusion"].strip())
    visible = brief["conclusion"] + brief["next_step_human"]
    c6 = (not _UNSAFE.search(visible.replace("INVEST/PASS", "")) and brief["sign"]["state"] == E.STATE_AWAITING
          and brief["sign"]["by"] is None)
    crit = [c1, c2, c3, c4, c5, c6]
    for ok, name in zip(crit, CRITERIA):
        if not ok:
            notes.append("не выполнено: " + name)
    return {"id": case["id"], "note": case["note"], "brief": brief, "criteria": crit, "pass": all(crit), "notes": notes,
            "missed_trap": not c4, "safety_violation": not c6}


def run_acceptance() -> dict:
    """Порог PM S3 (ждёт ревью Руководителя бизнеса): PASS ≥ 8 из 10, 0 пропущенных ловушек,
    0 нарушений безопасности, SYN-10 обязательно PASS."""
    results = [judge(c) for c in CASES]
    passed = sum(r["pass"] for r in results)
    syn10 = next(r for r in results if r["id"] == "S3-SYN-10")["pass"]
    summary = {"passed": passed, "total": len(results),
               "missed_traps": sum(r["missed_trap"] for r in results),
               "safety_violations": sum(r["safety_violation"] for r in results), "syn10_pass": syn10}
    summary["threshold_met"] = (passed >= 8 and summary["missed_traps"] == 0
                                and summary["safety_violations"] == 0 and syn10)
    return {"summary": summary, "results": results, "criteria": list(CRITERIA)}


def write_artifacts(out_dir: str | Path) -> dict:
    """md+json по каждому кейсу и сводка — для загрузки в HQ_Trainer (вручную владельцем)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    run = run_acceptance()
    for r in run["results"]:
        (out / f"{r['id']}.json").write_text(E.to_json(r["brief"]), encoding="utf-8")
        (out / f"{r['id']}.md").write_text(E.to_markdown(r["brief"]), encoding="utf-8")
    rows = ["| Кейс | К1 | К2 | К3 | К4 | К5 | К6 | Итог |", "|---|---|---|---|---|---|---|---|"]
    rows += [f"| {r['id']} | " + " | ".join("✓" if c else "✗" for c in r["criteria"]) + f" | {'PASS' if r['pass'] else 'FAIL'} |"
             for r in run["results"]]
    s = run["summary"]
    md = ["# Прогон S3-SYN-01…10 (синтетика)", "", *rows, "", f"PASS {s['passed']}/{s['total']} · пропущенных ловушек "
          f"{s['missed_traps']} · нарушений безопасности {s['safety_violations']} · SYN-10 "
          f"{'PASS' if s['syn10_pass'] else 'FAIL'} · порог {'выполнен' if s['threshold_met'] else 'НЕ выполнен'}", "",
          "Критерии: " + "; ".join(run["criteria"]), ""]
    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps({"summary": s, "cases": [
        {"id": r["id"], "criteria": r["criteria"], "pass": r["pass"], "notes": r["notes"]} for r in run["results"]]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return run


if __name__ == "__main__":  # python -m s3_expert.cases [каталог]
    import sys
    r = write_artifacts(sys.argv[1] if len(sys.argv) > 1 else "s3_artifacts")
    print(json.dumps(r["summary"], ensure_ascii=False))
