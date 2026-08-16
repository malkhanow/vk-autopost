#!/usr/bin/env python3
"""
Автопостинг фото из Яндекс.Диска в RSS-ленту, которую сам VK
периодически проверяет и публикует в группу (через встроенную функцию
VK "Импорт RSS" в настройках сообщества).

Логика одного запуска:
  1. Смотрим папку SOURCE_DIR на Яндекс.Диске.
  2. Берём самый старый (первый загруженный) файл.
  3. Скачиваем его во временный файл.
  4. Просим нейросеть написать описание на основе содержимого фото.
  5. Копируем фото в docs/images/ (эта папка публикуется через GitHub Pages).
  6. Добавляем новую запись в feed_items.json и полностью перегенерируем
     docs/feed.xml на основе последних записей.
  7. Переносим файл из SOURCE_DIR в POSTED_DIR на Диске.

Коммит и отправку изменений в репозиторий (git add/commit/push) делает
сам workflow (.github/workflows/daily-post.yml) следующим шагом, не
этот скрипт.

Если в SOURCE_DIR ничего нет — скрипт просто завершается без ошибки.

Нужные переменные окружения (задаются в GitHub Secrets):
  YANDEX_TOKEN        - OAuth-токен Яндекс.Диска
  OPENROUTER_API_KEY  - ключ OpenRouter
  SITE_BASE_URL       - базовый адрес GitHub Pages, например
                        https://malkhanow.github.io/vk-autopost
"""

import base64
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

# ---------- настройки ----------

SOURCE_DIR = "/to_post"
POSTED_DIR = "/posted"

ITEMS_FILE = "feed_items.json"
FEED_FILE = "docs/feed.xml"
IMAGES_DIR = "docs/images"
MAX_ITEMS_IN_FEED = 30  # сколько последних постов держим в ленте

OPENROUTER_MODEL = "openrouter/free"

CAPTION_PROMPT = (
    "Ты ведёшь соцсети компании, которая печатает кастомные принты на "
    "футболках и другой одежде. Посмотри на фото и напиши короткое, "
    "живое описание для поста: 1-3 предложения, без хэштегов, "
    "без markdown-разметки, на русском языке. Подчеркни то, что видно "
    "на конкретном фото (цвет, принт, ткань, деталь), не пиши общими "
    "фразами."
)

YANDEX_TOKEN = os.environ["YANDEX_TOKEN"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
SITE_BASE_URL = os.environ.get(
    "SITE_BASE_URL", "https://malkhanow.github.io/vk-autopost"
).rstrip("/")


# ---------- Яндекс.Диск ----------

def yandex_headers():
    return {"Authorization": f"OAuth {YANDEX_TOKEN}"}


def get_next_file():
    resp = requests.get(
        "https://cloud-api.yandex.net/v1/disk/resources",
        headers=yandex_headers(),
        params={
            "path": SOURCE_DIR,
            "limit": 100,
            "sort": "created",
            "fields": "_embedded.items.name,_embedded.items.path,_embedded.items.type",
        },
    )
    resp.raise_for_status()
    items = resp.json().get("_embedded", {}).get("items", [])
    files = [i for i in items if i.get("type") == "file"]
    return files[0] if files else None


def download_file(path, dest_path):
    resp = requests.get(
        "https://cloud-api.yandex.net/v1/disk/resources/download",
        headers=yandex_headers(),
        params={"path": path},
    )
    resp.raise_for_status()
    href = resp.json()["href"]
    with requests.get(href, stream=True) as r:
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
    )
    resp.raise_for_status()


# ---------- OpenRouter ----------

def generate_caption(image_path, max_retries=3):
    mime_type, _ = mimetypes.guess_type(image_path)
    mime_type = mime_type or "image/jpeg"
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": CAPTION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                    },
                ],
            }
        ],
    }

    last_error = None
    for attempt in range(1, max_retries + 1):
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=body,
        )
        if resp.status_code == 429:
            last_error = f"429 too many requests (попытка {attempt}/{max_retries})"
            print(last_error)
            time.sleep(15 * attempt)
            continue
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    raise RuntimeError(f"OpenRouter не ответил после {max_retries} попыток: {last_error}")


# ---------- работа с лентой ----------

def load_items():
    if not os.path.exists(ITEMS_FILE):
        return []
    with open(ITEMS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_items(items):
    with open(ITEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def build_feed_xml(items):
    now = format_datetime(datetime.now(timezone.utc))
    entries = []
    for item in items[:MAX_ITEMS_IN_FEED]:
        image_url = f"{SITE_BASE_URL}/images/{item['image']}"
        description_html = (
            f'&lt;img src="{escape(image_url)}"/&gt;'
            f"&lt;p&gt;{escape(item['caption'])}&lt;/p&gt;"
        )
        entries.append(
            "  <item>\n"
            f"    <title>{escape(item['caption'][:60])}</title>\n"
            f"    <link>{escape(SITE_BASE_URL)}/#{escape(item['guid'])}</link>\n"
            f"    <guid isPermaLink=\"false\">{escape(item['guid'])}</guid>\n"
            f"    <pubDate>{escape(item['pubDate'])}</pubDate>\n"
            f"    <description>{description_html}</description>\n"
            f"    <enclosure url=\"{escape(image_url)}\" type=\"image/jpeg\"/>\n"
            "  </item>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "<channel>\n"
        "  <title>Custom Studio - Печать на футболках</title>\n"
        f"  <link>{escape(SITE_BASE_URL)}</link>\n"
        "  <description>Автоматическая лента фото для импорта в VK</description>\n"
        f"  <lastBuildDate>{now}</lastBuildDate>\n"
        + "\n".join(entries) +
        "\n</channel>\n</rss>\n"
    )
    os.makedirs(os.path.dirname(FEED_FILE), exist_ok=True)
    with open(FEED_FILE, "w", encoding="utf-8") as f:
        f.write(xml)


# ---------- основной сценарий ----------

def main():
    next_file = get_next_file()
    if next_file is None:
        print("Нет новых фото в", SOURCE_DIR, "- сегодня постить нечего.")
        return

    filename = next_file["name"]
    src_path = next_file["path"]
    print(f"Беру файл: {src_path}")

    ext = os.path.splitext(filename)[1] or ".jpg"
    image_id = f"{uuid.uuid4().hex}{ext}"

    with tempfile.TemporaryDirectory() as tmp_dir:
        local_path = os.path.join(tmp_dir, filename)
        download_file(src_path, local_path)
        print("Файл скачан, запрашиваю подпись у нейросети...")

        caption = generate_caption(local_path)
        print("Подпись готова:", caption)

        os.makedirs(IMAGES_DIR, exist_ok=True)
        final_image_path = os.path.join(IMAGES_DIR, image_id)
        with open(local_path, "rb") as src, open(final_image_path, "wb") as dst:
            dst.write(src.read())

    items = load_items()
    items.insert(
        0,
        {
            "image": image_id,
            "caption": caption,
            "pubDate": format_datetime(datetime.now(timezone.utc)),
            "guid": uuid.uuid4().hex,
        },
    )
    save_items(items)
    build_feed_xml(items)
    print("Лента docs/feed.xml обновлена.")

    move_to_posted(src_path, filename)
    print(f"Файл перенесён в {POSTED_DIR}/{filename}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
