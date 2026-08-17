#!/usr/bin/env python3
"""
Автопостинг товаров из Яндекс.Диска - без ручной сортировки по папкам.

Все фото просто лежат вперемешку в SOURCE_DIR ("to_post"). Раз в день
скрипт:
  1. Скачивает ВСЕ ещё не опубликованные фото из SOURCE_DIR.
  2. Читает время съёмки каждого фото из EXIF (если есть).
  3. Показывает все фото разом нейросети и просит определить, какие из
     них - один и тот же товар (используя и визуальное сходство, и
     время съёмки как подсказку).
  4. Берёт САМУЮ РАННЮЮ группу (по времени съёмки) - это сегодняшний
     пост, остальные группы останутся на следующие запуски.
  5. Для выбранной группы просит нейросеть написать заголовок/описание/
     призыв по фиксированному шаблону.
  6. Публикует альбом в Telegram-канал.
  7. Добавляет запись в RSS-ленту (docs/feed.xml, обычный импорт, одно
     фото-обложка на пост - без режима "статья", чтобы пост в VK
     оставался обычным, без перехода на отдельную страницу).
  8. Переносит фото выбранной группы из SOURCE_DIR в POSTED_DIR.

Нужные переменные окружения (задаются в GitHub Secrets):
  YANDEX_TOKEN        - OAuth-токен Яндекс.Диска
  OPENROUTER_API_KEY  - ключ OpenRouter
  SITE_BASE_URL       - адрес GitHub Pages, например
                        https://malkhanow.github.io/vk-autopost
  TELEGRAM_BOT_TOKEN  - токен бота от @BotFather
  TELEGRAM_CHANNEL    - юзернейм канала, например @customstudio_print
"""

import base64
import io
import json
import mimetypes
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape

import requests
from PIL import Image, ExifTags

# ---------- настройки ----------

SOURCE_DIR = "/to_post"
POSTED_DIR = "/posted"

ITEMS_FILE = "feed_items.json"
FEED_FILE = "docs/feed.xml"
IMAGES_DIR = "docs/images"
STATE_FILE = "post_state.json"
MAX_ITEMS_IN_FEED = 30
MAX_CAROUSELS_PER_MONTH = 15  # бесплатный лимит Crosslybot на кросспостинг в VK

# Фиксированная бесплатная vision-модель — Mistral (pixtral умеет картинки,
# бесплатный план без кредитки, 2 req/min достаточно для ежедневного постинга).
MISTRAL_MODEL = "pixtral-12b-2409"
MAX_PHOTOS_PER_GROUPING_CALL = 8  # Mistral принимает максимум 8 изображений за запрос
MAX_PHOTOS_PER_POST = 4  # жёсткий предел фото в одном товаре/посте

BENEFITS_BLOCK = (
    "🔥 не трескается\n"
    "☔️ спокойно переносит стирки\n"
    "🎨 сохраняет яркость и детали"
)
CLOSING_BLOCK = (
    "📩 Присылай изображение в сообщения — обсудим и сделаем.\n"
    "✨ Custom Studio — носи то, что нравится тебе."
)

GROUPING_PROMPT = (
    "Тебе показаны фотографии товаров (одежда с кастомными принтами) "
    "вперемешку - несколько разных товаров, у каждого может быть "
    "несколько фото с разных ракурсов. К каждому фото приложена подпись "
    "с именем файла и временем съёмки (если известно).\n\n"
    "Определи, какие фото относятся к ОДНОМУ И ТОМУ ЖЕ физическому "
    "товару (разные ракурсы одной вещи), а какие - к разным товарам. "
    "Ориентируйся в первую очередь на то, что видно на фото (одна и та "
    "же вещь, цвет, принт), время съёмки используй как дополнительную "
    "подсказку (фото одного товара обычно снимают подряд, за несколько "
    "минут).\n\n"
    f"В ОДНОЙ группе не может быть больше {MAX_PHOTOS_PER_POST} фото - "
    "даже если тебе кажется, что похожих фото больше, разбей их на "
    "несколько групп. Не объединяй в одну группу фото, если не уверен, "
    "что это точно один и тот же физический товар - лучше сделать "
    "несколько маленьких групп, чем одну большую ошибочную.\n\n"
    "Ответь СТРОГО в формате JSON-ОБЪЕКТА (не массива) с одним ключом "
    '"groups", без markdown:\n'
    '{"groups": [{"files": ["имя1.jpg", "имя2.jpg"]}, {"files": ["имя3.jpg"]}]}\n\n'
    "Каждый файл должен встретиться ровно в одной группе."
)

