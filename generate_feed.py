#!/usr/bin/env python3
"""
Автопостинг товаров Custom Studio из Яндекс.Диска в Telegram.

Раз в день скрипт:
  1. Скачивает фото из папки /to_post на Яндекс.Диске (до 8 штук).
  2. Показывает их Mistral — определяет группы товаров.
  3. Берёт самую раннюю группу — это сегодняшний пост.
  4. Mistral пишет подпись (hook + body + cta).
  5. Публикует в Telegram-канал @customstudio_print.
  6. Crosslybot автоматически кросспостит в VK и Max.
  7. Переносит фото в /posted на Яндекс.Диске.

GitHub Secrets:
  YANDEX_TOKEN       — OAuth-токен Яндекс.Диска
  MISTRAL_API_KEY    — ключ Mistral AI
  TELEGRAM_BOT_TOKEN — токен бота от @BotFather
  TELEGRAM_CHANNEL   — @customstudio_print
"""

import base64
import io
import json
import mimetypes
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

import requests
from PIL import Image, ExifTags

# ---------- настройки ----------

SOURCE_DIR = "/Custom Studio Autopost/to_post"
POSTED_DIR = "/Custom Studio Autopost/posted"
STATE_FILE = "post_state.json"

MISTRAL_MODEL = "pixtral-12b-2409"
MAX_PHOTOS_PER_GROUPING_CALL = 8  # лимит Mistral на изображений за запрос
MAX_PHOTOS_PER_POST = 4           # максимум фото в одном посте

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
    "Тебе показаны фотографии из аккаунта компании по нанесению кастомных "
    "принтов на одежду (футболки, толстовки, худи, куртки, кепки и т.д.). "
    "На фото могут быть: готовые изделия с принтами, процесс печати, "
    "оборудование (термопресс, DTF-принтер). "
    "К каждому фото приложена подпись с именем файла и временем съёмки.\n\n"
    "Сгруппируй фото по принципу: одна группа = одно готовое изделие "
    "(разные ракурсы одной вещи). "
    "Фото только с оборудованием без изделия — помести каждое в отдельную "
    "группу из одного фото.\n"
    "Ориентируйся на визуальное сходство (цвет, принт, тип изделия), "
    "время съёмки используй как дополнительную подсказку.\n\n"
    f"В ОДНОЙ группе не может быть больше {MAX_PHOTOS_PER_POST} фото. "
    "Не объединяй разные изделия — лучше несколько маленьких групп, "
    "чем одна большая ошибочная.\n\n"
    "Ответь СТРОГО в формате JSON-ОБЪЕКТА с одним ключом "
    '"groups", без markdown:\n'
    '{"groups": [{"files": ["имя1.jpg", "имя2.jpg"]}, {"files": ["имя3.jpg"]}]}\n\n'
    "Каждый файл должен встретиться ровно в одной группе."
)

CAPTION_PROMPT = (
    "Ты ведёшь соцсети Custom Studio — компании, которая наносит кастомные "
    "принты на одежду методом DTF-печати. Ассортимент: футболки, толстовки, "
    "свитшоты, худи, куртки, штаны, кепки, майки и другие изделия.\n\n"
    "Посмотри на фото (может быть несколько ракурсов одного изделия) и напиши "
    "три части поста в формате JSON, БЕЗ markdown-разметки, на русском:\n\n"
    '{"hook": "...", "body": "...", "cta": "..."}\n\n'
    "hook — короткий цепляющий заголовок с эмодзи-кружком в начале "
    "(🖤/🔵/💙/🤍 и т.п.). Называй конкретно изделие и принт, "
    "например «🖤 Аниме-герой на чёрной оверсайз-толстовке» или "
    "«🤍 Семейный портрет на белой футболке».\n"
    "body — 1-3 предложения: опиши само ИЗДЕЛИЕ (тип, цвет, крой) и ПРИНТ "
    "(что изображено, стиль, детали). Пиши только про то, что видно на фото.\n"
    "cta — одно предложение: призыв прислать свой дизайн/фото/персонажа, "
    "чтобы мы перенесли его на такое же изделие.\n\n"
    "ВАЖНО:\n"
    "— Если на фото видно оборудование (термопресс, принтер, станок) — "
    "ИГНОРИРУЙ его полностью. Пиши только про изделие с принтом.\n"
    "— Если изделия на фото нет совсем (только оборудование или фон) — "
    "верни hook='Кастомная печать на заказ 🎨', "
    "body='Наносим любые принты на одежду — быстро и качественно.', "
    "cta='Пришли свой дизайн — обсудим и сделаем.'\n"
    "— НИКОГДА не указывай цену и не выдумывай её."
)

