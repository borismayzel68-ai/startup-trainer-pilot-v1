# HANDOFF

Обновлять при каждом коммите.

## Задача / issue
HQ-035 — потолок FACT (Refs #21), ветка `fix/hq-035-fact-ceiling` (от hq028/exit-intro через hq035/fact-signoff). PR → s1/tilda-readiness-pack, после #14.

## Сделано
- Код ставит максимум FACT_CANDIDATE («заявление с документом»); verified_by от LLM обнуляется.
- FACT — только POST /s1/jobs/<id>/slots/verify (персональный токен, роль из working_values.yaml fact_signoff.signer_roles = [analyst]), пакетом на слот, журнал s1_fact_signoffs (кто, когда, роль, основание).
- Тесты: tests/test_hq035_fact_signoff.py (ловушка, подпись → FACT + журнал, отказы). Баллы не изменились.

## Дальше
- Влить hq028/exit-intro после фикса основ фамилий (PR #14), только merge.

## Сломано или не проверено
- Кто подписывает (G-13) не решено: в конфиге только роль `analyst`, имена — через окружение.

## Последний коммит
см. git log -1 (HQ-035: роль analyst по Issue #21, HANDOFF)
