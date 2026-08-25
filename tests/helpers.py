"""Общие заглушки для тестов автопостинга.

Тесты не ходят в сеть и не требуют ключей: `requests` подменяется до
импорта проверяемого модуля, ответы Mistral и Яндекс.Диска задаёт сам тест.
"""

import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE_ENV = {
    "MISTRAL_API_KEY": "test-key",
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_CHANNEL": "@test",
    "YANDEX_TOKEN": "test-token",
}


class Response:
    """Ответ requests: работает и как объект, и как контекст-менеджер."""

    def __init__(self, status=200, content_type="image/jpeg",
                 body=b"\xff\xd8\xff\xe0test", headers=None, payload=None):
        self.status_code = status
        self.content = body
        self.ok = 200 <= status < 300
        self.text = body if isinstance(body, str) else ""
        self.headers = headers if headers is not None else {"Content-Type": content_type}
        self._payload = payload or {}

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i:i + chunk_size]

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RequestsStub(types.ModuleType):
    """Подмена requests: маршруты задаёт тест, всё остальное — ошибка сети.

    routes: url -> Response, или callable без аргументов, или None —
    None означает «соединение не удалось».
    """

    def __init__(self):
        super().__init__("requests")
        self.routes = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url))
        if url not in self.routes:
            raise RuntimeError(f"нет маршрута для {url}")
        route = self.routes[url]
        if callable(route):
            route = route()
        if route is None:
            raise RuntimeError("соединение не удалось")
        return route

    def post(self, url, **kwargs):
        self.calls.append(("post", url))
        return Response(payload={})


def load_module(name="generate_content"):
    """Импортирует модуль проекта с подменённым requests и фиктивными ключами."""
    os.environ.update(FAKE_ENV)
    stub = RequestsStub()
    sys.modules["requests"] = stub

    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, stub


class Checks:
    """Простой счётчик проверок — без внешних зависимостей."""

    def __init__(self, title):
        self.title = title
        self.failed = 0

    def ok(self, name, condition, info=""):
        if condition:
            print(f"ok   {name}")
        else:
            self.failed += 1
            print(f"FAIL {name}" + (f"  -> {info!r}" if info != "" else ""))

    def finish(self):
        if self.failed:
            print(f"\n=== {self.title}: провалов {self.failed}")
        else:
            print(f"\n=== {self.title}: все проверки прошли")
        return 1 if self.failed else 0
