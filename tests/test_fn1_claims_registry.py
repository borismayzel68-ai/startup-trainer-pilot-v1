"""FN1 (#114): реестр утверждений → документ → уровень E. Синтетика VC-99x. Сначала критерии, затем код."""
import itertools
import random

import pytest

from claims_registry import ClaimError, Registry, level_code

P = "VC-991"


def reg_with_claim():
    r = Registry()
    return r, r.add_claim(P, "Метод снижает время анализа на 30%", source="synthetic deck, стр. 4")


def test_claim_without_document_is_E0_and_cannot_be_raised():
    r, c = reg_with_claim()
    assert r.level(c) == 0 and r.view(c)["level"] == "E0"
    for target in (1, 2, 3, 4):
        with pytest.raises(ClaimError) as e:
            r.request_level(c, target)
        assert e.value.code == "no_document"
    assert r.request_level(c, 0) == 0


def test_ladder_requires_document_reference_human_mark_quote():
    r, c = reg_with_claim()
    d = r.add_document(P, "Отчёт испытаний (синтетика)")
    r.link(c, d)
    assert r.level(c) == 1
    r.edit_document(d, source_ref="https://example.test/report")
    assert r.level(c) == 2
    r.mark_independent(d, "Иванов А. (эксперт, синтетика)")
    assert r.level(c) == 3
    r.edit_document(d, quote="«снижение времени на 31%»")
    assert r.level(c) == 4 and r.view(c)["level"] == "E4"


def test_E4_needs_quote_and_reference_even_if_independent():
    r, c = reg_with_claim()
    d = r.add_document(P, "Акт", quote="прямая цитата")  # без ссылки
    r.link(c, d)
    r.mark_independent(d, "Петров В. (синтетика)")
    assert r.level(c) == 1  # без ссылки — не выше E1
    r.edit_document(d, source_ref="https://example.test/act")
    r.edit_document(d, quote=None)
    assert r.level(c) == 3  # независимый, но без цитаты — не E4


@pytest.mark.parametrize("who", [None, "", "  ", "ab", "LLM", "auto", "system", "Claude", "grok"])
def test_independent_only_with_human_mark(who):
    r, c = reg_with_claim()
    d = r.add_document(P, "Акт", source_ref="https://example.test/a")
    r.link(c, d)
    with pytest.raises(ClaimError) as e:
        r.mark_independent(d, who)
    assert e.value.code == "independent_by_required"
    assert r.level(c) == 2 and r.docs[d]["independent_by"] is None


def test_independent_by_cannot_be_set_by_editing():
    r, c = reg_with_claim()
    d = r.add_document(P, "Акт", source_ref="https://example.test/a")
    with pytest.raises(ClaimError) as e:
        r.edit_document(d, independent_by="Сидоров")
    assert e.value.code == "field_forbidden" and r.docs[d]["independent_by"] is None


def test_unlinking_document_lowers_level_and_denies_old_target():
    r, c = reg_with_claim()
    d = r.add_document(P, "Акт", source_ref="https://example.test/a")
    r.link(c, d)
    assert r.request_level(c, 2) == 2
    r.unlink(c, d)
    assert r.level(c) == 0
    with pytest.raises(ClaimError):
        r.request_level(c, 2)


def test_clearing_independent_mark_lowers_level():
    r, c = reg_with_claim()
    d = r.add_document(P, "Акт", source_ref="https://example.test/a")
    r.link(c, d)
    r.mark_independent(d, "Козлова М. (синтетика)")
    assert r.level(c) == 3
    r.clear_independent(d)
    assert r.level(c) == 2


def test_other_project_document_rejected():
    r, c = reg_with_claim()
    d = r.add_document("VC-992", "Чужой документ", source_ref="https://example.test/x")
    with pytest.raises(ClaimError) as e:
        r.link(c, d)
    assert e.value.code == "project_mismatch" and r.level(c) == 0


@pytest.mark.parametrize("call", [
    lambda r: r.add_claim("", "x"), lambda r: r.add_claim(P, "  "), lambda r: r.add_document(P, ""),
    lambda r: r.level("C-999"), lambda r: r.link("C-999", "D-999")])
def test_invalid_input_rejected(call):
    with pytest.raises(ClaimError):
        call(Registry())


def test_denied_raise_is_logged():
    r, c = reg_with_claim()
    with pytest.raises(ClaimError):
        r.request_level(c, 3)
    assert {"kind": "raise_denied", "claim": c, "target": 3} in r.events


def test_zero_raises_without_document_under_random_operations():
    """Свойство: при любой последовательности операций уровень утверждения без документов всегда E0,
    а любой принятый request_level не выше потолка по документам."""
    rnd = random.Random(99)
    for _ in range(200):
        r = Registry()
        claims = [r.add_claim(P, "утверждение %d" % i) for i in range(3)]
        docs = [r.add_document(P, "док %d" % i, source_ref=rnd.choice([None, "https://example.test/%d" % i]),
                               quote=rnd.choice([None, "цитата"])) for i in range(3)]
        for _ in range(25):
            op = rnd.choice(["link", "unlink", "mark", "clear", "req"])
            c, d = rnd.choice(claims), rnd.choice(docs)
            try:
                if op == "link":
                    r.link(c, d)
                elif op == "unlink":
                    r.unlink(c, d)
                elif op == "mark":
                    r.mark_independent(d, rnd.choice(["", "bot", "auto", "Смирнов (синтетика)"]))
                elif op == "clear":
                    r.clear_independent(d)
                else:
                    t = rnd.choice(range(5))
                    assert r.request_level(c, t) <= r.level(c)
            except ClaimError:
                pass
            for cid in claims:
                if not r.claims[cid]["doc_ids"]:
                    assert r.level(cid) == 0
                if r.level(cid) >= 3:
                    assert any(r.docs[x]["independent_by"] and r.docs[x]["independent_by"].lower() not in
                               {"bot", "auto"} for x in r.claims[cid]["doc_ids"])


def test_level_code():
    assert [level_code(i) for i in range(5)] == ["E0", "E1", "E2", "E3", "E4"]
