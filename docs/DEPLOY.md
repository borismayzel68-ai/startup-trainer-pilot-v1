# Деплой staging (MOS-6)

Развёртывание перенесено с Render на **Yandex Cloud (ru-central1)** по решению Бориса от 14.10.2026.
Полная пошаговая инструкция, список секретов и IAM-ролей, снимок перед миграцией и откат —
в [README.md](../README.md), раздел «Развёртывание в Yandex Cloud».

Файлы: `deploy/yc/deploy.sh`, `deploy/yc/rollback.sh`, `deploy/yc/deploy-workflow.example.yml`,
`scripts/migration_guard.py`, `scripts/migrate_job.py`, `scripts/seed_synthetic.py`.
Домен — плейсхолдер `<custom-domain>`. Выпуск на живых пользователей — только после утверждения Бориса.
