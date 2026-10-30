# Проверка стенда 30.10 · ловушки окружения staging (ветка `fix/staging-guard-env-traps`)

1. **Текст комментария как секрет (высокая серьёзность при ошибке настройки).** `docs/deploy/STAGING.md` п.1 велит
   `cp .env.example .env.staging`. В `.env.example` строки вида `SESSION_SECRET=   # Lockbox; openssl rand -hex 32 (≥32 символов)`.
   docker compose (env_file) отдаёт незаполненной переменной текст комментария. Проверено на master 30.10: стенд
   **стартует** с `secret_key = '# Lockbox; openssl rand -hex 32 (≥32 символов)'` — ключ подписи cookie известен из git,
   можно подделать сессию владельца. То же для `S1_ADMIN_TOKEN` (≥24 символа). Исправлено: `staging_guard.comment_values` —
   значение секрета, адреса или allowlist (`COMMENT_CHECKED`), начинающееся с «#», → отказ старта со списком имён;
   прочие переменные не проверяются (пароль SMTP может начинаться с «#»). Investor без scope = «*» оставлен как задумано (`arthur:investor:*`).
2. **Основатель без scope.** `email:founder` без кейса получает scope «*» (так задумано парсером MOS-1, тест
   `test_allowlist_parser`) — основатель видит и удаляет чужие сессии тренажёра. Парсер не менялся; на staging такая запись
   теперь → отказ старта.
- Документация: `docs/deploy/STAGING.md` п.2 дополнен. Тесты: `tests/test_mos6_staging.py::test_comment_text_as_value_refused`,
  `::test_founder_without_scope_refused`.
- Проверено на контейнере: наш `.env.staging` — старт есть; `.env.example` с частично заполненными значениями — master стартует
  с ключом-комментарием, ветка — отказ.
