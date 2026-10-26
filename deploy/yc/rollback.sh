#!/usr/bin/env bash
# MOS-6 · откат кода: вернуть web-сервис на предыдущий образ (ревизия Serverless Container).
# Использование: deploy/yc/rollback.sh <image-tag>   (теги — в Container Registry, yc container image list)
# Откат БД — отдельно: восстановление из резервной копии/PITR, см. README «Откат».
set -euo pipefail
: "${YC_REGISTRY_ID:?}" "${YC_NETWORK_ID:?}" "${YC_RUNTIME_SA_ID:?}" "${LOCKBOX_SECRET_ID:?}" "${AUTH_REDIRECT_URI:?}" "${PUBLIC_BASE_URL:?}"
TAG="${1:?укажите тег образа}"
SECRET_VER=$(yc lockbox secret get --id "$LOCKBOX_SECRET_ID" --format json \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["current_version"]["id"])')
ARGS=()
for k in DATABASE_URL SESSION_SECRET GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET AUTH_ALLOWLIST; do
  ARGS+=(--secret "environment-variable=$k,id=$LOCKBOX_SECRET_ID,version-id=$SECRET_VER,key=$k")
done
yc serverless container revision deploy --container-name mos-staging \
  --image "cr.yandex/${YC_REGISTRY_ID}/mos-staging:${TAG}" --command /app/deploy/yc/web-entrypoint.sh \
  --cores 1 --memory 1GB --concurrency 8 --execution-timeout 120s \
  --service-account-id "$YC_RUNTIME_SA_ID" --network-id "$YC_NETWORK_ID" \
  --environment "APP_ENV=staging,S1_LLM_PROVIDER=openai,AUTH_REDIRECT_URI=$AUTH_REDIRECT_URI,PUBLIC_BASE_URL=$PUBLIC_BASE_URL" "${ARGS[@]}"
echo "Код откатан на $TAG. Схема БД откатом кода не меняется; текущие миграции только добавляют."
