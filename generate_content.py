#!/usr/bin/env python3
"""
Вечерний автопостинг рубрик Custom Studio в Telegram.
Crosslybot автоматически кросспостит в VK и Max.

Расписание (16:00 UTC = 19:00 МСК):
  Пн — Мир печати и дизайна
  Вт — Идея для принта
  Ср — Пост из 24
  Чт — Уход за изделием (чётные недели) или Мир печати
  Пт — Мир печати и дизайна
  Сб — Идея для принта
  Вс — Пост из 24

GitHub Secrets:
  MISTRAL_API_KEY    — ключ Mistral AI
  TELEGRAM_BOT_TOKEN — токен бота
  TELEGRAM_CHANNEL   — @customstudio_print
  YANDEX_TOKEN       — OAuth-токен Яндекс.Диска
"""

import base64
import json
import mimetypes
import os
import random
import sys
import tempfile
import time
from datetime import date, datetime, timezone

import requests

# ---------- настройки ----------

MISTRAL_API_KEY = os.environ["MISTRAL_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL = os.environ["TELEGRAM_CHANNEL"]
YANDEX_TOKEN = os.environ["YANDEX_TOKEN"]

MISTRAL_TEXT_MODEL = "mistral-small-latest"
MISTRAL_VISION_MODEL = "pixtral-12b-2409"

RUBRICS_BASE = "/Custom Studio Autopost/rubrics"
STATE_FILE = "content_state.json"

# ---------- 24 поста ----------

POSTS_24 = [
    """Серая футболка с маленьким логотипом — самый простой вариант.

Но корпоративный мерч можно собрать интереснее: подобрать цвет одежды под фирменный стиль, сделать небольшой логотип спереди, крупную графику на спине, добавить имена сотрудников или отдельные детали для разных отделов.

В итоге получается не просто форма с логотипом, а полноценный визуальный стиль команды.

📩 Присылайте в сообщения — обсудим.""",

    """Частая история: фотография хорошая, но только на экране телефона.

Открываешь её на компьютере — разрешение маленькое, вокруг лишние объекты, лицо размыто.

Это ещё не значит, что фотографию нельзя напечатать. Смотрим исходник и решаем, что с ним можно сделать: убрать фон, почистить изображение, восстановить детали.

📩 Присылайте фото — посмотрим что можно сделать.""",

    """Иногда про мерч вспоминают за пару дней до мероприятия.

В таких ситуациях просто сразу говорите о сроках. Проверим загрузку производства и скажем, успеем или нет.

📩 Напишите — уточним сроки.""",

    """Если принт начал трескаться после нескольких стирок, проблема не всегда в самой печати.

На результат влияет сразу несколько вещей: качество материала, технология нанесения, температура, давление, подготовка изображения и уход за готовой вещью.

📩 Если есть вопросы по качеству — пишите.""",

    """В телефоне тысячи фотографий, большая часть из них просто лежит там годами.

Можно напечатать такую фотографию на футболке или худи — небольшой принт на груди или большую композицию на спине.

📩 Присылайте фото — сделаем.""",

    """Одинаковые футболки сразу собирают команду визуально.

Для спортивных команд, сотрудников, организаторов мероприятий — вариантов много. И совсем не обязательно заказывать огромную партию.

📩 Напишите — подберём вариант.""",

    """Для первого запуска не нужны склад и коробки с сотней одинаковых худи.

Можно начать с одного образца, показать аудитории и уже потом решить, стоит ли запускать полноценную коллекцию.

📩 Присылайте идею — обсудим.""",

    """Перед соревнованиями обычно думают о тренировках, составе и поездке. Форма тоже входит в список.

Футболки с названием команды, номера, фамилии, логотипы партнёров — всё можно собрать в одном заказе.

📩 Напишите — рассчитаем стоимость.""",

    """Для музыканта мерч — отдельная часть работы с аудиторией.

Футболка или худи после концерта уезжает домой вместе с человеком. Потом появляется на улице, в университете, на фотографии в соцсетях.

📩 Присылайте идею — обсудим.""",

    """Футболки, фартуки, худи, кепки.

Для кофейни, бара, магазина, студии или небольшого производства можно собрать линейку одежды для сотрудников. Такие вещи постепенно становятся частью интерьера и общего впечатления от места.

📩 Напишите — подберём вариант.""",

    """DTF хорошо подходит для сложных изображений.

Фотографии, градиенты, мелкие детали, большое количество цветов — всё это можно перенести на ткань. Не нужен большой тираж — можно напечатать один экземпляр.

📩 Напишите — рассчитаем стоимость.""",

    """Перед печатью важно проверить не только сам рисунок.

Размер, расположение, пропорции, цвет ткани — всё это влияет на итоговый вид вещи. Поэтому иногда мы немного меняем исходный макет.

📩 Присылайте макет — проверим и запустим.""",

    """Нужна одна футболка — сделаем одну.
Нужно несколько худи для друзей — сделаем несколько.
Понадобилась партия для компании — подготовим партию.

📩 Напишите — обсудим.""",

    """При обычном освещении выглядит как обычное нанесение.

Попадает свет фар или фонаря — поверхность начинает отражать свет. На чёрном худи небольшой светоотражающий элемент может выглядеть очень интересно.

📩 Присылайте идею — обсудим.""",

    """Разница в цене иногда кажется небольшой.

Но потом выясняется, что футболка тоньше, принт не того размера, цвет отличается от макета, а переделывать уже поздно.

📩 Напишите — расскажем подробно.""",

    """Логотип на сайте видит человек, пока находится на вашем сайте.

Логотип на одежде сотрудника может попасть ему на глаза в кофейне, на улице, на выставке.

📩 Напишите — обсудим.""",

    """Стоимость печати — это не только сама печать.

Есть футболка или худи, размер изображения, количество нанесений, подготовка файла, технология и объём заказа. Два внешне одинаковых заказа могут стоить по-разному.

📩 Присылайте — рассчитаем.""",

    """Концерт через месяц. Соревнования через две недели. Корпоратив в следующую пятницу.

Чем раньше известна дата, тем спокойнее проходит производство.

📩 Напишите — уточним сроки.""",

    """У многих в телефоне есть папка с идеями. Часть из них так и остаётся там.

Если какая-то идея нравится уже несколько месяцев, можно хотя бы сделать один тестовый экземпляр.

📩 Присылайте идею — обсудим.""",

    """Чёрный принт на чёрной футболке почти исчезнет. Белый на белой тоже.

Можно подобрать близкие оттенки и получить более спокойный дизайн. Цвет сильно меняет восприятие одной и той же картинки.

📩 Присылайте макет — подберём вариант.""",

    """Сначала получаем макет и обсуждаем задачу.

Потом определяем размер нанесения, место печати и подходящую вещь. После печати проверяем готовые изделия и только потом собираем заказ.

📩 Присылайте в сообщения — начнём.""",

    """Сначала одна футболка. Потом ещё одна, но уже с другой картинкой. Потом появляется идея сделать худи.

Начинать с десятка дизайнов совсем не обязательно.

📩 Присылайте идею — обсудим.""",

    """Принт можно сделать слишком высоким. Слишком низким. Сместить в сторону.

На макете разница иногда почти незаметна. На готовой футболке — уже бросается в глаза.

📩 Присылайте макет — проверим перед запуском.""",

    """Самые приятные сообщения приходят после заказа.

«Всё приехало вовремя». «Получилось даже лучше, чем представляли». «Пришли заказать ещё».

📩 Присылайте в сообщения — обсудим ваш заказ.""",
]

POST_CARE = """🧺 Как ухаживать за одеждой с принтом — чтобы он оставался ярким как в первый день

**Стирка**
Стирай при температуре не выше 30°C, слабый отжим. Перед стиркой выверни одежду наизнанку — это главное правило.

**Глажка**
Только через изнаночную сторону, средний нагрев. Никогда не гладь прямо по принту.

**Сушка**
Без барабанной сушилки — просто повесь сохнуть.

Соблюдаешь эти три правила — принт выдержит долго и останется ярким.

✨ Custom Studio — носи то, что нравится тебе."""

HOLIDAYS = [
    {"date": (1, 1),   "name": "Новый год",                    "theme": "новогодний мерч, подарки с принтом, корпоративная одежда"},
    {"date": (7, 1),   "name": "Рождество",                    "theme": "рождественские подарки с принтом"},
    {"date": (25, 1),  "name": "День студента",                "theme": "студенческий мерч, университетская символика"},
    {"date": (14, 2),  "name": "День влюблённых",              "theme": "парные вещи и принты с фото двоих"},
    {"date": (23, 2),  "name": "День защитника Отечества",     "theme": "подарки мужчинам, мужской мерч"},
    {"date": (8, 3),   "name": "Международный женский день",   "theme": "подарки женщинам, цветочные принты"},
    {"date": (12, 4),  "name": "День космонавтики",            "theme": "космическая тематика принтов на одежде"},
    {"date": (1, 5),   "name": "День труда",                   "theme": "яркие весенние принты на одежде"},
    {"date": (9, 5),   "name": "День Победы",                  "theme": "тематическая печать к 9 мая"},
    {"date": (1, 6),   "name": "День защиты детей",            "theme": "детский мерч, яркие принты для детей"},
    {"date": (12, 6),  "name": "День России",                  "theme": "патриотическая символика России на одежде"},
    {"date": (8, 7),   "name": "День семьи, любви и верности", "theme": "семейный мерч, фото на одежде"},
    {"date": (22, 8),  "name": "День флага России",            "theme": "триколор и символика России на одежде"},
    {"date": (1, 9),   "name": "День знаний",                  "theme": "школьный мерч, форма для класса"},
    {"date": (4, 10),  "name": "День животных",                "theme": "принты с животными и питомцами на одежде"},
    {"date": (31, 10), "name": "Хэллоуин",                     "theme": "хэллоуинская тематика, мрачная эстетика принтов"},
    {"date": (4, 11),  "name": "День народного единства",      "theme": "патриотическая символика на одежде"},
    {"date": (31, 12), "name": "Новый год (канун)",            "theme": "новогодний мерч, подарки с принтом"},
]

SERIOUS_HOLIDAYS = {"День Победы", "День народного единства", "День флага России", "День России"}

# ---------- Состояние ----------

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            try:
                return json.load(f)
            except Exception:
                pass
    return {"post24_index": 0, "used_photos": {}}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ---------- Яндекс.Диск ----------

def yandex_headers():
    return {"Authorization": f"OAuth {YANDEX_TOKEN}"}

def list_rubric_photos(rubric_name):
    path = f"{RUBRICS_BASE}/{rubric_name}"
    resp = requests.get(
        "https://cloud-api.yandex.net/v1/disk/resources",
        headers=yandex_headers(),
        params={"path": path, "limit": 100,
                "fields": "_embedded.items.name,_embedded.items.path,_embedded.items.type"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"Яндекс.Диск: не удалось получить файлы из {path}")
        return []
    items = resp.json().get("_embedded", {}).get("items", [])
    return [i for i in items if i.get("type") == "file"]

def download_yandex_file(yandex_path):
    dl = requests.get(
        "https://cloud-api.yandex.net/v1/disk/resources/download",
        headers=yandex_headers(),
        params={"path": yandex_path},
        timeout=30,
    )
    dl.raise_for_status()
    href = dl.json()["href"]
    ext = os.path.splitext(yandex_path)[1] or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    with requests.get(href, stream=True, timeout=60) as r:
        r.raise_for_status()
        for chunk in r.iter_content(8192):
            tmp.write(chunk)
    tmp.close()
    return tmp.name

def get_next_photo(rubric_name, state):
    """Берёт следующее фото без повторов. Когда все использованы — сбрасывает."""
    photos = list_rubric_photos(rubric_name)
    if not photos:
        return None, None

    used_key = f"used_{rubric_name}"
    used = set(state.get("used_photos", {}).get(rubric_name, []))
    all_names = {p["name"] for p in photos}

    # Если все использованы — сбрасываем
    if used >= all_names:
        used = set()

    available = [p for p in photos if p["name"] not in used]
    if not available:
        available = photos

    chosen = random.choice(available)
    used.add(chosen["name"])

    if "used_photos" not in state:
        state["used_photos"] = {}
    state["used_photos"][rubric_name] = list(used)

    local_path = download_yandex_file(chosen["path"])
    return local_path, chosen["name"]

# ---------- Mistral ----------

def call_mistral_text(prompt, max_retries=3):
    headers = {"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"}
    body = {"model": MISTRAL_TEXT_MODEL, "messages": [{"role": "user", "content": prompt}]}
    for attempt in range(1, max_retries + 1):
        resp = requests.post("https://api.mistral.ai/v1/chat/completions",
                             headers=headers, json=body, timeout=60)
        if resp.status_code == 429:
            print(f"429 (попытка {attempt})")
            time.sleep(15 * attempt)
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    raise RuntimeError("Mistral не ответил")

def call_mistral_vision(prompt, image_path, max_retries=3):
    mime_type, _ = mimetypes.guess_type(image_path)
    mime_type = mime_type or "image/jpeg"
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": f"data:{mime_type};base64,{image_b64}"},
    ]
    headers = {"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"}
    body = {"model": MISTRAL_VISION_MODEL, "messages": [{"role": "user", "content": content}]}
    for attempt in range(1, max_retries + 1):
        resp = requests.post("https://api.mistral.ai/v1/chat/completions",
                             headers=headers, json=body, timeout=90)
        if resp.status_code == 429:
            print(f"429 vision (попытка {attempt})")
            time.sleep(15 * attempt)
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    raise RuntimeError("Mistral vision не ответил")

def fetch_image_from_url(url):
    """Скачивает картинку по URL. Возвращает путь к временному файлу или None."""
    if not url or not url.startswith("http"):
        return None
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        ct = r.headers.get("Content-Type", "")
        if r.status_code == 200 and "image" in ct:
            ext = ".jpg" if "jpeg" in ct else ".png" if "png" in ct else ".jpg"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
            tmp.write(r.content)
            tmp.close()
            print(f"Картинка скачана: {url}")
            return tmp.name
        else:
            print(f"Не удалось скачать картинку ({r.status_code}): {url}")
    except Exception as e:
        print(f"Ошибка скачивания картинки: {e}")
    return None

def extract_image_url(text):
    """Извлекает IMAGE_URL из текста и возвращает (текст без строки, url)."""
    lines = text.split("\n")
    text_lines = []
    url = None
    for line in lines:
        if line.strip().startswith("IMAGE_URL:"):
            url = line.strip().replace("IMAGE_URL:", "").strip()
        else:
            text_lines.append(line)
    return "\n".join(text_lines).strip(), url

# ---------- Генерация рубрик ----------

def generate_world_of_print():
    prompt = """Ты пишешь пост для VK-группы Custom Studio — компания делает кастомную печать на одежде.

Найди одну интересную тему из мира печати, дизайна, уличной моды, мерча или брендинга. Это может быть тренд, технология, интересный кейс известного бренда.

Стиль: коротко, по делу, без пафоса. 3-4 абзаца, каждый 1-2 предложения.

Не использовать: "стоит отметить", "важно подчеркнуть", "уникальный", канцелярит, ИИ-шные обороты.

В конце одно предложение — необязательная связь с Custom Studio, только если она естественная.

Также укажи прямую ссылку на тематическое изображение из открытых источников в формате:
IMAGE_URL: https://...

Отвечай только текстом поста и строкой IMAGE_URL."""
    raw = call_mistral_text(prompt)
    text, url = extract_image_url(raw)
    photo_path = fetch_image_from_url(url)
    return text, photo_path

def generate_print_idea(photo_path):
    """Генерирует текст на основе фото."""
    month = datetime.now().month
    seasons = {12: "зима", 1: "зима", 2: "зима", 3: "весна", 4: "весна",
               5: "весна", 6: "лето", 7: "лето", 8: "лето", 9: "осень",
               10: "осень", 11: "осень"}
    season = seasons[month]
    prompt = f"""Ты пишешь пост для VK-группы Custom Studio — компания делает кастомную печать на одежде.

На фото — реальное изделие с принтом из нашего производства. Посмотри на него и напиши пост с идеей для принта, вдохновлённой тем что видишь.

Сейчас {season} — учти это если уместно.

Стиль: коротко, живо, без пафоса. 3-4 абзаца, каждый 1-2 предложения. Не пересказывай что на фото буквально — используй как вдохновение для идеи.

Не использовать: "уникальный", "стильный", "трендовый", канцелярит, ИИ-шные обороты.

В конце — сдержанный CTA: если идея нравится, макет можно прислать и мы напечатаем.

Отвечай только текстом поста."""
    return call_mistral_vision(prompt, photo_path)

def get_post24_text(idx, photo_path, state):
    """Каждый 3-й пост адаптирует текст под фото через vision."""
    base_text = POSTS_24[idx % len(POSTS_24)]
    adapt_count = state.get("post24_adapt_count", 0)

    if adapt_count % 3 == 2 and photo_path:
        # Адаптируем под фото
        prompt = f"""Ты пишешь пост для VK-группы Custom Studio — компания делает кастомную печать на одежде.

Вот готовый текст поста:
---
{base_text}
---

Посмотри на фото — это реальное изделие с принтом из нашего производства. Слегка адаптируй текст под то что видишь на фото — добавь одну-две детали про изделие или принт. Не переписывай полностью, сохраняй структуру и стиль.

Отвечай только текстом поста."""
        try:
            adapted = call_mistral_vision(prompt, photo_path)
            state["post24_adapt_count"] = adapt_count + 1
            return adapted
        except Exception as e:
            print(f"Vision для поста из 24 не сработал: {e}")

    state["post24_adapt_count"] = adapt_count + 1
    return base_text

def generate_holiday_post(holiday, post_num):
    name = holiday["name"]
    theme = holiday["theme"]
    serious = name in SERIOUS_HOLIDAYS

    cta_rule = (
        "CTA сдержанный: 'Если нужна тематическая печать к этой дате, заказ можно оформить заранее.' Без давления на покупку."
        if serious else
        "CTA активнее: 'Присылайте макет — оформим заказ.' или похожее."
    )

    if post_num == 1:
        task = f"Пост за неделю до {name}. Анонсируй что к этому дню будет скидка 10% на весь чек на тематическую печать ({theme})."
    elif post_num == 2:
        task = f"Пост за день до {name}. Скидка 10% на весь чек уже действует на тематическую печать ({theme})."
    else:
        task = f"Пост в день {name}. Поздравление — сдержанно, без пафоса. Скидка 10% на весь чек действует весь день на тематическую печать ({theme})."

    prompt = f"""Ты пишешь пост для VK-группы Custom Studio — компания делает кастомную печать на одежде.

{task}

Стиль: коротко, без пафоса, без клише. 3-5 предложений.

Не использовать: "уникальный", "специальное предложение", канцелярит, ИИ-шные обороты, восклицательные знаки подряд.

{cta_rule}

Также укажи прямую ссылку на тематическое изображение из открытых источников, подходящее к {name}, в формате:
IMAGE_URL: https://...

Отвечай только текстом поста и строкой IMAGE_URL."""

    raw = call_mistral_text(prompt)
    text, url = extract_image_url(raw)
    photo_path = fetch_image_from_url(url)
    return text, photo_path

# ---------- Telegram ----------

def post_to_telegram(text, photo_path=None):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    if photo_path and os.path.exists(photo_path):
        with open(photo_path, "rb") as f:
            resp = requests.post(
                f"{base_url}/sendPhoto",
                data={"chat_id": TELEGRAM_CHANNEL, "caption": text},
                files={"photo": f},
                timeout=60,
            )
        try:
            os.unlink(photo_path)
        except Exception:
            pass
    else:
        resp = requests.post(
            f"{base_url}/sendMessage",
            json={"chat_id": TELEGRAM_CHANNEL, "text": text},
            timeout=30,
        )
    resp.raise_for_status()
    print("Опубликовано в Telegram.")

# ---------- Праздники ----------

def check_holidays(today):
    for h in HOLIDAYS:
        day, month = h["date"]
        try:
            holiday_date = date(today.year, month, day)
        except ValueError:
            continue
        delta = (holiday_date - today).days
        if delta == 7:
            return h, 1
        if delta == 1:
            return h, 2
        if delta == 0:
            return h, 3
    return None

# ---------- Основная логика ----------

def main():
    today = date.today()
    weekday = today.weekday()
    week_number = today.isocalendar()[1]
    state = load_state()

    # Праздники — приоритет
    holiday_task = check_holidays(today)
    if holiday_task:
        holiday, post_num = holiday_task
        labels = {1: "за неделю", 2: "за день", 3: "в день праздника"}
        print(f"Праздничный пост: {holiday['name']} ({labels[post_num]})")
        text, photo_path = generate_holiday_post(holiday, post_num)
        print(f"Текст:\n{text}\n")
        post_to_telegram(text, photo_path)
        save_state(state)
        return

    # Обычное расписание
    if weekday == 0:
        print("Рубрика: Мир печати и дизайна")
        text, photo_path = generate_world_of_print()

    elif weekday == 1:
        print("Рубрика: Идея для принта")
        photo_path, photo_name = get_next_photo("print_idea", state)
        print(f"Выбрано фото: {photo_name}")
        if photo_path:
            text = generate_print_idea(photo_path)
        else:
            text = generate_print_idea(None) if False else call_mistral_text(
                "Напиши короткий пост с идеей для принта на одежде. 3-4 абзаца, без пафоса.")

    elif weekday == 2:
        print("Рубрика: Пост из 24")
        idx = state.get("post24_index", 0)
        photo_path, photo_name = get_next_photo("posts_24", state)
        print(f"Пост #{idx % len(POSTS_24) + 1}, фото: {photo_name}")
        text = get_post24_text(idx, photo_path, state)
        state["post24_index"] = idx + 1

    elif weekday == 3:
        if week_number % 2 == 0:
            print("Рубрика: Уход за изделием")
            text = POST_CARE
            photo_path, _ = get_next_photo("care", state)
        else:
            print("Рубрика: Мир печати и дизайна")
            text, photo_path = generate_world_of_print()

    elif weekday == 4:
        print("Рубрика: Мир печати и дизайна")
        text, photo_path = generate_world_of_print()

    elif weekday == 5:
        print("Рубрика: Идея для принта")
        photo_path, photo_name = get_next_photo("print_idea", state)
        print(f"Выбрано фото: {photo_name}")
        if photo_path:
            text = generate_print_idea(photo_path)
        else:
            text = call_mistral_text(
                "Напиши короткий пост с идеей для принта на одежде. 3-4 абзаца, без пафоса.")

    else:  # weekday == 6
        print("Рубрика: Пост из 24")
        idx = state.get("post24_index", 0)
        photo_path, photo_name = get_next_photo("posts_24", state)
        print(f"Пост #{idx % len(POSTS_24) + 1}, фото: {photo_name}")
        text = get_post24_text(idx, photo_path, state)
        state["post24_index"] = idx + 1

    print(f"Текст:\n{text}\n")
    post_to_telegram(text, photo_path)
    save_state(state)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
