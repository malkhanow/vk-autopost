#!/usr/bin/env python3
"""
Автопостинг для клиентов SMM-подписки (все, кроме Custom Studio — у неё
свой отдельный пайплайн в generate_feed.py / generate_content.py).

Раз в слот (утро/день/вечер) скрипт:
  1. Читает все clients/*.json — по одному конфигу на клиента,
     собранному сайтом CRM из брифа и рубрик.
  2. Пропускает клиентов с "active": false (тумблер на сайте).
  3. Для каждого клиента со слотом, который сейчас идёт, ищет среди его
     рубрик те, что расписаны на сегодняшний день недели.
     Опционально: если в конфиге клиента "morning_photo": true — расписание
     по дням недели для него отключается. Вместо этого его самый ранний
     выбранный слот (morning -> midday -> evening) всегда публикует фото
     из to_post/rubrics, а остальные слоты крутят текстовые рубрики по
     кругу. Без этого флага поведение клиента не меняется.
  4. Если у выбранной рубрики есть своя подпапка с фото
     ({yandex_folder}/rubrics/<тема>) — берёт оттуда следующее по очереди;
     иначе ищет в {yandex_folder}/to_post (общая утренняя очередь).
     После публикации фото переезжает в {yandex_folder}/posted.
  5. RouterAI (Gemini Flash Lite) пишет текст поста по промпту рубрики,
     с учётом style_prompt, тона и запретов клиента.
  6. Публикует в Telegram-канал клиента. Канал берётся из поля
     tg_channel в самом конфиге клиента (clients/{id}.json).
     Токен бота общий (TELEGRAM_BOT_TOKEN) — один бот, добавленный
     админом во все каналы клиентов.

Праздники идут поверх расписания рубрик и вытесняют обычный пост слота:
за 7 дней и за 1 день — вечером (самый поздний слот клиента), в сам день
праздника — утром (самый ранний слот) вместо ежедневной рубрики с фото.
Базовый список зашит в BASE_HOLIDAYS, нишевые клиент задаёт в CRM — они
приезжают в конфиг полем holidays_extra. Тариф СТАРТ праздники не
включает, как и выбор «Не нужны» в брифе.

Ручной тестовый запуск (кнопка "Сохранить и запустить" в CRM) передаёт
input client_id через workflow_dispatch — тогда день недели и слот не
проверяются, публикуется сразу следующая по очереди рубрика этого
клиента. Так проверяется, что канал и конфиг настроены правильно.

GitHub Secrets:
  ROUTERAI_KEY        — тот же ключ, что в Apps Script Script Properties
  TELEGRAM_BOT_TOKEN  — общий бот, admin во всех каналах клиентов
  YANDEX_TOKEN        — OAuth-токен Яндекс.Диска (общий, папки разные)
  PEXELS_KEY          — фотосток для праздничных постов. Не задан —
                        праздничные посты уходят текстом, без картинки.
"""

import glob
import json
import os
import sys
import tempfile

import requests

# ---------- настройки ----------

CLIENTS_DIR = "clients"
STATE_FILE = "clients_state.json"

ROUTERAI_URL = "https://routerai.ru/api/v1/chat/completions"
ROUTERAI_MODEL = os.environ.get("ROUTERAI_MODEL", "google/gemini-3.1-flash-lite")

