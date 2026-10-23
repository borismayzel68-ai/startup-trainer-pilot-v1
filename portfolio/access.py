"""Доступ к портфелю. owner / fund_staff — весь портфель; investor — только компании из своего scope (vc_id или '*').
founder и прочие роли — нет доступа (чужие компании не видит; собственный кабинет — не этот раздел)."""
from __future__ import annotations

from dataclasses import dataclass, field

FULL_ROLES = frozenset({"owner", "fund_staff"})
LIST_ROLES = FULL_ROLES | {"investor"}


@dataclass(frozen=True)
class Actor:
    role: str
    scopes: frozenset = field(default_factory=frozenset)


def can_list(actor: Actor | None) -> bool:
    return actor is not None and actor.role in LIST_ROLES


def can_view(actor: Actor | None, vc_id: str) -> bool:
    if not can_list(actor):
        return False
    if actor.role in FULL_ROLES:
        return True
    return "*" in actor.scopes or vc_id in actor.scopes
