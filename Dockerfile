# HQ-018 · E-1: образ приложения для локальной тестовой среды (только синтетические данные).
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pytest
COPY . .
RUN chmod +x deploy/yc/*.sh
ENV PORT=5001
EXPOSE 5001
# Миграции и seed — НЕ при старте контейнера: отдельный шаг деплоя (deploy/yc/deploy.sh, scripts/migrate_job.py).
# Локально: docker compose run --rm app python scripts/hq018_migrate.py
CMD ["sh", "-c", "gunicorn -b 0.0.0.0:${PORT} app:app"]
