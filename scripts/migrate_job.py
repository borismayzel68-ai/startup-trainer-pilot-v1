"""MOS-6 · Yandex Cloud: миграции как отдельный шаг деплоя (не из CI и не при старте web-сервиса).
Запускается как приватный Serverless Container `mos-staging-migrate` внутри VPC (БД без публичного адреса).
POST /run вызывает deploy/yc/deploy.sh с IAM-токеном. Если среди миграций есть меняющие/удаляющие данные,
в теле нужен {"snapshot_id": "<id резервной копии>"} — иначе миграции не применяются (HTTP 409)."""
import contextlib
import io
import json
import os
import pathlib
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hq018_migrate  # noqa: E402
import migration_guard  # noqa: E402


def run(body: dict) -> tuple:
    bad = migration_guard.destructive_migrations()
    if bad and not str(body.get("snapshot_id") or "").strip():
        return 409, {"ok": False, "error": "snapshot_required", "migrations": bad}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            hq018_migrate.main()
    except SystemExit as e:
        return 500, {"ok": False, "error": str(e.code), "log": buf.getvalue()}
    except Exception as e:  # noqa: BLE001 — любая ошибка миграции = провал шага деплоя
        return 500, {"ok": False, "error": type(e).__name__, "log": buf.getvalue()}
    return 200, {"ok": True, "destructive": bad, "snapshot_id": body.get("snapshot_id"), "log": buf.getvalue()}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/run":
            return self._send(404, {"ok": False})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:  # json.JSONDecodeError — подкласс ValueError
            return self._send(400, {"ok": False, "error": "bad_json"})
        self._send(*run(body if isinstance(body, dict) else {}))

    def do_GET(self):
        self._send(200 if self.path == "/health" else 404, {"ok": self.path == "/health"})

    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
