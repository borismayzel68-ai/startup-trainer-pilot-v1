"""
Веса и шкала измерений — канон v1 (NORM-07). Константы, не логика:
арифметика агрегации (compute_readiness) — тело S1, заглушка ниже.

ЗАМОРОЗКА (PROMPT v2, штаб 2026-08-23): веса rubric v1 не менять, новые
измерения не добавлять. team_and_execution = 0.12 зафиксирован отдельно
NORM-10 ("вес Team = 0.12, не менять") — не перевзвешивать даже если
источник (Gompers et al. 2020) появится в корпусе как полноценная карточка.
"""

from typing import Literal

Recommendation = Literal["not_ready", "iterate", "ready_for_investor_conversation"]

DIMENSION_WEIGHTS: dict[str, float] = {
    "problem_product_clarity": 0.14,
    "evidence": 0.16,
    "market_and_access": 0.12,
    "model_and_economics": 0.12,
    "team_and_execution": 0.12,  # NORM-10: заблокировано, не менять
    "risks_reg_ip_clinical_commercial": 0.14,
    "materials_quality": 0.10,
    "founder_answers_quality": 0.10,
}

assert abs(sum(DIMENSION_WEIGHTS.values()) - 1.0) < 1e-9, "веса канона должны суммироваться в 1.0"

SCALE_LABELS: dict[int, str] = {
    0: "нет сигнала",
    1: "лозунг",
    2: "гипотеза без опоры",
    3: "частичные / внутренние данные",
    4: "внешние данные или сильный протокол",
    5: "воспроизводимые данные + понятный следующий шаг",
}


RECOMMENDATIONS: tuple[str, ...] = ("not_ready", "iterate", "ready_for_investor_conversation")

# GAP-01 (решение штаба 2026-10-09): в каноне нет сопоставления слотов
# профиля с 8 измерениями. Пока его нет, ни один слот не даёт балла
# измерению — все баллы 0 «не подтверждено». Не заполнять без решения штаба.
DIMENSION_EVIDENCE_SOURCES: dict[str, list[str]] = {d: [] for d in DIMENSION_WEIGHTS}

# GAP-02: пороги recommendation штабом не утверждены. До решения —
# заглушка not_ready + проверка, что значение из трёх допустимых.
RECOMMENDATION_PLACEHOLDER: Recommendation = "not_ready"


def validate_recommendation(value: str) -> str:
    if value not in RECOMMENDATIONS:
        raise ValueError(f"recommendation вне канона: {value!r}")
    return value


def weighted_overall(scores: dict[str, float]) -> float:
    """Взвешенная сумма по замороженным весам, шкала 0..5. Требует все 8 измерений."""
    if set(scores) != set(DIMENSION_WEIGHTS):
        raise ValueError("scores должны покрывать ровно 8 измерений rubric v1")
    for dim, val in scores.items():
        if not 0 <= val <= 5:
            raise ValueError(f"балл {dim}={val} вне шкалы 0..5")
    return round(sum(DIMENSION_WEIGHTS[d] * scores[d] for d in DIMENSION_WEIGHTS), 4)


def _quotes_for(dimension: str, company_profile: dict) -> list[str]:
    quotes = []
    for slot_id in DIMENSION_EVIDENCE_SOURCES[dimension]:
        span = (company_profile.get(slot_id) or {}).get("evidence_span")
        if span:
            quotes.append(span)
    return quotes[:2]


def compute_readiness(company_profile: dict, qa_log: list) -> dict:
    """
    Вход: company_profile, qa_log.
    Выход: {"scores": {dimension: 0..5}, "overall": float, "recommendation": Recommendation}.
    ПРАВИЛО (NORM-07, обязательно к сохранению при реализации):
        - каждый балл требует 1-2 цитаты из materials/qa_log;
        - нет цитаты -> балл не может быть > 0 ("не подтверждено");
        - recommendation только из трёх значений выше, не текст произвольной формы.

    Реализация до закрытия GAP-01/GAP-02: балл > 0 возможен только при
    наличии цитат из источников измерения; источников пока нет -> 0.
    """
    scores: dict[str, float] = {}
    quotes: dict[str, list[str]] = {}
    for dim in DIMENSION_WEIGHTS:
        q = _quotes_for(dim, company_profile)
        quotes[dim] = q or ["не подтверждено"]
        scores[dim] = 0  # балл по цитатам не считается, пока нет утверждённого маппинга
    return {
        "scores": scores,
        "overall": weighted_overall(scores),
        "recommendation": validate_recommendation(RECOMMENDATION_PLACEHOLDER),
        "quotes": quotes,
        "weights": dict(DIMENSION_WEIGHTS),
        "open_gaps": ["GAP-01: нет маппинга слот->измерение", "GAP-02: нет порогов recommendation"],
        "answered_turns": sum(1 for e in qa_log or [] if (e.get("analysis") or {}).get("status") != "missing"),
    }
