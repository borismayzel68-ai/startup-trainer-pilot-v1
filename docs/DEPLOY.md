# Деплой staging (MOS-6): Yandex Cloud

Закрытый staging MOS / Artgen Copilot. **Только синтетические данные** (до решения юриста по #68: OpenAI в США, 152-ФЗ). **Выпуск на живых пользователей — только после явного утверждения Бориса.**

Облако — Yandex Cloud (решение Бориса, 14.10). Render больше не используется (`render.yaml` удалён).

## 1. Целевое окружение
- Облако `cloud-haze-227`, каталог `default`.
- ID облака и каталога **не хранятся в коде**: `YC_CLOUD_ID` / `YC_FOLDER_ID` — Repository variables GitHub (для CI) и/или переменные окружения оператора (`yc config`). Значения известны владельцу (см. консоль Yandex Cloud / комментарий в #57).
- Состав: Container Registry (образ), Serverless Container (приложение), Managed Service for PostgreSQL (БД), Lockbox (секреты).
- ⚠️ Состав сервисов выбран Claude как разумный минимум: текст требований из комментария к #57 в момент работы был недоступен исполнителю — сверить с ним при ревью.

## 2. Деплой через CI (предложение, не включён)
- Workflow лежит как `docs/deploy/yc-deploy.yml.proposed`: Claude не может пушить в `.github/workflows/` (нет права `workflows`). После ревью MOS Tech переносит его в `.github/workflows/yc-deploy.yml`.
- Запуск только вручную (`workflow_dispatch`).
- **Пока секрет `YC_SA_KEY` пуст, деплой-шаги пропускаются** (job зелёный, в логе notice). Сервисный аккаунт и ключ будут после 20.10.
- Repository variables: `YC_CLOUD_ID`, `YC_FOLDER_ID`, `YC_REGISTRY_ID`, `YC_CONTAINER_NAME`, `YC_SERVICE_ACCOUNT_ID`. Secret: `YC_SA_KEY` (JSON-ключ SA; роли `container-registry.images.pusher`, `serverless-containers.editor`, `iam.serviceAccounts.user`).

## 3. Секреты и переменные приложения
Секреты — в Yandex Lockbox, подключаются к ревизии контейнера; в репо и GitHub их нет.
| Переменная | Откуда |
|---|---|
| `DATABASE_URL` | Managed PostgreSQL (Lockbox) |
| `SESSION_SECRET` | `openssl rand -hex 32` (≥32 символов) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google Cloud → OAuth client (Web) |
| `AUTH_ALLOWLIST` | email через запятую; не из списка — 403 |
| `OAUTH_REDIRECT_URI` | `https://<custom-domain>/auth/callback` (добавить в Authorized redirect URIs) |
| `APP_ENV=staging` | включает вход и проверки staging |
| `STAGING_SYNTHETIC_ONLY` | по умолчанию `true`; `false` → приложение не стартует |

Без любой из обязательных переменных при `APP_ENV=staging` приложение не стартует и пишет, какой переменной нет. Список без значений — `.env.example`.

## 4. Только синтетические данные (до решения по #68)
При `APP_ENV=staging` приложение при старте:
1. отказывается запускаться, если `STAGING_SYNTHETIC_ONLY` выключен (`false`, `0`, `no`, `off`, любое значение кроме true/1/yes/on); не задан = `true`;
2. подключается к `DATABASE_URL` и отказывается запускаться, если в `users` есть запись с `is_synthetic = false` или email не на `@example.test`; недоступная БД — тоже отказ (fail-closed); если таблицы `users` ещё нет (до миграций) — проверка проходит.

Seed: `python scripts/seed_synthetic.py` — только синтетика `@example.test`; скрипт сам откажется работать на БД с не-синтетическими записями. **Реальный импорт данных на staging запрещён до одобрения Бориса и решения юриста.** Для LLM на staging по умолчанию `S1_LLM_PROVIDER=mock`.

Ограничение: проверка данных охватывает таблицу `users`; в S1-таблицах (`s1_readiness_jobs`) нет признака синтетичности — вебхук Tilda на staging закрыт сессией, реальные заявки туда не попадут.

## 5. Первый деплой
1. Создать в каталоге: реестр, Managed PostgreSQL, сервисный аккаунт, секреты Lockbox, Serverless Container (вручную или `yc`).
2. После деплоя ревизии: `/health` → 200; `/` без входа → редирект на `/auth/login`; `/api/*`, `/s1/*` → 401.
3. Миграции идут в команде запуска ревизии (`python scripts/hq018_migrate.py`, идемпотентны).

Закрыто на staging: все страницы и API кроме `/health`, `/robots.txt`, `/auth/*`. Индексация запрещена: `X-Robots-Tag: noindex, nofollow, noarchive`, `<meta name="robots">`, `robots.txt` с `Disallow: /`.

## 6. Custom domain
Домен: `<custom-domain>` (плейсхолдер). Привязать к Serverless Container (API Gateway / домен контейнера в консоли YC), сертификат — Certificate Manager; затем обновить `OAUTH_REDIRECT_URI` и redirect URI в Google; передеплоить.

## 7. Бэкап БД
Решение Д-79 (13.10.2026) было принято для Render. Для Managed PostgreSQL в Yandex Cloud: автоматические резервные копии и PITR — штатные средства сервиса (срок хранения и окно уточнить владельцу). Шаг резервной копии в деплой не добавляется; CI к базе не подключается. **Требует подтверждения Бориса/MOS Tech**: распространяется ли Д-79 на Yandex Cloud.

## 8. Откат
- Код: предыдущая ревизия Serverless Container (`yc serverless container revision list`, переключить трафик/задеплоить прежний образ).
- Миграции только добавляют (`CREATE TABLE IF NOT EXISTS`), откат кода их не требует. Откат схемы — восстановление БД из резервной копии/PITR.
- Аварийно закрыть доступ: остановить/удалить публичный доступ к контейнеру (очистить `AUTH_ALLOWLIST` нельзя — сервис не стартует).
