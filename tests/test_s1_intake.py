"""S1 · БП-01: Tilda → Readiness Pack. Без сети и без OpenAI (mock-LLM, mock-транспорт файлов)."""
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import httpx
import openpyxl
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import s1_mock_llm  # noqa: E402
from s1_intake.config import Settings, build_checklist, load_methodology, routing_for  # noqa: E402
from s1_intake.evaluator import model_cap_overrides  # noqa: E402
from s1_intake.llm import LLMClient  # noqa: E402
from s1_intake.main import init_s1  # noqa: E402
from s1_intake.models import Category, ClassificationResult, FileRef, ParsedDoc  # noqa: E402
from s1_intake.parsers import FileRejected, download, fetch_and_parse, parse_xlsx  # noqa: E402
from s1_intake.scoring import decide, enforce_rules, score  # noqa: E402
from s1_intake.store import ReadinessJobStore  # noqa: E402
from s1_intake.tilda import parse_payload  # noqa: E402

BASE = "https://ucarecdn.com"
WEBHOOK_KEY = "test-webhook-key"
ADMIN = {"Authorization": "Bearer " + "a" * 32}


# ------------------------------------------------------------- fixtures ----
def make_pdf(pages):
    """Минимальный текстовый PDF без внешних библиотек (ASCII, Helvetica)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        body = " ".join(f"BT /F1 14 Tf 40 {760 - 22 * i} Td ({line}) Tj ET" for i, line in enumerate(text.split("\n")))
        objs.append(f"<< /Length {len(body)} >>\nstream\n{body}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objs)} 0 R "
                    f"/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def make_xlsx():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "P&L"
    ws.append(["", "2026-01", "2026-02", "2026-03"])
    ws.append(["Выручка", 18000, 21000, 25000])
    ws.append(["Gross margin", 0.62, 0.64, 0.65])
    ws.append(["EBITDA", -40000, -42000, -45000])
    cf = wb.create_sheet("Cashflow")
    cf.append(["", "2026-01", "2026-02", "2026-03"])
    cf.append(["Burn", -41000, -43000, -45000])
    cf.append(["Cash", 400000, 357000, 312000])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


DECK_LINES = "CardioSense: wearable ECG patch with AI arrhythmia detection for outpatient monitoring.\n" \
             "Problem, product, team and round slides with enough extractable text for the parser."
FILES = {
    f"{BASE}/deck-uuid/deck.pdf": make_pdf([DECK_LINES, DECK_LINES, DECK_LINES]),
    f"{BASE}/model-uuid/model.xlsx": make_xlsx(),
    f"{BASE}/loi-uuid/loi.pdf": make_pdf(["Letter of Intent - Charite pilot, 200 patients.\n" + DECK_LINES]),
}

SAMPLE_FORM = {
    "tranid": "8123456:2001", "formid": "form123",
    "company": "CardioSense", "name": "Иван Петров", "email": "ivan@cardiosense.example", "consent": "yes",
    "jurisdiction": "РФ", "product_class": "MEDICAL_DEVICE",
    "intended_use": "амбулаторный мониторинг ЭКГ с AI-детекцией аритмий, software",
    "q_team": "CEO 8 лет в Philips Healthcare, CTO PhD. Оба full-time, вестинг 4 года.",
    "q_traction": "MRR 40 тыс. евро", "q_customers": "LOI с клиникой на пилот 200 пациентов",
    "q_burn": "45 тыс. евро в месяц, runway 7 месяцев", "q_round": "1,5 млн евро SAFE, pre-money 6 млн",
    "q_legal": "ООО, 100% у фаундеров", "q_market": "Рынок большой",
    "pitch_deck": f"{BASE}/deck-uuid/deck.pdf",
    "fin_model": f"{BASE}/model-uuid/model.xlsx",
    "evidence_files": f"{BASE}/loi-uuid/loi.pdf",
}


def mock_http():
    def handler(req):
        data = FILES.get(str(req.url))
        return httpx.Response(200, content=data) if data is not None else httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def settings(tmp):
    return Settings(llm_provider="mock", tilda_webhook_key=WEBHOOK_KEY, admin_token="a" * 32,
                    database_url=f"sqlite:///{tmp}/s1.db", analyst_emails=[], smtp_host="")


M = load_methodology()
ROUTING = routing_for("RF", "", "MEDICAL_DEVICE", SAMPLE_FORM["intended_use"])
CHECKLIST = build_checklist(M, ROUTING["modules"])


def cls_fixture():
    return ClassificationResult.model_validate(s1_mock_llm.responder("classify", ""))


# ---------------------------------------------------------------- tests ----
class S1RoutingChecklistTests(unittest.TestCase):
    def test_checklist_follows_trainer_routing(self):
        self.assertTrue(ROUTING["routing_complete"])
        for key in ("regulatory.classification_route", "ip_fto.ip_position", "clinical.clinical_plan",
                    "ai_data_cybersecurity.data_provenance",            # software в intended_use
                    "round_readiness.burn_runway", "legal_readiness.legal_entity"):
            self.assertIn(key, CHECKLIST)
        self.assertNotIn("diagnostics_validity.analytical_validity", CHECKLIST)   # не IVD

    def test_unrecognised_routing_falls_back_to_common_modules(self):
        r = routing_for("Марс", "", "SPACESHIP", "")
        self.assertFalse(r["routing_complete"])
        cl = build_checklist(M, r["modules"])
        self.assertIn("traction.traction_metrics", cl)
        self.assertNotIn("regulatory.classification_route", cl)

    def test_every_catalog_module_is_mapped_to_a_block(self):
        from question_engine_v2 import SLOT_CATALOG
        mapped = {mod for b in M["blocks"] for mod in b["modules"]}
        self.assertTrue(set(SLOT_CATALOG) <= mapped)


class S1IntakeParsingTests(unittest.TestCase):
    def test_tilda_payload(self):
        sub = parse_payload(SAMPLE_FORM, M)
        self.assertEqual(sub.submission_id, "8123456:2001")
        self.assertTrue(sub.consent)
        self.assertEqual({f.kind for f in sub.files}, {"pitch_deck", "fin_model", "evidence"})
        self.assertEqual(sub.product_class, "MEDICAL_DEVICE")

    def test_download_rejects_untrusted(self):
        s = settings(tempfile.mkdtemp())
        for url in ("http://ucarecdn.com/x.pdf", "https://169.254.169.254/latest/meta-data",
                    "https://evil.com/ucarecdn.com/x.pdf", "https://ucarecdn.com.evil.com/x.pdf"):
            with self.assertRaises(FileRejected, msg=url):
                download(url, s, mock_http())

    def test_pdf_and_xlsx(self):
        s = settings(tempfile.mkdtemp())
        d = fetch_and_parse(FileRef(kind="pitch_deck", url=f"{BASE}/deck-uuid/deck.pdf"), s, mock_http())
        self.assertTrue(d.ok, d.warnings)
        self.assertEqual(d.pages, 3)
        self.assertIn("ECG patch", d.text)
        text, metrics, _w, formulas = parse_xlsx(make_xlsx(), 60000)
        self.assertTrue({"revenue", "burn", "cash", "ebitda", "gross_margin"} <= set(metrics))
        self.assertEqual(formulas, 0)


class S1GuardrailTests(unittest.TestCase):
    def claims(self, sources=("deck", "model", "evidence"), caps=None):
        return {c.id: c for c in enforce_rules(cls_fixture(), M, CHECKLIST, set(sources), caps).claims}

    def test_deck_cannot_be_fact(self):
        c = self.claims()
        self.assertEqual((c["C02"].evidence_level, c["C02"].category), (1, Category.FOUNDER_CLAIM))
        # HQ-035 / канон Р-5: документ третьей стороны без подписи — FACT_CANDIDATE, не FACT
        self.assertEqual(c["C03"].category, Category.FACT_CANDIDATE)

    def test_phantom_evidence_removed(self):
        c = self.claims(sources=("deck", "model"))
        # фантомный evidence снят; осталась анкета → E1 (канон: заявление основателя = E1, не E3)
        self.assertEqual((c["C03"].evidence_level, c["C03"].category), (1, Category.FOUNDER_CLAIM))

    def test_hardcoded_model_is_e1(self):
        hard = ParsedDoc(kind="fin_model", source="model", name="m.xlsx", url="u", formula_cells=0)
        self.assertEqual(model_cap_overrides([hard], M), {"model": 1})
        self.assertEqual(self.claims(caps={"model": 1})["C04"].evidence_level, 1)

    def test_conflict_symmetric_and_slot_outside_route_dropped(self):
        c = self.claims()
        self.assertEqual(c["C06"].category, Category.CONFLICT)
        self.assertIsNone(c["C13"].slot)
        self.assertEqual(c["C10"].block, "team")   # блок вычислен из слота, не от модели

    def test_scores_and_verdict(self):
        s = score(enforce_rules(cls_fixture(), M, CHECKLIST, {"deck", "model", "evidence"}), M, CHECKLIST)
        self.assertIn("traction.traction_metrics", s.critical_conflicts)
        self.assertIn("problem_unmet_need.pain_evidence", s.critical_gaps)
        self.assertEqual(s.verdict, "HOLD")
        self.assertEqual(decide(80, True, [], [], M)[0], "NEXT")
        self.assertEqual(decide(80, False, [], [], M)[0], "PASS")
        self.assertEqual(decide(5, True, [], [], M, substantive_claims=1)[0], "PASS")   # пустая заявка
        self.assertEqual(decide(5, True, [], [], M, substantive_claims=12)[0], "HOLD")  # низкая доказательность ≠ отказ


class S1StoreTests(unittest.TestCase):
    def test_idempotent_create_and_single_claim(self):
        st = ReadinessJobStore(f"sqlite:///{tempfile.mkdtemp()}/x.db")
        a, created_a = st.create_or_get("t1", "P", "e@x", {"submission": {}})
        b, created_b = st.create_or_get("t1", "P", "e@x", {"submission": {}})
        self.assertEqual((a, created_a, created_b), (b, True, False))
        self.assertTrue(st.claim(a))
        self.assertFalse(st.claim(a))                    # второй воркер не возьмёт
        self.assertTrue(st.claim(a, "9999-01-01T00:00:00Z"))   # «зависшую» — возьмёт


class S1EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.s = settings(self.tmp)
        self.app = Flask("s1test")
        self.state = init_s1(self.app, self.s, llm_factory=lambda: LLMClient(self.s, s1_mock_llm.responder),
                             http_client=mock_http())
        self.c = self.app.test_client()

    def wait(self, job_id, timeout=30):
        t0 = time.time()
        while time.time() - t0 < timeout:
            job = self.state.store.get(job_id)
            if job["status"] not in ("received", "processing"):
                return job
            time.sleep(0.1)
        raise TimeoutError

    def test_full_flow(self):
        hdr = {"X-Webhook-Key": WEBHOOK_KEY}
        self.assertEqual(self.c.post("/s1/webhooks/tilda", data={"test": "test"}).data, b"ok")
        self.assertEqual(self.c.post("/s1/webhooks/tilda", data=SAMPLE_FORM).status_code, 403)
        self.assertEqual(self.c.post("/s1/webhooks/tilda", data=SAMPLE_FORM, headers=hdr).data, b"ok")
        self.c.post("/s1/webhooks/tilda", data=SAMPLE_FORM, headers=hdr)          # повтор доставки Tilda
        jobs = self.c.get("/s1/jobs", headers=ADMIN).get_json()
        self.assertEqual(len(jobs), 1)
        job = self.wait(jobs[0]["id"])
        self.assertEqual(job["status"], "ready_for_review", job["error"])
        self.assertEqual(job["verdict"], "HOLD")

        jid = job["id"]
        detail = self.c.get(f"/s1/jobs/{jid}", headers=ADMIN).get_json()
        self.assertIn("regulatory", detail["summary"]["modules"])
        self.assertTrue(detail["summary"]["revaluation_condition"])
        self.assertEqual(self.c.get(f"/s1/jobs/{jid}", headers={"Authorization": "Bearer x"}).status_code, 401)
        self.assertEqual(self.c.get(f"/s1/jobs/{jid}/secrets.txt", headers=ADMIN).status_code, 404)

        brief = self.c.get(f"/s1/jobs/{jid}/analyst-brief.html", headers=ADMIN).get_data(as_text=True)
        self.assertIn("Data Audit Table", brief)
        self.assertIn("без валидных ссылок", brief)        # выдуманный C99 Марка пойман
        founder = self.c.get(f"/s1/jobs/{jid}/founder-report.html", headers=ADMIN).get_data(as_text=True)
        for leak in ("HOLD", "Анна", "Марк", "Bear", "Bull", "вердикт", "Data Audit", "kill", "INTERNAL", "переоценки"):
            self.assertNotIn(leak, founder, f"утечка во внешний отчёт: {leak}")

        pdf = self.c.get(f"/s1/jobs/{jid}/founder-report.pdf", headers=ADMIN)
        self.assertIn(pdf.status_code, (200, 501))         # 501 — хост без pango
        if pdf.status_code == 200:
            self.assertEqual(pdf.data[:5], b"%PDF-")
        # TG-1: общий admin-токен не является персональной подписью → 403 (раньше: 409 из-за SMTP)
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/approve", headers=ADMIN).status_code, 403)
        # rerun использует кэш разбора файлов
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/rerun", headers=ADMIN).status_code, 200)
        self.assertEqual(self.wait(jid)["status"], "ready_for_review")

    def test_no_consent_rejected(self):
        form = {**SAMPLE_FORM, "consent": "", "tranid": "x:1"}
        self.c.post("/s1/webhooks/tilda", data=form, headers={"X-Webhook-Key": WEBHOOK_KEY})
        job = self.state.store.list()[0]
        self.assertEqual(job["status"], "rejected")

    def test_disabled_without_config(self):
        app = Flask("s1off")
        init_s1(app, Settings(llm_provider="openai", openai_api_key="", tilda_webhook_key="",
                              database_url=f"sqlite:///{self.tmp}/off.db"))
        c = app.test_client()
        self.assertEqual(c.post("/s1/webhooks/tilda", data={"test": "test"}).data, b"ok")
        self.assertEqual(c.post("/s1/webhooks/tilda", data=SAMPLE_FORM).status_code, 503)


if __name__ == "__main__":
    unittest.main()
