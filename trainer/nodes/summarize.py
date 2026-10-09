"""
Node: summarize — отчёт сессии.

Пишет: report_md, report_json, readiness (если сессия остановлена до первого
подсчёта), should_continue=False. Цифры не придумываются: в отчёт идут
только статусы, цитаты и тексты ответов. Дисклеймер обязателен (NORM-07).
S0.5-докстринг называл поле session_summary — в TrainerState это report_md/report_json.
"""

from __future__ import annotations

from typing import Any

from trainer.scoring import SCALE_LABELS, compute_readiness
from trainer.slots import SLOTS, SLOT_IDS, compute_gaps

DISCLAIMER = (
    "Оценка готовности — тренировочная, не инвестиционная рекомендация и не вердикт фонда. "
    "«ready_for_investor_conversation» означает готовность к содержательному разговору с аналитиком, "
    "не INVEST/PASS/GO."
)


def summarize(state: dict[str, Any]) -> dict[str, Any]:
    profile = state.get("company_profile") or {}
    qa_log = state.get("qa_log") or []
    readiness = state.get("readiness") or compute_readiness(profile, qa_log)
    asked_ids = {q.get("id") for q in state.get("asked_questions") or []}
    gaps = compute_gaps(profile, asked_ids)
    coverage = {s: 0 for s in ("fact", "hypothesis", "weak", "missing")}
    for sid in SLOT_IDS:
        coverage[(profile.get(sid) or {}).get("status", "missing")] += 1
    next_artifacts = [e["coach"] for e in qa_log if e.get("coach")]

    report_json = {
        "session_id": state.get("session_id"),
        "turns": state.get("turn", 0),
        "stop_reason": state.get("stop_reason") or "founder",
        "readiness": readiness,
        "profile_coverage": coverage,
        "profile": {sid: profile.get(sid) for sid in SLOT_IDS if sid in profile},
        "top_gaps": gaps[:3],
        "next_artifacts": next_artifacts,
        "qa_log": qa_log,
        "disclaimer": DISCLAIMER,
    }

    lines = [
        "# Отчёт сессии тренажёра",
        "",
        f"Сессия: `{report_json['session_id']}` · ходов: {report_json['turns']} · остановка: {report_json['stop_reason']}",
        "",
        "## Готовность",
        f"Итог (0–5, веса rubric v1): **{readiness['overall']}** · recommendation: `{readiness['recommendation']}`",
        "",
    ]
    for dim, score in readiness["scores"].items():
        label = SCALE_LABELS.get(int(score), "")
        quote = "; ".join(readiness.get("quotes", {}).get(dim, []))
        lines.append(f"- {dim} (вес {readiness['weights'][dim]}): {score} — {label}. Цитаты: {quote}")
    if readiness.get("open_gaps"):
        lines += ["", "Открытые вопросы методики: " + "; ".join(readiness["open_gaps"])]
    lines += ["", "## Профиль", f"fact {coverage['fact']} · hypothesis {coverage['hypothesis']} · "
              f"weak {coverage['weak']} · missing {coverage['missing']}", ""]
    lines += ["## Главные пробелы"]
    lines += [f"- {SLOTS[g['field_id']]['title']}: {g['status']}" for g in gaps[:3]] or ["- нет"]
    lines += ["", "## Что принести"]
    lines += [f"- {a['artifact']}" for a in next_artifacts] or ["- рекомендаций пока нет"]
    lines += ["", "---", DISCLAIMER]

    return {
        "readiness": readiness,
        "report_json": report_json,
        "report_md": "\n".join(lines),
        "should_continue": False,
    }
