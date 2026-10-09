"""
Запуск графа: start_session / answer / export_report (CLI-контракт ТЗ).

graph.py не меняется: компиляция и human interrupt — здесь.
  compile(interrupt_after=["ask"]) — после вопроса граф стоит, пока человек
  не ответит (решение штаба 2026-08-23 п.4).
  Изоляция сессий — thread_id = session_id.

Чекпоинтер: MemorySaver — только для разработки, не переживает рестарт.
Прод: SqliteSaver / PostgresSaver из langgraph-checkpoint-* с тем же thread_id.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from trainer.graph import build_graph
from trainer.state import initial_state


def compile_app(checkpointer=None):
    return build_graph().compile(
        checkpointer=checkpointer or MemorySaver(),
        interrupt_after=["ask"],
    )


def _config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def view(app, session_id: str) -> dict[str, Any]:
    snap = app.get_state(_config(session_id))
    s = snap.values
    done = not snap.next
    return {
        "session_id": session_id,
        "done": done,
        "turn": s.get("turn", 0),
        "question": None if done else (s.get("current_question") or {}).get("display"),
        "last_analysis": s.get("last_analysis"),
        "last_coach": s.get("last_coach"),
        "readiness": s.get("readiness"),
        "stop_reason": s.get("stop_reason"),
        "report_md": s.get("report_md") if done else None,
    }


def start_session(app, session_id: str, materials: list[dict], max_turns: int = 12) -> dict:
    state = initial_state(session_id, max_turns=max_turns)
    state["materials"] = materials
    app.invoke(state, _config(session_id))
    return view(app, session_id)


def answer(app, session_id: str, text: str) -> dict:
    config = _config(session_id)
    if not app.get_state(config).next:
        raise RuntimeError("сессия завершена или не начата")
    app.update_state(config, {"founder_message": text})
    app.invoke(None, config)
    return view(app, session_id)


def export_report(app, session_id: str, out_dir: str = "out") -> tuple[Path, Path]:
    s = app.get_state(_config(session_id)).values
    if not s.get("report_md"):
        raise RuntimeError("отчёт ещё не сформирован (сессия не остановлена)")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md, js = out / "session_report.md", out / "session_report.json"
    md.write_text(s["report_md"], encoding="utf-8")
    js.write_text(json.dumps(s["report_json"], ensure_ascii=False, indent=2), encoding="utf-8")
    return md, js
