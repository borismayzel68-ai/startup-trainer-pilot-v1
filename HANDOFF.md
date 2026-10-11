# HANDOFF

Обновлять при каждом коммите.

## Задача / issue
CI: тестовый workflow `.github/workflows/tests.yml`, ветка `ci/tests` → master (по запросу владельца).

## Сделано
- Workflow: pull_request (любая база) + push в master и s1/tilda-readiness-pack.
- ubuntu-latest, Python 3.13 (по Dockerfile ветки hq018/env-postgres), pip install из requirements*.txt (если есть) + pytest.
- Сервис postgres:16 с healthcheck, `HQ018_PG_URL` указывает на него; OPENAI_API_KEY не задан (LLM замокан, S1_LLM_PROVIDER=mock). INVITE_CODE/ADMIN_KEY/S1_FUND_NAME не задаются — тесты ждут дефолты (первый прогон с плейсхолдерами упал: 403 invite, golden-снимки).
- `python -m pytest -rA -v --junitxml=pytest-junit.xml`, лог и JUnit — артефакты `pytest-results`. Без `|| true`.

## Дальше
- Чтобы CI запускался на PR в s1/tilda-readiness-pack (#9, #10, #12, #13, #14, #26), tests.yml должен быть и в этой базовой ветке: после слияния этого PR в master — влить master в s1/tilda-readiness-pack, либо добавить тот же файл туда отдельным PR.

## Сломано или не проверено
- Секреты не добавлялись. `.github/workflows/claude.yml` (PR #8) не затронут.

## Последний коммит
0097f2f — CI: убрать плейсхолдеры INVITE_CODE/ADMIN_KEY/S1_FUND_NAME (ломают тесты); прогон зелёный: https://github.com/borismayzel68-ai/startup-trainer-pilot-v1/actions/runs/38134066999

## Слияние с master (Claude, 11.10)
- Конфликт add/add в HANDOFF.md (master получил шаблон из #24). Оставлена версия этого PR. Код не менялся.
