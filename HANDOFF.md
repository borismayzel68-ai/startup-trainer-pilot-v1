# HANDOFF

Обновлять при каждом коммите.

## Задача / issue
#31 — разрешить Claude (GitHub Action) запускать pytest и открывать PR. Ветка `ci/claude-allow-tests`. Исполнитель: Claude (из чата: GitHub App не может менять workflow — см. комментарий в #31).

## Сделано
- `.github/workflows/claude.yml`: setup-python 3.13 + зависимости (как в tests.yml), `S1_LLM_PROVIDER=mock`, `GH_TOKEN` = токен workflow.
- `--allowedTools`: `python -m pytest`, `pytest`, `gh pr create`, `gh pr view`, `git status/diff/log`.
- `--disallowedTools`: `gh pr merge`, `gh secret`, `gh api`, `git push … master`, `curl`, `wget`.
- Секреты не менялись.

## Дальше
- Ревью и мерж — Руководитель бизнеса (master защищён ruleset).
- После мержа: повторить проверочную задачу (#29), убедиться, что Claude сам гоняет pytest и открывает PR.

## Сломано или не проверено
- Сам запуск Action с новыми правами проверяется только после мержа в master (workflow на issue_comment берётся из master).

## Последний коммит
ci(claude): allow pytest and gh pr create, deny merge/secrets (#31)
