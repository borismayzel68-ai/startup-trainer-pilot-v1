"""
TrainerState — 1-в-1 с TASK_CLAUDE_S05.md / PROMPT_CLAUDE_STARTUP_TRAINER.md.

ЛИШНИХ ПОЛЕЙ НЕТ. Обоснование (раз ТЗ требует список «зачем» даже для
отсутствия дополнений):

- Домен/стадия для тегов retrieve('plan', ...) берутся из
  company_profile (слоты "стадия/фаза", био-слоты), не нужен отдельный
  top-level field — иначе состояние дублирует профиль и может разойтись.
- Юрисдикция для retrieve('coach', ...) — из company_profile
  (регуляторная карта), тем же аргументом.
- Противоречия (NORM-01/02) фиксируются внутри last_analysis / qa_log
  entry (`analysis` — свободная структура), отдельного top-level поля
  contradictions не заводим — иначе два места хранения одного факта.
- session_id уже обеспечивает изоляцию сессий (см. риски, §7 основного
  документа) — отдельного поля client/tenant не требуется в S0.5.

Если разработчик найдёт кейс, где этого набора не хватает — расширять
через append в существующие dict/list поля, не новыми top-level ключами,
без согласования со штабом.
"""

from typing import TypedDict, Literal

UserIntent = Literal["answer", "stop", "upload_note", "ask_help"]
StopReason = Literal["founder", "agent_low_value", "max_turns", ""]
FieldStatus = Literal["fact", "hypothesis", "missing", "weak"]
# РОВНО четыре значения — заморожено штабом 2026-08-23 (PROMPT v2).
# "unverified" НЕ отдельный пятый статус: claim со слайда/устного ответа
# без первички семантически unverified, но хранится как status="weak".
# Не добавлять пятое значение literal ни при какой формулировке ТЗ.


class TrainerState(TypedDict):
    session_id: str
    founder_message: str          # последний ввод человека
    user_intent: str              # answer | stop | upload_note | ask_help
    materials: list                # [{name, type, text_excerpt, source}]
    company_profile: dict          # поля: value, evidence_span, status, source
    unanswered_gaps: list
    asked_questions: list
    qa_log: list                   # [{q, a, analysis, coach}]
    current_question: dict         # {id, text, why, topic, severity}
    last_analysis: dict
    last_coach: dict
    readiness: dict                # scores + overall + recommendation
    turn: int
    max_turns: int
    should_continue: bool
    stop_reason: str               # founder | agent_low_value | max_turns | ""
    report_md: str
    report_json: dict


def initial_state(session_id: str, max_turns: int = 12) -> TrainerState:
    """Фабрика стартового состояния. Единственное место, где задаются
    дефолты — узлы не должны сами подставлять значения по умолчанию."""
    return TrainerState(
        session_id=session_id,
        founder_message="",
        user_intent="",
        materials=[],
        company_profile={},
        unanswered_gaps=[],
        asked_questions=[],
        qa_log=[],
        current_question={},
        last_analysis={},
        last_coach={},
        readiness={},
        turn=0,
        max_turns=max_turns,
        should_continue=True,
        stop_reason="",
        report_md="",
        report_json={},
    )
