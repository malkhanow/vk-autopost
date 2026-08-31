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

import base64
import glob
import json
import mimetypes
import os
import sys
import tempfile
import time

import requests

# ---------- настройки ----------

CLIENTS_DIR = "clients"
STATE_FILE = "clients_state.json"

ROUTERAI_URL = "https://routerai.ru/api/v1/chat/completions"
ROUTERAI_MODEL = os.environ.get("ROUTERAI_MODEL", "google/gemini-3.1-flash-lite")

ROUTERAI_KEY = os.environ["ROUTERAI_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
YANDEX_TOKEN = os.environ.get("YANDEX_TOKEN", "")
PEXELS_KEY   = os.environ.get("PEXELS_KEY", "")
WEBAPP_URL   = os.environ.get("WEBAPP_URL", "")
WEBAPP_TOKEN = os.environ.get("WEBAPP_TOKEN", "")


def report_post_status(client_id, status, date_str):
    if not WEBAPP_URL:
        return
    try:
        import json as _json
        payload = _json.dumps({
            "action": "update_post_status",
            "token": WEBAPP_TOKEN,
            "id": client_id,
            "status": status,
            "date": date_str,
        })
        sep = "&" if "?" in WEBAPP_URL else "?"
        url = WEBAPP_URL + sep + "action=update_post_status"
        if WEBAPP_TOKEN:
            url += "&token=" + WEBAPP_TOKEN
        resp = requests.post(
            url,
            headers={"Content-Type": "text/plain;charset=utf-8"},
            data=payload,
            timeout=30,
        )
        if not resp.ok:
            print(f"Статус не записан в таблицу: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        print(f"Не удалось записать статус в таблицу: {e}")

SLOT = os.environ.get("SLOT", "")               # morning / midday / evening
TEST_CLIENT_ID = os.environ.get("TEST_CLIENT_ID", "").strip()

DOW_ABBR = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

# Потолок размера фото. Телеграм не примет больше ~10 МБ, поэтому и в vision
# нет смысла отправлять то, что потом не опубликуется: иначе модель разберёт
# картинку, а в канал уйдёт текст без неё.
MAX_PHOTO_MB = 9.5

# Плановое время слотов в UTC. Совпадает с cron в clients-post.yml и с
# SLOT_MSK_HOUR в Code.gs: morning 10:00 МСК, midday 14:00, evening 19:00.
SLOT_UTC_HOUR = {"morning": 7, "midday": 11, "evening": 16}

# Насколько поздно ещё допустимо опубликовать пост слота. Триггер Apps Script
# укладывается в 15 минут; всё, что опоздало сильнее, — это задержка планировщика
# GitHub, и такой пост лучше пропустить, чем разбудить подписчиков ночью.
SLOT_WINDOW_HOURS = 2

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
        print(f"Яндекс Диск {resp.status_code} для пути '{path}': {resp.text[:200]}")
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
    Название рубрики -> подпапка с фото для неё, или None.
    None означает общую очередь to_post (рубрика без привязанной папки,
    например «Фото работ» или «Объекты и документы»).
    Сопоставление по ключевым словам: название рубрики редактируется
    в CRM руками и может немного отличаться от исходного.
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
    return None  # to_post: «Фото работ», «Объекты и документы» и т.п.


def rubric_uses_vision(rubric_name):
    """
    Нужен ли vision-анализ для этой рубрики.

    reviews: в папке могут быть скриншоты отзывов или фото объектов —
    модель читает текст или описывает ситуацию и пишет релевантный пост.

    to_post (is_photo_work, folder=None): фото работ конкретного бизнеса —
    модель анализирует и пишет пост по увиденному.

    Все остальные (tips, faq, ideas): фото-заглушки без анализа.
    """
    folder = rubric_folder(rubric_name)
    if folder == "rubrics/reviews":
        return True
    if folder is None:          # to_post — только если «фото» в названии
        name = (rubric_name or "").lower()
        return "фото" in name
    return False


def rubric_loops_photos(rubric_name):
    """
    Для ряда рубрик фото-заглушки крутятся по кругу бесконечно и НЕ
    переезжают в posted/ после публикации. Когда файлы закончатся, система
    начнёт с самого старого снова. Это позволяет загрузить несколько картинок
    один раз и не следить за пополнением папки.

    tips, faq — типичные заглушки без привязки к конкретному событию.

    to_post (None), ideas, reviews — НЕ зацикленные: фото уходит в posted/
    после публикации (для to_post/ideas — чтобы те же фото не повторялись
    в канале, для reviews — скриншот привязан к реальному отзыву).
    """
    folder = rubric_folder(rubric_name)
    return folder in ("rubrics/tips", "rubrics/faq")


def next_photo_for_client(yandex_folder, rubric_name="", state=None, client_id=""):
    """
    Следующее фото для рубрики клиента, или (None, None), если фото нет.

    Две стратегии зависят от рубрики:

    «Конечная» (to_post, ideas, reviews) — самый старый файл, после
    публикации переезжает в posted/. Когда папка опустеет, пост выйдет
    без фото.

    «Круговая» (tips, faq) — файлы не переезжают; система запоминает имя
    последнего использованного файла в state и берёт следующий по алфавиту.
    Когда дошли до конца списка — начинаем сначала. Позволяет загрузить
    несколько заглушек один раз и больше не следить за папкой.
    """
    folder = rubric_folder(rubric_name)
    source = f"{yandex_folder}/{folder}" if folder else f"{yandex_folder}/to_post"
    posted = f"{yandex_folder}/posted"
    files = list_folder(source)
    if not files:
        return None, None

    loops = rubric_loops_photos(rubric_name)

    if loops and state is not None and client_id:
        # круговой перебор: запоминаем имя последнего файла в state
        cs = client_state(state, client_id)
        loop_key = f"loop_{folder.replace('/', '_')}"
        last_name = cs.get(loop_key, "")
        # сортируем по имени — стабильный порядок, не зависящий от времени
        names = sorted(f["name"] for f in files)
        if last_name in names:
            idx = (names.index(last_name) + 1) % len(names)
        else:
            idx = 0
        chosen_name = names[idx]
        chosen = next(f for f in files if f["name"] == chosen_name)
        cs[loop_key] = chosen_name
        # при цикле НЕ передаём posted — фото остаётся на месте
        try:
            local_path = download_yandex_file(chosen["path"])
        except Exception as e:
            print(f"Не удалось скачать {chosen['path']}: {e}")
            return None, None
        return local_path, None   # None = не переносить в posted/
    else:
        # конечная стратегия: самый старый файл, после — в posted/
        chosen = files[0]         # list_folder сортирует по created
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
    last_error = None
    for attempt in range(3):
        if attempt:
            time.sleep(attempt * 3)
        try:
            resp = requests.post(
                ROUTERAI_URL,
                headers={"Authorization": f"Bearer {ROUTERAI_KEY}"},
                json=payload,
                # с картинкой запрос заметно тяжелее обычного текстового
                timeout=120,
            )
        except requests.RequestException as e:
            # обрыв связи или таймаут: сеть у раннера бывает нестабильной,
            # это повод повторить, а не терять публикацию
            last_error = e
            print(f"RouterAI: сеть недоступна ({e}), повтор...")
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            last_error = f"HTTP {resp.status_code}"
            print(f"RouterAI: HTTP {resp.status_code}, повтор...")
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"RouterAI: HTTP {resp.status_code} — {resp.text[:300]}")
        choices = (resp.json() or {}).get("choices") or []
        if not choices:
            raise RuntimeError(f"RouterAI вернул ответ без текста: {resp.text[:300]}")
        content = (choices[0].get("message") or {}).get("content")
        if not content:
            raise RuntimeError("RouterAI вернул пустой текст")
        return strip_model_noise(content)
    raise RuntimeError(f"RouterAI не отвечает после трёх попыток ({last_error})")


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


def append_hashtags(text, client):
    """
    Дописывает хэштеги из поля hashtags конфига клиента в конец поста.
    Если поле отсутствует или пустое — текст не меняется.
    """
    tags = (client.get("hashtags") or "").strip()
    if not tags:
        return text
    return text.rstrip() + "\n\n" + tags


def image_to_data_url(photo_path):
    """
    Локальное фото превращается в data URL для RouterAI.
    Фото не публикуется отдельно и никуда не загружается публично.
    """
    if not photo_path or not os.path.exists(photo_path):
        return None

    mime = mimetypes.guess_type(photo_path)[0] or "image/jpeg"
    if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        mime = "image/jpeg"

    with open(photo_path, "rb") as f:
        raw = f.read()

    # Не отправляем чрезмерно большие изображения в AI.
    # Если файл слишком большой, обычный пост всё равно сможет выйти.
    if len(raw) > MAX_PHOTO_MB * 1024 * 1024:
        print(f"Фото {os.path.basename(photo_path)} больше {MAX_PHOTO_MB} МБ — "
              "vision пропущен, оно всё равно не уйдёт в канал.")
        return None

    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def build_post(client, rubric, photo_path=None):
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

    style = (
        f"строго в стиле, описанном ниже:\n{client['style_prompt']}\n"
        if client.get("style_prompt")
        else "простым разговорным языком.\n"
    )

    # vision включается только для рубрик, где анализ реально нужен:
    # «Отзывы» (скриншоты/фото) и «Фото работ» (результаты работы).
    # tips, faq, ideas — заглушки без анализа.
    uses_vision = rubric_uses_vision(rubric.get("name", ""))

    if uses_vision and photo_path:
        folder = rubric_folder(rubric.get("name", ""))
        is_reviews = folder == "rubrics/reviews"

        if is_reviews:
            # В папке reviews — скриншоты отзывов или фото результатов.
            # Сначала пробуем прочитать текст с изображения, потом описать.
            context_parts = [
                "Ты получаешь изображение из рубрики «Отзывы и результаты».",
                "Если на изображении есть читаемый текст (скриншот переписки, "
                "отзыв, сообщение) — прочитай его и напиши пост от лица "
                "владельца бизнеса: поблагодари клиента, подчеркни суть отзыва, "
                "не называй имён без необходимости.",
                "Если текста нет или он нечитаем, но есть фото результата "
                "(объект, сделка, встреча) — напиши тематический пост по "
                "рубрике «Отзывы и результаты» без опоры на конкретику изображения.",
                "Не выдумывай суммы, адреса, имена и условия сделки.",
                f"Напиши пост 60–100 слов, {style}",
                "Ответь только текстом поста."
            ]
        else:
            # «Фото работ» и аналоги: анализируем что видим, пишем пост
            photo_cfg = client.get("photo_post") or {}
            photo_instruction = (
                photo_cfg.get("instruction")
                or client.get("photo_post_instruction")
                or ""
            )
            photo_topics = (
                photo_cfg.get("topics")
                or client.get("photo_post_topics")
                or []
            )
            context_parts = [
                "Это рубрика «Фото работ». Ты получаешь настоящее фото клиента.",
                "СНАЧАЛА внимательно изучи изображение и определи только то, "
                "что действительно можно увидеть: объекты, детали, тип места, "
                "ситуацию, визуальный контекст.",
                "Затем САМ выбери наиболее естественную и полезную тему для "
                "полноценного поста в рамках бизнеса клиента.",
                "Фото — отправная точка для мысли, а не повод написать сухое "
                "описание картинки.",
            ]
            if photo_instruction:
                context_parts.append(
                    "Специальная инструкция для этой ниши: " + str(photo_instruction)
                )
            if photo_topics:
                topic_text = ", ".join(str(x) for x in photo_topics if str(x).strip())
                if topic_text:
                    context_parts.append(
                        "Допустимые направления и идеи, которые можно учитывать: "
                        + topic_text
                        + ". Не нужно использовать их все и не нужно выбирать их "
                        "по фиксированному порядку — выбери наиболее релевантное "
                        "конкретному изображению."
                    )
            context_parts.extend([
                "Не пиши пост в формате «на фото изображено...».",
                "Не выдумывай цены, адреса, характеристики, факты сделки, "
                "результаты работы, документы, суммы или другие сведения, которых "
                "нет в данных клиента или которые нельзя достоверно установить "
                "по изображению.",
                "Если на фото есть документ или мелкий текст, который нельзя "
                "уверенно прочитать, не выдумывай его содержание.",
                "Если фотография сама по себе не даёт достаточно информации для "
                "конкретного утверждения, используй её только как визуальный "
                "контекст и выбери более общий экспертный или тематический угол.",
                "Напиши один полноценный пост 80–150 слов.",
                style,
                "Ответь только готовым текстом поста."
            ])

        user = "\n".join(lines) + "\n\n" + "\n".join(context_parts)
        data_url = image_to_data_url(photo_path)
        if data_url:
            try:
                return ai_text([
                    {"role": "system", "content": (
                        "Ты редактор и SMM-автор. Ты умеешь анализировать изображения: "
                        "читать текст на скриншотах и описывать визуальный контекст фотографий. "
                        "Используй изображение как реальный визуальный контекст, затем создавай "
                        "самостоятельный полезный пост. Текст должен звучать как пост владельца "
                        "бизнеса, а не как подпись к фотографии. Не показывай ход рассуждений."
                    )},
                    {"role": "user", "content": [
                        {"type": "text", "text": user},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ]},
                ], max_tokens=1100, temperature=0.8)
            except Exception as e:
                # Если vision-провайдер временно недоступен, не теряем публикацию:
                # пробуем старую текстовую генерацию без изображения.
                print(f"Vision-анализ фото не удался — пробую обычную генерацию: {e}")

    import random
    formats = random.choice([
        "Напиши пост в формате короткой истории из практики (80–120 слов). Начни с конкретной ситуации, заверши выводом.",
        "Напиши пост в формате неожиданного факта или наблюдения (60–90 слов). Начни с чего-то, что удивит читателя.",
        "Напиши пост в формате совета от эксперта (70–100 слов). Конкретный, практичный, без воды.",
        "Напиши пост в формате личного размышления (80–110 слов). Разговорный тон, как будто делишься мыслью с другом.",
        "Напиши пост в формате короткого диалога или цитаты клиента (60–90 слов). Живой, эмоциональный.",
        "Напиши пост в формате «до и после» или сравнения (70–100 слов). Покажи контраст или изменение.",
        "Напиши пост с неожиданного угла зрения (70–100 слов). Не стандартный совет, а взгляд изнутри профессии.",
    ])

    style_instruction = (
        f"СТИЛЬ АВТОРА (соблюдать обязательно): {client['style_prompt']}\n\n"
        if client.get("style_prompt") else ""
    )

    user = style_instruction + "\n".join(lines) + (
        f"\n\n{formats} "
        + style
        + "Не выдумывай цены, сроки и гарантии, если они в запретах. "
        "Ответь только текстом поста, без черновиков и пометок о проверке."
    )
    return ai_text([
        {"role": "system", "content": (
            "Ты пишешь посты для соцсетей малого бизнеса от лица владельца. "
            "Если задан style_prompt — это описание реальной манеры речи этого человека, "
            "её нужно точно воспроизвести: длина предложений, обращение, любимые обороты. "
            "Каждый пост должен отличаться от предыдущих по структуре и форме подачи — "
            "никогда не повторяй один и тот же формат два раза подряд. "
            "ЗАПРЕЩЕНО начинать пост со слов или фраз: «Знаете», «А знаете», «Вы знаете», "
            "«Знаете ли вы», «Интересный факт», «Факт дня», «Сегодня хочу», «Хочу поделиться», "
            "«Часто меня спрашивают», «Меня часто спрашивают», «Недавно я», «Однажды я». "
            "Начинай пост живо и по-разному — с действия, с наблюдения, с конкретной детали. "
            "В ответе — только готовый текст поста, ничего больше: ни черновиков, "
            "ни заметок о проверке длины, ни заголовков вроде «Пост:» или «Draft». "
            "Не показывай ход рассуждений — только финальный результат."
        )},
        {"role": "user", "content": user},
    ])


HOLIDAY_TASKS = {
    "before7": (
        "До праздника ровно неделя. "
        "Начни пост ИМЕННО с одной из этих фраз (выбери ту, что звучит лучше для этого праздника и тона клиента): "
        "«Уже через неделю — [название праздника]!», "
        "«До [название праздника] остаётся всего семь дней.», "
        "«Совсем скоро наступает [название праздника]…». "
        "После вступления — в двух-трёх предложениях расскажи, что это за день и почему он важен. "
        "Коротко, без исторической справки на полстраницы. Всего 60–80 слов.",
        280,
    ),
    "before1": (
        "Праздник наступает завтра. "
        "Начни пост ИМЕННО с одной из этих фраз (выбери ту, что звучит лучше): "
        "«Завтра — [название праздника]!», "
        "«Последний день перед [название праздником]. Завтра отмечаем!», "
        "«Уже завтра наступает [название праздника]…». "
        "После вступления — в трёх-четырёх предложениях расскажи про этот день: "
        "что отмечают, чем он важен людям, какое настроение он несёт. "
        "Без сухих исторических фактов — живо и тепло. Всего 80–100 слов.",
        420,
    ),
    "day": (
        "Сегодня праздник. "
        "Начни пост ИМЕННО с одной из этих фраз (выбери ту, что звучит лучше): "
        "«С [название праздником]!», "
        "«Сегодня [название праздника] — поздравляем всех, кто отмечает!», "
        "«Поздравляем с [название праздником]!». "
        "После поздравления — тёплые слова подписчикам, без официоза и без пересказа истории праздника. "
        "Искренне и по-человечески. Всего 60–80 слов.",
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
                if size > MAX_PHOTO_MB * 1024 * 1024:   # телеграм не примет больше
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

    def send_message(body, parse_mode="Markdown"):
        payload = {"chat_id": channel, "text": body}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return requests.post(f"{base_url}/sendMessage", json=payload, timeout=30)

    def send_photo(path, caption, parse_mode="Markdown"):
        data = {"chat_id": channel}
        if caption:
            data["caption"] = caption
            if parse_mode:
                data["parse_mode"] = parse_mode
        with open(path, "rb") as f:
            return requests.post(
                f"{base_url}/sendPhoto", data=data, files={"photo": f}, timeout=60
            )

    has_photo = bool(photo_path and os.path.exists(photo_path))
    if has_photo:
        size_mb = os.path.getsize(photo_path) / 1024 / 1024
        if size_mb > MAX_PHOTO_MB:
            print(f"Фото больше {MAX_PHOTO_MB} МБ — отправляю без него.")
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
                    resp = send_photo(photo_path, text, parse_mode=None)
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
        # id сообщения — по нему пост находится в канале, если возникнут вопросы
        try:
            mid = (resp.json().get("result") or {}).get("message_id")
        except Exception:
            mid = None
        print(f"Опубликовано в {channel}" + (f" (сообщение {mid})." if mid else "."))
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

    cs = client_state(state, client["client_id"])

    # У клиента на двух слотах день часто закрыт одной рубрикой — тогда оба
    # слота получали её же, и в канал уходило два поста на одну тему подряд.
    # Помним, что уже вышло сегодня, и отдаём второму слоту другое.
    if cs.get("last_day") != today_abbr:
        cs["last_day"] = today_abbr
        cs["today_used"] = []
    used = cs.get("today_used") or []

    pool = [r for r in matches if r.get("name") not in used]
    if not pool:
        # рубрики этого дня исчерпаны — берём любую другую из плана клиента,
        # лишь бы не повторить сегодняшнюю
        pool = [r for r in rubrics if r.get("name") not in used]
    if not pool:
        pool = matches

    idx = cs["tie_index"] % len(pool)
    cs["tie_index"] += 1
    chosen = pool[idx]
    cs["today_used"] = used + [chosen.get("name")]
    return chosen


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
    Если у клиента больше одного слота — первый (самый ранний) автоматически
    берёт фото-рубрику из to_post, остальные крутят текстовые рубрики по
    кругу без привязки к дням недели. Галочка morning_photo больше не нужна.

    Если слот один — обычное расписание по дням недели (как у клиентов
    на тарифе СТАРТ: один слот, всё по расписанию).

    Тестовый прогон всегда идёт по кругу по всем рубрикам.
    """
    if test_mode:
        return pick_rubric(client, today_abbr, state, test_mode)

    slot_names = [s.get("name") for s in client.get("slots", []) if s.get("name")]
    ordered_slots = [s for s in SLOT_ORDER if s in slot_names]

    # один слот — обычное расписание по дням
    if len(ordered_slots) <= 1:
        return pick_rubric(client, today_abbr, state, test_mode)

    rubrics = client.get("rubrics", [])
    if not rubrics:
        return None

    photo_rubrics = [r for r in rubrics if rubric_folder(r.get("name", "")) is None]
    text_rubrics = [r for r in rubrics if rubric_folder(r.get("name", "")) is not None]

    cs = client_state(state, client["client_id"])
    is_photo_slot = (slot == ordered_slots[0])
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
    from datetime import datetime, timezone
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

    # Расписание GitHub Actions — «по возможности»: сам GitHub пишет, что при
    # нагрузке запуск откладывается, а иногда пропускается совсем. На практике
    # один и тот же cron срабатывал и в 19:14, и в 00:26 UTC. Пост, вышедший
    # среди ночи у подписчиков клиента, хуже, чем не вышедший вовсе, поэтому
    # публикуем только если запуск попал в разумное окно вокруг слота.
    # Точное время обеспечивает триггер Apps Script, который дёргает workflow
    # через workflow_dispatch; расписание GitHub остаётся запасным вариантом.
    if not test_mode and SLOT in SLOT_UTC_HOUR:
        now_utc = datetime.now(timezone.utc)
        planned = now_utc.replace(
            hour=SLOT_UTC_HOUR[SLOT], minute=0, second=0, microsecond=0
        )
        drift_h = (now_utc - planned).total_seconds() / 3600
        if drift_h < -0.5 or drift_h > SLOT_WINDOW_HOURS:
            print(
                f"Слот «{SLOT}» запланирован на {SLOT_UTC_HOUR[SLOT]:02d}:00 UTC, "
                f"сейчас {now_utc:%H:%M} UTC — отклонение {drift_h:+.1f} ч. "
                f"Допустимо от -0.5 до +{SLOT_WINDOW_HOURS} ч, поэтому публикация "
                f"пропущена: пост не должен выходить у подписчиков глубокой ночью."
            )
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

        # Слот отрабатывает ровно один раз в день. Запусков у нас два источника:
        # точный триггер Apps Script и запасное расписание GitHub. Если GitHub
        # случайно сработает вовремя, оба попадут в окно ±2 часа — и клиент
        # получит два поста подряд. Отметка в состоянии это исключает.
        cs = client_state(state, cid)
        slot_done = cs.setdefault("slot_done", {})
        if not test_mode and SLOT and slot_done.get(SLOT) == today.isoformat():
            print(f"{cid}: слот «{SLOT}» сегодня уже отработал, пропускаю.")
            continue

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
            what = f"рубрику «{rubric.get('name')}»"
            photo_path, photo_meta = next_photo_for_client(
                client.get("yandex_folder", ""), rubric.get("name", ""),
                state=state, client_id=cid
            )
            try:
                # Только «Фото работ» получает изображение внутри AI-запроса.
                # Остальные рубрики сохраняют прежнюю текстовую логику.
                text = build_post(client, rubric, photo_path)
            except Exception as e:
                print(f"{cid}: ошибка генерации текста — {e}")
                # фото уже скачано во временный файл — убираем за собой.
                # В to_post оно остаётся: пост не вышел, очередь не двигаем
                if photo_path and os.path.exists(photo_path):
                    try:
                        os.unlink(photo_path)
                    except Exception:
                        pass
                continue

        text = append_hashtags(text, client)
        print(f"{cid}: публикую {what} в {channel}")
        try:
            post_to_telegram(channel, text, photo_path)
            posted_any = True
            # отметку ставим только после реальной публикации: если пост упал,
            # следующий запуск в том же окне должен попробовать ещё раз
            if not test_mode and SLOT:
                slot_done[SLOT] = today.isoformat()
            if holiday:
                mark_holiday_done(state, cid, today, holiday, kind)
            if photo_meta:
                src_path, filename, posted_dir = photo_meta
                move_to_posted(src_path, filename, posted_dir)
            # пишем статус обратно в таблицу — CRM покажет «Опубликован»
            date_str = today.strftime("%d.%m.%Y") + " " + __import__("datetime").datetime.now().strftime("%H:%M")
            report_post_status(cid, "Опубликован", date_str)
        except Exception as e:
            print(f"{cid}: ошибка публикации — {e}")
            date_str = today.strftime("%d.%m.%Y") + " " + __import__("datetime").datetime.now().strftime("%H:%M")
            report_post_status(cid, f"Ошибка: {str(e)[:80]}", date_str)

    save_state(state)
    if not posted_any:
        print("Ни одного поста не ушло за этот запуск.")


if __name__ == "__main__":
    main()
