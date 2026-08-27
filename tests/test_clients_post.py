#!/usr/bin/env python3
"""Многоклиентский движок: папки рубрик, слоты, праздники.

Проверяется отбор — что и в какой слот уходит. Сеть не нужна: ai_text и
Яндекс.Диск сюда не заходят, всё решается на данных конфига клиента.
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Checks, load_module  # noqa: E402

cp, http = load_module("clients_post")
c = Checks("многоклиентский движок")


def eq(name, got, want):
    c.ok(name, got == want, got)


def client(**over):
    base = {
        "client_id": "test",
        "tariff": "ПРО",
        "holidays": "Да, но без упоминания скидок",
        "slots": [{"name": "midday"}, {"name": "evening"}],
        "rubrics": [
            {"name": "Фото объектов", "days": ["пн"], "prompt": "p"},
            {"name": "Советы и полезное", "days": ["вт"], "prompt": "p"},
            {"name": "Частые вопросы", "days": ["ср"], "prompt": "p"},
            {"name": "Отзывы и результаты", "days": ["чт"], "prompt": "p"},
        ],
    }
    base.update(over)
    return base


# ---------- папки рубрик ----------

eq("«Советы» ищут фото в своей папке", cp.rubric_folder("Советы и полезное"), "rubrics/tips")
eq("«Частые вопросы» -> faq", cp.rubric_folder("Частые вопросы"), "rubrics/faq")
eq("«Идеи и вдохновение» -> ideas", cp.rubric_folder("Идеи и вдохновение"), "rubrics/ideas")
eq("«Отзывы и результаты» -> reviews", cp.rubric_folder("Отзывы и результаты"), "rubrics/reviews")
eq("рубрика с фото работ идёт в общую очередь", cp.rubric_folder("Фото объектов"), None)
# названия рубрик правятся руками в CRM — сопоставление должно это переживать
eq("другая формулировка всё равно находит папку",
   cp.rubric_folder("Полезные советы клиентам"), "rubrics/tips")


# ---------- слоты ----------

eq("ранний и поздний слот", cp.slot_bounds(client()), ("midday", "evening"))
eq("утро есть — оно и раннее",
   cp.slot_bounds(client(slots=[{"name": "morning"}, {"name": "evening"}])),
   ("morning", "evening"))
eq("один слот — он же и ранний, и поздний",
   cp.slot_bounds(client(slots=[{"name": "evening"}])), ("evening", "evening"))
eq("слотов нет — публиковать некуда", cp.slot_bounds(client(slots=[])), (None, None))


# ---------- morning_photo ----------

st = {}
photo = cp.pick_rubric_for_run(client(morning_photo=True), "midday", "сб", st, False)
eq("с флагом ранний слот берёт рубрику с фото", photo["name"], "Фото объектов")

text = cp.pick_rubric_for_run(client(morning_photo=True), "evening", "сб", st, False)
c.ok("поздний слот берёт текстовую рубрику",
     cp.rubric_folder(text["name"]) is not None, text["name"])

eq("без флага в субботу рубрик нет",
   cp.pick_rubric_for_run(client(), "midday", "сб", {}, False), None)
eq("без флага работает расписание по дням",
   cp.pick_rubric_for_run(client(), "midday", "вт", {}, False)["name"],
   "Советы и полезное")

st2 = {}
seen = [cp.pick_rubric_for_run(client(morning_photo=True), "evening", "сб", st2, False)["name"]
        for _ in range(3)]
c.ok("текстовые рубрики чередуются, а не залипают", len(set(seen)) > 1, seen)


# ---------- кому положены праздники ----------

eq("тариф СТАРТ праздников не получает", cp.holidays_for_client(client(tariff="СТАРТ")), [])
eq("«Не нужны» в брифе отключает праздники",
   cp.holidays_for_client(client(holidays="Не нужны")), [])
eq("на ПРО работает базовый список",
   len(cp.holidays_for_client(client())), len(cp.BASE_HOLIDAYS))
eq("нишевые добавляются к базовым",
   len(cp.holidays_for_client(client(holidays_extra="08.02 День риелтора"))),
   len(cp.BASE_HOLIDAYS) + 1)


# ---------- разбор нишевых праздников ----------

parsed = cp.parse_extra_holidays("08.02 День риелтора\n8.7 День семьи")
eq("разобраны обе строки", len(parsed), 2)
eq("день и месяц не перепутаны", (parsed[0]["day"], parsed[0]["month"]), (8, 2))
eq("однозначные числа читаются", (parsed[1]["day"], parsed[1]["month"]), (8, 7))
# клиент пишет руками — на опечатке падать нельзя
eq("мусорные строки пропускаются",
   cp.parse_extra_holidays("просто текст\n99.99 бред\n\n01.05 Праздник"),
   cp.parse_extra_holidays("01.05 Праздник"))


# ---------- что в какой слот ----------

def run(cl, d, slot, state=None):
    h, kind = cp.holiday_for_run(cl, d, slot, {} if state is None else state, False)
    return (h["name"], kind) if h else None


nyd = date(2027, 1, 1)
eq("поздравление уходит в ранний слот", run(client(), nyd, "midday"), ("Новый год", "day"))
eq("в поздний слот поздравление не дублируется", run(client(), nyd, "evening"), None)
eq("за день до праздника — вечером",
   run(client(), date(2026, 12, 31), "evening"), ("Новый год", "before1"))
eq("за неделю — тоже вечером",
   run(client(), date(2026, 12, 25), "evening"), ("Новый год", "before7"))
eq("в обычный день праздников нет", run(client(), date(2027, 6, 17), "midday"), None)

# 31 декабря сходятся три события: сам Канун, канун Нового года и неделя
# до Рождества. Клиент не должен получить три поста за день.
nye = date(2026, 12, 31)
eq("31 декабря в ранний слот — поздравление с Канунoм",
   run(client(), nye, "midday"), ("Канун Нового года", "day"))
eq("31 декабря вечером — ближайший праздник, а не недельный",
   run(client(), nye, "evening"), ("Новый год", "before1"))


# ---------- повторный запуск воркфлоу ----------

state = {}
first = cp.holiday_for_run(client(), nyd, "midday", state, False)
c.ok("первый запуск отдаёт праздник", first[0] is not None)
c.ok("после сбоя пост можно досдать — отметки ещё нет",
     cp.holiday_for_run(client(), nyd, "midday", state, False)[0] is not None)
cp.mark_holiday_done(state, "test", nyd, first[0], first[1])
eq("после успешной публикации дубля не будет",
   cp.holiday_for_run(client(), nyd, "midday", state, False), (None, None))
eq("тестовый прогон праздники не трогает",
   cp.holiday_for_run(client(), nyd, "midday", {}, True), (None, None))


# ---------- фотосток ----------

cp.PEXELS_KEY = ""
eq("без ключа праздничный пост уходит без фото", cp.fetch_stock_photo("new year"), None)
c.ok("у каждого базового праздника есть запасной запрос",
     all(h["key"] in cp.HOLIDAY_IMAGE_QUERIES for h in cp.BASE_HOLIDAYS))

sys.exit(c.finish())
