#!/bin/sh
# Web-сервис в Yandex Serverless Container. Миграций и seed здесь НЕТ — это отдельный шаг деплоя.
exec gunicorn -b 0.0.0.0:$PORT --timeout 120 app:app
