"""S3 · БП-07 «Эксперт: один вопрос → ответ → бриф». Чистая логика без сети, без LLM, без БД.

Вход: вопрос · границы компетенции · материалы · дата среза.
Выход: бриф по таблице выхода БП-07 (вопрос · чеклист · вывод · класс · доказательность · следующий шаг человека).

Инварианты (канон Р-5, G-12, G-4):
- система НЕ ставит класс/статус «ФАКТ» — ни в брифе, ни в утверждениях (константа FACT есть только как запрещённая);
- каждое утверждение имеет источник (материал + дата) или статус «неизвестно»;
- бриф рождается в состоянии «ждёт подписи»; подпись ставит только человек (см. blueprint.py);
- нет функции отправки наружу.
Ядро скоринга S1 и веса не используются и не меняются.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date

from . import topics as T

# --- словари --------------------------------------------------------------------------------------
STATUS_COMPANY = "со слов компании"
STATUS_CLAIM = "claim"
STATUS_UNKNOWN = "неизвестно"
STATUS_CONFLICT = "конфликт"
CLAIM_STATUSES = (STATUS_CLAIM, STATUS_COMPANY, STATUS_UNKNOWN, STATUS_CONFLICT)  # «факт» нет и не будет

FACT = "ФАКТ"  # ставит только человек; engine его не выдаёт (проверяется тестами и приёмкой)
CLASS_CLAIM, CLASS_GAP, CLASS_CONFLICT = "УТВЕРЖДЕНИЕ", "ПРОБЕЛ", "ПРОТИВОРЕЧИЕ"
CLASSES = (FACT, CLASS_CLAIM, CLASS_GAP, CLASS_CONFLICT)
ENGINE_CLASSES = (CLASS_CLAIM, CLASS_GAP, CLASS_CONFLICT)

OUT_ANSWER, OUT_GAP, OUT_REFUSAL = "ответ", "ПРОБЕЛ", "ОТКАЗ"
STATE_AWAITING = "ждёт подписи"
STATE_SIGNED = "подписан"

INPUT_FIELDS = (("question", "вопрос"), ("bounds", "границы компетенции"),
                ("materials", "материалы"), ("cutoff", "дата среза"))

# вертикаль LS (рабочий список исполнителя — утвердить PM; пересекается с vertical_s1.clinical_words)
LS_STEMS = ("клиник", "пациент", "диагност", "лечени", "терапи", "медицин", "врач", "регистрац", "медизделие",
            "биотех", "фарм", "лекарств", "препарат", "доклиник", "биомаркер", "ivd", "gmp", "когорт",
            "ls-", "life science", "медизд", "медицинск", "клеточн", "пептид", "опухол", "экг", "генн",
            "доклин", "животн", "кров")
# запросы, на которые S3 не отвечает никогда (рамки issue: нет INVEST/PASS, оценки компании, мед./юр. выводов)
FORBIDDEN = (
    ("INVEST/PASS", ("инвест", "invest", "pass/fail", "вердикт pass", "вложить")),
    ("оценка компании", ("оценк компании", "оценить компанию", "оценка компании", "стоимость компании",
                         "сколько стоит компания")),
    ("медицинский вывод", ("диагноз", "назначить лечение", "дозировк", "безопасен ли препарат",
                           "эффективен ли препарат", "можно ли лечить")),
    ("юридический вывод", ("законно ли", "юридическ", "нарушает ли закон", "легально ли", "правомерн")),
)
BROAD_MARKERS = ("все аспекты", "в целом", "оцените проект", "расскажите про всё", "всё о", "полный анализ",
                 "что вы думаете", "что вы о ней думаете", "ваше мнение о компании")
FACT_MARKERS = ("доказан", "установлен", "это факт", "общеизвестно", "известно, что", "подтверждён", "подтвержден",
                "бесспорно", "статистически значим", "полностью соответствует", "гарантирован")
STOP = {"какой", "какая", "какие", "каков", "какова", "каковы", "который", "которые", "имеется", "компании",
        "компания", "проекта", "проект", "этого", "после", "перед", "между", "может", "должен", "ожидается"}
_STOP_STEMS = {w[:5] for w in STOP}

MAX_QUESTION = 400
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_WORD = re.compile(r"[a-zа-яё0-9\-]+", re.I)


# --- утилиты --------------------------------------------------------------------------------------
def _stems(text: str) -> set[str]:
    return {w[:5] for w in _WORD.findall(text.lower()) if len(w) >= 5 and w[:5] not in _STOP_STEMS}


def _parse_date(v) -> date | None:
    try:
        return date.fromisoformat(str(v).strip())
    except (ValueError, TypeError):
        return None


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;])\s+|\n+", text or "")
    return [p.strip() for p in parts if len(p.split()) >= 3]


def _has(text: str, needles) -> bool:
    t = text.lower()
    return any(n in t for n in needles)


# --- 1. вход ------------------------------------------------------------------------------------
def validate_input(inp: dict) -> list[str]:
    """Пустое или негодное поле → список названий полей, которые нужно вернуть пользователю."""
    bad = []
    for key, label in INPUT_FIELDS:
        v = (inp or {}).get(key)
        if key == "materials":
            ok = isinstance(v, list) and any(isinstance(m, dict) and str(m.get("text", "")).strip() for m in v)
        elif key == "cutoff":
            ok = _parse_date(v) is not None
        else:
            ok = isinstance(v, str) and bool(v.strip())
        if not ok:
            bad.append(label)
    return bad


# --- 2. рамки -----------------------------------------------------------------------------------
def check_scope(inp: dict) -> dict:
    """→ {"ok", "reason", "suggestion", "suggested_questions", "topic", "checks"}. Порядок проверок фиксирован."""
    q, bounds = inp["question"].strip(), inp["bounds"]
    topic = T.detect_topic(q, inp.get("topic"))
    checks = []

    def add(name, ok, note=""):
        checks.append({"item": name, "ok": ok, "note": note})
        return ok

    def refuse(reason, suggestion=None, narrow=False):
        sq = _narrow_questions(inp) if narrow else []
        if sq and not suggestion:
            suggestion = "Выберите один узкий вопрос: " + " / ".join(f"«{x}»" for x in sq)
        return {"ok": False, "reason": reason, "suggestion": suggestion, "suggested_questions": sq,
                "topic": topic, "checks": checks}

    forbidden_item = "вопрос не требует INVEST/PASS, оценки компании, мед. или юр. вывода"
    for kind, words in FORBIDDEN:
        if _has(q, words):
            add(forbidden_item, False, kind)
            return refuse(f"вопрос требует запрещённого вывода ({kind}); такой вывод делает только человек", narrow=True)
    add(forbidden_item, True)

    case_text = " ".join([q, bounds] + [f"{m.get('title', '')} {m.get('text', '')}" for m in inp["materials"]])
    if not add("вертикаль LS", _has(case_text, LS_STEMS)):
        return refuse("вне вертикали LS (науки о жизни / медицина)")

    multi = q.count("?") > 1 or len(q) > MAX_QUESTION or _has(q, BROAD_MARKERS)
    if not add("один узкий вопрос", not multi, "несколько вопросов или слишком широкая формулировка" if multi else ""):
        return refuse("не один узкий вопрос",
                      None if _narrow_questions(inp) else
                      "Сузьте до одного вопроса об одном утверждении, остальные пришлите отдельными запросами",
                      narrow=True)

    in_bounds = bool(_stems(q) & _stems(bounds)) or (topic is not None and _has(bounds, T.TOPICS[topic]["keys"]))
    if not add("вопрос внутри границ компетенции", in_bounds):
        return refuse("вопрос вне заявленных границ компетенции",
                      "Расширьте границы компетенции или задайте вопрос по теме границ")
    return {"ok": True, "reason": "", "suggestion": None, "suggested_questions": [], "topic": topic, "checks": checks}


def _narrow_questions(inp: dict) -> list[str]:
    """2–3 узких вопроса на выбор: темы, которые видны в материалах, иначе — набор по умолчанию."""
    text = " ".join(f"{m.get('title', '')} {m.get('text', '')}" for m in inp.get("materials") or []).lower()
    seen = [k for k, t in T.TOPICS.items() if any(w in text for w in t["keys"])]
    keys = (seen + [k for k in T.DEFAULT_NARROW if k not in seen])[:3]
    return [T.TOPICS[k]["narrow"] for k in keys]


# --- 3. утверждения ------------------------------------------------------------------------------
def extract_claims(inp: dict, topic: str | None = None) -> list[dict]:
    """Каждое предложение материала и каждый структурный факт (`facts`) → утверждение с источником и статусом.
    Статуса «факт» не бывает. `facts` — [{key, value}]: в проде их извлекает LLM-шаг, в синтетике они заданы."""
    cutoff = _parse_date(inp["cutoff"])
    q_stems = _stems(inp["question"])
    topic_words = T.TOPICS[topic]["keys"] if topic else ()
    third_party = any(str(m.get("origin", "company")).lower() == "third_party" for m in inp["materials"])
    claims = []
    for mi, mat in enumerate(inp["materials"]):
        mid = str(mat.get("id") or f"M{mi + 1}")
        mdate = _parse_date(mat.get("date"))
        title = str(mat.get("title") or "").strip()
        has_source = bool(title) and mdate is not None
        company = str(mat.get("origin", "company")).lower() != "third_party"
        if not has_source:
            status, why = STATUS_UNKNOWN, "нет источника или даты"
        elif mdate > cutoff:
            status, why = STATUS_UNKNOWN, "источник позже даты среза"
        else:
            status, why = (STATUS_COMPANY if company else STATUS_CLAIM), ""
        base = {"source": title or None, "source_id": mid, "source_date": mdate.isoformat() if mdate else None}
        for si, sent in enumerate(_sentences(str(mat.get("text", "")))):
            strong = _has(sent, FACT_MARKERS)
            claims.append({
                **base, "id": f"{mid}.{si + 1}", "text": sent, "status": status, "note": why, "key": None,
                "relevant": bool(q_stems & _stems(sent)) or _has(sent, topic_words),
                # «факт без источника»: категоричное утверждение без источника/даты либо только со слов компании
                # (нет ни одного стороннего документа в материалах кейса)
                "fact_without_source": strong and (status == STATUS_UNKNOWN or (company and not third_party)),
            })
        for fi, f in enumerate(mat.get("facts") or []):
            claims.append({**base, "id": f"{mid}.f{fi + 1}", "text": f"{f['key']}: {f['value']}", "status": status,
                           "note": why, "key": str(f["key"]), "value": str(f["value"]), "relevant": True,
                           "fact_without_source": False})
    _mark_conflicts(claims)
    return claims


def _mark_conflicts(claims: list[dict]) -> None:
    usable = [c for c in claims if c["relevant"] and c["status"] in (STATUS_CLAIM, STATUS_COMPANY, STATUS_CONFLICT)]
    for i, a in enumerate(usable):
        for b in usable[i + 1:]:
            if a["source_id"] == b["source_id"]:
                continue
            if a["key"] or b["key"]:  # структурные факты: один ключ — разные значения
                hit = a["key"] and a["key"] == b["key"] and a["value"].strip().lower() != b["value"].strip().lower()
            else:  # текст: одна тема, разные числа
                na, nb = set(_NUM.findall(a["text"])), set(_NUM.findall(b["text"]))
                hit = na and nb and na != nb and len(_stems(a["text"]) & _stems(b["text"])) >= 2
            if hit:
                for c, other in ((a, b), (b, a)):
                    c["status"], c["note"] = STATUS_CONFLICT, f"расходится с {other['id']}"


def _missing_docs(inp: dict, topic: str | None) -> list[str]:
    """Обязательные документы темы, которых нет среди материалов (сверка по названию и типу документа)."""
    if not topic:
        return []
    names = " ".join(f"{m.get('title', '')} {m.get('kind', '')}" for m in inp["materials"]).lower()
    return [label for label, stems in T.TOPICS[topic]["required_docs"] if not all(s in names for s in stems)]


def _topic_checklist(inp: dict, topic: str | None) -> list[dict]:
    if not topic:
        return []
    text = " ".join(f"{m.get('title', '')} {m.get('text', '')} "
                    + " ".join(f"{f['key']} {f['value']}" for f in m.get("facts") or [])
                    for m in inp["materials"]).lower()
    return [{"item": item, "ok": any(w in text for w in words),
             "note": "упомянуто в материалах — проверить по документу" if any(w in text for w in words)
             else "в материалах нет — запросить"}
            for item, words in T.TOPICS[topic]["checklist"]]


# --- 4. бриф ------------------------------------------------------------------------------------
def build_brief(inp: dict) -> dict:
    """Главная функция. Вход должен пройти validate_input (иначе ValueError)."""
    bad = validate_input(inp)
    if bad:
        raise ValueError("пустые или некорректные поля: " + ", ".join(bad))
    scope = check_scope(inp)
    topic = scope["topic"]
    claims = extract_claims(inp, topic)
    cutoff = str(inp["cutoff"]).strip()
    flags = [c["id"] for c in claims if c["fact_without_source"]]
    traps, missing = [], []

    if not scope["ok"]:
        outcome, cls, evidence = OUT_REFUSAL, None, []
        conclusion = f"ОТКАЗ: {scope['reason']}."
        next_step = scope["suggestion"] or "Человек: вернуть вопрос автору с причиной отказа."
        flags = []
    else:
        evidence = [c for c in claims if c["relevant"]]
        usable = [c for c in evidence if c["status"] in (STATUS_CLAIM, STATUS_COMPANY)]
        conflicts = [c for c in evidence if c["status"] == STATUS_CONFLICT]
        missing = _missing_docs(inp, topic)
        parts = []
        if conflicts:
            pairs = _conflict_pairs(conflicts)
            traps += [{"type": CLASS_CONFLICT, "ref": k, "ids": ids} for k, ids in pairs]
            parts.append("Источники расходятся: " + "; ".join(
                f"{c['id']} ({c['source']}) — «{c.get('value') or c['text']}»" for c in conflicts) + ".")
        if missing:
            traps += [{"type": CLASS_GAP, "ref": f"нет документа: {d}", "ids": []} for d in missing]
            parts.append("В материалах нет: " + ", ".join(missing) + ".")
        if flags:
            traps += [{"type": CLASS_GAP, "ref": "факт без источника", "ids": flags}]
            parts.append("Категоричные утверждения без подтверждающего источника: " + ", ".join(
                f"{c['id']} «{c['text']}»" for c in claims if c["fact_without_source"]) + " — не приняты как доказанные.")
        if conflicts:
            outcome, cls = OUT_ANSWER, CLASS_CONFLICT
            next_step = "Человек: запросить у компании первичные документы и выяснить, какое из утверждений верно."
        elif missing or flags or not usable:
            outcome, cls = OUT_GAP, CLASS_GAP
            if not usable and not parts:
                parts.append("Подходящих утверждений с источником не найдено.")
            need = missing or ["документ-основание к утверждениям " + ", ".join(flags)] if (missing or flags) else \
                ["материал с источником и датой по этому вопросу"]
            next_step = "Человек: запросить у компании " + "; ".join(need) + "."
        else:
            outcome, cls = OUT_ANSWER, CLASS_CLAIM
            parts.append("Утверждения: " + "; ".join(f"{c['id']} «{c['text']}» ({c['status']})" for c in usable[:4])
                         + ". Это утверждения, а не подтверждённые факты.")
            next_step = "Человек: проверить утверждения по первичным документам и при подтверждении сам присвоить класс ФАКТ."
        conclusion = f"По материалам на {cutoff}: " + " ".join(parts)

    topic_name = T.TOPICS[topic]["name"] if topic else None
    return {
        "version": "s3-brief-0.2",
        "question": inp["question"].strip(),
        "bounds": inp["bounds"].strip(),
        "topic": topic,
        "topic_name": topic_name,
        "outcome": outcome,
        "class": cls,
        "checklist": scope["checks"] + (_topic_checklist(inp, topic) + _evidence_checks(claims) if scope["ok"] else []),
        "conclusion": conclusion,
        "traps": traps,
        "missing_docs": missing,
        "suggested_questions": scope["suggested_questions"],
        "evidence": [{"claim_id": c["id"], "claim": c["text"], "status": c["status"], "source": c["source"],
                      "source_date": c["source_date"], "cutoff": cutoff, "note": c["note"]} for c in evidence],
        "all_claims": [{"claim_id": c["id"], "status": c["status"], "relevant": c["relevant"],
                        "fact_without_source": c["fact_without_source"]} for c in claims],
        "unsourced_fact_flags": flags,
        "cutoff": cutoff,
        "next_step_human": next_step,
        "sign": {"state": STATE_AWAITING, "by": None, "at": None},
        "notice": "Бриф — внутренний, без оценки компании, INVEST/PASS, медицинских и юридических выводов. "
                  "Класс ФАКТ системой не ставится.",
    }


def _conflict_pairs(conflicts: list[dict]) -> list[tuple[str, list[str]]]:
    by_key: dict[str, list[str]] = {}
    for c in conflicts:
        by_key.setdefault(c["key"] or "текст", []).append(c["id"])
    return list(by_key.items())


def _evidence_checks(claims: list[dict]) -> list[dict]:
    rel = [c for c in claims if c["relevant"]]
    trap = any(c["fact_without_source"] for c in claims)
    return [
        {"item": "у каждого утверждения есть источник и дата",
         "ok": all(c["source"] and c["source_date"] for c in rel), "note": ""},
        {"item": "источники не позже даты среза", "ok": not any("позже" in c["note"] for c in rel), "note": ""},
        {"item": "конфликтов между источниками нет", "ok": not any(c["status"] == STATUS_CONFLICT for c in rel), "note": ""},
        {"item": "«факт без источника» не принят", "ok": not trap, "note": "найдено и не принято" if trap else ""},
    ]


# --- 5. сериализация -----------------------------------------------------------------------------
def brief_sha256(brief: dict) -> str:
    """Хеш содержимого без блока подписи — именно его подписывает человек."""
    body = {k: v for k, v in brief.items() if k != "sign"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def to_json(brief: dict) -> str:
    return json.dumps(brief, ensure_ascii=False, indent=2, sort_keys=True)


def _cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def to_markdown(brief: dict) -> str:
    sg = brief["sign"]
    sig = sg["state"] if not sg.get("by") else f"{sg['state']} · проверил: {sg['by']} · {sg['at']}"
    L = ["# Бриф S3 · вопрос → ответ", "", f"**Статус:** {sig}", "",
         "| Поле | Значение |", "|---|---|",
         f"| Вопрос | {_cell(brief['question'])} |",
         f"| Тема | {brief.get('topic_name') or '—'} |",
         f"| Исход | {brief['outcome']} |",
         f"| Класс | {brief['class'] or '—'} |",
         f"| Вывод | {_cell(brief['conclusion'])} |",
         f"| Дата среза | {brief['cutoff']} |",
         f"| Следующий шаг человека | {_cell(brief['next_step_human'])} |", "",
         "## Чеклист проверки", ""]
    L += [f"- [{'x' if c['ok'] else ' '}] {c['item']}" + (f" — {c['note']}" if c.get("note") else "")
          for c in brief["checklist"]]
    if brief.get("suggested_questions"):
        L += ["", "## Узкие вопросы на выбор", ""] + [f"- {x}" for x in brief["suggested_questions"]]
    L += ["", "## Доказательность (источник и дата среза)", ""]
    if brief["evidence"]:
        L += ["| Утверждение | Статус | Источник | Дата источника | Срез |", "|---|---|---|---|---|"]
        L += [f"| {_cell(e['claim'])} | {e['status']} | {_cell(e['source'] or '—')} | {e['source_date'] or '—'} | {e['cutoff']} |"
              for e in brief["evidence"]]
    else:
        L.append("Утверждений по теме вопроса нет.")
    L += ["", f"_{brief['notice']}_", ""]
    return "\n".join(L)