CAPTION_PROMPT = (
    "Ты ведёшь соцсети Custom Studio - компании, которая печатает "
    "кастомные принты на футболках, куртках, сумках и другой одежде. "
    "Посмотри на фото товара (может быть несколько ракурсов одного и "
    "того же изделия) и напиши три части поста в формате JSON, "
    "БЕЗ markdown-разметки, на русском:\n\n"
    '{"hook": "...", "body": "...", "cta": "..."}\n\n'
    "hook - один короткий цепляющий заголовок с эмодзи-кружком в начале "
    "(🖤/🔵/💙 и т.п.), например «🖤 Рэй на чёрной оверсайз футболке 👀🔥».\n"
    "body - 1-3 предложения: что именно сделали, для кого/какой повод, "
    "какие детали видно на фото (цвет, принт, ткань, деталь). Пиши "
    "конкретно про то, что видно на фото, не общими фразами.\n"
    "cta - одно предложение с призывом прислать свой дизайн для похожего "
    "изделия, в стиле «А ещё ты можешь прислать свой дизайн, картинку "
    "или персонажа — мы перенесём его на одежду» (адаптируй под то, что "
    "на фото - футболка/куртка/сумка/другое).\n\n"
    "НИКОГДА не указывай цену и не выдумывай её."
)

YANDEX_TOKEN = os.environ["YANDEX_TOKEN"]
MISTRAL_API_KEY = os.environ["MISTRAL_API_KEY"]
SITE_BASE_URL = os.environ.get(
    "SITE_BASE_URL", "https://malkhanow.github.io/vk-autopost"
).rstrip("/")
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL = os.environ["TELEGRAM_CHANNEL"]
# Мостовой канал: сюда шлём только «карусельные» посты, его слушает
# Crosslybot и репостит их в VK каруселью. Если пусто - карусельный режим
# выключен, всё уходит в VK одиночками через RSS (как раньше).
TELEGRAM_BRIDGE_CHANNEL = os.environ.get("TELEGRAM_BRIDGE_CHANNEL", "").strip()


# ---------- Яндекс.Диск ----------

def yandex_headers():
    return {"Authorization": f"OAuth {YANDEX_TOKEN}"}


