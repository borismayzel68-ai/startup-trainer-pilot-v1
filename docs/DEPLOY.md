# Деплой staging (MOS-6): Render + Postgres

Закрытый staging MOS / Artgen Copilot. Только синтетические данные. **Выпуск на живых пользователей — только после явного утверждения Бориса.**

## 1. Что поднимает Blueprint (`render.yaml`)
- web service `mos-staging` (Dockerfile, `dockerCommand` = gunicorn без seed), `healthCheckPath: /health`;
- managed Postgres `mos-staging-db` (внешний доступ закрыт);
- `preDeployCommand: python scripts/hq018_migrate.py` — миграции при каждом деплое (идемпотентны).

## 2. Секреты (только Render dashboard → Environment)
| Переменная | Откуда |
|---|---|
| `DATABASE_URL` | подставляется Blueprint из БД |
| `SESSION_SECRET` | генерируется Blueprint (≥32 символов) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google Cloud → OAuth client (Web) |
| `AUTH_ALLOWLIST` | email через запятую; не из списка — 403 |
| `OAUTH_REDIRECT_URI` | `https://<custom-domain>/auth/callback` (добавить в Authorized redirect URIs) |

Без любой из первых пяти при `APP_ENV=staging` приложение не стартует и пишет, какой переменной нет. Список без значений — `.env.example`. Секреты в репо не коммитятся.

## 3. Первый деплой
1. Render → New → Blueprint → этот репозиторий, ветка `master`. Заполнить `sync: false` переменные.
2. После деплоя: `/health` → 200; `/` без входа → редирект на `/auth/login`; `/api/*`, `/s1/*` → 401.
3. Seed (по желанию, в Render Shell): `python scripts/seed_synthetic.py` — только синтетика `@example.test`; скрипт откажется работать, если в `users` есть не-синтетические записи.
4. **Реальный импорт данных на staging запрещён до одобрения Бориса.**

Закрыто на staging: все страницы и API кроме `/health`, `/robots.txt`, `/auth/*`. Вебхук Tilda (`/s1/*`) на staging недоступен без сессии — это намеренно. Индексация запрещена: `X-Robots-Tag: noindex, nofollow, noarchive`, `<meta name="robots">`, `robots.txt` с `Disallow: /`.

## 4. Custom domain
Домен: `<custom-domain>` (плейсхолдер).
1. Render → Service → Settings → Custom Domains → Add `<custom-domain>`.
2. DNS у регистратора: поддомен — `CNAME <sub> → mos-staging.onrender.com`; apex — `A`/`ALIAS` по значениям из Render. Старые `AAAA` удалить.
3. Дождаться Verify; сертификат Let's Encrypt Render выпускает автоматически, HTTP → HTTPS редирект включён.
4. Обновить `OAUTH_REDIRECT_URI` и redirect URI в Google; передеплоить.

## 5. Бэкап БД
- Render Postgres (платный план): автоматические ежедневные бэкапы (Point-in-Time Recovery), восстановление — Dashboard → БД → Recovery.
- Ручной дамп: `pg_dump "$EXTERNAL_DATABASE_URL" -Fc -f staging-$(date +%F).dump` (на время дампа временно добавить свой IP в allow list; дамп не коммитить, он содержит БД).
- Восстановление в новую БД: `pg_restore --no-owner -d "$NEW_DATABASE_URL" staging-YYYY-MM-DD.dump`.

## 6. Откат
- Код: Render → Service → Events/Deploys → предыдущий успешный деплой → **Rollback** (`autoDeploy: false`, деплой только вручную).
- Миграции только добавляют (`CREATE TABLE IF NOT EXISTS`), откат кода их не требует. Если нужна откатка схемы — восстановить БД из бэкапа (п. 5) и откатить код.
- Аварийно закрыть доступ: очистить `AUTH_ALLOWLIST` невозможно (сервис не стартует) — вместо этого Suspend сервис.
