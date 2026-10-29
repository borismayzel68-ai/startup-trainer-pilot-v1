"""HQ-042 S5: только синтетика, без сети."""
import pathlib

import pytest

from portfolio.access import Actor
from portfolio_update import (NEEDS_EVIDENCE, AccessDenied, EntryPassport, PortfolioUpdates,
                              reconcile, render_report)

P = EntryPassport("SYN-CO-1", {"field_a": "x", "field_b": 10})
INV = Actor("investor", frozenset({"SYN-CO-1"}))


def test_match():
    assert reconcile(P, {"field_a": "x", "field_b": 10}, actor=INV) == []


def test_mismatch_listed():
    d = reconcile(P, {"field_a": "y", "field_c": 1}, actor=INV)
    assert {(x.field, x.kind) for x in d} == {("field_a", "changed"), ("field_b", "missing_in_update"),
                                              ("field_c", "not_in_passport")}
    assert [x for x in d if x.field == "field_c"][0].entry_value == NEEDS_EVIDENCE


def test_new_round_creates_flag_once():
    u = PortfolioUpdates()
    f = u.mark_new_round("SYN-CO-1", "R2", actor=INV)
    assert f.status == "OPEN" and f.rules == NEEDS_EVIDENCE
    assert u.mark_new_round("SYN-CO-1", "R2", actor=INV) is f
    assert len(u.flags) == 1


def test_question_answer():
    u = PortfolioUpdates()
    q = u.ask("SYN-CO-1", "Какой статус раунда?", actor=INV)
    u.answer(q.id, "синтетический ответ", actor=INV)
    assert q.answer == "синтетический ответ"
    with pytest.raises(ValueError):
        u.answer(q.id, "ещё", actor=INV)
    with pytest.raises(LookupError):
        u.answer("missing", "x", actor=INV)


@pytest.mark.parametrize("actor", [Actor("owner"), Actor("fund_staff"), INV, Actor("investor", frozenset({"*"}))])
def test_allowed_actors(actor):
    u = PortfolioUpdates()
    assert reconcile(P, {"field_a": "x", "field_b": 10}, actor=actor) == []
    u.mark_new_round("SYN-CO-1", "R2", actor=actor)
    q = u.ask("SYN-CO-1", "q", actor=actor)
    assert u.answer(q.id, "a", actor=actor).answer == "a"


def test_investor_foreign_company_denied():
    u = PortfolioUpdates()
    q = u.ask("SYN-CO-1", "q", actor=INV)
    other = Actor("investor", frozenset({"SYN-CO-2"}))
    with pytest.raises(AccessDenied):
        reconcile(P, {}, actor=other)
    with pytest.raises(AccessDenied):
        u.mark_new_round("SYN-CO-1", "R2", actor=other)
    with pytest.raises(AccessDenied):
        u.ask("SYN-CO-1", "q", actor=other)
    with pytest.raises(AccessDenied):
        u.answer(q.id, "a", actor=other)
    assert q.answer is None


@pytest.mark.parametrize("actor", [Actor("founder", frozenset({"*"})), Actor("expert"), Actor("clinic"), None])
def test_other_roles_denied(actor):
    u = PortfolioUpdates()
    q = u.ask("SYN-CO-1", "q", actor=INV)
    with pytest.raises(AccessDenied):
        reconcile(P, {}, actor=actor)
    with pytest.raises(AccessDenied):
        u.mark_new_round("SYN-CO-1", "R2", actor=actor)
    with pytest.raises(AccessDenied):
        u.ask("SYN-CO-1", "q", actor=actor)
    with pytest.raises(AccessDenied):
        u.answer(q.id, "a", actor=actor)


def test_question_ids_unique():
    u = PortfolioUpdates()
    ids = {u.ask("SYN-CO-1", f"q{i}", actor=INV).id for i in range(50)}
    assert len(ids) == 50
    u.questions.pop(0)
    assert u.ask("SYN-CO-1", "new", actor=INV).id not in ids


def test_doc_example_matches_render():
    doc = pathlib.Path(__file__).resolve().parent.parent / "docs" / "hq042_reconcile_example.md"
    d = reconcile(P, {"field_a": "y", "field_c": 1}, actor=INV)
    assert doc.read_text(encoding="utf-8").startswith(render_report(P, d).rstrip("\n"))