ROUTERAI_KEY = os.environ["ROUTERAI_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
YANDEX_TOKEN = os.environ.get("YANDEX_TOKEN", "")
PEXELS_KEY = os.environ.get("PEXELS_KEY", "")

SLOT = os.environ.get("SLOT", "")               # morning / midday / evening
TEST_CLIENT_ID = os.environ.get("TEST_CLIENT_ID", "").strip()

DOW_ABBR = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

# ---------- праздники ----------
#
# Базовый список — одинаковый у всех клиентов. Нишевые праздники клиент
# задаёт в CRM, они приезжают в конфиг полем holidays_extra построчно
# в виде "ДД.ММ Название".
#
# Тариф СТАРТ праздничные посты не включает — см. holidays_for_client().
# solemn=True — сдержанный тон, без поздравлений-праздничности и без
# любых упоминаний предложений (задел на 9 мая и подобные даты).

BASE_HOLIDAYS = [
    {"key": "new_year",   "month": 1,  "day": 1,  "name": "Новый год",                    "solemn": False},
    {"key": "christmas",  "month": 1,  "day": 7,  "name": "Рождество Христово",           "solemn": False},
    {"key": "valentine",  "month": 2,  "day": 14, "name": "День всех влюблённых",         "solemn": False},
    {"key": "defender",   "month": 2,  "day": 23, "name": "День защитника Отечества",     "solemn": False},
    {"key": "womens_day", "month": 3,  "day": 8,  "name": "Международный женский день",   "solemn": False},
    {"key": "spring_may", "month": 5,  "day": 1,  "name": "Праздник Весны и Труда",       "solemn": False},
    {"key": "knowledge",  "month": 9,  "day": 1,  "name": "День знаний",                  "solemn": False},
    {"key": "nye",        "month": 12, "day": 31, "name": "Канун Нового года",            "solemn": False},
]

# что публикуем: за 7 дней и за 1 день — вечером, в сам день — утром
HOLIDAY_KINDS = {
    "before7": "анонс за неделю",
    "before1": "анонс за день",
    "day": "поздравление",
}

# Запасные запросы к фотостоку: обычно запрос придумывает модель, но если
# она не ответила — берём отсюда, чтобы пост не остался без картинки.
# Английский — на стоках по нему в разы больше материала, чем по русскому.
HOLIDAY_IMAGE_QUERIES = {
    "new_year":   "new year celebration lights",
    "christmas":  "christmas candles winter",
    "valentine":  "valentines day hearts",
    "defender":   "red carnations memorial",
    "womens_day": "spring flowers bouquet women",
    "spring_may": "spring blossom sunny day",
    "knowledge":  "school books autumn",
    "nye":        "new year eve fireworks",
}
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


# ---------- состояние (ротация рубрик и фото) ----------

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            try:
                return json.load(f)
            except Exception:
                pass
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def client_state(state, client_id):
    return state.setdefault(client_id, {"tie_index": 0, "used_photos": []})


# ---------- Яндекс.Диск ----------

def yandex_headers():
    return {"Authorization": f"OAuth {YANDEX_TOKEN}"}


def list_folder(path):
    if not YANDEX_TOKEN:
        return []
    resp = requests.get(
        "https://cloud-api.yandex.net/v1/disk/resources",
        headers=yandex_headers(),
        params={
            "path": path, "limit": 200, "sort": "created",
            "fields": "_embedded.items.name,_embedded.items.path,"
                      "_embedded.items.type,_embedded.items.created",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        return []
    items = resp.json().get("_embedded", {}).get("items", [])
    return [
        i for i in items
        if i.get("type") == "file"
        and os.path.splitext(i.get("name", ""))[1].lower() in ALLOWED_IMAGE_EXTS
    ]


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


def move_to_posted(src_path, filename, posted_dir):
    resp = requests.post(
        "https://cloud-api.yandex.net/v1/disk/resources/move",
        headers=yandex_headers(),
        params={"from": src_path, "path": f"{posted_dir}/{filename}", "overwrite": "true"},
        timeout=30,
    )
    if resp.status_code not in (200, 201, 202):
        print(f"Не удалось перенести {filename} в posted: {resp.text[:200]}")


def rubric_folder(rubric_name):
    """
    Название рубрики -> подпапка с фото для неё, или None (рубрика без
    привязанных фото — например «Идеи» на чистом тексте у части клиентов).
    Сопоставление по ключевым словам, а не точным совпадением: название
    рубрики редактируется в CRM руками и может немного отличаться от
    исходного из списка тем.
    """
    name = (rubric_name or "").lower()
    if "совет" in name or "польз" in name:
        return "rubrics/tips"
    if "вопрос" in name or "чзв" in name or "faq" in name:
        return "rubrics/faq"
    if "иде" in name or "вдохнов" in name:
        return "rubrics/ideas"
    if "отзыв" in name or "результат" in name:
        return "rubrics/reviews"
    return None  # «Фото работ» и остальное — общая утренняя очередь to_post


def next_photo_for_client(yandex_folder, rubric_name=""):
    """
    Следующее неиспользованное фото для рубрики клиента, или (None, None),
    если фото нет. Если у рубрики есть своя подпапка (rubric_folder) —
    фото ищутся там; иначе — в общей утренней очереди to_post. Опубликованные
    фото в любом случае переезжают в общий posted/, независимо от того,
    откуда были взяты.
    """
    folder = rubric_folder(rubric_name)
    source = f"{yandex_folder}/{folder}" if folder else f"{yandex_folder}/to_post"
    posted = f"{yandex_folder}/posted"
    files = list_folder(source)
    if not files:
        return None, None
    chosen = files[0]  # список уже отсортирован по created — берём самое старое
    try:
        local_path = download_yandex_file(chosen["path"])
    except Exception as e:
        print(f"Не удалось скачать {chosen['path']}: {e}")
        return None, None
    return local_path, (chosen["path"], chosen["name"], posted)


# ---------- RouterAI ----------

def ai_text(messages, max_tokens=900, temperature=0.8):
    payload = {
        "model": ROUTERAI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "reasoning_effort": "low",
    }
    for attempt in range(3):
        if attempt:
            import time
            time.sleep(attempt * 3)
        resp = requests.post(
            ROUTERAI_URL,
            headers={"Authorization": f"Bearer {ROUTERAI_KEY}"},
            json=payload,
            timeout=60,
        )
        if resp.status_code in (429,) or resp.status_code >= 500:
            print(f"RouterAI: HTTP {resp.status_code}, повтор...")
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"RouterAI: HTTP {resp.status_code} — {resp.text[:300]}")
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        return strip_model_noise(content)
    raise RuntimeError("RouterAI не отвечает после трёх попыток")


def strip_model_noise(text):
    import re
    s = str(text or "")
    s = re.sub(r"^```[\s\S]*?\n|```$", "", s)
    s = re.sub(
        r"^.*\b(Draft \d+|Checking Constraints?|Word count check)\b.*$",
        "", s, flags=re.IGNORECASE | re.MULTILINE,
    )
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


def build_post(client, rubric):
    forbidden = client.get("forbidden") or []
    lines = [
        f"Бизнес: {client.get('business', '')}",
        f"Город: {client.get('city', '')}",
        f"Тон: {client.get('tone', '')}",
        f"Рубрика: {rubric.get('name', '')}",
        f"Инструкция рубрики: {rubric.get('prompt', '')}",
    ]
    if forbidden:
        lines.append("Запрещено упоминать: " + "; ".join(forbidden))
    if client.get("cta"):
        lines.append(f"Призыв к действию в конце: {client['cta']}")

    system = (
        "Ты пишешь посты для соцсетей малого бизнеса от лица владельца. "
        "В ответе — только готовый текст поста, ничего больше: ни черновиков, "
        "ни заметок о проверке длины, ни заголовков вроде «Пост:» или «Draft». "
        "Не показывай ход рассуждений — только финальный результат."
    )
    user = "\n".join(lines) + (
        "\n\nНапиши один пост по этой рубрике: 60–120 слов, "
        + (
            f"строго в стиле, описанном ниже:\n{client['style_prompt']}\n"
            if client.get("style_prompt") else "простым разговорным языком.\n"
        )
        + "Не выдумывай цены, сроки и гарантии, если они в запретах. "
        "Ответь только текстом поста, без черновиков и пометок о проверке."
    )
    return ai_text([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])


HOLIDAY_TASKS = {
    "before7": (
        "До праздника неделя. Напомни об этом и в двух-трёх предложениях "
        "расскажи, что это за день. Коротко, без исторической справки на "
        "полстраницы.",
        260,
    ),
    "before1": (
        "Праздник завтра. В четырёх-пяти предложениях расскажи про этот день: "
        "что отмечают, откуда пошло, чем он важен людям.",
        420,
    ),
    "day": (
        "Праздник сегодня. Поздравь подписчиков — тепло и по-человечески, "
        "без официоза и без пересказа истории праздника.",
        320,
    ),
}


def build_holiday_post(client, holiday, kind):
    task, max_tokens = HOLIDAY_TASKS[kind]
    forbidden = client.get("forbidden") or []
    mode = str(client.get("holidays", "")).strip().lower()

    if holiday.get("solemn"):
        # памятные даты: ровный тон, никаких предложений и приглашений
        offer = ("Это памятная дата. Тон сдержанный и уважительный. "
                 "Никаких скидок, предложений и приглашений.")
    elif "со скидк" in mode:
        # размер скидки скрипту неизвестен — цифры выдумывать нельзя
        offer = ("Можно упомянуть, что к празднику действует специальное "
                 "предложение, но без конкретных цифр, процентов и условий.")
    else:
        offer = "Не упоминай скидки, акции и любые специальные предложения."

    lines = [
        f"Бизнес: {client.get('business', '')}",
        f"Город: {client.get('city', '')}",
        f"Тон: {client.get('tone', '')}",
        f"Праздник: {holiday['name']}",
        f"Задача: {task}",
        offer,
    ]
    if forbidden:
        lines.append("Запрещено упоминать: " + "; ".join(forbidden))
    if client.get("cta") and not holiday.get("solemn"):
        lines.append(f"Призыв к действию в конце: {client['cta']}")

    system = (
        "Ты пишешь праздничные посты для соцсетей малого бизнеса от лица "
        "владельца. В ответе — только готовый текст поста, ничего больше: "
        "ни черновиков, ни заметок о проверке длины, ни заголовков. "
        "Не показывай ход рассуждений."
    )
    user = "\n".join(lines) + (
        "\n\nНапиши пост"
        + (
            f" строго в стиле, описанном ниже:\n{client['style_prompt']}\n"
            if client.get("style_prompt") else " простым разговорным языком.\n"
        )
        + "Не выдумывай факты о бизнесе, даты, цифры и условия. "
        "Ответь только текстом поста."
    )
    return ai_text(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
    )


def holiday_image_query(holiday):
    """
    Поисковый запрос к фотостоку под конкретный праздник. Придумывает модель:
    она знает, что 23 февраля — это не ёлка, а «День риелтора» — ключи и дом.
    Просить у модели готовую ссылку на картинку нельзя: URL она выдумает,
    поэтому её дело — только запрос, а картинку отдаёт сток.
    """
    import re
    fallback = HOLIDAY_IMAGE_QUERIES.get(holiday["key"], "")
    try:
        raw = ai_text(
            [
                {"role": "system", "content":
                 "Ты подбираешь поисковый запрос для фотостока. В ответе — "
                 "только сам запрос: 2–4 слова на английском, без кавычек, "
                 "без пояснений и без точки в конце."},
                {"role": "user", "content":
                 f"Праздник: {holiday['name']}. Нужна атмосферная фотография "
                 f"для поздравительного поста. Без надписей и текста на фото, "
                 f"без узнаваемых людей крупным планом."},
            ],
            max_tokens=30, temperature=0.5,
        )
        # модель иногда добавляет кавычки, эмодзи или пояснение — чистим
        cleaned = re.sub(r"[^A-Za-z0-9 ]", " ", raw)
        cleaned = " ".join(cleaned.split()[:5]).strip()
        if cleaned:
            return cleaned
        print(f"Пустой запрос к стоку для «{holiday['name']}» — беру запасной")
    except Exception as e:
        print(f"Не удалось подобрать запрос к стоку ({e}) — беру запасной")
    return fallback


def fetch_stock_photo(query):
    """
    Картинка с Pexels по запросу, или None. Без ключа PEXELS_KEY просто
    возвращает None — праздничный пост тогда уйдёт текстом.
    """
    import random

    if not PEXELS_KEY:
        print("PEXELS_KEY не задан — праздничный пост уйдёт без фото.")
        return None
    if not query:
        return None
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": query, "per_page": 15, "orientation": "landscape"},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"Pexels: HTTP {resp.status_code} по запросу «{query}»")
            return None
        photos = resp.json().get("photos", [])
        if not photos:
            print(f"Pexels: ничего не нашлось по запросу «{query}»")
            return None
        # не первый попавшийся: иначе один и тот же кадр из года в год
        photo = random.choice(photos[:10])
        url = (photo.get("src") or {}).get("large") or (photo.get("src") or {}).get("original")
        if not url:
            return None

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            size = 0
            for chunk in r.iter_content(8192):
                size += len(chunk)
                if size > 9 * 1024 * 1024:      # телеграм не примет больше
                    tmp.close()
                    os.unlink(tmp.name)
                    print("Картинка со стока слишком большая — пропускаю.")
                    return None
                tmp.write(chunk)
        tmp.close()
        if os.path.getsize(tmp.name) < 1024:    # пустышка вместо картинки
            os.unlink(tmp.name)
            return None
        print(f"Фото со стока: «{query}» · автор {photo.get('photographer', '—')}")
        return tmp.name
    except Exception as e:
        print(f"Не удалось получить фото со стока ({e}) — пост уйдёт без него.")
        return None


def holiday_photo(holiday):
    return fetch_stock_photo(holiday_image_query(holiday))


# ---------- Telegram (с фолбэками — пост должен уйти всегда) ----------

def post_to_telegram(channel, text, photo_path=None):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    CAPTION_LIMIT = 1024

    # Resolve the channel username to its real Telegram chat_id first.
    # This makes publication deterministic in GitHub Actions.
    chat_resp = requests.get(
        f"{base_url}/getChat",
        params={"chat_id": channel},
        timeout=30,
    )
    chat_resp.raise_for_status()

    chat_data = chat_resp.json()
    if not chat_data.get("ok"):
        raise RuntimeError(f"Telegram getChat error: {chat_data}")

    chat = chat_data["result"]
    chat_id = chat["id"]

    print(
        f"Telegram target: {channel} -> "
        f"id={chat_id}, username=@{chat.get('username', '')}, "
        f"type={chat.get('type')}"
    )

    def send_message(body, parse_mode="Markdown"):
        payload = {"chat_id": chat_id, "text": body}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return requests.post(
            f"{base_url}/sendMessage",
            json=payload,
            timeout=30,
        )

    def send_photo(path, caption, parse_mode="Markdown"):
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
            if parse_mode:
                data["parse_mode"] = parse_mode
        with open(path, "rb") as f:
            return requests.post(
                f"{base_url}/sendPhoto",
                data=data,
                files={"photo": f},
                timeout=60,
            )

    has_photo = bool(photo_path and os.path.exists(photo_path))
    if has_photo:
        size_mb = os.path.getsize(photo_path) / 1024 / 1024
        if size_mb > 9.5:
            print("Фото больше 9.5 МБ — отправляю без него.")
            has_photo = False

    try:
        if has_photo:
            if len(text) > CAPTION_LIMIT:
                resp = send_photo(photo_path, None)
                if not resp.ok:
                    print("Ошибка фото:", resp.text)
                resp = send_message(text)
                if not resp.ok:
                    resp = send_message(text, parse_mode=None)
            else:
                resp = send_photo(photo_path, text)
                if not resp.ok:
                    resp = send_photo(
                        photo_path,
                        text,
                        parse_mode=None,
                    )
                    if not resp.ok:
                        resp = send_message(text)
                        if not resp.ok:
                            resp = send_message(text, parse_mode=None)
        else:
            resp = send_message(text)
            if not resp.ok:
                resp = send_message(text, parse_mode=None)

        if not resp.ok:
            print("ИТОГОВАЯ ОШИБКА:", resp.status_code, resp.text)
            resp.raise_for_status()

        print(f"Опубликовано в {channel}.")
        print(f"Telegram ответ: {resp.status_code} {resp.text[:200]}")
        print(f"Текст поста: {text[:300]}")
        return resp

    finally:
        if photo_path and os.path.exists(photo_path):
            try:
                os.unlink(photo_path)
            except Exception:
                pass


# ---------- выбор рубрики ----------

def matching_rubrics(client, today_abbr):
    out = []
    for r in client.get("rubrics", []):
        days = [str(d).strip().lower() for d in (r.get("days") or [])]
        if today_abbr in days:
            out.append(r)
    return out


def pick_rubric(client, today_abbr, state, test_mode):
    rubrics = client.get("rubrics", [])
    if not rubrics:
        return None
    if test_mode:
        cs = client_state(state, client["client_id"])
        idx = cs["tie_index"] % len(rubrics)
        cs["tie_index"] += 1
        return rubrics[idx]

    matches = matching_rubrics(client, today_abbr)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    cs = client_state(state, client["client_id"])
    idx = cs["tie_index"] % len(matches)
    cs["tie_index"] += 1
    return matches[idx]


SLOT_ORDER = ["morning", "midday", "evening"]


# ---------- праздники: отбор ----------

def parse_extra_holidays(raw):
    """
    Нишевые праздники клиента из CRM. Одна строка — один праздник:
        27.02 День риелтора
    Разделитель после даты любой (пробел, тире, точка). Мусорные строки
    молча пропускаются — клиент пишет руками, падать из-за опечатки нельзя.
    """
    import re
    out = []
    if isinstance(raw, list):
        lines = [str(x) for x in raw]
    else:
        lines = str(raw or "").splitlines()
    for line in lines:
        m = re.match(r"\s*(\d{1,2})[.\-/](\d{1,2})\s*[\-—–:.]?\s*(.+?)\s*$", line)
        if not m:
            continue
        day, month, name = int(m.group(1)), int(m.group(2)), m.group(3).strip()
        if not (1 <= month <= 12 and 1 <= day <= 31) or not name:
            continue
        out.append({
            "key": f"extra-{month:02d}-{day:02d}",
            "month": month, "day": day, "name": name, "solemn": False,
        })
    return out


def holidays_for_client(client):
    """
    Праздники, актуальные для клиента. Пусто — значит праздничных постов
    у него нет вообще: тариф СТАРТ их не включает, плюс в брифе может
    стоять «Не нужны».
    """
    if str(client.get("tariff", "")).strip().upper() == "СТАРТ":
        return []
    mode = str(client.get("holidays", "")).strip().lower()
    if mode.startswith("не нужн"):
        return []
    return BASE_HOLIDAYS + parse_extra_holidays(client.get("holidays_extra"))


def find_holiday(holidays, target):
    for h in holidays:
        if h["month"] == target.month and h["day"] == target.day:
            return h
    return None


def slot_bounds(client):
    """
    Самый ранний и самый поздний из выбранных клиентом слотов. Поздравление
    уходит в ранний («утро»), анонсы — в поздний («вечер»), даже если клиент
    выбрал, скажем, только midday и evening.
    """
    names = [s.get("name") for s in client.get("slots", []) if s.get("name")]
    ordered = [s for s in SLOT_ORDER if s in names]
    if not ordered:
        return None, None
    return ordered[0], ordered[-1]


def holiday_done_key(today, holiday, kind):
    return f"{today.isoformat()}:{holiday['key']}:{kind}"


def holiday_for_run(client, today, slot, state, test_mode):
    """
    Что публикуем сегодня в этот слот: (праздник, вид) или (None, None).
    Приоритет — сам праздник, потом «завтра», потом «через неделю»: 31 декабря
    это одновременно Канун НГ, канун Нового года и неделя до Рождества, и
    получить три поста в один день клиент не должен.
    Тестовый прогон праздники не трогает — он проверяет обычный конвейер.
    """
    from datetime import timedelta

    if test_mode:
        return None, None
    holidays = holidays_for_client(client)
    if not holidays:
        return None, None
    earliest, latest = slot_bounds(client)
    if not earliest:
        return None, None

    cs = client_state(state, client["client_id"])
    done = cs.get("holiday_done", [])

    def take(target, kind):
        h = find_holiday(holidays, target)
        if h and holiday_done_key(today, h, kind) not in done:
            return h, kind
        return None, None

    if slot == earliest:
        h, kind = take(today, "day")
        if h:
            return h, kind

    if slot == latest:
        h, kind = take(today + timedelta(days=1), "before1")
        if h:
            return h, kind
        h, kind = take(today + timedelta(days=7), "before7")
        if h:
            return h, kind

    return None, None


def mark_holiday_done(state, client_id, today, holiday, kind):
    """Отметка ставится только после успешной публикации — иначе ручной
    перезапуск воркфлоу после сбоя не смог бы досдать пост."""
    cs = client_state(state, client_id)
    done = cs.get("holiday_done", [])
    done.append(holiday_done_key(today, holiday, kind))
    cs["holiday_done"] = done[-30:]


def photo_slot_for_client(client):
    """
    Самый ранний из выбранных слотов клиента (morning -> midday -> evening).
    Используется только при morning_photo=true — это и есть "утренний" слот
    с фото, даже если у клиента он физически называется midday/evening.
    """
    names = [s.get("name") for s in client.get("slots", [])]
    for slot in SLOT_ORDER:
        if slot in names:
            return slot
    return None


def pick_rubric_for_run(client, slot, today_abbr, state, test_mode):
    """
    Точка входа вместо pick_rubric() из main(). Без morning_photo — старое
    поведение (расписание по дням недели), ничего не меняется для тех, кому
    это не нужно. С morning_photo=true: слот, ближайший к утру из выбранных
    клиентом, всегда отдаёт рубрику с фото (rubric_folder == None, обычно
    «Фото работ»); остальные слоты крутят текстовые рубрики по кругу, без
    привязки к дням недели.
    """
    if test_mode or not client.get("morning_photo"):
        return pick_rubric(client, today_abbr, state, test_mode)

    rubrics = client.get("rubrics", [])
    if not rubrics:
        return None

    photo_rubrics = [r for r in rubrics if rubric_folder(r.get("name", "")) is None]
    text_rubrics = [r for r in rubrics if rubric_folder(r.get("name", "")) is not None]

    cs = client_state(state, client["client_id"])
    is_photo_slot = slot == photo_slot_for_client(client)
    pool = (photo_rubrics or rubrics) if is_photo_slot else (text_rubrics or rubrics)
    if not pool:
        return None

    key = "photo_tie_index" if is_photo_slot else "text_tie_index"
    idx = cs.get(key, 0) % len(pool)
    cs[key] = cs.get(key, 0) + 1
    return pool[idx]


# ---------- основной цикл ----------

def load_clients():
    out = []
    for path in sorted(glob.glob(os.path.join(CLIENTS_DIR, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Пропускаю {path}: не читается ({e})")
            continue
        if not data.get("client_id"):
            continue
        out.append(data)
    return out


def main():
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Moscow"))
    except Exception:
        now = datetime.utcnow()
    today = now.date()
    today_abbr = DOW_ABBR[today.weekday()]

    test_mode = bool(TEST_CLIENT_ID)
    if not test_mode and not SLOT:
        print("Не задан ни SLOT, ни TEST_CLIENT_ID — нечего делать.")
        sys.exit(0)

    clients = load_clients()
    if test_mode:
        clients = [c for c in clients if c["client_id"] == TEST_CLIENT_ID]
        if not clients:
            print(f"Клиент {TEST_CLIENT_ID} не найден среди clients/*.json")
            sys.exit(1)
    else:
        clients = [c for c in clients if c.get("active", True)]
        clients = [
            c for c in clients
            if any(s.get("name") == SLOT for s in c.get("slots", []))
        ]

    if not clients:
        print("Подходящих клиентов на этот запуск нет.")
        return

    state = load_state()
    posted_any = False

    for client in clients:
        cid = client["client_id"]

        # канал проверяем до выбора рубрики: иначе очередь рубрик сдвинулась бы
        # у клиента, который всё равно не может опубликоваться
        channel = client.get("tg_channel")
        if not channel:
            print(f"{cid}: в конфиге клиента не задан tg_channel, пропускаю.")
            continue

        holiday, kind = holiday_for_run(client, today, SLOT, state, test_mode)

        if holiday:
            # праздничный пост вытесняет обычную рубрику этого слота; фото из
            # очереди клиента не тратим — оно ждёт своей рубрики
            try:
                text = build_holiday_post(client, holiday, kind)
            except Exception as e:
                print(f"{cid}: ошибка генерации праздничного текста — {e}")
                continue
            what = f"праздник «{holiday['name']}» — {HOLIDAY_KINDS[kind]}"
            # фото из очереди клиента не тратим — оно ждёт своей рубрики;
            # к празднику картинка приходит с фотостока по теме
            photo_path, photo_meta = holiday_photo(holiday), None
        else:
            rubric = pick_rubric_for_run(client, SLOT, today_abbr, state, test_mode)
            if not rubric:
                print(f"{cid}: сегодня рубрик нет, пропускаю.")
                continue
            try:
                text = build_post(client, rubric)
            except Exception as e:
                print(f"{cid}: ошибка генерации текста — {e}")
                continue
            what = f"рубрику «{rubric.get('name')}»"
            photo_path, photo_meta = next_photo_for_client(
                client.get("yandex_folder", ""), rubric.get("name", "")
            )

        print(f"{cid}: публикую {what} в {channel}")
        try:
            post_to_telegram(channel, text, photo_path)
            posted_any = True
            if holiday:
                mark_holiday_done(state, cid, today, holiday, kind)
            if photo_meta:
                src_path, filename, posted_dir = photo_meta
                move_to_posted(src_path, filename, posted_dir)
        except Exception as e:
            print(f"{cid}: ошибка публикации — {e}")
            raise
    save_state(state)
    if not posted_any:
        print("Ни одного поста не ушло за этот запуск.")


if __name__ == "__main__":
    main()
