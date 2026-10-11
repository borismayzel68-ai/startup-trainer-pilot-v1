"""HQ-028: единый механизм ссылок с ограниченным сроком (HMAC-SHA256 + exp).
Используется для бесплатной части (HQ-020), страницы диалога/кабинета основателя и ссылок для инвесторов.
Подделка → 403, просрочка → 410, отзыв согласия → 403 (проверяет вызывающий код через check_extra)."""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def sign(secret: str, purpose: str, ident: str, exp: int) -> str:
    # формат «free» сохранён с HQ-020, чтобы уже выданные ссылки не сломались
    msg = f"{ident}.{exp}" if purpose == "free" else f"{purpose}.{ident}.{exp}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def make(secret: str, purpose: str, ident: str, ttl_s: int, prefix: str) -> dict:
    exp = now_ts() + ttl_s
    return {"path": f"{prefix}/{ident}?exp={exp}&sig={sign(secret, purpose, ident, exp)}", "expires": exp}


def verify(secret: str, purpose: str, ident: str, exp_raw: str, sig: str) -> tuple[int, str]:
    """→ (http-код, причина); 200 — ссылка действительна."""
    try:
        exp = int(exp_raw or "0")
    except ValueError:
        return 400, "bad link"
    if not secret or not hmac.compare_digest(sig or "", sign(secret, purpose, ident, exp)):
        return 403, "forbidden"
    if exp < now_ts():
        return 410, "link expired"
    return 200, ""
