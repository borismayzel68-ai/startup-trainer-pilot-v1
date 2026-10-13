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
            "ls-", "life science")
# запросы, на которые S3 не отвечает никогда (рамки issue: нет INVEST/PASS, оценки компании, мед./юр. выводов)
FORBIDDEN = (
    ("INVEST/PASS", ("инвест", "invest", "pass/fail", "вердикт pass", "вложить")),
    ("оценка компании", ("оценк компании", "оценить компанию", "оценка компании", "стоимость компании",
                         "сколько стоит компания")),
    ("медицинский вывод", ("диагноз", "назначить лечение", "дозировк", "безопасен ли препарат",
                           "эффективен ли препарат", "можно ли лечить")),
    ("юридический вывод", ("законно ли", "юридическ", "нарушает ли закон", "легально ли", "правомерн")),
)
BROAD_MARKERS = ("все аспекты", "в целом", "оцените проект", "расскажите про всё", "всё о", "полный анализ")
FACT_MARKERS = ("доказан", "установлен", "это факт", "общеизвестно", "известно, что", "подтверждён", "подтвержден",
                "бесспорно")
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
    """→ {"ok": bool, "reason": str, "suggestion": str|None, "checks": [...]}. Порядок проверок фиксирован."""
    q, bounds = inp["question"].strip(), inp["bounds"]
    checks = []

    def add(name, ok, note=""):
        checks.append({"item": name, "ok": ok, "note": note})
        return ok

    forbidden_item = "вопрос не требует INVEST/PASS, оценки компании, мед. или юр. вывода"
    for kind, words in FORBIDDEN:
        if _has(q, words):
            add(forbidden_item, False, kind)
            return {"ok": False, "reason": f"вопрос требует запрещённого вывода ({kind}); S3 такие выводы не даёт",
                    "suggestion": "Переформулируйте как проверяемый вопрос о наличии и источнике конкретного утверждения",
                    "checks": checks}
    add(forbidden_item, True)

    if not add("вертикаль LS", _has(f"{q} {bounds}", LS_STEMS)):
        return {"ok": False, "reason": "вне вертикали LS (науки о жизни / медицина)", "suggestion": None,
                "checks": checks}

    multi = q.count("?") > 1 or len(q) > MAX_QUESTION or _has(q, BROAD_MARKERS)
    if not add("один узкий вопрос", not multi, "несколько вопросов или слишком широкая формулировка" if multi else ""):
        return {"ok": False, "reason": "не один узкий вопрос",
                "suggestion": "Сузьте до одного вопроса об одном утверждении, остальные пришлите отдельными запросами",
                "checks": checks}

    if not add("вопрос внутри границ компетенции", bool(_stems(q) & _stems(bounds))):
        return {"ok": False, "reason": "вопрос вне заявленных границ компетенции",
                "suggestion": "Расширьте границы компетенции или задайте вопрос по теме границ", "checks": checks}
    return {"ok": True, "reason": "", "suggestion": None, "checks": checks}


# --- 3. утверждения ------------------------------------------------------------------------------
def extract_claims(inp: dict) -> list[dict]:
    """Каждое предложение материала → утверждение с источником и статусом. Статуса «факт» не бывает."""
    cutoff = _parse_date(inp["cutoff"])
    q_stems = _stems(inp["question"])
    claims = []
    for mi, mat in enumerate(inp["materials"]):
        mid = str(mat.get("id") or f"M{mi + 1}")
        mdate = _parse_date(mat.get("date"))
        title = str(mat.get("title") or "").strip()
        has_source = bool(title) and mdate is not None
        company = str(mat.get("origin", "company")).lower() != "third_party"
        for si, sent in enumerate(_sentences(str(mat.get("text", "")))):
            if not has_source:
                status, why = STATUS_UNKNOWN, "нет источника или даты"
            elif mdate > cutoff:
                status, why = STATUS_UNKNOWN, "источник позже даты среза"
            else:
                status, why = (STATUS_COMPANY if company else STATUS_CLAIM), ""
            claims.append({
                "id": f"{mid}.{si + 1}", "text": sent, "status": status, "note": why,
                "source": title or None, "source_id": mid, "source_date": mdate.isoformat() if mdate else None,
                "relevant": bool(q_stems & _stems(sent)),
                "fact_without_source": _has(sent, FACT_MARKERS) and status == STATUS_UNKNOWN,
            })
    _mark_conflicts(claims)
    return claims


def _mark_conflicts(claims: list[dict]) -> None:
    usable = [c for c in claims if c["relevant"] and c["status"] in (STATUS_CLAIM, STATUS_COMPANY)]
    for i, a in enumerate(usable):
        for b in usable[i + 1:]:
            if a["source_id"] == b["source_id"]:
                continue
            na, nb = set(_NUM.findall(a["text"])), set(_NUM.findall(b["text"]))
            if na and nb and na != nb and len(_stems(a["text"]) & _stems(b["text"])) >= 2:
                for c, other in ((a, b), (b, a)):
                    c["status"], c["note"] = STATUS_CONFLICT, f"расходится с {other['id']}"


