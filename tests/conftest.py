import sys

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "auth_enforced: тест работает с включённым RBAC (MOS-1, #52)")


@pytest.fixture(autouse=True)
def _legacy_tests_without_rbac(request):
    """Тесты до MOS-1 вызывают маршруты без входа через Google. Для них RBAC выключен флагом конфигурации
    (не переменной окружения — в проде отключить нельзя). Тесты с маркером auth_enforced проверяют RBAC."""
    app_module = sys.modules.get("app")
    if app_module is None:
        yield
        return
    flask_app = app_module.app
    previous = flask_app.config.get("AUTH_ENFORCED", True)
    flask_app.config["AUTH_ENFORCED"] = bool(request.node.get_closest_marker("auth_enforced"))
    yield
    flask_app.config["AUTH_ENFORCED"] = previous
