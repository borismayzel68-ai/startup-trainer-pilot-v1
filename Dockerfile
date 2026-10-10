# HQ-018 · E-1: образ приложения для локальной тестовой среды (только синтетические данные).
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pytest
COPY . .
ENV PORT=10000
EXPOSE 10000
CMD ["sh", "-c", "python scripts/hq018_migrate.py && python scripts/hq018_seed.py && gunicorn -b 0.0.0.0:${PORT} app:app"]
