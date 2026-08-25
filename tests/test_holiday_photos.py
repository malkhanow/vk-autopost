#!/usr/bin/env python3
"""Праздничные посты: сначала rubrics/holidays, потом интернет."""

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Checks, load_module  # noqa: E402

gc, http = load_module()
c = Checks("праздничные фото")

HOLIDAY = {"name": "8 Марта", "theme": "женский день", "date": (8, 3)}

gc.call_mistral_text = lambda prompt, max_retries=3: (
    "Текст праздничного поста.\nIMAGE_URL: https://net.ru/pic.jpg"
)

# Куда сходили за картинкой в интернет
web = []
gc.fetch_topic_image = lambda *args, **kwargs: (
    web.append((args, kwargs)) or "/tmp/from-web.jpg"
)

# ---------- В папке есть фото ----------

listed = []
gc.list_rubric_photos = lambda rubric: listed.append(rubric) or [
    {"name": "8marta.jpg", "path": "/disk/holidays/8marta.jpg"},
    {"name": "spring.png", "path": "/disk/holidays/spring.png"},
]
gc.download_yandex_file = lambda path: "/tmp/local-" + os.path.basename(path)

state = {}
text, photo = gc.generate_holiday_post(HOLIDAY, 3, state)
c.ok("берётся локальное фото", str(photo).startswith("/tmp/local-"), photo)
c.ok("смотрим именно в rubrics/holidays", listed == ["holidays"], listed)
c.ok("в интернет не ходим", not web)
c.ok("фото отмечено использованным", len(state["used_photos"]["holidays"]) == 1, state)
c.ok("строка IMAGE_URL вырезана из текста", "IMAGE_URL" not in text, text)

first = state["used_photos"]["holidays"][0]
_, second = gc.generate_holiday_post(HOLIDAY, 3, state)
c.ok("второй пост — другое фото",
     os.path.basename(str(second)) != "local-" + first, (first, second))
c.ok("отмечены оба", len(state["used_photos"]["holidays"]) == 2, state)

_, third = gc.generate_holiday_post(HOLIDAY, 3, state)
c.ok("круг замкнулся — счётчик сброшен, фото есть",
     third is not None and len(state["used_photos"]["holidays"]) == 1, state)

# ---------- Папка пустая или её нет ----------

gc.list_rubric_photos = lambda rubric: []
web.clear()
log = io.StringIO()
with contextlib.redirect_stdout(log):
    _, photo = gc.generate_holiday_post(HOLIDAY, 1, {})
log = log.getvalue()

c.ok("пустая папка — идём в интернет", photo == "/tmp/from-web.jpg", photo)
c.ok("тема запроса — праздник", web and web[0][0][0] == "8 Марта — женский день", web)
c.ok("первая ссылка из поста передана",
     web and web[0][1].get("first_url") == "https://net.ru/pic.jpg", web)
c.ok("в логе видно, почему пошли в интернет", "беру картинку из интернета" in log, log)

# ---------- Яндекс.Диск недоступен ----------

def boom(rubric):
    raise RuntimeError("Яндекс.Диск ответил 500")


gc.list_rubric_photos = boom
web.clear()
log = io.StringIO()
with contextlib.redirect_stdout(log):
    _, photo = gc.generate_holiday_post(HOLIDAY, 2, {})
log = log.getvalue()

c.ok("сбой Диска не роняет пост", photo == "/tmp/from-web.jpg", photo)
c.ok("сбой попал в лог", "Не удалось взять фото" in log, log)

sys.exit(c.finish())
