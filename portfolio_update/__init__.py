"""HQ-042 S5 (Д-59 / D-013): сверка обновления с паспортом входа, триггер частичного выхода,
вопросы компании. Чистая логика в памяти, без сети и без отправки в мессенджер.

Точка подключения: паспорт портфеля (MOS-4, PR #80) в master не влит. Когда влит — собрать
EntryPassport из его полей (EntryPassport.fields: имя поля -> значение при входе)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from portfolio.access import Actor, can_list, can_view

NEEDS_EVIDENCE = "NEEDS_EVIDENCE"


class AccessDenied(PermissionError):
    pass


def require_access(actor: Optional[Actor], company_id: str) -> None:
    """Доступ как в portfolio/access.py (MOS-1/MOS-4): owner/fund_staff — любая компания,
    investor — только компании из своего scope; прочие роли и None — отказ."""
    if not can_view(actor, company_id):
        role = actor.role if actor is not None else None
        raise AccessDenied(f"роль {role!r} не имеет доступа к компании {company_id!r}")


@dataclass(frozen=True)
class EntryPassport:
    company_id: str
    fields: dict[str, Any]  # зафиксировано при входе; состав полей не выдумываем


@dataclass(frozen=True)
class Diff:
    field: str
    entry_value: Any
    update_value: Any
    kind: str  # changed | missing_in_update | not_in_passport


def reconcile(passport: EntryPassport, update: dict[str, Any], *, actor: Optional[Actor]) -> list[Diff]:
    """Сравнивает поля обновления с паспортом входа; возвращает список расхождений."""
    require_access(actor, passport.company_id)
    diffs: list[Diff] = []
    for k, v in passport.fields.items():
        if k not in update:
            diffs.append(Diff(k, v, None, "missing_in_update"))
        elif update[k] != v:
            diffs.append(Diff(k, v, update[k], "changed"))
    for k, v in update.items():
        if k not in passport.fields:
            diffs.append(Diff(k, NEEDS_EVIDENCE, v, "not_in_passport"))
    return diffs


@dataclass
class ExitCheckFlag:
    company_id: str
    round_label: str
    status: str = "OPEN"
    # Правила частичного выхода в задаче не заданы — не выдумываем.
    rules: str = NEEDS_EVIDENCE


@dataclass
class Question:
    id: str
    company_id: str
    text: str
    answer: Optional[str] = None


@dataclass
class PortfolioUpdates:
    flags: list[ExitCheckFlag] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)

    def mark_new_round(self, company_id: str, round_label: str, *, actor: Optional[Actor]) -> ExitCheckFlag:
        """Новый раунд -> флаг проверки частичного выхода (идемпотентно по компании+раунду)."""
        require_access(actor, company_id)
        for f in self.flags:
            if f.company_id == company_id and f.round_label == round_label:
                return f
        f = ExitCheckFlag(company_id, round_label)
        self.flags.append(f)
        return f

    def ask(self, company_id: str, text: str, *, actor: Optional[Actor]) -> Question:
        """Только модель вопроса; во внешний мессенджер ничего не отправляется."""
        require_access(actor, company_id)
        if not text.strip():
            raise ValueError("пустой вопрос")
        q = Question(uuid.uuid4().hex, company_id, text)
        self.questions.append(q)
        return q

    def answer(self, question_id: str, text: str, *, actor: Optional[Actor]) -> Question:
        if not can_list(actor):  # до поиска: не раскрываем наличие вопроса посторонним ролям
            raise AccessDenied(f"роль {actor.role if actor else None!r} не имеет доступа")
        q = next((q for q in self.questions if q.id == question_id), None)
        if q is None:
            raise LookupError(f"вопрос {question_id} не найден")
        require_access(actor, q.company_id)
        if q.answer is not None:
            raise ValueError("на вопрос уже есть ответ")
        if not text.strip():
            raise ValueError("пустой ответ")
        q.answer = text
        return q


def render_report(passport: EntryPassport, diffs: list[Diff]) -> str:
    lines = [f"# Сверка обновления с паспортом входа — {passport.company_id}", ""]
    if not diffs:
        lines.append("Расхождений нет.")
    for d in diffs:
        lines.append(f"- `{d.field}` [{d.kind}]: вход={d.entry_value!r}, обновление={d.update_value!r}")
    return "\n".join(lines) + "\n"