def list_files_in_source():
    resp = requests.get(
        "https://cloud-api.yandex.net/v1/disk/resources",
        headers=yandex_headers(),
        params={
            "path": SOURCE_DIR,
            "limit": 200,
            "sort": "created",
            "fields": "_embedded.items.name,_embedded.items.path,_embedded.items.type,_embedded.items.created",
        },
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json().get("_embedded", {}).get("items", [])
    return [i for i in items if i.get("type") == "file"]


def download_file(path, dest_path):
    resp = requests.get(
        "https://cloud-api.yandex.net/v1/disk/resources/download",
        headers=yandex_headers(),
        params={"path": path},
        timeout=30,
    )
    resp.raise_for_status()
    href = resp.json()["href"]
    with requests.get(href, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)


def move_to_posted(src_path, filename):
    dest_path = f"{POSTED_DIR}/{filename}"
    resp = requests.post(
        "https://cloud-api.yandex.net/v1/disk/resources/move",
        headers=yandex_headers(),
        params={"from": src_path, "path": dest_path, "overwrite": "true"},
        timeout=30,
    )
    resp.raise_for_status()


# ---------- EXIF ----------

def get_exif_datetime(local_path, fallback_iso):
    """Возвращает время съёмки в виде строки, либо fallback (время загрузки на Диск)."""
    try:
        img = Image.open(local_path)
        exif = img.getexif()
        if exif:
            for tag_id, value in exif.items():
                tag = ExifTags.TAGS.get(tag_id)
                if tag in ("DateTimeOriginal", "DateTime"):
                    return value  # формат "YYYY:MM:DD HH:MM:SS"
    except Exception:
        pass
    return fallback_iso


# ---------- Mistral ----------

def call_mistral_vision(prompt_text, image_entries, max_retries=3):
    """image_entries: список (подпись_текст, путь_к_файлу)."""
    content = [{"type": "text", "text": prompt_text}]
    for label, path in image_entries:
        mime_type, _ = mimetypes.guess_type(path)
        mime_type = mime_type or "image/jpeg"
        with open(path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")
        if label:
            content.append({"type": "text", "text": label})
        content.append(
            {
                "type": "image_url",
                "image_url": f"data:{mime_type};base64,{image_b64}",
            }
        )

    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": MISTRAL_MODEL,
        "messages": [{"role": "user", "content": content}],
    }

    last_error = None
    for attempt in range(1, max_retries + 1):
        resp = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=90,
        )
        if resp.status_code == 429:
            last_error = f"429 too many requests (попытка {attempt}/{max_retries})"
            print(last_error)
            time.sleep(15 * attempt)
            continue
        if resp.status_code == 400:
            raise RuntimeError(f"Mistral 400 Bad Request: {resp.text}")
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            last_error = f"Mistral вернул ошибку: {data['error']}"
            print(last_error)
            time.sleep(15 * attempt)
            continue
        if "choices" not in data:
            raise RuntimeError(f"Неожиданный ответ Mistral (нет 'choices'): {data}")
        raw = data["choices"][0]["message"]["content"].strip().strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
        start = raw.find("{")
        if start == -1:
            raise RuntimeError(f"В ответе нейросети нет JSON-объекта: {raw!r}")
        decoder = json.JSONDecoder()
        parsed_obj, _ = decoder.raw_decode(raw, start)
        return parsed_obj

    raise RuntimeError(f"Mistral не ответил после {max_retries} попыток: {last_error}")


def group_photos(local_files):
    """local_files: список (filename, local_path, exif_time_str)."""
    entries = [
        (f"Файл: {name}, время съёмки: {exif_time}", path)
        for name, path, exif_time in local_files
    ]
    result = call_mistral_vision(GROUPING_PROMPT, entries)
    # нейросеть иногда возвращает JSON по-разному оформленным - подстраиваемся
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        if "groups" in result and isinstance(result["groups"], list):
            return result["groups"]
        # на случай если модель вернула один объект-группу без обёртки
        if "files" in result:
            return [result]
    raise RuntimeError(f"Неожиданный формат ответа группировки: {result}")


def generate_post_text(local_paths):
    entries = [("", path) for path in local_paths]
    result = call_mistral_vision(CAPTION_PROMPT, entries)
    return result["hook"], result["body"], result["cta"]


def assemble_full_text(hook, body, cta):
    return f"{hook}\n\n{body}\n\n{BENEFITS_BLOCK}\n\n{cta}\n\n{CLOSING_BLOCK}"


# ---------- Telegram ----------

