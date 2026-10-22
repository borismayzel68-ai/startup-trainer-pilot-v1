"""S2 · шлюз мандата фонда: чистая логика. Правила — из конфига investment_policy.yaml, не из кода.
Результат: FIT / NO_FIT / EXCEPTION. EXCEPTION сам себя не разрешает — решает человек (Human Gate, blueprint)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml

FIT, NO_FIT, EXCEPTION = "FIT", "NO_FIT", "EXCEPTION"
PKG = Path(__file__).resolve().parent
FIELDS = ("check_rub", "instrument", "sector", "innovative")
KIND = {"preferred": FIT, "exception": EXCEPTION, "no_fit": NO_FIT}


def load_policy(path: str | None = None) -> dict:
    p = Path(path or os.environ.get("S2_POLICY_PATH") or PKG / "investment_policy.yaml")
    with open(p, encoding="utf-8") as f:
        policy = yaml.safe_load(f)
    for k in ("check_rub", "instruments", "sectors"):
        if k not in policy:
            raise ValueError("investment_policy: нет раздела " + k)
    return policy


def validate_input(app: dict) -> list[str]:
    bad = [k for k in FIELDS if app.get(k) in (None, "")]
    v = app.get("check_rub")
    if "check_rub" not in bad and (isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0):
        bad.append("check_rub")
    if "innovative" not in bad and not isinstance(app.get("innovative"), bool):
        bad.append("innovative")
    return bad


def evaluate(app: dict, policy: dict) -> dict:
    """NO_FIT сильнее EXCEPTION, EXCEPTION сильнее FIT."""
    lo, hi = policy["check_rub"]["min"], policy["check_rub"]["max"]
    chk, instrument, sector = app["check_rub"], app["instrument"], app["sector"]
    kind = policy["instruments"].get(instrument, policy.get("unknown_instrument", "exception"))
    sector_ok = sector in policy["sectors"]
    innov_ok = app["innovative"] or not policy.get("require_innovative", True)
    reasons = [
        {"rule": "check", "verdict": FIT if lo <= chk <= hi else NO_FIT,
         "note": f"чек {chk:g} ₽, мандат {lo}–{hi} ₽"},
        {"rule": "instrument", "verdict": KIND.get(kind, EXCEPTION), "note": f"инструмент {instrument}: {kind}"},
        {"rule": "sector", "verdict": FIT if sector_ok and innov_ok else NO_FIT,
         "note": f"сектор {sector}, инновационный={app['innovative']}"},
    ]
    verdicts = {r["verdict"] for r in reasons}
    result = NO_FIT if NO_FIT in verdicts else EXCEPTION if EXCEPTION in verdicts else FIT
    return {"result": result, "reasons": reasons, "policy_status": policy.get("status", "")}


def sha256(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
