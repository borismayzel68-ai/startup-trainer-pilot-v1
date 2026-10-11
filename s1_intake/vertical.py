"""HQ-020 · вертикаль LS/HT (решение PM 11.10): in → оценка; out → out_of_scope без оценки;
пограничное → ручная проверка (manual_review), никогда не автоматический отказ."""
from __future__ import annotations

from .models import Submission


def classify_vertical(sub: Submission, m: dict) -> tuple[str, str]:
    v = m["vertical_s1"]
    pc = (sub.product_class or "").strip().upper()
    text = f"{sub.intended_use} " + " ".join(a.value for a in sub.answers)
    text = text.lower()
    clinical = any(w in text for w in v["clinical_words"])
    out_hit = any(w in text for w in v["out_words"])
    if pc in v["in_classes"]:
        return "in", f"класс продукта {pc}"
    if pc in v["out_classes"]:
        return ("manual_review", f"{pc}, но есть клинические формулировки") if clinical else \
               ("out", f"{pc} без клинических заявлений")
    if out_hit and not clinical:
        return "out", "общий IT / SaaS / финтех / e-commerce / wellness без медицинского применения"
    return "manual_review", f"класс продукта «{pc or 'не указан'}» — пограничный случай"
