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


def compute_readiness(company_profile: dict, qa_log: list) -> dict:
    """
    Вход: company_profile, qa_log.
    Выход: {"scores": {dimension: 0..5}, "overall": float, "recommendation": Recommendation}.
    ПРАВИЛО (NORM-07, обязательно к сохранению при реализации):
        - каждый балл требует 1-2 цитаты из materials/qa_log;
        - нет цитаты -> балл не может быть > 0 ("не подтверждено");
        - recommendation только из трёх значений выше, не текст произвольной формы.
    """
    raise NotImplementedError("compute_readiness: тело реализуется в S1")
