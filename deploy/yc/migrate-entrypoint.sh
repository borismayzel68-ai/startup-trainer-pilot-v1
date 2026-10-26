#!/bin/sh
# Приватный контейнер миграций: вызывается только с IAM-токеном (роль serverless.containers.invoker).
exec python scripts/migrate_job.py
