"""HQ-020: сессия диалога раздела 1 (Р0 → Р1 → Р2 → финал) поверх скоринга yaml + s1_intake.

Диалог НЕ считает балл сам: ответы меняют вход (Submission) и задача пересчитывается тем же ядром.
Состояние — обычный dict (хранится в data_json заявки, т.е. в Postgres/SQLite), очереди в памяти нет.
Правила:
  • вопрос о стадии (если стадия «не подтверждена») — всегда ПЕРВЫЙ в Р0;
  • вопросы «не оценено, нужен ответ» по зонам З1–З7 (scores.ls.dialogue_questions) — в Р1;
  • по слотам N_A_BY_STAGE вопросы не задаются (HQ-016 §3 а);
  • лимиты 7/12/5 и число раундов — из methodology.yaml (dialogue_s1), сверх лимита → отказ;
  • третий раунд — только с записью решения человека;
  • пока на вопрос о стадии нет ответа, полный отчёт в Human Gate не идёт.
"""
from __future__ import annotations

from datetime import datetime, timezone

ROUNDS = ("Р0", "Р1", "Р2", "final")
K_SLOTS = {"К1": "k1_clinical_stage", "К2": "k2_regulatory_path", "К3": "k3_ip", "К4": "k4_science_poc",
           "К5": "k5_manufacturing", "К6": "k6_reimbursement"}


class DialogueError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_session(scores: dict, m: dict, previous: dict | None = None) -> dict:
    """Собрать (или пересобрать после пересчёта) сессию. Ответы прошлой сессии сохраняются."""
    cfg = m["dialogue_s1"]
    ls = scores.get("ls") or {}
    prev = previous or {}
    answered = {a["qid"] for a in prev.get("answers", [])}
    r0, r1 = [], []
    for q in ls.get("dialogue_questions") or []:
        qid = "stage" if q["signal"] == "stage" else f"{q.get('zone') or 'Z'}:{q['signal']}"
        item = {"qid": qid, "question": q["question"], "source": "stage" if qid == "stage" else "zone",
                "zone": q.get("zone")}
        (r0 if qid == "stage" else r1).append(item)
    if ls.get("gate_request"):
        r0.append({"qid": "gate", "question": ls["gate_request"], "source": "gate", "zone": None})
    # Р1: слоты К по порядку важности; N_A_BY_STAGE пропускаются
    items = {it["key"].split(".")[-1]: it for b in scores.get("blocks", []) for it in b.get("items", [])}
    k_rows = []
    for k in cfg["r1_order"]:
        it = items.get(K_SLOTS.get(k, ""))
        if not it or it.get("applicability") == "N_A_BY_STAGE" or it.get("status") == "FACT":
            continue
        k_rows.append({"qid": f"{k}:{it['key']}", "question": it["label"], "source": "slot", "zone": None})
    r1 = k_rows + r1
    stage_pending = bool(ls.get("stage_uncertain")) and "stage" not in answered
    if stage_pending:  # PM 11.10: вопрос о стадии — всегда первый
        r0.sort(key=lambda q: q["qid"] != "stage")
    return {
        "round": prev.get("round", "Р0"), "rounds_used": prev.get("rounds_used", 1),
        "limits": dict(cfg["limits"]), "queue": {"Р0": r0, "Р1": r1}, "asked": prev.get("asked", {"Р0": [], "Р1": [], "Р2": []}),
        "answers": prev.get("answers", []), "r2_items": prev.get("r2_items", []),
        "r2_not_requested": prev.get("r2_not_requested", []), "human_decisions": prev.get("human_decisions", []),
        "clarifications": prev.get("clarifications", 0),
        "stage_pending": stage_pending, "gate_missing": bool(ls.get("gate_request")), "exit": prev.get("exit"),
        "question_count": sum(len(v) for v in prev.get("asked", {}).values()),
    }


def first_question(session: dict) -> dict | None:
    q = session["queue"].get(session["round"]) or []
    asked = set(session["asked"].get(session["round"], []))
    return next((x for x in q if x["qid"] not in asked), None)


