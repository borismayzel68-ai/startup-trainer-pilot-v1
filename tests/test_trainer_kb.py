"""
retrieve канона: карточки из knowledge/10_cards/ подтягиваются по
каноническим фильтрам §7.3 из 00_INDEX.md (index_boost), а не только по тексту.
Фикстура — ТЕСТОВАЯ карточка во временном каталоге, не канон NORM-03.
Канонические карточки NORM-01…16 в репозитории пока отсутствуют.
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

    def test_repo_without_cards_returns_empty_not_error(self):
        _common.get_kb.cache_clear()
        self.assertEqual(_common.retrieve_snippets("questions biotech seed"), [])


if __name__ == "__main__":
    unittest.main()
