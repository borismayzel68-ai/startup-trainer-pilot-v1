"""HQ-028 · выход S1: журнал согласий и отзыв, ссылки со сроком (единый механизм), тизер с проверкой
обезличивания (ловушка), окно фонда 21 день, подпись человека и G-4 перед любой передачей.
Только синтетика; ничего не отправляется (send_email подменён и не вызывается)."""
import dataclasses
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_s1_intake as base  # noqa: E402
from test_tg1_human_gate import ALICE, Tg1Base  # noqa: E402
from s1_intake import exit_intro, links  # noqa: E402
from s1_intake.config import load_methodology  # noqa: E402

CFG = load_methodology()["working"]["hq028"]
CLEAN = {"area": "MedTech / цифровое здоровье", "stage": "прототип", "raise": "1,5 млн евро на сертификацию",
         "strengths": [{"text": "Опытная команда (синтетика)", "status": "заявление"}] * 3,
         "risks": [{"text": "Регуляторный путь не подтверждён (синтетика)", "status": "заявление"}] * 3}


class Base28(Tg1Base):
    def setUp(self):
        super().setUp()
        self.state.settings = dataclasses.replace(self.s, free_link_secret="s" * 32)

    def cabinet(self, jid):
        return self.c.post(f"/s1/jobs/{jid}/dialogue-link", headers=base.ADMIN).get_json()["path"]

    def cab_url(self, path, action):
        base_path, q = path.split("?")
        return base_path.replace("/s1/dialogue/", "/s1/cabinet/") + f"/{action}?{q}"

    def signed_teaser(self, jid):
        self.c.post(f"/s1/jobs/{jid}/teaser", headers=base.ADMIN, json={"edit": CLEAN})
        r = self.c.post(f"/s1/jobs/{jid}/teaser/sign", headers=ALICE, json={"fund_decision": "фонд готов к знакомству"})
        self.assertEqual(r.status_code, 200, r.get_json())

    def expire_window(self, jid):
        self.c.post(f"/s1/jobs/{jid}/fund-timer/start", headers=base.ADMIN)
        t = self.state.store.get(jid)["data"]["fund_timer"]
        t["ends_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.state.store.update(jid, data_patch={"fund_timer": t})

    def release(self, jid, kind, recipient):
        r = self.c.post(f"/s1/jobs/{jid}/transfers", headers=base.ADMIN, json={"kind": kind, "recipient": recipient})
        self.assertEqual(r.status_code, 200, r.get_json())
        g = self.c.post(f"/s1/transfers/{r.get_json()['transfer_id']}/g4", headers=ALICE, json={"decision": "yes"})
        self.assertEqual(g.status_code, 200, g.get_json())
        return g.get_json()["path"]


class ConsentTests(Base28):
    def test_no_consent_blocks_transfer(self):
        jid = self.ready_job()
        self.signed_teaser(jid)
        r = self.c.post(f"/s1/jobs/{jid}/transfers", headers=base.ADMIN, json={"kind": "teaser", "recipient": "fund"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("согласия", " ".join(r.get_json()["blocked"]))

    def test_consent_log_who_what_whom_when_and_revoke_blocks(self):
        jid = self.ready_job()
        cab = self.cabinet(jid)
        self.assertEqual(self.c.post(self.cab_url(cab, "consent"), json={"scope": "teaser", "recipient": "fund"}).status_code, 200)
        log = self.c.get(f"/s1/jobs/{jid}/consents", headers=base.ADMIN).get_json()
        self.assertEqual((log[0]["granted_by"], log[0]["scope"], log[0]["recipient"]), ("founder", "teaser", "fund"))
        self.assertTrue(log[0]["granted_at"])
        self.signed_teaser(jid)
        link = self.release(jid, "teaser", "fund")
        self.assertEqual(self.c.get(link).status_code, 200)
        self.c.post(self.cab_url(cab, "revoke"), json={})
        self.assertEqual(self.c.get(link).status_code, 403)  # ссылка перестала работать сразу
        log = self.c.get(f"/s1/jobs/{jid}/consents", headers=base.ADMIN).get_json()
        self.assertTrue(log[0]["revoked_at"])
        r = self.c.post(f"/s1/jobs/{jid}/transfers", headers=base.ADMIN, json={"kind": "teaser", "recipient": "fund"})
        self.assertEqual(r.status_code, 409)  # отзыв согласия блокирует передачу
        self.assertEqual(self.sent, [])

    def test_revoke_between_request_and_g4_blocks(self):
        jid = self.ready_job()
        cab = self.cabinet(jid)
        self.c.post(self.cab_url(cab, "consent"), json={"scope": "teaser", "recipient": "fund"})
        self.signed_teaser(jid)
        tid = self.c.post(f"/s1/jobs/{jid}/transfers", headers=base.ADMIN,
                          json={"kind": "teaser", "recipient": "fund"}).get_json()["transfer_id"]
        self.c.post(self.cab_url(cab, "revoke"), json={})
        self.assertEqual(self.c.post(f"/s1/transfers/{tid}/g4", headers=ALICE, json={"decision": "yes"}).status_code, 409)


class LinkTests(Base28):
    def test_expired_link_does_not_open(self):
        jid = self.ready_job()
        self.c.post(self.cab_url(self.cabinet(jid), "consent"), json={"scope": "teaser", "recipient": "fund"})
        self.signed_teaser(jid)
        link = self.release(jid, "teaser", "fund")
        tid = link.split("/s1/shared/")[1].split("?")[0]
        past = links.now_ts() - 5
        sig = links.sign("s" * 32, "shared", tid, past)
        self.assertEqual(self.c.get(f"/s1/shared/{tid}?exp={past}&sig={sig}").status_code, 410)
        self.assertEqual(self.c.get(link.replace("sig=", "sig=0")).status_code, 403)

    def test_default_ttl_14_days_single_mechanism(self):
        jid = self.ready_job()
        for ep in ("free-link", "dialogue-link"):
            exp = self.c.post(f"/s1/jobs/{jid}/{ep}", headers=base.ADMIN).get_json()["expires"]
            self.assertAlmostEqual(exp - links.now_ts(), 14 * 86400, delta=5)
        self.assertEqual(CFG["link_ttl_days"], 14)


class TeaserTests(Base28):
    def test_trap_names_and_patents_and_clinics(self):
        jid = self.ready_job()
        job = self.state.store.get(jid)
        trap = {**CLEAN, "area": "CardioSense — MedTech", "raise": "Иван Петров привлекает 1,5 млн",
                "strengths": [{"text": "Патент RU 2712345 на способ", "status": "факт"},
                               {"text": "Пилот в клинике Шарите", "status": "заявление"}]}
        t = {**exit_intro.build_teaser(job, CFG), **trap}
        v = " | ".join(exit_intro.deid_check(t, job, CFG))
        for must in ("название компании", "имя основателя", "номер патента", "клиника"):
            self.assertIn(must, v)
        r = self.c.post(f"/s1/jobs/{jid}/teaser", headers=base.ADMIN, json={"edit": trap}).get_json()
        self.assertTrue(r["deid_violations"])
        s = self.c.post(f"/s1/jobs/{jid}/teaser/sign", headers=ALICE, json={"fund_decision": "да"})
        self.assertEqual(s.status_code, 409)  # обезличивание не пройдено — подписи нет

    def test_teaser_structure_and_disclaimer(self):
        jid = self.ready_job()
        t = self.c.post(f"/s1/jobs/{jid}/teaser", headers=base.ADMIN, json={"edit": CLEAN}).get_json()
        self.assertEqual(t["deid_violations"], [])
        self.assertEqual(len(t["strengths"]), 3)
        self.assertEqual(len(t["risks"]), 3)
        for x in t["strengths"] + t["risks"]:
            self.assertIn(x["status"], ("факт", "заявление"))
        self.assertEqual(t["disclaimer"], "Не инвестиционная рекомендация")
        for k in ("fin_model", "patents", "clinics"):
            self.assertNotIn(k, t)

    def test_no_human_signature_no_transfer(self):
        jid = self.ready_job()
        self.c.post(self.cab_url(self.cabinet(jid), "consent"), json={"scope": "teaser", "recipient": "fund"})
        self.c.post(f"/s1/jobs/{jid}/teaser", headers=base.ADMIN, json={"edit": CLEAN})
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/teaser/sign", headers=base.ADMIN,
                                     json={"fund_decision": "да"}).status_code, 403)  # общий токен — не подпись
        r = self.c.post(f"/s1/jobs/{jid}/transfers", headers=base.ADMIN, json={"kind": "teaser", "recipient": "fund"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("тизер не подписан человеком", r.get_json()["blocked"])

    def test_report_link_has_disclaimer_and_needs_human_gate(self):
        jid = self.ready_job()
        self.c.post(self.cab_url(self.cabinet(jid), "consent"), json={"scope": "report", "recipient": "fund"})
        r = self.c.post(f"/s1/jobs/{jid}/transfers", headers=base.ADMIN, json={"kind": "report", "recipient": "fund"})
        self.assertIn("полный отчёт не подписан на Human Gate", r.get_json()["blocked"])
        self.c.post(f"/s1/jobs/{jid}/approve", headers=ALICE)
        link = self.release(jid, "report", "fund")
        html = self.c.get(link).get_data(as_text=True)
        self.assertIn("Не инвестиционная рекомендация", html)


class FundWindowTests(Base28):
    def test_other_investor_blocked_until_21_days(self):
        jid = self.ready_job()
        cab = self.cabinet(jid)
        self.c.post(self.cab_url(cab, "consent"), json={"scope": "teaser", "recipient": "angel-1"})
        self.signed_teaser(jid)
        r = self.c.post(f"/s1/jobs/{jid}/transfers", headers=base.ADMIN, json={"kind": "teaser", "recipient": "angel-1"})
        self.assertEqual(r.status_code, 409)  # окно не запущено
        self.c.post(f"/s1/jobs/{jid}/fund-timer/start", headers=base.ADMIN)
        t = self.state.store.get(jid)["data"]["fund_timer"]
        days = (datetime.fromisoformat(t["ends_at"]) - datetime.fromisoformat(t["started_at"])).days
        self.assertEqual(days, 21)
        r = self.c.post(f"/s1/jobs/{jid}/transfers", headers=base.ADMIN, json={"kind": "teaser", "recipient": "angel-1"})
        self.assertEqual(r.status_code, 409)  # до 21 дня — заблокировано
        self.assertIn("окно фонда", " ".join(r.get_json()["blocked"]))
        self.expire_window(jid)
        st = self.c.get(f"/s1/jobs/{jid}/fund-timer", headers=base.ADMIN).get_json()
        self.assertEqual(st["state"], "expired")  # срабатывание = смена статуса
        self.assertEqual(self.sent, [])
        link = self.release(jid, "teaser", "angel-1")
        self.assertEqual(self.c.get(link).status_code, 200)

    def test_rounds_also_through_g4_only(self):
        jid = self.ready_job()
        self.c.post(self.cab_url(self.cabinet(jid), "consent"), json={"scope": "teaser", "recipient": "rounds"})
        self.signed_teaser(jid)
        self.expire_window(jid)
        tid = self.c.post(f"/s1/jobs/{jid}/transfers", headers=base.ADMIN,
                          json={"kind": "teaser", "recipient": "rounds"}).get_json()["transfer_id"]
        self.assertEqual(self.c.post(f"/s1/transfers/{tid}/g4", headers=base.ADMIN, json={"decision": "yes"}).status_code, 403)
        self.assertEqual(self.c.post(f"/s1/transfers/{tid}/g4", headers=ALICE, json={"decision": "no"}).get_json()["status"],
                         "declined")
        with mock.patch("httpx.post") as hp:
            self.c.get(f"/s1/jobs/{jid}/fund-timer", headers=base.ADMIN)
            hp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
