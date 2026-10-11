"""Настройки S1-интейка (только ENV, без секретов в коде) и сборка чек-листа
из канона репозитория (routing_v2 + question_engine_v2.SLOT_CATALOG)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from question_engine_v2 import SLOT_CATALOG
from routing_v2 import norm_jurisdiction, norm_product_class, parse_target_markets, route

PKG = Path(__file__).resolve().parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, str(default)).lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str = "") -> list[str]:
    return [x.strip().lower() for x in _env(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    # --- вебхук Tilda / доступ аналитика ---
    tilda_webhook_key_name: str = field(default_factory=lambda: _env("S1_TILDA_WEBHOOK_KEY_NAME", "X-Webhook-Key"))
    tilda_webhook_key: str = field(default_factory=lambda: _env("S1_TILDA_WEBHOOK_KEY"))
    admin_token: str = field(default_factory=lambda: _env("S1_ADMIN_TOKEN"))

    # --- LLM (те же переменные, что у тренажёра) ---
    llm_provider: str = field(default_factory=lambda: _env("S1_LLM_PROVIDER", "openai"))  # openai | mock
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL"))
    openai_model: str = field(default_factory=lambda: _env("S1_OPENAI_MODEL") or _env("OPENAI_MODEL", "gpt-5.6"))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("S1_LLM_TIMEOUT_S", "180")))

    # --- файлы ---
    file_host_allowlist: list[str] = field(
        default_factory=lambda: _env_list("S1_FILE_HOST_ALLOWLIST", "ucarecdn.com,tupwidget.com,tildacdn.com")
    )
    max_file_mb: int = field(default_factory=lambda: int(_env("S1_MAX_FILE_MB", "25")))
    max_doc_chars: int = field(default_factory=lambda: int(_env("S1_MAX_DOC_CHARS", "60000")))

    # --- хранение (та же БД, что у тренажёра; отдельная таблица) ---
    database_url: str = field(
        default_factory=lambda: _env("SESSION_DATABASE_URL") or _env("DATABASE_URL") or "sqlite:///trainer_sessions.db"
    )
    retention_days: int = field(default_factory=lambda: int(_env("S1_RETENTION_DAYS", "90")))
    methodology_path: Path = field(default_factory=lambda: Path(_env("S1_METHODOLOGY_PATH", str(PKG / "methodology.yaml"))))
    prompts_dir: Path = field(default_factory=lambda: Path(_env("S1_PROMPTS_DIR", str(PKG / "prompts"))))

    # --- доставка ---
    auto_send_founder_report: bool = field(default_factory=lambda: _env_bool("S1_AUTO_SEND_FOUNDER_REPORT", False))
    analyst_emails: list[str] = field(default_factory=lambda: _env_list("S1_ANALYST_EMAILS"))
    smtp_host: str = field(default_factory=lambda: _env("S1_SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(_env("S1_SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: _env("S1_SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _env("S1_SMTP_PASSWORD"))
    smtp_from: str = field(default_factory=lambda: _env("S1_SMTP_FROM"))
    notify_webhook_url: str = field(default_factory=lambda: _env("S1_NOTIFY_WEBHOOK_URL"))
    fund_name: str = field(default_factory=lambda: _env("S1_FUND_NAME", "Artgen Seed Fund"))

    def problems(self) -> list[str]:
        """Конфигурация, при которой интейк небезопасно включать."""
        out = []
        if not self.tilda_webhook_key:
            out.append("S1_TILDA_WEBHOOK_KEY is empty — webhook would accept anyone")
        if len(self.admin_token) < 24:
            out.append("S1_ADMIN_TOKEN missing or shorter than 24 chars")
        if self.llm_provider == "openai" and not self.openai_api_key:
            out.append("OPENAI_API_KEY is empty")
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_methodology(path: str | None = None) -> dict:
    p = Path(path) if path else get_settings().methodology_path
    with open(p, encoding="utf-8") as f:
        m = yaml.safe_load(f)
    _check(m)
    return m


def _check(m: dict) -> None:
    if abs(sum(b["weight"] for b in m["blocks"]) - 1.0) > 1e-6:
        raise ValueError("Block weights must sum to 1.0")
    known = set(SLOT_CATALOG) | set(m.get("extra_modules", {})) | set(m.get("ls_milestones", {}))
    mapped = [mod for b in m["blocks"] for mod in b["modules"]]
    unknown = set(mapped) - known
    if unknown:
        raise ValueError(f"Unknown modules in blocks: {sorted(unknown)}")
    if len(mapped) != len(set(mapped)):
        raise ValueError("Module mapped to more than one block")
    unmapped = known - set(mapped)
    if unmapped:  # новый модуль в SLOT_CATALOG должен попасть в отчёт, а не потеряться
        raise ValueError(f"Modules not mapped to any block: {sorted(unmapped)}")
    keys = [b["key"] for b in m["blocks"]]
    for q in m["form"]["questions"]:
        if q["block"] not in keys:
            raise ValueError(f"Question {q['name']} refers to unknown block {q['block']}")


def routing_for(jurisdiction: str, target_markets, product_class: str, intended_use: str) -> dict:
    """Маршрутизация тренажёра. Невалидные/пустые поля -> только общие модули + флаг."""
    j = norm_jurisdiction(jurisdiction)
    pc = norm_product_class(product_class)
    tm = parse_target_markets(target_markets) if j == "MULTI" else []
    if not j or not pc:
        from routing_v2 import COMMON
        return {"jurisdiction": j, "product_class": pc, "target_markets": tm, "modules": list(COMMON),
                "routing_complete": False}
    modules, _reasons, check, markets = route(j, tm, intended_use, pc)
    return {"jurisdiction": j, "product_class": pc, "target_markets": tm, "modules": modules,
            "active_markets": markets, "classification_check_required": check, "routing_complete": True}


def build_checklist(m: dict, modules: list[str], ls_milestones: bool = False) -> dict[str, dict]:
    """{'module.slot': {label, why, critical, module, block, block_label}} для выбранных модулей
    + всегда доп. модули готовности к раунду. Порядок — порядок блоков.
    ls_milestones=True (HQ-016): добавить LS-вехи К1–К6 вместо IT-слотов N/A по стадии."""
    block_of = {mod: b for b in m["blocks"] for mod in b["modules"]}
    critical = set(m.get("critical_slots", []))
    labels = m.get("slot_labels", {})
    ls_mods = m.get("ls_milestones", {}) if ls_milestones else {}
    active = list(dict.fromkeys(list(modules) + list(m.get("extra_modules", {})) + list(ls_mods)))

    rows: dict[str, dict] = {}
    for b in m["blocks"]:
        for mod in b["modules"]:
            if mod not in active:
                continue
            specs = SLOT_CATALOG.get(mod) or m.get("extra_modules", {}).get(mod) or ls_mods.get(mod, [])
            for spec in specs:
                key = f"{mod}.{spec['slot']}"
                rows[key] = {
                    "label": labels.get(key) or spec.get("why", key),
                    "why": spec.get("why", ""),
                    "critical": key in critical,
                    "module": mod,
                    "block": b["key"],
                    "block_label": b["label"],
                }
                if mod in ls_mods:
                    rows[key].update(label=spec["label"], ls_k=spec["k"], next_step=spec["next_step"])
    return rows
