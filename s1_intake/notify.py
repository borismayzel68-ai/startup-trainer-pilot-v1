"""Доставка: email (SMTP) и опциональный webhook-пинг (Telegram-бот / n8n / Make)."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from .config import Settings

log = logging.getLogger(__name__)


def smtp_configured(s: Settings) -> bool:
    return bool(s.smtp_host and s.smtp_from)


def send_email(s: Settings, to: list[str], subject: str, html: str,
               attachments: list[tuple[str, bytes]] = ()) -> None:
    """attachments: [(filename, bytes)] — .pdf или .html."""
    if not smtp_configured(s):
        raise RuntimeError("SMTP not configured")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, ", ".join(to), subject
    msg.set_content("Откройте письмо в HTML-режиме или см. вложение.")
    msg.add_alternative(html, subtype="html")
    for name, data in attachments:
        is_pdf = name.endswith(".pdf")
        msg.add_attachment(data, maintype="application" if is_pdf else "text",
                           subtype="pdf" if is_pdf else "html", filename=name)
    ctx = ssl.create_default_context()
    if s.smtp_port == 465:
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ctx, timeout=30) as smtp:
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
            smtp.starttls(context=ctx)
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)


def ping_webhook(s: Settings, payload: dict) -> None:
    """Только метаданные (без содержимого отчётов) — канал может быть сторонним."""
    if not s.notify_webhook_url:
        return
    try:
        httpx.post(s.notify_webhook_url, json=payload, timeout=10)
    except httpx.HTTPError as e:
        log.warning("notify webhook failed: %s", e)
