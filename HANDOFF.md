# HANDOFF

Обновлять при каждом коммите.

## Задача / issue
HQ-036 — линия A9 (#6 head 83ab1f7 + #11 head 90e2d22) на линии tilda (Refs #22), ветка `chore/hq-036-rebase-a9-on-tilda` от fix/hq-035-fact-ceiling (85d424a, верх линии tilda с HQ-035). PR → s1/tilda-readiness-pack.
Влит f518a66 из fix/hq-035-fact-ceiling (HQ-035 · G-13) merge-коммитом.

## Сделано
### HQ-035 — потолок FACT (база, до 85d424a)
- Код ставит максимум FACT_CANDIDATE («заявление с документом»); verified_by от LLM обнуляется.
- FACT — только POST /s1/jobs/<id>/slots/verify (персональный токен, роль из working_values.yaml fact_signoff.signer_roles), пакетом на слот, журнал s1_fact_signoffs (кто, когда, роль, основание).
- Тесты: tests/test_hq035_fact_signoff.py (ловушка, подпись → FACT + журнал, отказы). Баллы не изменились.

### HQ-036 — A9 на линии tilda
- Влит 90e2d22 (содержит 83ab1f7) merge-коммитом, без rebase.
- app.py: импорты объединены (s1 init_s1 + founder_package/iso_z/utc_now из A9; post_completion_json больше не импортируется — A9 доставляет completion через delivery_worker); лимит запроса 20 МБ (A9) и init_s1 (S1) — оба сохранены.
- README.md: раздел S1 сохранён целиком; строка Worker: `python delivery_worker.py` из A9 — рядом с Build/Start.
- Полный прогон: 197 passed, 0 failed, 0 skipped (с тестами A9).

### HQ-035 · G-13 (f518a66, влит сюда merge-коммитом)
- G-13 закрыт владельцем: FACT ставит только роль owner или назначенный им analyst (выданный аккаунт с ролью analyst). signer_roles = [owner, analyst], без имён. Тесты G13SignerRoleTests: чужая роль → 403, слот остаётся FACT_CANDIDATE; owner и analyst подписывают. Ловушку из Issue #21 не меняли.
- Ревью — Claude; merge только после PASS от Claude (не GPT).

## Дальше
- HQ-022: ветка от этой линии, только merge.

## Сломано или не проверено
- «CI прогон зелёный» (Issue #22): в репо нет тестового workflow (только claude.yml в PR #8) — пробел для PM.
- delivery_worker вживую (Render worker) не запускался — только тесты.
- Имена подписантов и их роли — только через окружение S1_APPROVERS / S1_APPROVER_ROLES; в репо не хранятся.

## Последний коммит
см. git log -1 (Merge fix/hq-035-fact-ceiling (G-13) в chore/hq-036-rebase-a9-on-tilda)
