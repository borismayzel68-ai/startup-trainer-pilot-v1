"""HQ-042 S5 (Д-59 / D-013): сверка обновления с паспортом входа, триггер частичного выхода,
вопросы компании. Чистая логика в памяти, без сети и без отправки в мессенджер.

Точка подключения: паспорт портфеля (MOS-4, PR #80) в master не влит. Когда влит — собрать
EntryPassport из его полей (EntryPassport.fields: имя поля -> значение при входе)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
# Роли фонда — из db/migrations/001_roles_access.sql. NEEDS_EVIDENCE: входит ли 'signer' — вопрос в HANDOFF.md.
FUND_ROLES = frozenset({"investor"})


class AccessDenied(PermissionError):
    pass


def require_fund_role(role: Optional[str]) -> None:
    if role not in FUND_ROLES:
        raise AccessDenied(f"роль {role!r} не имеет доступа (только роли фонда)")


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


def reconcile(passport: EntryPassport, update: dict[str, Any], *, role: str) -> list[Diff]:
    """Сравнивает поля обновления с паспортом входа; возвращает список расхождений."""
    require_fund_role(role)
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
    id: int
    company_id: str
    text: str
    answer: Optional[str] = None


@dataclass
class PortfolioUpdates:
    flags: list[ExitCheckFlag] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)

    def mark_new_round(self, company_id: str, round_label: str, *, role: str) -> ExitCheckFlag:
        """Новый раунд -> флаг проверки частичного выхода (идемпотентно по компании+раунду)."""
        require_fund_role(role)
        for f in self.flags:
            if f.company_id == company_id and f.round_label == round_label:
                return f
        f = ExitCheckFlag(company_id, round_label)
        self.flags.append(f)
        return f

    def ask(self, company_id: str, text: str, *, role: str) -> Question:
        """Только модель вопроса; во внешний мессенджер ничего не отправляется."""
        require_fund_role(role)
        if not text.strip():
            raise ValueError("пустой вопрос")
        q = Question(len(self.questions) + 1, company_id, text)
        self.questions.append(q)
        return q

    def answer(self, question_id: int, text: str, *, role: str) -> Question:
        require_fund_role(role)
        q = next((q for q in self.questions if q.id == question_id), None)
        if q is None:
            raise LookupError(f"вопрос {question_id} не найден")
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
