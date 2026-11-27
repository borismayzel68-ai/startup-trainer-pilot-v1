"""FN1 (#114, решение Бориса 26.11): реестр утверждений → документ → уровень E.

Критерии приёмки (синтетика): 0 повышений E без документа; «независимый» — только с отметкой человека (independent_by).
Уровень НИКОГДА не хранится как вводимое значение: он вычисляется из привязанных документов (потолок), поэтому
повысить E без документа невозможно по построению; запрос выше потолка отклоняется ValueError.

НЕ ПОДТВЕРЖДЕНО: условия ступеней E1–E4 ниже приняты консервативно (док. 63 §9 и ТЗ 67 в репозитории нет),
сверить по эталону b15 (app/rul.js). Единственное из docs/STATE.md (15.10): E4 — только с прямой цитатой и ссылкой.
  E0 — утверждение без документа
  E1 — есть привязанный документ
  E2 — документ со ссылкой на источник (source_ref)
  E3 — документ со ссылкой, помеченный человеком как независимый (independent_by)
  E4 — независимый документ + прямая цитата + ссылка
Хранилище — в памяти процесса, маршрутов и миграций нет (персистентность — отдельным PR). Только синтетика.
"""
import itertools

LEVELS = (0, 1, 2, 3, 4)
_BOT_MARKS = {"llm", "ai", "auto", "system", "bot", "model", "gpt", "claude", "grok", "codex", "gemini", "none", "null"}


class ClaimError(ValueError):
    """Нарушение правила реестра; text — причина по-русски, code — машинный код."""

    def __init__(self, code, text):
        super().__init__(text)
        self.code, self.text = code, text


def level_code(level):
    return "E%d" % level


class Registry:
    def __init__(self):
        self._ids = itertools.count(1)
        self.claims, self.docs, self.events = {}, {}, []

    def _log(self, kind, **kw):
        self.events.append(dict(kind=kind, **kw))

    # ---- утверждения и документы ------------------------------------------
    def add_claim(self, project_id, text, source=None):
        text = (text or "").strip()
        if not str(project_id or "").strip() or not text:
            raise ClaimError("claim_invalid", "Нужны номер проекта и текст утверждения")
        cid = "C-%d" % next(self._ids)
        self.claims[cid] = {"id": cid, "project_id": project_id, "text": text, "source": source, "doc_ids": []}
        self._log("claim_added", claim=cid)
        return cid

    def add_document(self, project_id, title, source_ref=None, quote=None, registered_by=None):
        if not str(title or "").strip() or not str(project_id or "").strip():
            raise ClaimError("doc_invalid", "Нужны номер проекта и название документа")
        did = "D-%d" % next(self._ids)
        self.docs[did] = {"id": did, "project_id": project_id, "title": title.strip(),
                          "source_ref": (source_ref or "").strip() or None, "quote": (quote or "").strip() or None,
                          "registered_by": registered_by, "independent_by": None}
        self._log("doc_added", doc=did)
        return did

    def link(self, claim_id, doc_id):
        c, d = self._claim(claim_id), self._doc(doc_id)
        if c["project_id"] != d["project_id"]:
            raise ClaimError("project_mismatch", "Документ другого проекта нельзя привязать к утверждению")
        if doc_id not in c["doc_ids"]:
            c["doc_ids"].append(doc_id)
            self._log("linked", claim=claim_id, doc=doc_id)

    def unlink(self, claim_id, doc_id):
        c = self._claim(claim_id)
        if doc_id in c["doc_ids"]:
            c["doc_ids"].remove(doc_id)
            self._log("unlinked", claim=claim_id, doc=doc_id)

    def mark_independent(self, doc_id, independent_by):
        """Отметка человека. Пустое имя и служебные/машинные метки отклоняются."""
        who = str(independent_by or "").strip()
        if len(who) < 3 or who.lower() in _BOT_MARKS:
            raise ClaimError("independent_by_required", "«Независимый» ставит только человек: укажите, кто именно")
        self._doc(doc_id)["independent_by"] = who
        self._log("independent_marked", doc=doc_id, by=who)

    def clear_independent(self, doc_id):
        self._doc(doc_id)["independent_by"] = None
        self._log("independent_cleared", doc=doc_id)

    def edit_document(self, doc_id, **fields):
        """Правка документа. Поле independent_by так не меняется (только mark_independent)."""
        d = self._doc(doc_id)
        for k, v in fields.items():
            if k not in ("title", "source_ref", "quote"):
                raise ClaimError("field_forbidden", "Это поле так не меняется: %s" % k)
            d[k] = (v or "").strip() or None
        self._log("doc_edited", doc=doc_id)

    # ---- уровень -----------------------------------------------------------
    def level(self, claim_id):
        """Потолок E по привязанным документам; без документа — 0. Единственный источник уровня."""
        best = 0
        for did in self._claim(claim_id)["doc_ids"]:
            d = self.docs[did]
            lv = 1
            if d["source_ref"]:
                lv = 2
                if d["independent_by"]:
                    lv = 3
                    if d["quote"]:
                        lv = 4
            best = max(best, lv)
        return best

    def request_level(self, claim_id, target):
        """Запрос уровня: возвращает target, если он не выше потолка, иначе ClaimError. Понижать можно всегда."""
        if target not in LEVELS:
            raise ClaimError("level_invalid", "Уровень от E0 до E4")
        ceiling = self.level(claim_id)
        if target > ceiling:
            self._log("raise_denied", claim=claim_id, target=target)
            raise ClaimError("no_document", "Уровень %s не подтверждён документом (сейчас максимум %s)"
                             % (level_code(target), level_code(ceiling)))
        return target

    def view(self, claim_id):
        c = self._claim(claim_id)
        return {"id": c["id"], "project_id": c["project_id"], "text": c["text"], "level": level_code(self.level(claim_id)),
                "docs": [dict(self.docs[d]) for d in c["doc_ids"]]}

    def _claim(self, cid):
        if cid not in self.claims:
            raise ClaimError("claim_unknown", "Утверждение не найдено")
        return self.claims[cid]

    def _doc(self, did):
        if did not in self.docs:
            raise ClaimError("doc_unknown", "Документ не найден")
        return self.docs[did]
