"""HQ-035 / канон Р-5: код ставит максимум FACT_CANDIDATE («заявление с документом»); FACT — только подпись
человека с ролью из списка подписантов, пакетом на слот, с журналом (кто, когда, роль, основание). Синтетика."""
import dataclasses
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_s1_intake as base  # noqa: E402
import test_tg1_human_gate as tg1  # noqa: E402
from s1_intake.models import Category  # noqa: E402
from s1_intake.scoring import enforce_rules  # noqa: E402

ALICE = {"Authorization": f"Bearer {tg1.ALICE_TOKEN}"}
BOB = {"Authorization": f"Bearer {tg1.BOB_TOKEN}"}


def items(job):
    brief = job["data"]["readiness_pack"]["internal_analyst_brief"]
    return {it["key"]: it for b in brief["scores"]["blocks"] for it in b["items"]}


class TrapTests(unittest.TestCase):
    def test_third_party_document_without_signature_is_not_fact(self):
        """Ловушка: документ третьей стороны (E3), модель сама пишет FACT и verified_by — всё равно не FACT."""
        fx = base.cls_fixture()
        for c in fx.claims:
            c.verified_by = "analyst"
        out = {c.id: c for c in enforce_rules(fx, base.M, base.CHECKLIST, {"deck", "model", "evidence"}).claims}
        self.assertEqual(out["C03"].evidence_level, 3)
        self.assertEqual(out["C03"].category, Category.FACT_CANDIDATE)
        self.assertIsNone(out["C03"].verified_by)
        self.assertFalse(any(c.category == Category.FACT for c in out.values()))

    def test_public_label(self):
        labels = base.M["working"]["fact_signoff"]["public_labels"]
        self.assertEqual(labels["FACT_CANDIDATE"], "заявление с документом")

    def test_signer_list_has_roles_not_names(self):
        roles = base.M["working"]["fact_signoff"]["signer_roles"]
        self.assertEqual(roles, ["analyst"])


class SignoffEndpointTests(tg1.Tg1Base):
    def setUp(self):
        super().setUp()
        self.s = dataclasses.replace(self.s, approver_roles={"alice": "analyst", "bob": "operator"})
        self.state.settings = self.s

    def candidate(self, jid):
        it = items(self.state.store.get(jid))
        cands = [k for k, v in it.items() if v["status"] == "FACT_CANDIDATE"]
        self.assertTrue(cands, "в синтетическом кейсе должен быть слот с документом третьей стороны")
        self.assertFalse([k for k, v in it.items() if v["status"] == "FACT"], "без подписи FACT нет")
        return cands[0]

    def test_verified_slot_becomes_fact_and_is_logged(self):
        jid = self.ready_job()
        slot = self.candidate(jid)
        before = self.state.store.get(jid)["data"]["readiness_pack"]["internal_analyst_brief"]["scores"]["readiness"]
        r = self.c.post(f"/s1/jobs/{jid}/slots/verify", headers=ALICE, json={"slot": slot, "basis": "сверил LOI с реестром (синтетика)"})
        self.assertEqual(r.status_code, 200, r.get_json())
        job = tg1.base.S1EndToEndTests.wait(self, jid)
        it = items(job)
        self.assertEqual(it[slot]["status"], "FACT")
        brief = job["data"]["readiness_pack"]["internal_analyst_brief"]
        b3 = next(b for b in job["data"]["pack_blocks"] if b["n"] == 3)
        signed = [c for c in b3["claims"] if c["class"] == "FACT"]
        self.assertTrue(signed and all(c["verified_by"] == "analyst" and c["label"] == "факт" for c in signed))
        self.assertFalse([c for c in b3["claims"] if c["class"] == "FACT_CANDIDATE" and c["verified_by"]])
        self.assertEqual(brief["scores"]["readiness"], before)  # подпись меняет статус, не балл
        log = self.c.get(f"/s1/jobs/{jid}/fact-signoffs", headers=base.ADMIN).get_json()
        self.assertEqual([(x["slot"], x["signer"], x["role"]) for x in log], [(slot, "alice", "analyst")])
        self.assertTrue(log[0]["at"] and log[0]["basis"])

    def test_wrong_role_admin_token_and_missing_basis_refused(self):
        jid = self.ready_job()
        slot = self.candidate(jid)
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/slots/verify", headers=BOB, json={"slot": slot, "basis": "x"}).status_code, 403)
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/slots/verify", headers=base.ADMIN, json={"slot": slot, "basis": "x"}).status_code, 403)
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/slots/verify", headers=ALICE, json={"slot": slot}).status_code, 400)
        self.assertEqual(self.state.store.fact_signoffs(jid), [])

    def test_founder_claim_slot_cannot_be_signed_to_fact(self):
        jid = self.ready_job()
        it = items(self.state.store.get(jid))
        low = next(k for k, v in it.items() if v["status"] == "FOUNDER_CLAIM")
        r = self.c.post(f"/s1/jobs/{jid}/slots/verify", headers=ALICE, json={"slot": low, "basis": "x"})
        self.assertEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