YANDEX_TOKEN = os.environ["YANDEX_TOKEN"]
MISTRAL_API_KEY = os.environ["MISTRAL_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL = os.environ["TELEGRAM_CHANNEL"]


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
    try:
        img = Image.open(local_path)
        exif = img.getexif()
        if exif:
            for tag_id, value in exif.items():
                tag = ExifTags.TAGS.get(tag_id)
                if tag in ("DateTimeOriginal", "DateTime"):
                    return value
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
    if isinstance(result, list):
        return result
    return result.get("groups", [])


def generate_post_text(local_paths):
    entries = [("", path) for path in local_paths]
    result = call_mistral_vision(CAPTION_PROMPT, entries)
    return result["hook"], result["body"], result["cta"]


def assemble_full_text(hook, body, cta):
    return f"{hook}\n\n{body}\n\n{BENEFITS_BLOCK}\n\n{cta}\n\n{CLOSING_BLOCK}"


# ---------- Telegram ----------

def post_to_telegram(channel, local_paths, text):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    if len(local_paths) == 1:
        with open(local_paths[0], "rb") as f:
            resp = requests.post(
                f"{base_url}/sendPhoto",
                data={"chat_id": channel, "caption": text},
                files={"photo": f},
                timeout=60,
            )
        resp.raise_for_status()
    else:
        media = []
        files = {}
        for idx, path in enumerate(local_paths):
            key = f"photo{idx}"
            media.append({
                "type": "photo",
                "media": f"attach://{key}",
                **({"caption": text} if idx == 0 else {}),
            })
            files[key] = open(path, "rb")
        try:
            resp = requests.post(
                f"{base_url}/sendMediaGroup",
                data={"chat_id": channel, "media": json.dumps(media)},
                files=files,
                timeout=60,
            )
            resp.raise_for_status()
        finally:
            for fobj in files.values():
                fobj.close()


# ---------- состояние ----------

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------- основной сценарий ----------

def main():
    files = list_files_in_source()
    if not files:
        print("Нет новых фото в", SOURCE_DIR, "- сегодня постить нечего.")
        return

    files = files[:MAX_PHOTOS_PER_GROUPING_CALL]

    with tempfile.TemporaryDirectory() as tmp_dir:
        local_files = []
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

        capped_groups = []
        for g in groups:
            valid_files = [n for n in g.get("files", []) if n in by_name]
            if not valid_files:
                continue
            if len(valid_files) > MAX_PHOTOS_PER_POST:
                valid_files = sorted(
                    valid_files, key=lambda n: by_name[n][1]
                )[:MAX_PHOTOS_PER_POST]
            capped_groups.append({"files": valid_files})
        groups = capped_groups

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

        print("Публикую в Telegram-канал...")
        post_to_telegram(TELEGRAM_CHANNEL, local_paths, full_text)
        print("Опубликовано. Crosslybot автоматически кросспостит в VK и Max.")

        yandex_paths_to_move = [(by_name[n][2], n) for n in chosen_files]

    for yandex_path, name in yandex_paths_to_move:
        move_to_posted(yandex_path, name)
    print(f"Перенесено в {POSTED_DIR}: {[n for _, n in yandex_paths_to_move]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
