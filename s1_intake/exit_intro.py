"""HQ-028 · Выход S1: журнал согласий с отзывом, тизер с проверкой обезличивания, таймер фонда 21 день,
передачи только через G-4. Ничего наружу не отправляется: «передача» = запись в журнале + ссылка,
которую владелец передаёт сам."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- таймер ----
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
def build_teaser(job: dict, cfg: dict) -> dict:
    """Д-5: область, стадия, сколько привлекают и на что, по 3 сильных стороны и риска со статусами «факт»/
    «заявление», решение фонда. Без финмодели, патентов и клиник. Названия и имён нет."""
    data = job["data"]
    brief = data["readiness_pack"]["internal_analyst_brief"]
    cls = brief["classification"]
    cat = {c["id"]: c["category"] for c in cls["claims"]}
    status = lambda ids: "факт" if ids and all(cat.get(i) == "FACT" for i in ids) else "заявление"  # noqa: E731
    n = cfg["teaser_items"]
    strengths = [{"text": a["point"], "status": status(a.get("claim_ids", []))}
                 for a in brief["bull"]["arguments"][:n]]
    risks = [{"text": a["point"], "status": status(a.get("claim_ids", []))} for a in brief["bear"]["arguments"][:n]]
    ls = brief["scores"].get("ls") or {}
    answers = {a["field"]: a["value"] for a in brief["submission"].get("answers", [])}
    return {"area": cls.get("sector") or "", "stage": ls.get("stage_label") or cls.get("stage") or "",
            "raise": answers.get("q_round", ""), "strengths": strengths, "risks": risks,
            "fund_decision": None, "disclaimer": cfg["disclaimer"]}


def _teaser_text(t: dict) -> str:
    parts = [t["area"], t["stage"], t["raise"]] + [x["text"] for x in t["strengths"] + t["risks"]]
    return "\n".join(str(p) for p in parts) + "\n" + str(t.get("fund_decision") or "")


def deid_check(teaser: dict, job: dict, cfg: dict) -> list[str]:
    """Проверка обезличивания: нет названия, имён, номеров патентов, клиник. Пусто = прошла."""
    text = _teaser_text(teaser)
    low = text.lower()
    sub = job["data"]["submission"]
    bad = []
    for label, value in (("название компании", sub.get("project_name") or job.get("project_name")),
                         ("имя основателя", sub.get("founder_name"))):
        for token in [value] + (value or "").split():
            if token and len(token) >= 3 and token.lower() in low:
                bad.append(f"{label}: «{token}»")
                break
    email = sub.get("founder_email") or ""
    if email and email.lower() in low:
        bad.append("e-mail основателя")
    for p in cfg["deid"]["patent_patterns"]:
        if re.search(p, text, flags=re.IGNORECASE):
            bad.append(f"номер патента ({p})")
    for w in cfg["deid"]["clinic_words"]:
        if w.lower() in low:
            bad.append(f"клиника («{w.strip()}»)")
    return bad


# ------------------------------------------------------------- передача ----
def transfer_blockers(job: dict, store, kind: str, recipient: str, cfg: dict) -> list[str]:
    """Проверка перед КАЖДОЙ передачей: согласие, окно фонда, подпись человека."""
    out = []
    if not store.consent_active(job["id"], kind, recipient):
        out.append("нет действующего согласия основателя на эту передачу (или оно отозвано)")
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
