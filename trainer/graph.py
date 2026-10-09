"""
Сборка графа. S0.5: структура рёбер зафиксирована, .compile()/.invoke()
не вызывается — это часть S1 (python -m trainer.demo).

Комментарий по чекпоинтеру (не реализуется в S0.5):
    из langgraph.checkpoint.memory import MemorySaver
    graph = builder.compile(checkpointer=MemorySaver())
    # Прод: заменить на Postgres/SQLite checkpointer, MemorySaver только
    # для локальной разработки — состояние не переживает рестарт процесса.
"""

from langgraph.graph import StateGraph, START, END

from trainer.state import TrainerState
from trainer.nodes import (
    ingest, extract_profile, plan_next_question, ask,
    route_intent, route_after_intent, analyze_answer, coach,
    update_readiness, decide, route_after_decide, summarize,
)


def build_graph() -> StateGraph:
    builder = StateGraph(TrainerState)

    builder.add_node("ingest", ingest)
    builder.add_node("extract_profile", extract_profile)
    builder.add_node("plan_next_question", plan_next_question)
    builder.add_node("ask", ask)
    builder.add_node("route_intent", route_intent)
    builder.add_node("analyze_answer", analyze_answer)
    builder.add_node("coach", coach)
    builder.add_node("update_readiness", update_readiness)
    builder.add_node("decide", decide)
    builder.add_node("summarize", summarize)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "extract_profile")
    builder.add_edge("extract_profile", "plan_next_question")
    builder.add_edge("plan_next_question", "ask")
    builder.add_edge("ask", "route_intent")

    # Условное ребро №1 — после route_intent (NORM-09: stop безусловен)
    builder.add_conditional_edges(
        "route_intent",
        route_after_intent,
        {
            "answer": "analyze_answer",
            "stop": "summarize",
            "upload_note": "ingest",
            "ask_help": "ask",
        },
    )

    builder.add_edge("analyze_answer", "coach")
    builder.add_edge("coach", "update_readiness")
    builder.add_edge("update_readiness", "decide")

    # Условное ребро №2 — после decide
    builder.add_conditional_edges(
        "decide",
        route_after_decide,
        {
            "continue": "plan_next_question",
            "stop": "summarize",
        },
    )

    builder.add_edge("summarize", END)

    return builder  # .compile() — в S1