def ask(session: dict, qid: str, clarification: bool = False) -> dict:
    rnd = session["round"]
    if rnd not in ("Р0", "Р1"):
        raise DialogueError(f"в раунде {rnd} вопросы не задаются")
    if rnd == "Р1" and session.get("gate_missing"):
        raise DialogueError("гейт «юрисдикция × тип актива» не заполнен — вопрос Р1 не задаётся")
    lim = session["limits"]["r0" if rnd == "Р0" else "r1"]
    if len(session["asked"][rnd]) >= lim:
        raise DialogueError(f"лимит {rnd}: {lim} вопросов")
    if clarification:
        if rnd != "Р1" or session["clarifications"] >= session["limits"]["clarifications_r1"]:
            raise DialogueError("лимит уточнений при противоречии")
        session["clarifications"] += 1
    if session["stage_pending"] and qid != "stage":
        raise DialogueError("сначала вопрос о стадии")
    session["asked"][rnd].append(qid)
    session["question_count"] += 1
    return session


def answer(session: dict, qid: str, text: str, stage: str | None = None) -> dict:
    if qid == "stage":
        if not stage:
            raise DialogueError("ответ о стадии должен содержать код стадии")
        session["stage_pending"] = False
    session["answers"].append({"qid": qid, "answer": text, "stage": stage, "at": _now()})
    return session


def next_round(session: dict, human: str | None = None, reason: str | None = None) -> dict:
    i = ROUNDS.index(session["round"])
    nxt = ROUNDS[min(i + 1, len(ROUNDS) - 1)]
    if session["rounds_used"] >= session["limits"]["rounds"] and nxt != "final" and session["round"] == "Р2":
        if not (human and reason):
            raise DialogueError("третий раунд — только решением человека с записью причины")
        session["human_decisions"].append({"by": human, "reason": reason, "at": _now(), "what": "третий раунд"})
    session["round"] = nxt
    if nxt in ("Р1", "Р2"):
        session["rounds_used"] += nxt == "Р2"
    return session


def add_r2_item(session: dict, item: str, reason: str) -> dict:
    if len(session["r2_items"]) >= session["limits"]["r2"]:
        session["r2_not_requested"].append({"item": item, "reason": "не запрашивалось: лимит досылки"})
        return session
    session["r2_items"].append({"item": item, "reason": reason, "state": "дошлю позже", "files": []})
    return session


def attach_file(session: dict, item: str, file_name: str) -> dict:
    for it in session["r2_items"]:
        if it["item"] == item:
            it["files"].append(file_name)
            it["state"] = "закрыт файлом"
            return session
    raise DialogueError(f"нет пункта Р2 {item}")


def expire_r2(session: dict) -> dict:
    for it in session["r2_items"]:
        if it["state"] == "дошлю позже":
            it["state"] = "истёк"
            it["closed_as"] = "MISSING/CLAIM"
            it["close_reason"] = "срок досылки истёк, файл не прислан"
    return session


EXIT_CODES = {"В1": "все применимые слоты SCORED/MISSING с причиной, N/A с na_reason, Р2 закрыт",
              "В2": "исчерпан лимит раундов или срок досылки", "В3": "отказ основателя",
              "В4": "новая информация не меняет статус/барьеры/следующий шаг"}


def record_exit(session: dict, code: str, note: str = "", human: str | None = None) -> dict:
    """A5: условие выхода записывается в сессии; без В1–В4 — только решение человека с причиной."""
    if code not in EXIT_CODES and not (human and note):
        raise DialogueError("условие выхода не выполнено: нужно В1–В4 или решение человека с причиной")
    if code == "В1" and any(it["state"] == "дошлю позже" for it in session["r2_items"]):
        raise DialogueError("В1: есть незакрытые пункты Р2")
    session["exit"] = {"code": code, "meaning": EXIT_CODES.get(code, "решение человека"), "note": note,
                       "by": human, "at": _now()}
    session["round"] = "final"
    return session


def ready_for_gate(session: dict) -> tuple[bool, str]:
    """Можно ли полному отчёту идти в Human Gate (approve)."""
    if session.get("stage_pending"):
        return False, "стадия не подтверждена: основатель ещё не ответил на вопрос о стадии (первый вопрос раздела 1)"
    return True, ""
