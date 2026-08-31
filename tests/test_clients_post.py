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


# ---------- окно публикации слота ----------

# Расписание GitHub Actions опаздывает на часы, а иногда пропускает запуск.
# Один и тот же cron срабатывал и в 19:14, и в 00:26 UTC — посты выходили
# ночью. Публикуем только если запуск попал в окно вокруг планового времени.
def in_window(slot, hour, minute=0):
    from datetime import datetime, timezone
    now = datetime(2026, 8, 29, hour, minute, tzinfo=timezone.utc)
    planned = now.replace(hour=cp.SLOT_UTC_HOUR[slot], minute=0,
                          second=0, microsecond=0)
    drift = (now - planned).total_seconds() / 3600
    return -0.5 <= drift <= cp.SLOT_WINDOW_HOURS

eq("слоты заданы в UTC", cp.SLOT_UTC_HOUR,
   {"morning": 7, "midday": 11, "evening": 16})

eq("запуск точно по плану — публикуем", in_window("evening", 16, 0), True)
eq("опоздание на 15 минут — публикуем", in_window("evening", 16, 15), True)
eq("опоздание на полтора часа — ещё публикуем", in_window("evening", 17, 30), True)
eq("опоздание на 2.5 часа — пропускаем", in_window("evening", 18, 30), False)
# реальные случаи из логов, из-за которых посты выходили ночью
eq("реальный запуск в 00:26 UTC — пропускаем", in_window("evening", 0, 26), False)
eq("реальный запуск в 19:14 UTC — пропускаем", in_window("evening", 19, 14), False)
eq("задержка GitHub на 10 часов — пропускаем", in_window("midday", 21, 18), False)
eq("утренний слот вовремя — публикуем", in_window("morning", 7, 10), True)
eq("дневной слот вовремя — публикуем", in_window("midday", 11, 5), True)


# ---------- vision и стратегии фото ----------

eq("reviews включает vision", cp.rubric_uses_vision("Отзывы и результаты"), True)
eq("Фото работ включает vision", cp.rubric_uses_vision("Фото работ"), True)
eq("tips не включает vision", cp.rubric_uses_vision("Советы и полезное"), False)
eq("faq не включает vision", cp.rubric_uses_vision("Частые вопросы"), False)
eq("ideas не включает vision", cp.rubric_uses_vision("Идеи и вдохновение"), False)
eq("Объекты без слова фото — нет vision", cp.rubric_uses_vision("Объекты и документы"), False)
eq("рубрика с фото в названии без папки — vision", cp.rubric_uses_vision("Фото объектов"), True)

eq("tips крутится по кругу", cp.rubric_loops_photos("Советы и полезное"), True)
eq("faq крутится по кругу", cp.rubric_loops_photos("Частые вопросы"), True)
eq("ideas не крутится", cp.rubric_loops_photos("Идеи и вдохновение"), False)
eq("reviews не крутится", cp.rubric_loops_photos("Отзывы и результаты"), False)
eq("to_post не крутится", cp.rubric_loops_photos("Фото работ"), False)


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

# два слота → первый (midday) автоматически берёт фото-рубрику,
# второй (evening) крутит текстовые по кругу без привязки к дням
st = {}
photo = cp.pick_rubric_for_run(client(), "midday", "сб", st, False)
eq("первый слот (midday) берёт фото-рубрику",
   photo["name"], "Фото объектов")

text = cp.pick_rubric_for_run(client(), "evening", "сб", st, False)
c.ok("второй слот (evening) берёт текстовую рубрику",
     cp.rubric_folder(text["name"]) is not None, text["name"])

st2 = {}
seen = [cp.pick_rubric_for_run(client(), "evening", "сб", st2, False)["name"]
        for _ in range(3)]
c.ok("текстовые рубрики чередуются, а не залипают", len(set(seen)) > 1, seen)

# один слот → обычное расписание по дням
one_slot = client(slots=[{"name": "midday"}])
eq("один слот: в субботу рубрик нет (не назначено)",
   cp.pick_rubric_for_run(one_slot, "midday", "сб", {}, False), None)
