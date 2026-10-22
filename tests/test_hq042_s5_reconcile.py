"""HQ-042 S5: только синтетика, без сети."""
import pathlib

import pytest

from portfolio_update import (NEEDS_EVIDENCE, AccessDenied, EntryPassport, PortfolioUpdates,
                              reconcile, render_report)

P = EntryPassport("SYN-CO-1", {"field_a": "x", "field_b": 10})


def test_match():
    assert reconcile(P, {"field_a": "x", "field_b": 10}, role="investor") == []


def test_mismatch_listed():
    d = reconcile(P, {"field_a": "y", "field_c": 1}, role="investor")
    assert {(x.field, x.kind) for x in d} == {("field_a", "changed"), ("field_b", "missing_in_update"),
                                              ("field_c", "not_in_passport")}
    assert [x for x in d if x.field == "field_c"][0].entry_value == NEEDS_EVIDENCE


def test_new_round_creates_flag_once():
    u = PortfolioUpdates()
    f = u.mark_new_round("SYN-CO-1", "R2", role="investor")
    assert f.status == "OPEN" and f.rules == NEEDS_EVIDENCE
    assert u.mark_new_round("SYN-CO-1", "R2", role="investor") is f
    assert len(u.flags) == 1


def test_question_answer():
    u = PortfolioUpdates()
    q = u.ask("SYN-CO-1", "Какой статус раунда?", role="investor")
    u.answer(q.id, "синтетический ответ", role="investor")
    assert q.answer == "синтетический ответ"
    with pytest.raises(ValueError):
        u.answer(q.id, "ещё", role="investor")
    with pytest.raises(LookupError):
        u.answer(99, "x", role="investor")


@pytest.mark.parametrize("role", ["founder", "expert", "clinic", None])
def test_only_fund_roles(role):
    u = PortfolioUpdates()
    with pytest.raises(AccessDenied):
        reconcile(P, {}, role=role)
    with pytest.raises(AccessDenied):
        u.mark_new_round("c", "R2", role=role)
    with pytest.raises(AccessDenied):
        u.ask("c", "q", role=role)
    q = PortfolioUpdates()
    q.ask("c", "q", role="investor")
    with pytest.raises(AccessDenied):
        q.answer(1, "a", role=role)


def test_doc_example_matches_render():
    doc = pathlib.Path(__file__).resolve().parent.parent / "docs" / "hq042_reconcile_example.md"
    d = reconcile(P, {"field_a": "y", "field_c": 1}, role="investor")
    assert doc.read_text(encoding="utf-8").startswith(render_report(P, d).rstrip("\n"))
