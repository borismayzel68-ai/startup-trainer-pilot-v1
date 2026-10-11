"""HQ-028 · Выход S1: журнал согласий с отзывом, тизер с проверкой обезличивания, таймер фонда 21 день,
передачи только через G-4. Ничего наружу не отправляется: «передача» = запись в журнале + ссылка,
которую владелец передаёт сам."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- таймер ----
def fire_if_due(store, job: dict, cfg: dict) -> dict:
    """Срабатывание окна: статус expired + внутренняя задача в очереди подписанта (один раз). Наружу — ничего."""
    w = fund_window(job["data"], cfg)
    if w["state"] == "expired" and job["data"]["fund_timer"].get("state") != "expired":
        store.update(job["id"], data_patch={"fund_timer": {**job["data"]["fund_timer"], "state": "expired"}})
        store.add_task_once(job["id"], job["data"]["fund_timer"]["started_by"], cfg["timer_task_title"])
    return w


def fund_window(data: dict, cfg: dict, now: datetime | None = None) -> dict:
    """Окно фонда: не запущено / идёт / истекло. Срабатывание = только смена статуса (ничего не отправляется)."""
    t = data.get("fund_timer")
    if not t:
        return {"state": "not_started"}
    ends = datetime.fromisoformat(t["ends_at"])
    state = "running" if (now or _now()) < ends else "expired"
    return {**t, "state": state}


def start_fund_window(cfg: dict, by: str, now: datetime | None = None) -> dict:
    n = now or _now()
    return {"started_at": n.isoformat(timespec="seconds"), "started_by": by,
            "ends_at": (n + timedelta(days=cfg["fund_window_days"])).isoformat(timespec="seconds")}


# ------------------------------------------------------------------ тизер ----
TEASER_KEYS = ("area", "stage", "raise", "strengths", "risks", "fund_decision", "disclaimer")


def build_teaser(job: dict, cfg: dict) -> dict:
    """Д-5: только поля структуры. Сильные стороны и риски — подписи слотов методологии (не текст деки и
    не реплики LLM) со статусом «факт» (FACT) / «заявление» (остальное). Решение фонда — из списка, при подписи."""
    data = job["data"]
    brief = data["readiness_pack"]["internal_analyst_brief"]
    cls = brief["classification"]
    items = [it for b in brief["scores"]["blocks"] for it in b["items"] if it.get("applicability") != "N_A_BY_STAGE"]
    stat = lambda it: {"FACT": "факт", "FACT_CANDIDATE": "заявление с документом"}.get(it["status"], "заявление")  # noqa: E731
    n = cfg["teaser_items"]
    lvl = lambda it: it.get("best_level") if it.get("best_level") is not None else -1  # noqa: E731
    strong = sorted([it for it in items if lvl(it) >= 1], key=lambda it: -lvl(it))[:n]
    weak = sorted(items, key=lambda it: lvl(it) if it["status"] not in ("UNKNOWN", "CONFLICT") else -1)[:n]
    ls = brief["scores"].get("ls") or {}
    answers = {a["field"]: a["value"] for a in brief["submission"].get("answers", [])}
    return {"area": cls.get("sector") or "", "stage": ls.get("stage_label") or cls.get("stage") or "",
            "raise": answers.get("q_round", ""),
            "strengths": [{"text": it["label"], "status": stat(it)} for it in strong],
            "risks": [{"text": it["label"], "status": stat(it)} for it in weak],
            "fund_decision": None, "disclaimer": cfg["disclaimer"]}


def _teaser_text(t: dict) -> str:
    parts = [t.get("area"), t.get("stage"), t.get("raise")] + [x["text"] for x in t.get("strengths", []) + t.get("risks", [])]
    return "\n".join(str(p or "") for p in parts) + "\n" + str(t.get("fund_decision") or "")


def case_blacklist(job: dict, cfg: dict) -> dict[str, str]:
    """Чёрный список кейса: название, основатель, команда (из q_team / поля team_names), домен из формы."""
    sub = job["data"]["submission"]
    out = {}
    def add(label, value):
        for tok in [value] + re.split(r"[\s,;]+", value or ""):
            tok = (tok or "").strip(" .,:;()«»\"'")
            if len(tok) >= 3 and tok not in cfg["name_stopwords"]:
                out.setdefault(tok.lower(), label)
    add("название компании", sub.get("project_name") or job.get("project_name") or "")
    add("имя основателя", sub.get("founder_name") or "")
    email = sub.get("founder_email") or ""
    if "@" in email:
        dom = email.split("@", 1)[1].lower()
        out[dom] = "домен"
        out.setdefault(dom.split(".")[0], "домен")
    answers = {a["field"]: a["value"] for a in sub.get("answers", [])}
    raw = sub.get("team_names") or answers.get("team_names") or ""
    team_text = (answers.get("q_team") or "") + " " + raw
    # рабочее: имя/фамилия — слово с заглавной буквы (кириллица/латиница), кроме ролей и юрформ
    for m in re.finditer(r"\b([А-ЯЁA-Z][а-яёa-z]{2,})\b", team_text):
        if m.group(1) not in cfg["name_stopwords"]:
            out.setdefault(m.group(1).lower(), "имя в команде")
    return out


def _deck_overlap(t: dict, job: dict, cfg: dict) -> list[str]:
    n = int(cfg["deck_ngram_words"])
    deck = " ".join(d.get("text", "") for d in job["data"].get("parsed_docs") or [] if d.get("kind") == "pitch_deck")
    words = re.findall(r"\w+", deck.lower())
    grams = {" ".join(words[i:i + n]) for i in range(max(len(words) - n + 1, 0))}
    hits = []
    for field in ("area", "stage", "raise"):
        w = re.findall(r"\w+", str(t.get(field) or "").lower())
        if any(" ".join(w[i:i + n]) in grams for i in range(max(len(w) - n + 1, 0))):
            hits.append(f"свободный текст из деки в поле {field}")
    for x in t.get("strengths", []) + t.get("risks", []):
        w = re.findall(r"\w+", str(x.get("text") or "").lower())
        if any(" ".join(w[i:i + n]) in grams for i in range(max(len(w) - n + 1, 0))):
            hits.append("свободный текст из деки в сильных сторонах / рисках")
    return hits


def deid_check(teaser: dict, job: dict, cfg: dict) -> list[str]:
    """Проверка обезличивания + только поля Д-5. Пусто = прошла (ручная проверка перед отправкой остаётся)."""
    bad = [f"лишнее поле «{k}» (только структура Д-5)" for k in teaser if k not in TEASER_KEYS
           and k not in ("deid_violations", "signed_by", "signed_at")]
    text = _teaser_text(teaser)
    low = text.lower()
    for tok, label in case_blacklist(job, cfg).items():
        if re.search(r"(?<!\w)" + re.escape(tok) + r"(?!\w)", low):
            bad.append(f"{label}: «{tok}»")
    for p in cfg["deid"]["patent_patterns"]:
        if re.search(p, text, flags=re.IGNORECASE):
            bad.append(f"номер патента ({p})")
    for w in cfg["deid"]["clinic_words"]:
        if w.lower() in low:
            bad.append(f"клиника («{w.strip()}»)")
    return bad + _deck_overlap(teaser, job, cfg)


# ------------------------------------------------------------- передача ----
def transfer_blockers(job: dict, store, kind: str, recipient: str, cfg: dict) -> list[str]:
    """Проверка перед КАЖДОЙ передачей: согласие, окно фонда, подпись человека."""
    out = []
    if not store.consent_active(job["id"], kind, recipient):
        out.append("нет действующего согласия основателя на эту передачу (или оно отозвано)")
    if job["data"].get("fund_timer"):
        fire_if_due(store, job, cfg)
    if recipient != cfg["fund_recipient"] and fund_window(job["data"], cfg)["state"] != "expired":
        out.append(f"окно фонда {cfg['fund_window_days']} дней не истекло — другим инвесторам не показываем")
    if kind == "teaser":
        t = job["data"].get("teaser") or {}
        if not t.get("signed_by"):
            out.append("тизер не подписан человеком")
        if t.get("deid_violations"):
            out.append("тизер не прошёл проверку обезличивания")
    if kind == "report" and not any(a["outcome"] == "sent" for a in store.approvals(job["id"])):
        out.append("полный отчёт не подписан на Human Gate")
    return out
