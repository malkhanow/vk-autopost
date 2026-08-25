#!/usr/bin/env python3
"""Загрузка картинок из интернета: фильтр форматов и повторы.

Проверяет два сценария из логов: модель отдаёт ссылку на svg (Telegram
отвечает 400) и ссылка с фотостока протухает до скачивания (404).
"""

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Checks, Response, load_module  # noqa: E402

gc, http = load_module()
c = Checks("картинки из интернета")

# Ссылки, которые «даёт модель» при запросе новой картинки
next_urls = []


def fake_mistral(prompt, max_retries=3):
    return f"IMAGE_URL: {next_urls.pop(0)}" if next_urls else "IMAGE_URL: https://site.ru/none.jpg"


gc.call_mistral_text = fake_mistral


def fetched(url):
    """Скачивает и сразу убирает временный файл, возвращает (имя, лог).

    Лог нужен, чтобы отличать осознанный пропуск от случайного падения:
    и то, и другое возвращает None.
    """
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        path = gc.fetch_image_from_url(url)
    if path:
        os.unlink(path)
    return path, log.getvalue()


def skipped(url, reason):
    """Ссылка пропущена, и пропущена именно по этой причине."""
    path, log = fetched(url)
    return path is None and reason in log, log.strip()


# ---------- Неподдерживаемые форматы отсеиваются по расширению ----------

for url in ("https://site.ru/pic.svg", "https://site.ru/pic.gif",
            "https://site.ru/a/pic.SVG?w=800&fit=crop"):
    http.calls.clear()
    path, log = fetched(url)
    c.ok(f"не качаем {url.rsplit('/', 1)[-1]}",
         path is None and not http.calls and "Telegram не принимает" in log,
         (http.calls, log.strip()))

# ---------- ...и по Content-Type ----------

http.routes.update({
    "https://site.ru/svg-ct": Response(content_type="image/svg+xml"),
    "https://site.ru/html": Response(content_type="text/html; charset=utf-8"),
    "https://site.ru/no-ct": Response(headers={}),
})
c.ok("Content-Type image/svg+xml — пропуск",
     *skipped("https://site.ru/svg-ct", "Неподдерживаемый тип «image/svg+xml»"))
c.ok("Content-Type text/html — пропуск",
     *skipped("https://site.ru/html", "Неподдерживаемый тип «text/html»"))
c.ok("без Content-Type — пропуск",
     *skipped("https://site.ru/no-ct", "Неподдерживаемый тип «не указан»"))

# ---------- Подходящие форматы скачиваются с верным расширением ----------

for content_type, ext in (("image/jpeg", ".jpg"), ("image/png", ".png"),
                          ("image/webp", ".webp"), ("image/jpg", ".jpg"),
                          ("image/jpeg; charset=utf-8", ".jpg")):
    url = f"https://site.ru/{content_type}"
    http.routes[url] = Response(content_type=content_type)
    path = gc.fetch_image_from_url(url)
    c.ok(f"{content_type} -> {ext}", path is not None and path.endswith(ext), path)
    if path:
        os.unlink(path)

# ---------- Протухшие ссылки и битые ответы ----------

http.routes.update({
    "https://unsplash.com/dead.jpg": Response(status=404, body=b""),
    "https://site.ru/empty.jpg": Response(body=b""),
    "https://site.ru/boom.jpg": None,          # обрыв соединения
    "https://ok.ru/good.jpg": Response(),
})
c.ok("404 — пропуск", *skipped("https://unsplash.com/dead.jpg", "(404)"))
c.ok("пустой файл — пропуск", *skipped("https://site.ru/empty.jpg", "пустой файл"))
c.ok("обрыв соединения — пропуск", *skipped("https://site.ru/boom.jpg", "Ошибка скачивания"))

# ---------- Повторы ----------

next_urls[:] = ["https://unsplash.com/dead.jpg", "https://ok.ru/good.jpg"]
path = gc.fetch_topic_image("тема поста", first_url="https://site.ru/pic.svg")
c.ok("svg -> 404 -> успех на третьей попытке",
     path is not None and path.endswith(".jpg"), path)
if path:
    os.unlink(path)

calls = []
gc.call_mistral_text = lambda p, max_retries=3: calls.append(p) or "IMAGE_URL: https://ok.ru/good.jpg"
path = gc.fetch_topic_image("тема поста", first_url="https://ok.ru/good.jpg")
c.ok("успех с первой ссылки — модель не дёргаем", path is not None and not calls)
if path:
    os.unlink(path)
gc.call_mistral_text = fake_mistral

# ---------- Все попытки провалились ----------

next_urls[:] = ["https://unsplash.com/dead.jpg", "https://site.ru/pic.gif"]
log = io.StringIO()
with contextlib.redirect_stdout(log):
    path = gc.fetch_topic_image("тема поста", first_url="https://site.ru/pic.svg")
log = log.getvalue()

c.ok("три провала — пост уйдёт без фото", path is None)
c.ok("ровно три попытки", log.count("Картинка, попытка") == 3, log.count("Картинка, попытка"))
c.ok("нужная строка в логе",
     "Не удалось найти подходящую картинку после 3 попыток" in log, log)

# ---------- Локальные файлы рубрики ----------

http.routes["https://cloud-api.yandex.net/v1/disk/resources"] = Response(payload={
    "_embedded": {"items": [
        {"type": "file", "name": "a.svg", "path": "/p/a.svg"},
        {"type": "file", "name": "b.jpg", "path": "/p/b.jpg"},
        {"type": "file", "name": "c.PNG", "path": "/p/c.PNG"},
        {"type": "dir", "name": "sub", "path": "/p/sub"},
    ]}
})
names = [p["name"] for p in gc.list_rubric_photos("holidays")]
c.ok("в рубрике остаются только картинки", names == ["b.jpg", "c.PNG"], names)

sys.exit(c.finish())
