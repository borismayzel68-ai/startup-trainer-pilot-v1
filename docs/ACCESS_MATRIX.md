# Матрица доступа: роли × маршруты (MOS-1, Refs #52)

Источник истины — код: декоратор `require_role(roles, scope, external)` в `auth.py`; для S1 — таблица `S1_POLICY` в `app.py`.
Приложение **не стартует**, если у какого-либо маршрута нет `require_role` / `public`; тот же контроль есть в `tests/test_auth.py`.
Сайт не публичный: вход только по allowlist, `noindex` (meta + `X-Robots-Tag` + `robots.txt` Disallow: /).

## Вход
- Google OAuth (OIDC): `GET /auth/login` → Google → `GET /auth/callback`.
- `AUTH_ALLOWLIST` — `email:роль[:scope]`, записи через запятую/`;`. Scope по умолчанию `*`. Email вне списка → **403**, пользователь не создаётся.
- Стартовые гранты создаются из allowlist при входе. Отозванный грант (`role_grants.revoked_at`) **не воскрешается** и не действует: роли читаются из БД на каждом запросе.
- Env: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `SESSION_SECRET`, `AUTH_ALLOWLIST`, при необходимости `AUTH_REDIRECT_URI`.
- Cookie: `Secure`, `HttpOnly`, `SameSite=Lax`, срок 12 ч. CSRF: любой не-GET запрос с сессией должен нести `X-CSRF-Token` (или поле `csrf_token`); токен — `GET /auth/me` / meta `csrf-token`.

## Scope
`role_grants.scope`: `*` — все кейсы; иначе ID кейса (для тренажёра — `project_id`, для S1 — `job_id`).
«ID кейса или `*`» — грант подходит, если scope = `*` или совпадает с кейсом маршрута. «Только `*`» — маршруты без одного кейса (списки, свипы). Неизвестный кейс → только грант `*`.

## Роли
owner (Владелец фонда), fund_staff (Сотрудник фонда), investor (Фонд / инвестор), founder (Основатель), expert, signer, clinic, syndicator.
Роли expert, signer, clinic, syndicator заведены в БД, но ни одному маршруту пока не разрешены (кроме общих `/`, `/auth/me`, `/auth/logout`).

## Типы внешней проверки (`external`)
Маршруты, которые вызывают не люди в браузере, сохранили собственную проверку. Сессия с подходящей ролью и scope проходит по ролям; запрос без сессии (или с Bearer/секретом) передаётся проверке маршрута. Итог (allow/deny по коду ответа) пишется в `audit_log`.
- `secret` — Bearer-токен S1 (`S1_ADMIN_TOKEN`, персональные токены `S1_APPROVERS`), ключ вебхука Tilda, `X-Completion-Secret`.
- `link` — подписанные ссылки S1 (диалог, free-часть, кабинет).
- Подписи Human Gate / teaser / G4 / факт-подтверждение требуют **персональный токен** (`S1_APPROVERS`): сессия их не заменяет.

## Маршруты
| Маршрут | Методы | Роли | Scope | Внешняя проверка |
|---|---|---|---|---|
| `/health` | GET | публичный | — | — |
| `/robots.txt` | GET | публичный | — | — |
| `/auth/login`, `/auth/callback` | GET | публичный | — | — |
| `/auth/me`, `/auth/logout` | GET, POST | любая роль | любой грант | — |
| `/` | GET | любая роль | любой грант | — |
| `/api/start` | POST | founder, owner | ID кейса (`project_id`) или `*` | — |
| `/api/session/<sid>` | GET | founder, investor, fund_staff, owner | кейс сессии или `*` | secret |
| `/api/session/<sid>/report.md`, `/report.json` | GET | founder, investor, fund_staff, owner | кейс сессии или `*` | secret |
| `/api/session/<sid>/answer` | POST | founder, owner | кейс сессии или `*` | secret |
| `/api/session/<sid>/feedback` | POST | founder, fund_staff, owner | кейс сессии или `*` | secret |
| `/api/session/<sid>` | DELETE | founder, owner | кейс сессии или `*` | secret |
| `/api/completion-events`, `/complete`, `/package-plan`, `/package-verify` | POST | owner | только `*` | secret (`X-Completion-Secret`) |
| `/admin/sessions` | GET | owner | только `*` | — |
| `/admin/audit` | GET | owner | только `*` | — |
| `/admin?key=…` | — | **удалён → 404** (`ADMIN_KEY` больше не существует) | — | — |
| `/s1/webhooks/tilda` | POST | owner | только `*` | secret (ключ вебхука) |
| `/s1/jobs` | GET | owner, fund_staff, investor | только `*` | secret |
| `/s1/tasks`, `/s1/fund-timers/sweep` | GET, POST | owner, fund_staff | только `*` | secret |
| `/s1/jobs/<id>`, `/jobs/<id>/<artifact>`, `/free-part`, `/approvals`, `/fund-timer` | GET | owner, fund_staff, investor | `job_id` или `*` | secret |
| `/s1/jobs/<id>/approve`, `/call`, `/consents`, `/dialogue*`, `/dialogue-link`, `/free-link`, `/fact-signoffs`, `/rerun`, `/slots/verify`, `/teaser`, `/teaser/sign`, `/transfers`, `/vertical`, `/s1/transfers/<tid>/g4` | GET, POST | owner, fund_staff | `job_id` или `*` | secret (+ персональный токен для подписей) |
| `/s1/dialogue/<id>`, `/s1/free/<id>`, `/s1/cabinet/<id>/consent`, `/revoke`, `/consents` | GET, POST | founder, owner, fund_staff | `job_id` или `*` | link |
| `/s1/shared/<tid>` | GET | investor, owner, fund_staff | любой грант | link |
| `/s6/gates`, `/s6/gates/<id>`, `/s6/gates/<id>/forward-to-s2` | GET, POST | owner, fund_staff | только `*` | — |
| `POST /s2/checks` | POST | owner, fund_staff или admin-токен, и только пройденный S6. Нет id → 409. Периметр NEEDS_EVIDENCE → **403 blocked** и запись deny в `audit_log` для всех ролей, включая admin и owner. Исключение — явное решение человека (`s6.human_waiver` в аудит-логе), не поле release в JSON | — | secret |
| `/s6/gates/<id>/release` | POST | owner | только `*` | — (решение человека о снятии блокировки) |

Отказ в любом маршруте: без входа — 401 (HTML-GET → redirect `/auth/login`), вошёл, но нет роли/scope или нет CSRF — 403.

## Аудит (`audit_log`)
Поля: время, actor (email), action (`login` / `logout` / `view` / `change`), object (путь), result (`allow` / `deny`), IP (`X-Forwarded-For`, иначе адрес сокета), detail (кейс, роли, причина отказа).
Пишутся: вход и отказ во входе, выход, каждый allow/deny по маршрутам (просмотр кабинета/сессии — `view`, изменения — `change`). Просмотр: `GET /admin/audit` (owner).
`/health` и `/robots.txt` не аудируются. Данные в аудите — только метаданные доступа, без содержимого кейсов.

## Известные ограничения
- Миграция `001_roles_access.sql` применяется приложением при старте (Postgres) и скриптом `scripts/hq018_migrate.py`; в SQLite (тесты) используется эквивалентный DDL.
- Сроки сессии 12 ч; принудительного завершения чужих сессий нет (отзыв гранта действует мгновенно, но не завершает сессию).
- `X-Forwarded-For` берётся из заголовка как есть; за прокси Render его выставляет платформа.
