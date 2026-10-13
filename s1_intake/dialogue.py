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


def _branch(product_class: str) -> str:
    pc = (product_class or "").upper()
    return {"THERAPEUTIC": "drug", "SOFTWARE_POTENTIAL_MD": "software"}.get(pc, "device")


def build_session(scores: dict, m: dict, previous: dict | None = None, product_class: str = "") -> dict:
    """Собрать (или пересобрать после пересчёта) сессию по банку вопросов S1_DIALOG_SCENARIO_v0.1 §3.
    Ответы прошлой сессии сохраняются."""
    cfg, bank = m["dialogue_s1"], m["dialogue_questions_s1"]
    ls = scores.get("ls") or {}
    is_ls = bool(ls) and ls.get("asset_class") in m["ls_stage_rule"]["asset_classes"]
    prev = previous or {}
    answered = {a["qid"] for a in prev.get("answers", [])}
    pc = (product_class or ls.get("asset_class") or "").upper()
    zones = {z["id"]: z for z in ls.get("zones") or []}
    covered = set()
    # Р0: Q0.1–Q0.7; Q0.4 — вопрос о стадии (qid "stage"), всегда первый, пока стадия не подтверждена
    r0 = []
    for q in bank["r0"]:
        if q.get("only") and pc not in q["only"]:
            continue
        r0.append({"qid": "stage" if q.get("stage") else q["id"], "id": q["id"], "question": q["text"],
                   "source": "stage" if q.get("stage") else ("gate" if q.get("gate") else "r0"), "zone": None})
        covered.add(q.get("signal"))
    stage_pending = bool(ls.get("stage_uncertain")) and "stage" not in answered
    if stage_pending:
        r0.sort(key=lambda q: q["qid"] != "stage")
    # Р1
    items = {it["key"].split(".")[-1]: it for b in scores.get("blocks", []) for it in b.get("items", [])}
    r1 = []
    if is_ls:
        br = _branch(pc)
        for k in cfg["r1_order"]:
            if k in bank["r1_ls"]:
                it = items.get(K_SLOTS.get(k, ""))
                if it and it.get("applicability") == "N_A_BY_STAGE" and k != "К3":  # IP никогда не N/A
                    continue
                texts = bank["r1_ls"][k]
                r1.append({"qid": f"Q1.{k}", "id": f"Q1.{k}", "question": texts.get(br) or texts.get("all"),
                           "source": "slot", "zone": None, "slot": it["key"] if it else None})
            elif k == "controls":  # обязательные контрольные вопросы: выручка (Q1.R) и биоматериалы (Q1.Z6)
                for q in bank["r1_common"]:
                    if q.get("ls_only"):
                        r1.append({"qid": q["id"], "id": q["id"], "question": q["text"], "source": "control",
                                   "zone": None})
                        covered.add(q.get("signal"))
            elif k in ("Q1.T", "Q1.M"):
                q = next(x for x in bank["r1_common"] if x["id"] == k)
                r1.append({"qid": q["id"], "id": q["id"], "question": q["text"], "source": "common", "zone": None,
                           "proposal": bool(q.get("proposal"))})
            elif k == "hospital_drug" and br == "drug":  # PM 11.10: З2 «лекарство в стационаре», только ЛП
                zq = m["working"]["r1_drug_hospital_question"]
                r1.append({"qid": zq["id"], "id": zq["id"], "question": m["ls_zone_signals"][zq["signal"]],
                           "source": "control", "zone": "З2"})
                covered.add(zq["signal"])
            elif k == "zones_now":
                for zid, text in bank["zones_now"].items():  # отдельный вопрос только по зоне «сейчас»
                    if zones.get(zid, {}).get("status") == "сейчас":
                        r1.append({"qid": f"{zid}:now", "id": zid, "question": text, "source": "zone_now", "zone": zid})
    else:
        for q in bank["r1_it"] + [q for q in bank["r1_common"] if not q.get("ls_only")]:
            r1.append({"qid": q["id"], "id": q["id"], "question": q["text"], "source": "it", "zone": None,
                       "proposal": bool(q.get("proposal"))})
    # вопросы зон «не оценено, нужен ответ» (HQ-016 T6), которые не покрыты Q0.7 / Q1.Z6
    for q in ls.get("dialogue_questions") or []:
        if q["signal"] == "stage" or q["signal"] in covered:
            continue
        r1.append({"qid": f"{q.get('zone') or 'Z'}:{q['signal']}", "id": q.get("zone"), "question": q["question"],
                   "source": "zone", "zone": q.get("zone")})
    if ls.get("gate_request"):
        r0.append({"qid": "gate", "id": "gate", "question": ls["gate_request"], "source": "gate", "zone": None})
    # рабочий лимит Р1: хвост очереди не задаётся, записывается с причиной. Уточнения при противоречии
    # (≤ 2) занимают места в лимите, но не в очереди — очередь короче на их число, Q1.R / Q1.Z6 стоят выше хвоста
    lim1 = cfg["limits"]["r1"] - prev.get("clarifications", 0)
    r1, r1_over = r1[:lim1], [{"qid": q["qid"], "reason": "не задавался: лимит Р1"} for q in r1[lim1:]]
    return {
        "round": prev.get("round", "Р0"), "rounds_used": prev.get("rounds_used", 1),
        "limits": dict(cfg["limits"]), "queue": {"Р0": r0, "Р1": r1},
        "asked": prev.get("asked", {"Р0": [], "Р1": [], "Р2": []}),
        "answers": prev.get("answers", []), "r2_items": prev.get("r2_items", []),
        "r2_not_requested": prev.get("r2_not_requested", []), "human_decisions": prev.get("human_decisions", []),
        "clarifications": prev.get("clarifications", 0), "path": "LS" if is_ls else "IT", "r1_not_asked": r1_over,
        "r2_deadline_days": m["working"]["r2_deadline_days"],
        "stage_pending": stage_pending, "gate_missing": bool(ls.get("gate_request")), "exit": prev.get("exit"),
        "question_count": sum(len(v) for v in prev.get("asked", {}).values()),
    }


