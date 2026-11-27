-- Откат 008_login_tokens (#114). Идемпотентно; удаляет выданные, но не использованные токены. Вход через Google не затрагивается.
DROP TABLE IF EXISTS login_tokens;
