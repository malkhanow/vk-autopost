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
  4. Если фото есть в {yandex_folder}/to_post — берёт следующее по
     очереди и переносит в {yandex_folder}/posted после публикации.
  5. RouterAI (Gemini Flash Lite) пишет текст поста по промпту рубрики,
     с учётом style_prompt, тона и запретов клиента.
  6. Публикует в Telegram-канал клиента. Канал берётся из поля
     tg_channel в самом конфиге клиента (clients/{id}.json).
     Токен бота общий (TELEGRAM_BOT_TOKEN) — один бот, добавленный
     админом во все каналы клиентов.

Ручной тестовый запуск (кнопка "Сохранить и запустить" в CRM) передаёт
input client_id через workflow_dispatch — тогда день недели и слот не
проверяются, публикуется сразу следующая по очереди рубрика этого
клиента. Так проверяется, что канал и конфиг настроены правильно.

GitHub Secrets:
  ROUTERAI_KEY        — тот же ключ, что в Apps Script Script Properties
  TELEGRAM_BOT_TOKEN  — общий бот, admin во всех каналах клиентов
  YANDEX_TOKEN        — OAuth-токен Яндекс.Диска (общий, папки разные)
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

SLOT = os.environ.get("SLOT", "")               # morning / midday / evening
TEST_CLIENT_ID = os.environ.get("TEST_CLIENT_ID", "").strip()

DOW_MAP = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
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


def next_photo_for_client(yandex_folder):
    """Следующее неиспользованное фото клиента, или (None, None), если фото нет."""
    to_post = f"{yandex_folder}/to_post"
    posted = f"{yandex_folder}/posted"
    files = list_folder(to_post)
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
        print(f"Опубликовано в {channel}.")
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
        today_abbr = list(DOW_MAP.keys())[
            list(DOW_MAP.values()).index(
                datetime.now(ZoneInfo("Europe/Moscow")).weekday()
            )
        ]
    except Exception:
        today_abbr = list(DOW_MAP.keys())[datetime.utcnow().weekday()]

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
        rubric = pick_rubric(client, today_abbr, state, test_mode)
        if not rubric:
            print(f"{cid}: сегодня рубрик нет, пропускаю.")
            continue

        channel = client.get("tg_channel")
        if not channel:
            print(f"{cid}: в конфиге клиента не задан tg_channel, пропускаю.")
            continue

        try:
            text = build_post(client, rubric)
        except Exception as e:
            print(f"{cid}: ошибка генерации текста — {e}")
            continue

        photo_path, photo_meta = next_photo_for_client(client.get("yandex_folder", ""))

        print(f"{cid}: публикую рубрику «{rubric.get('name')}» в {channel}")
        try:
            post_to_telegram(channel, text, photo_path)
            posted_any = True
            if photo_meta:
                src_path, filename, posted_dir = photo_meta
                move_to_posted(src_path, filename, posted_dir)
        except Exception as e:
            print(f"{cid}: ошибка публикации — {e}")

    save_state(state)
    if not posted_any:
        print("Ни одного поста не ушло за этот запуск.")


if __name__ == "__main__":
    main()