# --- 4. бриф ------------------------------------------------------------------------------------
def build_brief(inp: dict) -> dict:
    """Главная функция. Вход должен пройти validate_input (иначе ValueError)."""
    bad = validate_input(inp)
    if bad:
        raise ValueError("пустые или некорректные поля: " + ", ".join(bad))
    scope = check_scope(inp)
    claims = extract_claims(inp)
    cutoff = str(inp["cutoff"]).strip()
    flags = [c["id"] for c in claims if c["fact_without_source"]]

    if not scope["ok"]:
        outcome, cls, evidence = OUT_REFUSAL, None, []
        conclusion = f"ОТКАЗ: {scope['reason']}."
        next_step = scope["suggestion"] or "Человек: вернуть вопрос автору с причиной отказа."
    else:
        evidence = [c for c in claims if c["relevant"]]
        usable = [c for c in evidence if c["status"] in (STATUS_CLAIM, STATUS_COMPANY)]
        conflicts = [c for c in evidence if c["status"] == STATUS_CONFLICT]
        if conflicts:
            outcome, cls = OUT_ANSWER, CLASS_CONFLICT
            conclusion = (f"По материалам на {cutoff} источники расходятся: "
                          + "; ".join(f"{c['id']} — «{c['text']}»" for c in conflicts) + ". Единого ответа нет.")
            next_step = "Человек: запросить у компании первичные документы и выяснить, какое из утверждений верно."
        elif usable:
            outcome, cls = OUT_ANSWER, CLASS_CLAIM
            conclusion = (f"По материалам на {cutoff}: "
                          + "; ".join(f"{c['id']} — «{c['text']}» ({c['status']})" for c in usable)
                          + ". Это утверждения, а не подтверждённые факты: независимой проверки в системе нет.")
            next_step = "Человек: проверить утверждения по первичным документам и при подтверждении сам присвоить класс ФАКТ."
        else:
            outcome, cls = OUT_GAP, CLASS_GAP
            extra = " Утверждения без источника не приняты." if evidence else ""
            conclusion = f"По материалам на {cutoff} ответа нет: подходящих утверждений с источником не найдено.{extra}"
            next_step = "Человек: запросить у компании материал с источником и датой по этому вопросу."

    if flags:
        conclusion += f" Отмечено «факт без источника»: {', '.join(flags)} — не использовано."
    return {
        "version": "s3-brief-0.1",
        "question": inp["question"].strip(),
        "bounds": inp["bounds"].strip(),
        "outcome": outcome,
        "class": cls,
        "checklist": scope["checks"] + _evidence_checks(claims, scope["ok"]),
        "conclusion": conclusion,
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


def _evidence_checks(claims: list[dict], in_scope: bool) -> list[dict]:
    if not in_scope:
        return []
    rel = [c for c in claims if c["relevant"]]
    trap = any(c["fact_without_source"] for c in claims)
    return [
        {"item": "у каждого утверждения есть источник и дата",
         "ok": all(c["source"] and c["source_date"] for c in rel), "note": ""},
        {"item": "источники не позже даты среза", "ok": not any("позже" in c["note"] for c in rel), "note": ""},
        {"item": "конфликтов между источниками нет", "ok": not any(c["status"] == STATUS_CONFLICT for c in rel), "note": ""},
        {"item": "«факт без источника» не принят", "ok": not trap, "note": "найдено и отброшено" if trap else ""},
        {"item": "статус «факт» системой не ставится", "ok": True, "note": ""},
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
         f"| Исход | {brief['outcome']} |",
         f"| Класс | {brief['class'] or '—'} |",
         f"| Вывод | {_cell(brief['conclusion'])} |",
         f"| Дата среза | {brief['cutoff']} |",
         f"| Следующий шаг человека | {_cell(brief['next_step_human'])} |", "",
         "## Чеклист проверки", ""]
    L += [f"- [{'x' if c['ok'] else ' '}] {c['item']}" + (f" — {c['note']}" if c.get("note") else "")
          for c in brief["checklist"]]
    L += ["", "## Доказательность (источник и дата среза)", ""]
    if brief["evidence"]:
        L += ["| Утверждение | Статус | Источник | Дата источника | Срез |", "|---|---|---|---|---|"]
        L += [f"| {_cell(e['claim'])} | {e['status']} | {_cell(e['source'] or '—')} | {e['source_date'] or '—'} | {e['cutoff']} |"
              for e in brief["evidence"]]
    else:
        L.append("Утверждений по теме вопроса нет.")
    L += ["", f"_{brief['notice']}_", ""]
    return "\n".join(L)
