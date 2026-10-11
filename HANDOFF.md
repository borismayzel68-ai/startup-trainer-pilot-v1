# HANDOFF

Обновлять при каждом коммите.

## Задача / issue
HQ-035 — потолок FACT (Refs #21), ветка `fix/hq-035-fact-ceiling` (от hq028/exit-intro через hq035/fact-signoff). PR → s1/tilda-readiness-pack, после #14.

## Сделано
- Код ставит максимум FACT_CANDIDATE («заявление с документом»); verified_by от LLM обнуляется.
- FACT — только POST /s1/jobs/<id>/slots/verify (персональный токен, роль из working_values.yaml fact_signoff.signer_roles = [owner, analyst]), пакетом на слот, журнал s1_fact_signoffs (кто, когда, роль, основание).
- Тесты: tests/test_hq035_fact_signoff.py (ловушка, подпись → FACT + журнал, отказы). Баллы не изменились.
- G-13 закрыт владельцем: FACT ставит только роль owner или назначенный им analyst (выданный аккаунт с ролью analyst). signer_roles = [owner, analyst], без имён. Тесты G13SignerRoleTests: чужая роль → 403, слот остаётся FACT_CANDIDATE; owner и analyst подписывают. Ловушку из Issue #21 не меняли.
- Ревью — Claude; merge только после PASS от Claude (не GPT).

## Дальше
- Влит hq028/exit-intro 42de16b (фикс основ имён, PR #14) — merge, без rebase.

## Сломано или не проверено
- Имена подписантов и их роли — только через окружение S1_APPROVERS / S1_APPROVER_ROLES; в репо не хранятся.

## Последний коммит
см. git log -1 (G-13: роль owner в подписантах FACT + тесты)