eq("один слот: работает расписание по дням",
   cp.pick_rubric_for_run(one_slot, "midday", "вт", {}, False)["name"],
   "Советы и полезное")


# ---------- два слота в один день ----------

# у клиента на двух слотах день часто закрыт одной рубрикой: раньше оба слота
# получали её же и в канал уходило два поста на одну тему подряд
one_day = client(rubrics=[
    {"name": "Советы и полезное", "days": ["пн"], "prompt": "p"},
    {"name": "Частые вопросы", "days": ["вт"], "prompt": "p"},
    {"name": "Идеи и вдохновение", "days": ["ср"], "prompt": "p"},
])
# при двух слотах midday=фото-пул, evening=текстовый.
# one_day имеет только текстовые рубрики — фото-пул пуст, midday
# тоже берёт из текстового (запасной пул)
st_day = {}
first = cp.pick_rubric_for_run(one_day, "midday", "пн", st_day, False)
second = cp.pick_rubric_for_run(one_day, "evening", "пн", st_day, False)
c.ok("оба слота получают рубрику (фото-пул пуст — берём текстовый)",
     first is not None and second is not None, (first, second))

# один слот → расписание по дням, второй день — своя рубрика
st_day2 = {}
one_slot_cl = client(slots=[{"name": "midday"}], rubrics=one_day["rubrics"])
cp.pick_rubric_for_run(one_slot_cl, "midday", "пн", st_day2, False)
third = cp.pick_rubric_for_run(one_slot_cl, "midday", "вт", st_day2, False)
eq("один слот: новый день — своя рубрика", third["name"], "Частые вопросы")

# когда на день назначено две рубрики, обе и выходят
# при двух слотах: midday → фото-рубрика, evening → текстовая
# "два слота — фото и текст" (не "два слота — две рубрики одного дня")
two_day = client(rubrics=[
    {"name": "Фото объектов", "days": ["пн"], "prompt": "p"},
    {"name": "Советы и полезное", "days": ["пн"], "prompt": "p"},
    {"name": "Частые вопросы", "days": ["пн"], "prompt": "p"},
])
st_two = {}
got = {cp.pick_rubric_for_run(two_day, sl, "пн", st_two, False)["name"]
       for sl in ("midday", "evening")}
c.ok("два слота — фото и текст разные",
     len(got) == 2 and "Фото объектов" in got, got)

# у клиента с одной рубрикой повторять больше нечего — пост всё равно выходит
solo = client(rubrics=[{"name": "Советы и полезное", "days": ["пн"], "prompt": "p"}])
st_solo = {}
a = cp.pick_rubric_for_run(solo, "midday", "пн", st_solo, False)
b = cp.pick_rubric_for_run(solo, "evening", "пн", st_solo, False)
c.ok("единственная рубрика не блокирует второй слот", a and b, (a, b))


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

# 31 декабря сходятся два события: канун Нового года и неделя до Рождества.
# Клиент не должен получить два поста за день — берётся ближайшее.
nye = date(2026, 12, 31)
eq("31 декабря утром праздничного поста нет",
   run(client(), nye, "midday"), None)
eq("31 декабря вечером — ближайший праздник, а не недельный",
   run(client(), nye, "evening"), ("Новый год", "before1"))
c.ok("канун Нового года отдельным праздником не считается",
     all(h["key"] != "nye" for h in cp.BASE_HOLIDAYS))


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


# ---------- праздничные фото ----------

cp.YANDEX_TOKEN = ""
cp.HOLIDAYS_FOLDER = "Autopost WORK/holidays"
eq("без доступа к Диску праздничный пост уходит без фото",
   cp.holiday_photo({"yandex_folder": "x"}, cp.BASE_HOLIDAYS[0], "day", {}, "test"), None)
c.ok("у каждого вида праздничного поста есть своя подпапка",
     all(k in cp.HOLIDAY_PHOTO_SUBFOLDER for k in cp.HOLIDAY_KINDS))
c.ok("у каждого базового праздника есть ключ для папки",
     all(h.get("key") for h in cp.BASE_HOLIDAYS))

sys.exit(c.finish())
