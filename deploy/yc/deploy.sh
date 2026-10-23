#!/usr/bin/env bash
# MOS-6 · деплой staging в Yandex Cloud (ru-central1): образ -> снимок БД (если нужен) -> миграции -> web.
# Запускается оператором или deploy-workflow (docs/deploy/yc-deploy.yml.proposed), НЕ из tests.yml.
# Секреты сюда не передаются: приложение читает их из Yandex Lockbox (секрет подключается к ревизии).
# Не проверено на живом облаке: флаги yc сверить с `yc serverless container revision deploy --help`.
set -euo pipefail

: "${YC_FOLDER_ID:?}" "${YC_REGISTRY_ID:?}" "${YC_NETWORK_ID:?}" "${YC_RUNTIME_SA_ID:?}" "${YC_PG_CLUSTER_ID:?}" "${LOCKBOX_SECRET_ID:?}" "${AUTH_REDIRECT_URI:?}" "${PUBLIC_BASE_URL:?}"
TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)}"
IMAGE="cr.yandex/${YC_REGISTRY_ID}/mos-staging:${TAG}"
WEB=mos-staging
MIG=mos-staging-migrate

# Сначала только чтение/проверки, потом изменения.
SECRET_VER=$(yc lockbox secret get --id "$LOCKBOX_SECRET_ID" --format json \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["current_version"]["id"])')

# Все ключи секрета (имена без значений) подключаются как переменные окружения контейнера.
secret_args() {
  for k in DATABASE_URL SESSION_SECRET GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET AUTH_ALLOWLIST; do
    printf -- '--secret environment-variable=%s,id=%s,version-id=%s,key=%s\n' "$k" "$LOCKBOX_SECRET_ID" "$SECRET_VER" "$k"
  done
}

echo "== 1. образ $IMAGE"
yc container registry configure-docker >/dev/null
docker build -t "$IMAGE" .
docker push "$IMAGE"

echo "== 2. деструктивные миграции?"
SNAPSHOT_ID=""
set +e; python3 scripts/migration_guard.py; GUARD=$?; set -e
if [ "$GUARD" -eq 10 ]; then
  echo "Есть миграции, меняющие/удаляющие данные: делаю резервную копию кластера (точка восстановления)."
  OP=$(yc managed-postgresql cluster backup "$YC_PG_CLUSTER_ID" --format json)
  SNAPSHOT_ID=$(printf '%s' "$OP" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("metadata",{}).get("backup_id") or d.get("id",""))')
  [ -n "$SNAPSHOT_ID" ] || { echo "Резервная копия не создана — деплой остановлен, миграции НЕ применялись." >&2; exit 1; }
  echo "backup: $SNAPSHOT_ID (жду статус DONE)"
  # Ждём не дольше ~10 минут (40 x 15 с); любой статус кроме CREATING/DONE — ошибка, миграции не применяются.
  BACKUP_OK=""
  for _ in $(seq 40); do
    BSTATUS=$(yc managed-postgresql backup get "$SNAPSHOT_ID" --format json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')
    case "$BSTATUS" in
      DONE) BACKUP_OK=1; break ;;
      CREATING) sleep 15 ;;
      *) echo "Резервная копия $SNAPSHOT_ID в статусе '$BSTATUS' — деплой остановлен, миграции НЕ применялись." >&2; exit 1 ;;
    esac
  done
  [ -n "$BACKUP_OK" ] || { echo "Резервная копия $SNAPSHOT_ID не готова за 10 минут — деплой остановлен, миграции НЕ применялись." >&2; exit 1; }
elif [ "$GUARD" -ne 0 ]; then
  echo "migration_guard упал (код $GUARD)" >&2; exit 1
fi

echo "== 3. миграции (отдельный приватный контейнер $MIG)"
yc serverless container get --name "$MIG" >/dev/null 2>&1 || yc serverless container create --name "$MIG" >/dev/null
# shellcheck disable=SC2046
yc serverless container revision deploy --container-name "$MIG" --image "$IMAGE" \
  --command /app/deploy/yc/migrate-entrypoint.sh \
  --cores 1 --memory 512MB --concurrency 1 --execution-timeout 300s \
  --service-account-id "$YC_RUNTIME_SA_ID" --network-id "$YC_NETWORK_ID" \
  $(secret_args) >/dev/null
MIG_URL=$(yc serverless container get --name "$MIG" --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["url"])')
CODE=$(curl -sS -o /tmp/migrate.out -w '%{http_code}' -X POST "${MIG_URL%/}/run" \
  -H "Authorization: Bearer $(yc iam create-token)" -H 'Content-Type: application/json' \
  -d "{\"snapshot_id\": \"${SNAPSHOT_ID}\"}")
cat /tmp/migrate.out; echo
[ "$CODE" = 200 ] || { echo "Миграции не применены (HTTP $CODE) — web не обновлялся." >&2; exit 1; }

echo "== 4. web-сервис $WEB"
yc serverless container get --name "$WEB" >/dev/null 2>&1 || yc serverless container create --name "$WEB" >/dev/null
# shellcheck disable=SC2046
yc serverless container revision deploy --container-name "$WEB" --image "$IMAGE" \
  --command /app/deploy/yc/web-entrypoint.sh \
  --cores 1 --memory 1GB --concurrency 8 --execution-timeout 120s \
  --service-account-id "$YC_RUNTIME_SA_ID" --network-id "$YC_NETWORK_ID" \
  --environment APP_ENV=staging,S1_LLM_PROVIDER=mock,AUTH_REDIRECT_URI="$AUTH_REDIRECT_URI",PUBLIC_BASE_URL="$PUBLIC_BASE_URL" \
  $(secret_args) >/dev/null
# Публичный вызов нужен браузерам; доступ к страницам закрывает сам сервис (вход + allowlist).
yc serverless container allow-unauthenticated-invoke --name "$WEB" >/dev/null

WEB_URL=$(yc serverless container get --name "$WEB" --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["url"])')
echo "== 5. проверка ${WEB_URL}health"
curl -fsS "${WEB_URL%/}/health" && echo
echo "OK: $IMAGE (откат: deploy/yc/rollback.sh)"
