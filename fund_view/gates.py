"""MOS-5: три шлюза Operating Manual (чистая логика, без БД).
Thesis-first: нет тезиса → нет deep work. Buyer-first: кто и когда купит. Engagement gate: участие ASF зафиксировано.
Не пройден шлюз → переход в DD/IC запрещён (проверка в dealflow.status.change_status, т.е. на сервере)."""
from __future__ import annotations

GATED_STATUSES = frozenset({"DD", "IC"})
GATE_NAMES = ("thesis", "buyer", "engagement")


class GateBlocked(ValueError):
    def __init__(self, failed: list[str]):
        self.failed = failed
        super().__init__("переход заблокирован шлюзами: " + ", ".join(failed))


def evaluate(snapshot: dict | None) -> dict[str, bool]:
    s = snapshot or {}
    filled = lambda k: bool((s.get(k) or "").strip())  # noqa: E731
    return {
        "thesis": filled("thesis"),
        "buyer": filled("buyer") and filled("buyer_when"),
        "engagement": s.get("asf_engagement") == "FIXED",
    }


def failed_gates(snapshot: dict | None) -> list[str]:
    res = evaluate(snapshot)
    return [g for g in GATE_NAMES if not res[g]]


def check_transition(to_status: str, snapshot: dict | None) -> None:
    if to_status in GATED_STATUSES:
        failed = failed_gates(snapshot)
        if failed:
            raise GateBlocked(failed)
