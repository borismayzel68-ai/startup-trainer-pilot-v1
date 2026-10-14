"""HQ-016: синтетические кейсы (НЕ реальные) и снимок пайплайна для golden-сравнения (T3).
Снимок: evaluate() на mock-LLM без файлов → generate_pack() с фиксированными job_id и датой."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

import s1_mock_llm  # noqa: E402
from s1_intake.config import Settings, load_methodology  # noqa: E402
from s1_intake.evaluator import evaluate  # noqa: E402
from s1_intake.formatter import generate_pack  # noqa: E402
from s1_intake.llm import LLMClient  # noqa: E402
from s1_intake.models import ParsedDoc  # noqa: E402
from s1_intake.tilda import parse_payload  # noqa: E402

FIXED = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)

_BASE = {"company": "Synth Co", "name": "Синтетический основатель", "email": "founder@example.test",
         "consent": "yes", "q_team": "Два основателя full-time (синтетика)."}

# Не-LS (T3 / S-7): IT-сервис без МИ-функций, сервис, wellness.
NON_LS = {
    "it_wellness": {**_BASE, "tranid": "synthetic:it-1", "jurisdiction": "РФ", "product_class": "WELLNESS_INFORMATION",
                    "intended_use": "SaaS для записи в клинику, без медицинских функций",
                    "q_traction": "MRR 3 тыс. евро (синтетика)"},
    "lab_service": {**_BASE, "tranid": "synthetic:svc-1", "jurisdiction": "РФ", "product_class": "LAB_SERVICE",
                    "intended_use": "лабораторный сервис для клиник"},
    "no_gate": {**_BASE, "tranid": "synthetic:nogate-1", "jurisdiction": "", "product_class": "",
                "intended_use": "не указано"},
}


def settings(tmp: str = "/tmp") -> Settings:
    return Settings(llm_provider="mock", tilda_webhook_key="k", admin_token="a" * 32,
                    database_url=f"sqlite:///{tmp}/hq016.db", analyst_emails=[], smtp_host="")


# P0 #36: источник, на который ссылается claim, должен быть реально загружен. LS-кейсы ниже ссылаются на
# deck/evidence — им нужны синтетические «загруженные» документы; golden-снимок остаётся «без файлов».
SYNTH_DOCS = [ParsedDoc(kind=k, source=src, name=f"synthetic-{src}", url=f"https://example.test/{src}")
              for k, src in (("pitch_deck", "deck"), ("evidence", "evidence"))]


def run(form: dict, cls_override=None, verified_slots=None, docs=None):
    s = settings()
    m = load_methodology(str(s.methodology_path))
    sub = parse_payload(form, m)
    sub.received_at = FIXED

    def responder(stage, user):
        if stage == "classify" and cls_override is not None:
            return cls_override
        return s1_mock_llm.responder(stage, user)

    if docs is None:
        docs = SYNTH_DOCS if cls_override is not None else []
    res = evaluate("synthetic-job", sub, docs, LLMClient(s, responder), s, m, verified_slots=verified_slots)
    res.created_at = FIXED
    return res, generate_pack(res, m, s.fund_name), m


def snapshot(form: dict) -> dict:
    res, pack, _ = run(form)
    return {"scores": json.loads(res.scores.model_dump_json()),
            "readiness_pack": pack["readiness_pack"],
            "founder_report_html": pack["founder_report_html"],
            "analyst_brief_html": pack["analyst_brief_html"]}


if __name__ == "__main__":  # генерация golden: hq016_* — на базовом коммите 0f72b0a; hq035_* — после HQ-035
    import sys as _sys
    prefix = _sys.argv[1] if len(_sys.argv) > 1 else "hq016"
    out = Path(__file__).resolve().parent / "golden"
    out.mkdir(exist_ok=True)
    for name, form in NON_LS.items():
        (out / f"{prefix}_{name}.json").write_text(
            json.dumps(snapshot(form), ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        print("golden", name)


# ---------------------------------------------------------------- LS-кейсы ----
def _claim(cid, slot, level, sources, text):
    return {"id": cid, "slot": slot, "statement": text, "sources": sources, "source_ref": "синтетика",
            "category": "FACT" if level >= 3 else "FOUNDER_CLAIM", "evidence_level": level,
            "evidence_basis": "синтетический пример", "conflicts_with": [], "verification_step": "—"}


def ls_classification(extra_claims=()):
    claims = [
        _claim("C01", "team_governance.critical_roles", 1, ["deck"], "CSO — PhD фармакология (синтетика)"),
        _claim("C02", "team_governance.ownership", 3, ["evidence"], "Доли закреплены договором (синтетика)"),
        _claim("C03", "problem_unmet_need.pain_evidence", 1, ["deck"], "Неудовлетворённая потребность (синтетика)"),
        _claim("C04", "financing_milestone.funding_need", 1, ["deck"], "Нужно 1 млн (синтетика)"),
        _claim("C05", "ls_milestones_product.k1_clinical_stage", 3, ["evidence"], "Отчёт доклиники CRO (синтетика)"),
        _claim("C06", "ls_milestones_product.k4_science_poc", 3, ["evidence"], "PoC in vivo, отчёт (синтетика)"),
        _claim("C07", "ls_milestones_risks.k2_regulatory_path", 1, ["deck"], "Путь РФ, ПП № 1684 (синтетика)"),
        _claim("C08", "ls_milestones_risks.k3_ip", 3, ["evidence"], "Заявка на изобретение подана (синтетика)"),
        _claim("C09", "ls_milestones_market.k6_reimbursement", 0, ["form"], "Платит ОМС (синтетика, без документа)"),
    ]
    return {"company_summary": "Синтетический LS-стартап", "sector": "Biotech", "stage": "доклиника",
            "thesis_fit": {"fit": True, "reason": "LS"}, "claims": claims + list(extra_claims)}


LS = {
    "ls_preclinical_drug": {**_BASE, "tranid": "synthetic:ls-1", "jurisdiction": "РФ", "product_class": "THERAPEUTIC",
                            "intended_use": "синтетический ЛП, доклиника", "ls_stage": "preclinical"},
    "ls_md_prototype": {**_BASE, "tranid": "synthetic:ls-2", "jurisdiction": "РФ", "product_class": "MEDICAL_DEVICE",
                        "intended_use": "синтетическое МИ, прототип", "ls_stage": "poc"},
    "ls_registered": {**_BASE, "tranid": "synthetic:ls-3", "jurisdiction": "РФ", "product_class": "MEDICAL_DEVICE",
                      "intended_use": "синтетическое МИ, РУ получено", "ls_stage": "registered"},
    "ls_no_gate": {**_BASE, "tranid": "synthetic:ls-5", "jurisdiction": "", "product_class": "",
                   "intended_use": "синтетика", "ls_stage": "preclinical"},
}
REVENUE_CLAIM = _claim("C10", "traction.traction_metrics", 0, ["form"], "Выручка 10 тыс. (синтетика, без документа)")