def post_to_telegram(channel, image_paths, caption):
    if len(image_paths) == 1:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        with open(image_paths[0], "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": channel, "caption": caption},
                files={"photo": f},
                timeout=60,
            )
        resp.raise_for_status()
        if not resp.json().get("ok"):
            raise RuntimeError(f"Telegram API error: {resp.json()}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
    media, files, open_files = [], {}, []
    try:
        for idx, path in enumerate(image_paths[:10]):
            field = f"photo{idx}"
            item = {"type": "photo", "media": f"attach://{field}"}
            if idx == 0:
                item["caption"] = caption
            media.append(item)
            fh = open(path, "rb")
            open_files.append(fh)
            files[field] = fh
        resp = requests.post(
            url,
            data={"chat_id": channel, "media": json.dumps(media)},
            files=files,
            timeout=90,
        )
    finally:
        for fh in open_files:
            fh.close()
    resp.raise_for_status()
    if not resp.json().get("ok"):
        raise RuntimeError(f"Telegram API error: {resp.json()}")


# ---------- RSS-лента ----------

def load_items():
    if not os.path.exists(ITEMS_FILE):
        return []
    with open(ITEMS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_items(items):
    with open(ITEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def load_state():
    """Счётчик карусельных постов за текущий месяц (переживает перезапуски
    за счёт коммита post_state.json обратно в репо)."""
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def build_feed_xml(items):
    now = format_datetime(datetime.now(timezone.utc))
    entries = []
    for item in items[:MAX_ITEMS_IN_FEED]:
        # поддержка старой схемы ленты: новые записи имеют images[]/full_text/hook,
        # старые - image (одиночное) и caption. Без этого одна старая запись
        # роняла весь фид с KeyError: 'images'.
        images = item.get("images") or (
            [item["image"]] if item.get("image") else []
        )
        if not images:
            continue  # запись без картинки пропускаем, а не валим весь прогон
        text = item.get("full_text") or item.get("caption", "")
        hook = item.get("hook") or text[:80]
        cover_url = f"{SITE_BASE_URL}/images/{images[0]}"
        description_html = (
            f'&lt;img src="{escape(cover_url)}"/&gt;'
            f"&lt;p&gt;{escape(text).replace(chr(10), ' ')}&lt;/p&gt;"
        )
        entries.append(
            "  <item>\n"
            f"    <title>{escape(hook[:80])}</title>\n"
            f"    <link>{escape(SITE_BASE_URL)}/#{escape(item['guid'])}</link>\n"
            f"    <guid isPermaLink=\"false\">{escape(item['guid'])}</guid>\n"
            f"    <pubDate>{escape(item['pubDate'])}</pubDate>\n"
            f"    <description>{description_html}</description>\n"
            f"    <enclosure url=\"{escape(cover_url)}\" type=\"image/jpeg\"/>\n"
            "  </item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "<channel>\n"
        "  <title>Custom Studio - Печать на футболках</title>\n"
        f"  <link>{escape(SITE_BASE_URL)}</link>\n"
        "  <description>Автоматическая лента товаров для импорта в VK</description>\n"
        f"  <lastBuildDate>{now}</lastBuildDate>\n"
        + "\n".join(entries) +
        "\n</channel>\n</rss>\n"
    )
    os.makedirs(os.path.dirname(FEED_FILE), exist_ok=True)
    with open(FEED_FILE, "w", encoding="utf-8") as f:
        f.write(xml)


# ---------- основной сценарий ----------

def main():
    files = list_files_in_source()
    if not files:
        print("Нет новых фото в", SOURCE_DIR, "- сегодня постить нечего.")
        return

    files = files[:MAX_PHOTOS_PER_GROUPING_CALL]

    now = datetime.now(timezone.utc)
    state = load_state()
    if state.get("month") != now.strftime("%Y-%m"):
        state = {"month": now.strftime("%Y-%m"), "carousels": 0}

    # "Карусельный" день (пост уходит в VK каруселью через мостовой канал):
    # по нечётным числам месяца ("через день"), пока не упёрлись в бесплатный
    # лимит Crosslybot (15/мес) и только если мостовой канал вообще настроен.
    # Иначе - "одиночный" день: пост уходит в VK через RSS одной фотографией.
    is_carousel_day = (
        bool(TELEGRAM_BRIDGE_CHANNEL)
        and now.day % 2 == 1
        and state.get("carousels", 0) < MAX_CAROUSELS_PER_MONTH
    )
    print(
        "Режим на сегодня:",
        "КАРУСЕЛЬ (мостовой канал -> VK)" if is_carousel_day
        else "ОДИНОЧКА (RSS -> VK)",
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        local_files = []  # (name, local_path, exif_time, yandex_path)
        for idx, f in enumerate(files, start=1):
            print(f"Скачиваю фото {idx}/{len(files)}: {f['name']}...")
            local_path = os.path.join(tmp_dir, f["name"])
            download_file(f["path"], local_path)
            exif_time = get_exif_datetime(local_path, f.get("created", ""))
            local_files.append((f["name"], local_path, exif_time, f["path"]))

        print(f"Скачано {len(local_files)} фото, определяю группы товаров...")

        if len(local_files) == 1:
            groups = [{"files": [local_files[0][0]]}]
        else:
            groups = group_photos([(n, p, t) for n, p, t, _ in local_files])

        by_name = {n: (p, t, yp) for n, p, t, yp in local_files}

        # жёсткая защита: даже если нейросеть ошиблась и слепила
        # группу больше MAX_PHOTOS_PER_POST - обрезаем её здесь,
        # а не доверяем только текстовой инструкции
        capped_groups = []
        for g in groups:
            valid_files = [n for n in g.get("files", []) if n in by_name]
            if not valid_files:
                continue
            if len(valid_files) > MAX_PHOTOS_PER_POST:
                print(
                    f"Внимание: нейросеть предложила группу из "
                    f"{len(valid_files)} фото, обрезаю до {MAX_PHOTOS_PER_POST} "
                    f"(беру самые ранние по времени съёмки): {valid_files}"
                )
                valid_files = sorted(
                    valid_files, key=lambda n: by_name[n][1]
                )[:MAX_PHOTOS_PER_POST]
            capped_groups.append({"files": valid_files})
        groups = capped_groups

        # находим группу с самым ранним временем съёмки
        def group_earliest(g):
            times = [by_name[n][1] for n in g["files"] if n in by_name]
            return min(times) if times else "9999"

        groups = [g for g in groups if any(n in by_name for n in g["files"])]
        if not groups:
            print("Не удалось сгруппировать фото, пропускаю запуск.")
            return
        chosen = min(groups, key=group_earliest)
        chosen_files = [n for n in chosen["files"] if n in by_name]
        print(f"Выбрана группа (товар) из {len(chosen_files)} фото: {chosen_files}")

        local_paths = [by_name[n][0] for n in chosen_files]

        print("Запрашиваю текст поста у нейросети...")
        hook, body, cta = generate_post_text(local_paths)
        full_text = assemble_full_text(hook, body, cta)
        print("Текст готов:\n", full_text)

        # основной Telegram-канал получает пост всегда (твоя ТГ-аудитория)
        print("Публикую в основной Telegram-канал...")
        post_to_telegram(TELEGRAM_CHANNEL, local_paths, full_text)
        print("Опубликовано в основной Telegram-канал.")

        image_ids = []
        if is_carousel_day:
            # Карусельный день: дублируем пост в мостовой канал. Его слушает
            # Crosslybot и публикует в VK каруселью. В RSS этот пост НЕ
            # добавляем, иначе в VK будет дубль (карусель + одиночка).
            print("Публикую в мостовой канал (для VK-карусели через Crosslybot)...")
            post_to_telegram(TELEGRAM_BRIDGE_CHANNEL, local_paths, full_text)
            print("Опубликовано в мостовой канал.")
        else:
            # Одиночный день: сохраняем картинку(и) под обложку RSS-поста в VK.
            os.makedirs(IMAGES_DIR, exist_ok=True)
            for local_path in local_paths:
                ext = os.path.splitext(local_path)[1] or ".jpg"
                image_id = f"{uuid.uuid4().hex}{ext}"
                with open(local_path, "rb") as src, open(
                    os.path.join(IMAGES_DIR, image_id), "wb"
                ) as dst:
                    dst.write(src.read())
                image_ids.append(image_id)

        yandex_paths_to_move = [(by_name[n][2], n) for n in chosen_files]

    # --- вне временной папки ---
    if is_carousel_day:
        state["carousels"] += 1
        print(
            f"Карусель засчитана: {state['carousels']}/{MAX_CAROUSELS_PER_MONTH} "
            f"за {state['month']}. Пост в VK опубликует Crosslybot из мостового канала."
        )
    else:
        items = load_items()
        items.insert(
            0,
            {
                "images": image_ids,
                "hook": hook,
                "full_text": full_text,
                "pubDate": format_datetime(now),
                "guid": uuid.uuid4().hex,
            },
        )
        save_items(items)
        build_feed_xml(items)
        print("Лента docs/feed.xml обновлена (одиночный пост для VK).")

    save_state(state)

    for yandex_path, name in yandex_paths_to_move:
        move_to_posted(yandex_path, name)
    print(f"Перенесено в {POSTED_DIR}: {[n for _, n in yandex_paths_to_move]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
