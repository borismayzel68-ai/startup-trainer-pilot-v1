# Staging на своей машине: docker-compose.staging.yml

Не проверено: образ и compose на машине автора не собирались и не запускались (Docker не было). Первая сборка на стенде — это и есть проверка.

## Подготовка
1. `cp .env.example .env.staging`, `chmod 600 .env.staging`; заполнить значения (не в git). Добавить `STAGING_DB_PASSWORD` (пароль Postgres внутри compose).
2. `DATABASE_URL` / `SESSION_DATABASE_URL` в `.env.staging` не задавать — compose собирает адрес базы сам.
   Внимание (проверка стенда 30.10): docker compose читает `env_file` так, что незаполненная строка из `.env.example` вида
   `SESSION_SECRET=   # комментарий` получает значение «# комментарий». Незаполненные строки удалить; комментарии — только
   на отдельной строке. Staging с таким значением не стартует (`staging_guard.comment_values`).
   `AUTH_ALLOWLIST`: основатель — только с кейсом, `email:founder:<ID кейса>`; без кейса staging не стартует
   (иначе scope «*» — доступ ко всем кейсам).
3. За nginx: `TRUSTED_PROXY_COUNT=1` (по умолчанию в compose). Обязательны `PUBLIC_BASE_URL=https://<домен>`, `AUTH_REDIRECT_URI=https://<домен>/auth/callback`, `SESSION_SECRET`, Google OAuth, `AUTH_ALLOWLIST`, `OPENAI_API_KEY`, `OPENAI_BASE_URL` (не api.openai.com).
4. nginx — по `docs/deploy/nginx.staging.conf.example`, сертификат certbot.

## Запуск
```
docker compose --env-file .env.staging -f docker-compose.staging.yml config -q   # проверка файла
docker compose --env-file .env.staging -f docker-compose.staging.yml up -d --build
docker compose -f docker-compose.staging.yml ps          # migrate: Exited (0); app и db: healthy
docker compose -f docker-compose.staging.yml logs migrate app
```
`--env-file` нужен для подстановки `${STAGING_DB_PASSWORD}`; без него compose остановится с ошибкой «задайте STAGING_DB_PASSWORD».

## Что проверить
- `migrate` завершился с кодом 0, в логе `migrations ok`; seed не запускался.
- Демо-пользователей нет: `docker compose -f docker-compose.staging.yml exec db psql -U trainer -d trainer_staging -c "select count(*) from users where email like '%@example.test'"` → 0.
- `curl -i https://<домен>/health` → 200; `curl -I http://<домен>/` → 301 на https.
- Cookie сессии: Secure, HttpOnly, SameSite=Lax; в `/admin/audit` IP пользователя, а не адрес nginx.
- Дальше — `docs/for-sergey/02_TZ_chek-list_priyomki_stenda.md` (если влит).

## Известные пробелы
- `delivery_worker.py` не запускается (как и в `docker-compose.yml`, Dockerfile, deploy.sh).
- 2 воркера gunicorn: лимит кнопки «Сообщить о проблеме» (в памяти процесса) считается на воркер.
- Бэкап Postgres не настроен — нужен отдельный дамп за пределы машины.
