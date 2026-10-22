"""Правила RAG (Д-68) — единственное место, где живут пороги и логика светофора S5.

Runway: красный < red_below, жёлтый red_below..yellow_upto включительно, зелёный > yellow_upto (по умолчанию 9 / 12 мес).
Пороги настраиваемые, каждый в диапазоне 6–18 мес, red_below < yellow_upto.
Отклонение burn от плана > 20% — отдельный флаг, цвет не меняет. Апдейт просрочен при > 14 дней без обновления.
Детерминированно: «сегодня» передаётся аргументом, часов и сети нет.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

RUNWAY_RED_BELOW = Decimal("9")
RUNWAY_YELLOW_UPTO = Decimal("12")
THRESHOLD_MIN = Decimal("6")
THRESHOLD_MAX = Decimal("18")
BURN_DEVIATION_LIMIT = Decimal("0.20")   # строго больше → флаг
UPDATE_STALE_DAYS = 14                   # строго больше → флаг (14 дней — ещё не просрочено)

RED, YELLOW, GREEN, UNKNOWN = "RED", "YELLOW", "GREEN", "UNKNOWN"


class InvalidThresholds(ValueError):
    pass


@dataclass(frozen=True)
class Thresholds:
    red_below: Decimal = RUNWAY_RED_BELOW
    yellow_upto: Decimal = RUNWAY_YELLOW_UPTO

    def __post_init__(self):
        object.__setattr__(self, "red_below", Decimal(str(self.red_below)))
        object.__setattr__(self, "yellow_upto", Decimal(str(self.yellow_upto)))
        for name, v in (("red_below", self.red_below), ("yellow_upto", self.yellow_upto)):
            if not THRESHOLD_MIN <= v <= THRESHOLD_MAX:
                raise InvalidThresholds(f"{name}={v} вне диапазона {THRESHOLD_MIN}–{THRESHOLD_MAX} мес")
        if self.red_below >= self.yellow_upto:
            raise InvalidThresholds("red_below должен быть меньше yellow_upto")


DEFAULT_THRESHOLDS = Thresholds()


@dataclass(frozen=True)
class RagResult:
    color: str
    color_reason: str
    burn_flag: bool
    burn_deviation: Optional[Decimal]
    burn_reason: str
    update_overdue: bool
    update_days: Optional[int]
    update_reason: str

    def explanation(self) -> list[str]:
        return [self.color_reason, self.burn_reason, self.update_reason]


def runway_color(runway_months, th: Thresholds = DEFAULT_THRESHOLDS) -> tuple[str, str]:
    if runway_months is None:
        return UNKNOWN, "Runway не указан — цвет не определён"
    r = Decimal(str(runway_months))
    if r < th.red_below:
        return RED, f"Runway {r} мес < {th.red_below} мес → красный"
    if r <= th.yellow_upto:
        return YELLOW, f"Runway {r} мес в диапазоне {th.red_below}–{th.yellow_upto} мес → жёлтый"
    return GREEN, f"Runway {r} мес > {th.yellow_upto} мес → зелёный"


def burn_deviation(burn, plan_burn) -> Optional[Decimal]:
    """|burn − plan| / plan; None, если план не задан или равен нулю."""
    if burn is None or plan_burn is None or Decimal(str(plan_burn)) == 0:
        return None
    b, p = Decimal(str(burn)), Decimal(str(plan_burn))
    return abs(b - p) / p


def evaluate(runway_months, burn, plan_burn, last_update: Optional[date], today: date,
             th: Thresholds = DEFAULT_THRESHOLDS) -> RagResult:
    color, color_reason = runway_color(runway_months, th)
    dev = burn_deviation(burn, plan_burn)
    if dev is None:
        burn_flag, burn_reason = False, "Отклонение burn не оценено: нет burn или plan_burn"
    else:
        burn_flag = dev > BURN_DEVIATION_LIMIT
        lim = BURN_DEVIATION_LIMIT * 100
        burn_reason = (f"Burn отклоняется от плана на {dev * 100:.1f}% "
                       + (f"(> {lim:.0f}%) → флаг" if burn_flag else f"(≤ {lim:.0f}%) → нет флага"))
    if last_update is None:
        days, overdue = None, True
        update_reason = "Апдейт инвестору ни разу не был — просрочка"
    else:
        days = (today - last_update).days
        overdue = days > UPDATE_STALE_DAYS
        update_reason = (f"Последний апдейт {days} дн. назад "
                         + (f"(> {UPDATE_STALE_DAYS}) → просрочка" if overdue else f"(≤ {UPDATE_STALE_DAYS}) → в срок"))
    return RagResult(color, color_reason, burn_flag, dev, burn_reason, overdue, days, update_reason)
