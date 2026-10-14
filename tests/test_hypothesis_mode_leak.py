"""Чужой текст из поля другого сценария не должен попадать в сессию (дефект 14.10, VC-994)."""
import os, re, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SESSION_DATABASE_URL", "sqlite:///:memory:")
import app as appmod  # noqa: E402


class HypothesisModeLeakTests(unittest.TestCase):
    def test_template_has_no_prefilled_example_text(self):
        html = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(encoding="utf-8")
        for tid in ("ha", "hb"):
            m = re.search(rf'<textarea id="{tid}"[^>]*>(.*?)</textarea>', html, re.S)
            self.assertIsNotNone(m)
            self.assertEqual(m.group(1).strip(), "", tid)

    def _start(self, mode):
        c = appmod.app.test_client()
        data = {"invite_code": appmod.INVITE_CODE, "consent": "true", "project_id": "VC-993", "project_name": "Синтетика",
                "founder_name": "Тест", "founder_email": "t@example.test", "jurisdiction": "RF",
                "intended_use": "синтетика", "product_class": "WELLNESS_INFORMATION", "mode": mode,
                "hypothesis_a": "ТЕКСТ-A", "hypothesis_b": "ТЕКСТ-B", "description": "синтетика"}
        r = c.post("/api/start", data=data)
        return r

    def test_mode_a_ignores_hypothesis_b(self):
        saved = {}
        orig = appmod.SESSIONS.__class__.__setitem__

        def spy(store, k, v):
            saved[k] = dict(v)
            return orig(store, k, v)
        appmod.SESSIONS.__class__.__setitem__ = spy
        orig_next = appmod.next_question
        appmod.next_question = lambda s: {"question": "q", "why": ""}
        try:
            r = self._start("A")
        finally:
            appmod.SESSIONS.__class__.__setitem__ = orig
            appmod.next_question = orig_next
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        s = list(saved.values())[-1]
        self.assertEqual(s["hypothesis_a"], "ТЕКСТ-A")
        self.assertEqual(s["hypothesis_b"], "")


if __name__ == "__main__":
    unittest.main()
