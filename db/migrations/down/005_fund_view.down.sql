-- Откат 005_fund_view (MOS-5 вид фонда). Применять вручную, только после снимка БД (данные decision_snapshots/ic_decisions удаляются).
-- Отдельного VIEW в 005 нет: миграция создаёт таблицы decision_snapshots, ic_decisions, триггер и функцию — их и удаляем.
-- Идемпотентно (IF EXISTS). Папка down/ не читается hq018_migrate.py (glob только db/migrations/*.sql).
DROP VIEW IF EXISTS fund_view;
DROP TRIGGER IF EXISTS ic_decisions_no_change ON ic_decisions;
DROP TABLE IF EXISTS ic_decisions;
DROP TABLE IF EXISTS decision_snapshots;
DROP FUNCTION IF EXISTS fund_view_append_only();
