"""
KnowledgeBase.retrieve(query, k) — keyword-поиск по markdown-карточкам
в ./knowledge (рекурсивно, включая 10_cards/). Без embeddings, без
Google Drive, без сети. Единственный файл S0.5 с рабочим телом.

v0.2 (после PROMPT_CLAUDE_STARTUP_TRAINER (2) + 00_INDEX.md):
- Схема карточки §7.1 расширена (authors, year, stage_fit, product_class,
  ingest_date). Реальные карточки NORM-01..12, выданные штабом, эти поля
  пока НЕ содержат — парсер этого не требует (все поля опциональны,
  неизвестные bullet-ключи всё равно попадают в KnowledgeCard.meta),
  но это зафиксировано как открытый разрыв, не подчищено молча (см.
  CARD_SCHEMA_GAP ниже).
- Канонические фильтры §7.3 читаются из knowledge/00_INDEX.md и
  ИСПОЛЬЗУЮТСЯ для доп. ранжирования (index_boost), а не только keyword
  по телу/tags карточки — по прямому требованию ТЗ(2):
  "Дополнительные keyword-tags карточки допустимы, но не заменяют эти
  фильтры."
- НЕ добавлен параметр node в сигнатуру retrieve(query, k) — сигнатура
  из ТЗ стабильна. Канонический фильтр передаётся как часть query
  вызывающим узлом (см. конвенции в docstring узлов и prompts.py).

CARD_SCHEMA_GAP: 00_INDEX.md/PROMPT(2) требуют, чтобы карточка несла
поля §7.1 (id, title, authors, year, type, jurisdiction, domain,
stage_fit, product_class, priority, status, do_not_use_for,
summary_norm, chunk_hints, ingest_date). Ни одна из выданных карточек
NORM-01..12 не содержит authors/year/stage_fit/product_class/
ingest_date. Это расхождение канона с самим собой (ТЗ(2) обогнало
факт. состояние карточек) — не моя зона решения, но retrieve() не
должен падать на отсутствующих полях (и не падает: см. KnowledgeCard.meta).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

# известные алиасы узлов, встречающиеся и в tags карточек, и в колонке
# "узлы" 00_INDEX.md — нужны только для tag_index_mismatches(),
# не участвуют в scoring.
_NODE_ALIASES = {
    "extract": "extract_profile",
    "plan": "plan_next_question",
    "analyze": "analyze_answer",
    "score": "update_readiness",
    "score-5": "update_readiness",  # NORM-10: измерение 5 = team_and_execution
    "ask": "ask",
    "coach": "coach",
    "route_intent": "route_intent",
    "decide": "decide",
    "summarize": "summarize",
    "update_readiness": "update_readiness",
    "ingest": "ingest",
}


@dataclass
class KnowledgeCard:
    card_id: str
    title: str
    path: str
    tags: list[str] = field(default_factory=list)
    chunk_hints: str = ""
    do_not_use_for: str = ""
    body: str = ""
    meta: dict[str, str] = field(default_factory=dict)   # все bullet-поля §7.1, что нашлись
    score: float = 0.0

    @property
    def domain(self) -> str:
        return self.meta.get("domain", "")

    @property
    def jurisdiction(self) -> str:
        return self.meta.get("jurisdiction", "")

    @property
    def status(self) -> str:
        return self.meta.get("status", "")


@dataclass
class IndexEntry:
    card_id: str
    file: str
    nodes: list[str]          # нормализовано через _NODE_ALIASES, дубликаты убраны
    filter_tokens: set[str]   # §7.3, токенизировано
    extra_tags: list[str]


_TOKEN_RE = re.compile(r"[a-zа-яё0-9_]+", re.IGNORECASE)
_META_LINE_RE = re.compile(r"^\s*-\s*([a-z_]+)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_INDEX_ROW_RE = re.compile(r"^\|\s*(NORM-\d+)\s*\|(.+)\|$")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _parse_card(md_path: Path) -> KnowledgeCard:
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    title = md_path.stem
    meta: dict[str, str] = {}
    body_start = 0

    for i, line in enumerate(lines):
        if line.startswith("# ") and title == md_path.stem:
            title = line[2:].strip()
            continue
        m = _META_LINE_RE.match(line)
        if m:
            meta[m.group(1).lower()] = m.group(2).strip()
            continue
        if line.startswith("## "):
            body_start = i
            break

    body = "\n".join(lines[body_start:]) if body_start else text
    tags = [t.strip() for t in meta.get("tags", "").split(",") if t.strip()]

    return KnowledgeCard(
        card_id=meta.get("id", md_path.stem),
        title=title,
        path=str(md_path),
        tags=tags,
        chunk_hints=meta.get("chunk_hints", ""),
        do_not_use_for=meta.get("do_not_use_for", ""),
        body=body,
        meta=meta,
    )


def _parse_index(index_path: Path) -> dict[str, IndexEntry]:
    """Парсит таблицу 00_INDEX.md. Отсутствие файла — не ошибка, просто
    index_boost не применяется (чистый keyword остаётся рабочим)."""
    entries: dict[str, IndexEntry] = {}
    if not index_path.exists():
        return entries

    for line in index_path.read_text(encoding="utf-8").splitlines():
        m = _INDEX_ROW_RE.match(line.strip())
        if not m:
            continue
        norm_id = m.group(1)
        cells = [c.strip() for c in m.group(2).split("|")]
        if len(cells) < 4:
            continue
        _file, nodes_cell, filter_cell, extra_cell = cells[:4]

        nodes = []
        for n in nodes_cell.split(","):
            n = n.strip()
            if n in _NODE_ALIASES:
                nodes.append(_NODE_ALIASES[n])
            elif n:
                nodes.append(n)  # неизвестный алиас — не роняем парсинг

        filter_tokens = _tokenize(filter_cell)
        extra_tags = [t.strip() for t in extra_cell.split(";")[0].split(",") if t.strip()]

        entries[norm_id] = IndexEntry(
            card_id=norm_id, file=_file, nodes=sorted(set(nodes)),
            filter_tokens=filter_tokens, extra_tags=extra_tags,
        )
    return entries


class KnowledgeBase:
    """
    Загружает все .md из knowledge_dir (рекурсивно) один раз при
    __init__, плюс отдельно 00_INDEX.md как таблицу канонических
    фильтров (§7.3). retrieve() не читает диск заново.
    """

    def __init__(self, knowledge_dir: str = "./knowledge") -> None:
        self.knowledge_dir = Path(knowledge_dir)
        self._cards: list[KnowledgeCard] = []
        if self.knowledge_dir.exists():
            for md_path in sorted(self.knowledge_dir.rglob("*.md")):
                if md_path.name == "00_INDEX.md":
                    continue
                self._cards.append(_parse_card(md_path))
        self._index: dict[str, IndexEntry] = _parse_index(
            self.knowledge_dir / "00_INDEX.md"
        )

    def retrieve(self, query: str, k: int = 3) -> list[KnowledgeCard]:
        """
        score(card) = keyword_score + index_boost

        keyword_score = 3*|title∩query| + 2*|tags∩query| +
                         2*|chunk_hints∩query| + 1*|body∩query|

        index_boost = 4 * |filter_tokens(card, из 00_INDEX)∩query|
        — по требованию ТЗ(2): канонические фильтры §7.3 не заменяются
        короткими tags карточки, поэтому запрос узла (см. конвенции в
        prompts.py: "questions {domain} {stage}",
        "evidence_standard {study_design}", "next_artifact {jurisdiction}")
        должен поднимать нужные карточки даже если слово "questions"/
        "evidence_standard" не встречается в самом тексте карточки — эти
        токены сопоставляются через 00_INDEX, а не через body.

        Возвращает top-k карточек co score > 0, по убыванию score, при
        равенстве — по card_id.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scored: list[KnowledgeCard] = []
        for card in self._cards:
            title_hits = len(query_tokens & _tokenize(card.title))
            tags_hits = len(query_tokens & _tokenize(" ".join(card.tags)))
            hints_hits = len(query_tokens & _tokenize(card.chunk_hints))
            body_hits = len(query_tokens & _tokenize(card.body))
            keyword_score = 3 * title_hits + 2 * tags_hits + 2 * hints_hits + body_hits

            idx = self._index.get(card.card_id)
            index_boost = 4 * len(query_tokens & idx.filter_tokens) if idx else 0

            total = keyword_score + index_boost
            if total > 0:
                c = KnowledgeCard(**{**card.__dict__, "score": float(total)})
                scored.append(c)

        scored.sort(key=lambda c: (-c.score, c.card_id))
        return scored[:k]

    def tag_index_mismatches(self) -> list[tuple[str, list[str], list[str]]]:
        """
        Диагностика для разработчика: где node-теги внутри самой карточки
        расходятся со списком узлов в 00_INDEX. НЕ используется в
        retrieve() — это отдельный аудит-инструмент, потому что
        расхождение карточка/индекс — решение штаба, не движка.
        Возвращает [(card_id, card_tag_nodes, index_nodes), ...] только
        для несовпадающих карточек.
        """
        mismatches = []
        for card in self._cards:
            card_nodes = sorted({
                _NODE_ALIASES[t] for t in card.tags if t in _NODE_ALIASES
            })
            idx = self._index.get(card.card_id)
            index_nodes = sorted(idx.nodes) if idx else []
            if set(card_nodes) != set(index_nodes):
                mismatches.append((card.card_id, card_nodes, index_nodes))
        return mismatches