def r2_candidates(scores: dict, m: dict) -> list[dict]:
    """§4.1: MISSING по К1/К2/К3/К4/К7 и CLAIM ≤ 2 по ним, в порядке К2, К4, К1, К3, К7."""
    items = {it["key"].split(".")[-1]: it for b in scores.get("blocks", []) for it in b.get("items", [])}
    out = []
    for k in m["dialogue_questions_s1"]["r2_priority"]:
        it = items.get(K_SLOTS.get(k, ""))
        if not it or it.get("applicability") == "N_A_BY_STAGE":
            continue
        if it.get("status") in ("UNKNOWN", "FOUNDER_CLAIM", "CONFLICT") or it.get("applicability") == "MISSING":
            out.append({"item": f"Q1.{k}", "slot": it["key"],
                        "reason": "MISSING" if it.get("applicability") == "MISSING" or it.get("status") == "UNKNOWN"
                        else "CLAIM: документ может поднять балл выше 2"})
    return out


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


def _due(session: dict, now: datetime | None = None) -> str:
    from datetime import timedelta
    return ((now or datetime.now(timezone.utc)) + timedelta(days=session.get("r2_deadline_days", 14))).isoformat(
        timespec="seconds")


def add_r2_item(session: dict, item: str, reason: str) -> dict:
    if len(session["r2_items"]) >= session["limits"]["r2"]:
        session["r2_not_requested"].append({"item": item, "reason": "не запрашивалось: лимит досылки"})
        return session
    session["r2_items"].append({"item": item, "reason": reason, "state": "дошлю позже", "files": [],
                                "due": _due(session)})
    return session


def expire_overdue(session: dict, now: datetime | None = None) -> list[str]:
    """Срок досылки (рабочее: 14 дней) прошёл → пункт «истёк» (MISSING/CLAIM с причиной)."""
    now = now or datetime.now(timezone.utc)
    done = []
    for it in session["r2_items"]:
        if it["state"] == "дошлю позже" and it.get("due") and datetime.fromisoformat(it["due"]) < now:
            it.update(state="истёк", closed_as="MISSING/CLAIM", close_reason="не дослано к сроку")
            done.append(it["item"])
    return done


def resume_r2(session: dict, item: str, now: datetime | None = None) -> dict:
    """Возобновить истёкший пункт: снова «дошлю позже» с новым сроком."""
    for it in session["r2_items"]:
        if it["item"] == item:
            if it["state"] != "истёк":
                raise DialogueError("возобновить можно только истёкший пункт")
            it.update(state="дошлю позже", due=_due(session, now))
            it.pop("closed_as", None)
            it.pop("close_reason", None)
            it.setdefault("resumed", 0)
            it["resumed"] += 1
            return session
    raise DialogueError(f"нет пункта Р2 {item}")


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
