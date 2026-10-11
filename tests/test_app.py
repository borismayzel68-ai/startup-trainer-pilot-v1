import os, unittest, io
from unittest import mock
os.environ.setdefault("OPENAI_API_KEY","test-key")
os.environ.setdefault("INVITE_CODE","PILOT-S1-EXT-001")
os.environ.setdefault("SESSION_DATABASE_URL","sqlite:///:memory:")
import app as appmod

class StartApiTests(unittest.TestCase):
    def setUp(self):
        appmod.SESSIONS.clear()
        appmod.app.testing=True
        self.c=appmod.app.test_client()

    def base(self):
        return {
            "invite_code":"PILOT-S1-EXT-001",
            "project_id":"VC-178",
            "project_name":"Doctum",
            "founder_name":"Test Founder",
            "founder_email":"founder@example.com",
            "jurisdiction":"RF",
            "intended_use":"storage and visualization of laboratory history",
            "product_class":"WELLNESS_INFORMATION",
            "mode":"B",
            "consent":"true",
        }

    def post(self,data=None):
        return self.c.post("/api/start",data=data or self.base())

    def test_valid_start_a2_a5_state(self):
        r=self.post()
        self.assertEqual(r.status_code,200,r.get_json())
        j=r.get_json()
        self.assertEqual(j["project_id"],"VC-178")
        self.assertEqual(j["routing_steps"],["jurisdiction","intended_use","product_class"])
        self.assertEqual(j["jurisdiction"],"RF")
        self.assertEqual(j["product_class"],"WELLNESS_INFORMATION")
        self.assertIn("regulatory",j["selected_modules"])
        self.assertIn("ai_data_cybersecurity",j["selected_modules"])
        self.assertIn("pilots_workflow",j["selected_modules"])
        self.assertNotIn("diagnostics_validity",j["selected_modules"])
        self.assertFalse(j["classification_check_required"])
        self.assertEqual(j["current_question"]["profile_field"],"stage")
        self.assertFalse(j["input_manifest"]["deck"]["present"])
        self.assertNotIn("event_status",j)
        self.assertTrue(j["expires_at"])

    def test_project_id_trim_and_suffix(self):
        x=self.base(); x["project_id"]="VC-178 "
        self.assertEqual(self.post(x).get_json()["project_id"],"VC-178")
        x=self.base(); x["project_id"]="VC-100a"
        self.assertEqual(self.post(x).get_json()["project_id"],"VC-100a")

    def test_project_id_missing_or_name_rejected(self):
        x=self.base(); x.pop("project_id")
        r=self.post(x); self.assertEqual(r.status_code,400); self.assertEqual(r.get_json()["code"],"project_id_required")
        x=self.base(); x["project_id"]="Doctum"
        r=self.post(x); self.assertEqual(r.status_code,400)

    def test_required_routing_fields(self):
        for field in ("jurisdiction","intended_use","product_class","founder_name","founder_email"):
            with self.subTest(field=field):
                x=self.base(); x.pop(field)
                self.assertEqual(self.post(x).status_code,400)
        x=self.base(); x["consent"]="false"
        self.assertEqual(self.post(x).status_code,400)

    def test_multi_targets_from_single_ui_string(self):
        x=self.base(); x["jurisdiction"]="MULTI"; x["target_markets"]="RF, EU"
        r=self.post(x)
        self.assertEqual(r.status_code,200,r.get_json())
        j=r.get_json()
        self.assertEqual(j["target_markets"],["RF","EU"])
        self.assertEqual(j["active_markets"],["EU","RF"])

    def test_multi_requires_two_targets(self):
        x=self.base(); x["jurisdiction"]="MULTI"; x["target_markets"]="RF"
        r=self.post(x)
        self.assertEqual(r.status_code,400)
        self.assertEqual(r.get_json()["code"],"target_markets_required")

    def test_intended_use_trigger_without_reclassification(self):
        x=self.base(); x["intended_use"]="interpretation of results and personalized recommendations"
        r=self.post(x); self.assertEqual(r.status_code,200,r.get_json())
        j=r.get_json()
        self.assertTrue(j["classification_check_required"])
        self.assertEqual(j["product_class"],"WELLNESS_INFORMATION")
        self.assertNotIn("diagnostics_validity",j["selected_modules"])

    def test_invite_and_mode_regression(self):
        x=self.base(); x["invite_code"]="wrong"
        self.assertEqual(self.post(x).status_code,403)
        x=self.base(); x["mode"]="Z"
        self.assertEqual(self.post(x).status_code,400)


    def test_non_pdf_regression(self):
        x=self.base()
        x["deck"]=(io.BytesIO(b"not-pdf"),"deck.txt")
        r=self.c.post("/api/start",data=x,content_type="multipart/form-data")
        self.assertEqual(r.status_code,400)
        self.assertEqual(r.get_json()["code"],"deck_mime_invalid")

    def test_feedback_persists_and_delete_removes_session(self):
        started=self.post()
        self.assertEqual(started.status_code,200,started.get_json())
        sid=started.get_json()["id"]
        response=self.c.post(
            f"/api/session/{sid}/feedback",
            json={"rating":5,"text":"ok"},
        )
        self.assertEqual(response.status_code,200)
        stored=appmod.SESSIONS.get(sid)
        self.assertEqual(stored["feedback"]["rating"],5)
        response=self.c.delete(f"/api/session/{sid}")
        self.assertEqual(response.status_code,200)
        self.assertIsNone(appmod.SESSIONS.get(sid))
        self.assertEqual(
            self.c.get(f"/api/session/{sid}").status_code,
            404,
        )

    def test_ui_fields_and_report_header(self):
        html=(__import__("pathlib").Path(appmod.app.root_path)/"templates"/"index.html").read_text(encoding="utf-8")
        for field in ("projectId","founderName","founderEmail","jurisdiction","targetMarkets","intendedUse","productClass","stage","userRole","buyer","payer"):
            self.assertIn(f'id="{field}"',html)
        self.assertIn("project_id||state.project_id",html)
        self.assertIn("state.phase==='full_readiness'",html)
        self.assertIn("до покрытия обязательных слотов",html)

    def test_a8_completion_webhook_is_configured(self):
        self.assertEqual(appmod.TRAINER_VERSION,"s1-ext-pilot-v1.6-founder-package")
        self.assertTrue(hasattr(appmod,"TRAINER_EVENT_WEBHOOK"))

    def test_full_readiness_continues_through_question_13(self):
        x=self.base()
        x.update({
            "stage":"pilots","user":"doctor",
            "buyer":"clinic","payer":"clinic",
            "intended_use":"interpretation of results and personalized recommendations",
        })
        with mock.patch.object(appmod,"call_json",return_value={"covered":[]}):
            started=self.post(x)
            self.assertEqual(started.status_code,200,started.get_json())
            state=started.get_json()
            self.assertTrue(state["classification_check_required"])
            sid=state["id"]
            for _ in range(12):
                response=self.c.post(
                    f"/api/session/{sid}/answer",
                    json={"answer":"нет данных"},
                )
                self.assertEqual(response.status_code,200,response.get_json())
                state=response.get_json()
            self.assertFalse(state["done"])
            self.assertEqual(state["phase"],"full_readiness")
            self.assertEqual(state["turn"],12)
            self.assertIsNotNone(state["current_question"])
            response=self.c.post(
                f"/api/session/{sid}/answer",
                json={"answer":"нет данных"},
            )
            state=response.get_json()
            self.assertEqual(state["turn"],13)
            self.assertEqual(len(state["qa"]),13)
            self.assertFalse(state["done"])
            while not state["done"] and state["turn"]<50:
                response=self.c.post(
                    f"/api/session/{sid}/answer",
                    json={"answer":"нет данных"},
                )
                state=response.get_json()
            self.assertTrue(state["done"])
            self.assertGreater(state["turn"],12)
            self.assertEqual(
                state["stop_reason"],
                "required_coverage_complete",
            )

    def test_enums_unchanged(self):
        self.assertEqual(appmod.ALLOWED_STATUS,{"fact","hypothesis","missing","weak"})
        self.assertEqual(appmod.ALLOWED_REC,{"not_ready","iterate","ready_for_investor_conversation"})
        self.assertEqual(appmod.QUICK_TRIAGE_MAX_TURNS,12)
        self.assertFalse(hasattr(appmod,"MAX_TURNS"))

if __name__=="__main__":
    unittest.main()
