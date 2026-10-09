"""
retrieve канона: карточки из knowledge/10_cards/ подтягиваются по
каноническим фильтрам §7.3 из 00_INDEX.md (index_boost), а не только по тексту.
KnowledgeRetrieveTest — тестовая фикстура во временном каталоге (не канон).
CanonCardsTest — реальные NORM-01…16 в knowledge/10_cards/ (GAP-03 закрыт).
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from trainer.kb import KnowledgeBase
from trainer.nodes import _common

REPO_INDEX = Path(__file__).resolve().parents[1] / "knowledge" / "00_INDEX.md"

FIXTURE_CARD = """# Тестовая карточка (фикстура)
- id: NORM-03
- tags: one_question
- status: active

## Тело
Фикстура для проверки retrieve. Слово-фильтр здесь намеренно не упоминается.
"""


class KnowledgeRetrieveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(REPO_INDEX, self.tmp / "00_INDEX.md")
        (self.tmp / "10_cards").mkdir()
        (self.tmp / "10_cards" / "03_one_question_max_value.md").write_text(FIXTURE_CARD, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp)
        os.environ.pop("TRAINER_KNOWLEDGE_DIR", None)
        _common.get_kb.cache_clear()

    def test_card_found_by_canonical_filter_from_index(self):
        hits = KnowledgeBase(str(self.tmp)).retrieve("questions biotech seed", k=3)
        self.assertEqual([c.card_id for c in hits], ["NORM-03"])

    def test_nodes_read_cards_from_configured_dir(self):
        os.environ["TRAINER_KNOWLEDGE_DIR"] = str(self.tmp)
        _common.get_kb.cache_clear()
        snippets = _common.retrieve_snippets("questions biotech seed")
        self.assertEqual(snippets[0]["id"], "NORM-03")



REPO_KB = Path(__file__).resolve().parents[1] / "knowledge"


class CanonCardsTest(unittest.TestCase):
    """Канонические NORM-01…16 из HANDOFF_CARDS_AND_INDEX (штаб, 2026-10-09)."""

    def setUp(self):
        os.environ.pop("TRAINER_KNOWLEDGE_DIR", None)
        _common.get_kb.cache_clear()
        self.kb = KnowledgeBase(str(REPO_KB))

    def test_all_16_canonical_cards_loaded(self):
        ids = sorted(c.card_id for c in self.kb._cards)
        self.assertEqual(ids, [f"NORM-{i:02d}" for i in range(1, 17)])
        self.assertTrue(all(c.status == "canonical" for c in self.kb._cards))

    def test_canonical_node_queries_not_empty(self):
        # канонические фильтры §7.3 по узлам (00_INDEX.md)
        expected = {
            "questions biotech medtech unknown": "NORM-03",          # plan_next_question
            "evidence_standard study_design Трекшн": "NORM-14",      # analyze_answer
            "next_artifact unknown": "NORM-04",                      # coach
            "stop max_turns": "NORM-09",                             # decide / route
        }
        for query, card in expected.items():
            hits = [c.card_id for c in self.kb.retrieve(query, k=3)]
            self.assertTrue(hits, query)
            self.assertIn(card, hits, query)

    def test_retrieve_by_tag(self):
        self.assertEqual([c.card_id for c in self.kb.retrieve("grade", k=3)], ["NORM-15"])

    def test_nodes_use_repo_cards_by_default(self):
        snippets = _common.retrieve_snippets("next_artifact unknown")
        self.assertTrue(snippets)
        self.assertEqual(snippets[0]["id"], "NORM-04")


if __name__ == "__main__":
    unittest.main()
