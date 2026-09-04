#!/usr/bin/env python3
"""
Автопостинг для клиентов SMM-подписки SAS (Smart Automation System).
Один скрипт обслуживает всех клиентов: конфиг на клиента лежит в
clients/{client_id}.json и собирается CRM из брифа.

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

Фото к праздникам берутся из своей библиотеки на Яндекс.Диске
(HOLIDAYS_FOLDER, одна папка на праздник), а не с фотостока: сток по
запросу выдавал случайные кадры и в канал клиента уходил мусор.
"""

import base64
import glob
import json
import mimetypes
import os
import re
import sys
import tempfile
import time

import requests

# ---------- настройки ----------

CLIENTS_DIR = "clients"
STATE_FILE = "clients_state.json"

ROUTERAI_URL = "https://routerai.ru/api/v1/chat/completions"
ROUTERAI_MODEL = os.environ.get("ROUTERAI_MODEL", "google/gemini-3.1-flash-lite")
# Глубина «размышления» модели. Для текстов с большим числом одновременных
# ограничений (рубрика + стиль + формат + запреты) low слишком часто роняет
# часть инструкций — по умолчанию берём medium. Переопределяется секретом.
ROUTERAI_REASONING = os.environ.get("ROUTERAI_REASONING", "medium")

ROUTERAI_KEY = os.environ["ROUTERAI_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
YANDEX_TOKEN = os.environ.get("YANDEX_TOKEN", "")
# Общая библиотека праздничных фото на Яндекс.Диске: одна папка на праздник,
# имя папки = ключ праздника из BASE_HOLIDAYS (для нишевых — extra-ММ-ДД).
# Фото кладутся руками, не расходуются и перебираются по кругу.
HOLIDAYS_FOLDER = os.environ.get("HOLIDAYS_FOLDER", "Autopost WORK/holidays")
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
]

# что публикуем: за 7 дней и за 1 день — вечером, в сам день — утром
HOLIDAY_KINDS = {
    "before7": "анонс за неделю",
    "before1": "анонс за день",
    "day": "поздравление",
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


def list_folder(path, quiet=False):
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
        # quiet=True — путь необязательный (личная папка клиента, подпапка
        # праздника): её отсутствие это норма, а не ошибка, и засорять лог
        # четырьмя «404» на каждый праздничный пост не нужно.
        if not (quiet and resp.status_code == 404):
            print(f"Яндекс Диск {resp.status_code} для пути '{path}': {resp.text[:200]}")
        return []
    items = resp.json().get("_embedded", {}).get("items", [])
    files = [i for i in items if i.get("type") == "file"]
    ok = [i for i in files
          if os.path.splitext(i.get("name", ""))[1].lower() in ALLOWED_IMAGE_EXTS]
    # Публикуем только изображения. Всё остальное — видео, архивы, документы —
    # игнорируется, но раньше исчезало молча и копилось в папке незаметно.
    # Теперь лишние файлы видны в логе запуска.
    if len(ok) < len(files) and not quiet:
        skipped = [i.get("name", "") for i in files if i not in ok]
        print(f"В '{path}' пропущено файлов (не изображения): {len(skipped)} "
              f"— {', '.join(skipped[:5])}")
    return ok


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


def rubric_uses_vision(rubric_name_or_dict):
    """
    Нужен ли vision-анализ для этой рубрики.

    Принимает строку (название) или словарь рубрики из конфига клиента.

    Vision включается когда:
    - kind == "photo_case" или kind == "results" (before_after) — явный сигнал
      что в папке реальные фото/карточки для анализа;
    - folder == "rubrics/reviews" — скриншоты отзывов;
    - folder is None и в названии есть «фото» — старый фолбэк для совместимости.

    tips, faq, ideas — заглушки без анализа, vision не нужен.
    """
    if isinstance(rubric_name_or_dict, dict):
        kind = str(rubric_name_or_dict.get("kind") or "").strip().lower()
        if kind in ("photo_case", "before_after"):
            return True
        name = str(rubric_name_or_dict.get("name") or "")
    else:
        name = str(rubric_name_or_dict or "")

    folder = rubric_folder(name)
    if folder == "rubrics/reviews":
        return True
    if folder is None:
        return "фото" in name.lower()
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

# Последний ответ модели пришёл обрезанным по лимиту токенов. Нужен вызовам,
# где ответ — не проза, а список: у обрезанного списка последний элемент
# всегда битый, и его надо выбросить.
LAST_ANSWER_TRUNCATED = False

# «Размышления» модели списываются из того же max_tokens, что и сам ответ.
# При reasoning_effort выше low короткий лимит съедается рассуждением, и до
# текста дело не доходит: именно так хештеги обрезало на полуслове. Ниже
# этого потолка лимит не опускаем.
REASONING_TOKEN_FLOOR = 900


# Знаки, на которые имеет право заканчиваться готовый пост.
TERMINAL_CHARS = ".!?…"


def _meaningful_tail(text):
    """
    Последний значащий символ текста. Эмодзи, кавычки и скобки в самом хвосте
    отбрасываются: пост «Отличного дня! 🎉» закончен, хотя кончается смайлом.
    """
    s = (text or "").rstrip()
    while s and s[-1] not in TERMINAL_CHARS and not s[-1].isalnum():
        s = s[:-1].rstrip()
    return s[-1] if s else ""


def text_is_complete(text):
    """
    Похож ли ответ на законченную прозу.

    Единственный надёжный признак обрыва, который виден без провайдера:
    текст кончается на полуслове, а не на точке. Реальный случай — у клиента
    вышел пост, оборванный на «Я часто замечаю, как меняется настроение
    человека», потому что модель вернула finish_reason=stop и обрезка,
    привязанная к finish_reason=length, просто не сработала.
    """
    tail = _meaningful_tail(text)
    # именно tail != "": пустая строка входит в любую строку, и без этой
    # проверки пустой ответ модели считался бы законченным постом
    return bool(tail) and tail in TERMINAL_CHARS


def ai_text(messages, max_tokens=900, temperature=0.8, reasoning=None, prose=True):
    """
    prose=True  — ответ должен быть законченным текстом; незаконченный
                  считается обрывом и перезапрашивается.
    prose=False — ответ не проза (список тегов и т.п.), проверять хвост
                  на точку бессмысленно.
    """
    global LAST_ANSWER_TRUNCATED
    LAST_ANSWER_TRUNCATED = False
    effort = str(reasoning or ROUTERAI_REASONING or "low").strip().lower()
    if effort not in ("low", "none", "minimal"):
        max_tokens = max(max_tokens, REASONING_TOKEN_FLOOR)
    payload = {
        "model": ROUTERAI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "reasoning_effort": effort,
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
        try:
            data = resp.json() or {}
        except ValueError:
            raise RuntimeError(f"RouterAI вернул не JSON: {resp.text[:300]}")
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"RouterAI вернул ответ без текста: {resp.text[:300]}")
        content = (choices[0].get("message") or {}).get("content")
        if not content:
            raise RuntimeError("RouterAI вернул пустой текст")

        finish = str(choices[0].get("finish_reason") or "stop")
        usage = data.get("usage") or {}
        # Расход токенов в логе — чтобы следующий обрыв разбирался по Actions,
        # а не по скриншоту из канала клиента.
        print(
            "RouterAI: finish_reason={} | токены prompt={} completion={} "
            "| лимит {}".format(
                finish,
                usage.get("prompt_tokens", "?"),
                usage.get("completion_tokens", "?"),
                payload["max_tokens"],
            )
        )

        clean = strip_model_noise(content)

        # Обрыв виден двумя способами, и полагаться только на первый нельзя:
        #   1) провайдер честно говорит finish_reason=length;
        #   2) finish_reason=stop, но текст кончается на полуслове.
        # Второй случай — реальный, именно так в канал ушёл обрубленный пост.
        incomplete = (finish == "length") or (prose and not text_is_complete(clean))
        if incomplete:
            if attempt < 2:
                grown = int(payload["max_tokens"] * 1.6)
                print(
                    f"RouterAI: ответ неполный (finish_reason={finish}), "
                    f"повтор с лимитом {grown}..."
                )
                payload["max_tokens"] = grown
                last_error = "неполный ответ"
                continue
            print("RouterAI: ответ по-прежнему неполный — обрезаю по последнему "
                  "законченному предложению.")
            LAST_ANSWER_TRUNCATED = True
            return trim_to_sentence(clean)
        return clean
    raise RuntimeError(f"RouterAI не отвечает после трёх попыток ({last_error})")


def trim_to_sentence(text):
    """Отрезает оборванный хвост до последнего законченного предложения."""
    text = (text or "").rstrip()
    if not text or text_is_complete(text):
        return text
    cut = max(text.rfind("."), text.rfind("!"), text.rfind("?"), text.rfind("…"))
    # если знак конца предложения нашёлся не в самом начале — режем по нему
    if cut > len(text) * 0.4:
        return text[: cut + 1].rstrip()
    return text


# Служебные пометки, которыми модель сопровождает ответ. Стоят всегда
# в конце — после готового текста, а не внутри него.
MODEL_NOISE_MARKERS = re.compile(
    r"^.*\b("
    r"Draft \d+|Checking Constraints?|Word count check|Final (?:answer|version)|"
    r"Проверка длины|Итоговая проверка|Количество слов|Подсчёт слов"
    r")\b.*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_model_noise(text):
    """
    Убирает обёртку и служебные пометки модели.

    Пометки режутся ОТ ПЕРВОЙ И ДО КОНЦА текста. Раньше вырезалась только сама
    строка — и если модель обрывала мысль, а следом ставила «Word count check»,
    от вырезанной строки оставалась дырка, а недописанное предложение над ней
    превращалось в отдельный абзац и уезжало в канал.
    """
    s = str(text or "").strip()
    s = re.sub(r"^```[a-zA-Z]*[ \t]*\n?", "", s)
    s = re.sub(r"\n?```[ \t]*$", "", s)
    m = MODEL_NOISE_MARKERS.search(s)
    if m:
        s = s[: m.start()]
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


def append_cta(text, client):
    """
    Дописывает призыв к действию из карточки клиента дословно.

    Раньше CTA уходил в промпт строкой «Призыв к действию в конце» — модель
    считала это темой для пересказа и переписывала его своими словами,
    теряя телефон и ник. Заглушка клиента меняться не должна.
    """
    cta = (client.get("cta") or "").strip()
    if not cta:
        return text
    body = (text or "").rstrip()
    # Если модель всё же дописала CTA сама — не дублируем.
    tail = body[-len(cta) - 40:].lower() if body else ""
    first_line = cta.splitlines()[0].strip().lower()
    if first_line and first_line in tail:
        return body
    return body + "\n\n" + cta


# Потолок числа тегов под постом. Раньше стоял 10, но из-за обрезанного
# ответа модели фактически выходило 1–3 — и лента к этому привыкла. Десять
# тегов под каждым постом читаются как спам, поэтому фиксируем 5.
HASHTAGS_MAX = 5


def normalize_tag(raw):
    """Тег без решётки, пробелов и знаков препинания: #Страхование."""
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё_]", "", (raw or "").replace("#", ""))


def build_hashtags_ai(client, post_text, need):
    """
    Подбирает теги под конкретный пост. Постоянный набор из конфига под каждой
    публикацией выглядит спамом и не попадает в тему, поэтому теги пишутся по
    готовому тексту — уже после генерации и подстановки CTA.
    """
    if need <= 0:
        return []

    lines = [
        f"Бизнес: {client.get('business', '')}",
        f"Город: {client.get('city', '')}",
        "Текст поста:",
        (post_text or "")[:1500],
    ]
    forbidden = client.get("forbidden") or []
    if forbidden:
        lines.append("Не делай теги по темам: " + "; ".join(forbidden))

    system = (
        "Ты подбираешь хештеги для постов малого бизнеса в соцсетях. "
        "В ответе — только теги через запятую, без решёток, без пояснений "
        "и без нумерации."
    )
    user = "\n".join(lines) + (
        f"\n\nПодбери до {need} хештегов на русском языке, относящихся именно "
        "к этому посту. Каждый тег — одно или два слова слитно, без пробелов "
        "и знаков препинания. Смешивай теги по теме поста, по нише бизнеса "
        "и по городу. Ответь одной строкой через запятую."
    )

    # Подбор тегов — механическая задача, «размышление» здесь только съедает
    # лимит токенов. Явно ставим low и просторный max_tokens: обрезанный
    # ответ означает битый последний тег (реальный случай — «#недвижимостьсп»).
    raw = ai_text(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=400,
        temperature=0.5,
        reasoning="low",
        prose=False,
    )
    truncated = LAST_ANSWER_TRUNCATED

    parts = re.split(r"[,\n]+", raw or "")
    # Ответ оборвался по лимиту — последний фрагмент почти наверняка
    # недописанное слово. Выбрасываем его, а не публикуем обрубок.
    if truncated and len(parts) > 1 and not re.search(r"[,\n]\s*$", raw or ""):
        dropped = parts.pop().strip()
        if dropped:
            print(f"Ответ с тегами оборван — отбрасываю неполный тег «{dropped}».")

    out, seen = [], set()
    for part in parts:
        tag = normalize_tag(part)
        # однобуквенные обрывки и слишком длинные склейки в теги не годятся
        if len(tag) < 3 or len(tag) > 30:
            continue
        if tag.lower() in seen:
            continue
        seen.add(tag.lower())
        out.append(tag)
        if len(out) >= need:
            break
    return out


def append_hashtags(text, client, holiday=None):
    """
    Дописывает до HASHTAGS_MAX тегов: сперва постоянные из поля hashtags
    конфига, остальное добирает подбором под текст поста.

    У памятных дат тегов нет совсем — «#страхование» под постом о Дне Победы
    выглядит как реклама на памятной дате.
    """
    if holiday and holiday.get("solemn"):
        return text

    tags, seen = [], set()
    for part in re.split(r"[,\s]+", client.get("hashtags") or ""):
        tag = normalize_tag(part)
        if not tag or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        tags.append(tag)

    need = HASHTAGS_MAX - len(tags)
    if need > 0:
        try:
            for tag in build_hashtags_ai(client, text, need):
                if tag.lower() in seen:
                    continue
                seen.add(tag.lower())
                tags.append(tag)
        except Exception as e:
            # теги — украшение, из-за них пост срываться не должен
            print(f"Теги подобрать не удалось ({e}), публикую без них.")

    if not tags:
        return text
    return text.rstrip() + "\n\n" + " ".join("#" + t for t in tags[:HASHTAGS_MAX])


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


# Зачины, с которых пост начинаться не должен. Список продублирован в
# crm/Code.gs (FORBIDDEN_OPENERS) — правишь здесь, правь и там, иначе
# примеры для клиента и боевые посты разойдутся по манере подачи.
FORBIDDEN_OPENERS = [
    "знаете", "а знаете", "вы знаете", "знаете ли вы",
    "часто слышу", "часто слышу вопрос", "часто мне задают", "мне часто задают",
    "часто меня спрашивают", "меня часто спрашивают", "часто спрашивают",
    "ко мне часто обращаются", "один из самых частых вопросов", "самый частый вопрос",
    "интересный факт", "факт дня", "сегодня хочу", "хочу поделиться",
    "недавно я", "однажды я", "представьте",
]


def has_forbidden_opener(text):
    """Начинается ли пост с дежурной формулы."""
    head = re.sub(r"^[^а-яёa-z]+", "", (text or "").lower())[:60]
    return any(head.startswith(f) for f in FORBIDDEN_OPENERS)


def openers_rule():
    quoted = ", ".join(f"«{f}»" for f in FORBIDDEN_OPENERS)
    return (
        f"ЗАПРЕЩЕНО начинать пост со слов или фраз: {quoted}. "
        "Первое предложение задаётся отдельной инструкцией «Заход» ниже — "
        "следуй именно ей. "
    )


# ---------- рубрика: тип, формат, тема ----------
#
# Раньше формат поста выбирался случайно из общего списка и не знал, какая
# сейчас рубрика. Инструкция формата стояла в промпте последней и по факту
# перебивала инструкцию рубрики: в рубрике «Разбор ситуации (FAQ)» модели
# выпадало «напиши историю из практики» — и выходила история, а не разбор
# вопроса. Теперь формат выбирается ИЗ НАБОРА своей рубрики, и каждый
# вариант набора решает задачу этой рубрики, только разной подачей.


def rubric_kind(rubric):
    """
    Тип рубрики. Принимает либо словарь рубрики, либо просто её название.

    Приоритет: явное поле "kind" в конфиге клиента (его проставляет CRM при
    создании рубрики) -> угадывание по ключевым словам названия -> "default".
    Названия рубрик клиент правит руками и в каждой нише они свои, поэтому
    угадывание — запасной путь, а не основной.
    """
    if isinstance(rubric, dict):
        explicit = str(rubric.get("kind") or "").strip().lower()
        if explicit in RUBRIC_ANCHOR:
            return explicit
        name = str(rubric.get("name") or "")
    else:
        name = str(rubric or "")

    name = name.lower()
    for kind, words in RUBRIC_KEYWORDS:
        if any(w in name for w in words):
            return kind
    return "default"


# Порядок важен: проверяется сверху вниз, первое совпадение выигрывает.
# «Фото работ» стоит выше «работы», чтобы не перехватывалось рубрикой услуг.
RUBRIC_KEYWORDS = [
    ("photo_case",   ["фото работ", "фото работы", "наши работы", "фотоотч"]),
    ("faq",          ["вопрос", "чзв", "faq", "разбор", "ответы на"]),
    ("docs",         ["документ", "объект", "юрид", "договор", "справк"]),
    ("results",      ["результат", "отзыв", "кейс", "клиент говор", "благодар"]),
    ("before_after", ["до и после", "до/после", "преображ", "трансформац"]),
    ("promo",        ["акци", "скидк", "спецпредлож", "предложение недел", "промо"]),
    ("news",         ["новинк", "новост", "поступлен", "ассортимент", "что нового"]),
    ("howto",        ["уход", "инструкц", "как ухаживать", "как выбрать", "как понять", "памятк", "лайфхак"]),
    ("team",         ["мастер", "команд", "о нас", "сотрудник", "знакомств", "специалист"]),
    ("service",      ["услуг", "процедур", "прайс", "что мы делаем", "работ"]),
    ("inspiration",  ["вдохнов", "уют", "иде", "атмосфер", "настроен", "эстетик"]),
    ("observation",  ["наблюден", "практик", "изнутри", "будни", "закулис"]),
    ("tips",         ["совет", "польз", "рекоменд"]),
]


# Что пост ОБЯЗАН сделать, чтобы считаться постом этой рубрики.
# Идёт в промпт отдельной строкой с пометкой «ГЛАВНОЕ ТРЕБОВАНИЕ».
RUBRIC_ANCHOR = {
    "faq": (
        "Это рубрика вопросов и ответов. Пост ОБЯЗАН отвечать на один "
        "конкретный вопрос читателя. Сам вопрос должен быть дословно "
        "сформулирован в первых двух предложениях — так, чтобы читатель "
        "узнал в нём свой. Дальше — ответ по существу. "
        "Пост-история, пост-наблюдение и пост-размышление здесь НЕ подходят."
    ),
    "docs": (
        "Это рубрика про объекты и документы. Пост ОБЯЗАН разбирать один "
        "конкретный документ, справку, выписку или этап оформления: что это, "
        "зачем нужно, что в нём смотреть и что бывает, если этого не сделать. "
        "Общие рассуждения о рынке здесь НЕ подходят."
    ),
    "results": (
        "Это рубрика результатов. Пост ОБЯЗАН показать один завершённый "
        "рабочий кейс по схеме: задача клиента — что было сделано — чем "
        "закончилось. Без сумм, сроков, процентов и имён."
    ),
    "inspiration": (
        "Это рубрика про эмоцию и настроение. Пост ОБЯЗАН опираться на "
        "ощущение, впечатление или внутреннее состояние, связанное с темой "
        "рубрики. Инструкций, чек-листов и разборов документов здесь быть "
        "не должно."
    ),
    "photo_case": (
        "Это рубрика «Фото работ». Пост ОБЯЗАН разбирать одну конкретную "
        "задачу из практики: что было исходно, какой подход выбран и почему, "
        "к чему это привело. Без сумм, сроков, гарантий и обещаний "
        "конкретного результата."
    ),
    "observation": (
        "Это рубрика наблюдений. Пост ОБЯЗАН содержать закономерность или "
        "вывод, который читатель сам бы не заметил, и практическую пользу "
        "из него. Не пересказ одного случая, а именно наблюдение."
    ),
    "tips": (
        "Это рубрика советов. Пост ОБЯЗАН давать читателю понятное "
        "действие, которое он может выполнить сам."
    ),
    "before_after": (
        "Это рубрика «до и после». Пост ОБЯЗАН показать контраст: что было, "
        "что стало и за счёт чего. Без сумм и обещаний гарантированного "
        "результата."
    ),
    "promo": (
        "Это рубрика предложений. Пост ОБЯЗАН содержать одно понятное "
        "предложение и понятное действие для читателя. Сначала польза для "
        "клиента, потом само предложение — не наоборот."
    ),
    "news": (
        "Это рубрика новинок. Пост ОБЯЗАН представить что-то одно новое и "
        "объяснить, чем оно полезно клиенту, а не просто перечислить свойства."
    ),
    "team": (
        "Это рубрика про людей. Пост ОБЯЗАН показать конкретного человека или "
        "команду через дело: что делает, чем занимается, почему делает так. "
        "Без анкетных перечислений и без пафоса про «профессионалов своего дела»."
    ),
    "howto": (
        "Это обучающая рубрика. Пост ОБЯЗАН объяснить, как что-то делать "
        "правильно: последовательность, условия, типичная ошибка. Читатель "
        "должен уйти с понятным действием."
    ),
    "service": (
        "Это рубрика про услуги. Пост ОБЯЗАН раскрыть одну услугу или этап "
        "работы: что входит, как проходит, какой результат получает клиент. "
        "Не перечисляй весь прайс в одном посте."
    ),
    "default": (
        "Пост ОБЯЗАН целиком выполнить инструкцию рубрики, приведённую ниже. "
        "Если в инструкции перечислено несколько тем — выбери ОДНУ и раскрой "
        "её до конца, не пытайся охватить все. Формат подачи — только способ "
        "изложения, он никогда не отменяет задачу рубрики."
    ),
}


# Форматы подачи внутри рубрики. scene=True — единственный вариант, где
# разрешена сцена «я пришла / ко мне обратились». Один такой на набор:
# при ротации по кругу сцены получаются примерно в каждом пятом посте,
# а не в каждом, как было раньше.
RUBRIC_FORMATS = {
    "faq": [
        {"scene": False, "text": "Формат «вопрос — ответ». Первая строка — сам вопрос читателя, одним предложением. Дальше 2–3 абзаца ответа по существу. 100–140 слов."},
        {"scene": False, "text": "Формат «развенчание мифа». Начни с распространённого мнения по теме, затем объясни, как на самом деле и почему. 90–130 слов."},
        {"scene": False, "text": "Формат «пошаговый ответ». Вопрос первой строкой, затем 3–4 коротких пункта, что делать и в каком порядке. 90–130 слов."},
        {"scene": False, "text": "Формат «два варианта». Вопрос первой строкой, затем сравни два способа его решить и объясни, какой я советую и почему. 100–140 слов."},
        {"scene": True, "text": "Формат «вопрос с консультации». Первая строка — вопрос, который прозвучал на встрече, дословно. Дальше — как я на него отвечаю. 90–130 слов."},
    ],
    "docs": [
        {"scene": False, "text": "Формат «разбор одного документа». Назови документ в первой строке, затем: что это, кто выдаёт, что в нём смотреть в первую очередь. 100–140 слов."},
        {"scene": False, "text": "Формат «чек-лист». Короткое вступление и 4–5 пунктов, что проверить. Каждый пункт — с пояснением в одну строку. 100–140 слов."},
        {"scene": False, "text": "Формат «расшифровка термина». Возьми одно официальное слово из документов и объясни его человеческим языком. 80–120 слов."},
        {"scene": False, "text": "Формат «типичная ошибка». Назови ошибку, которую совершают с этим документом, объясни последствие и как её не допустить. 90–130 слов."},
        {"scene": False, "text": "Формат «порядок действий». Опиши, в какой последовательности собираются документы на этом этапе и почему порядок важен. 100–140 слов."},
    ],
    "results": [
        {"scene": False, "text": "Формат «задача — решение — итог». Три коротких абзаца, по одному на каждую часть. 90–130 слов."},
        {"scene": False, "text": "Формат «что стояло на кону». Начни с того, что было важно для клиента, затем — что было сделано, чтобы это сохранить. 90–130 слов."},
        {"scene": False, "text": "Формат «нетипичный запрос». Опиши необычную задачу, с которой обратился клиент, и как она была решена. 90–130 слов."},
        {"scene": False, "text": "Формат «что удалось предотвратить». Расскажи, какую проблему удалось заметить заранее и чем это помогло. 90–130 слов."},
        {"scene": True, "text": "Формат «короткий кейс из практики». Одна работа от обращения до завершения, спокойно и по фактам. 90–130 слов."},
    ],
    "inspiration": [
        {"scene": False, "text": "Формат «атмосфера». Начни с описания ощущения или настроения, связанного с темой рубрики, затем свяжи это с выбором клиента. 80–120 слов."},
        {"scene": False, "text": "Формат «маленькая деталь». Возьми одну небольшую деталь из темы рубрики и объясни, как она меняет общее впечатление. 80–120 слов."},
        {"scene": False, "text": "Формат «размышление». Спокойная мысль по теме рубрики, без инструкций и списков. 70–110 слов."},
        {"scene": False, "text": "Формат «сравнение настроений». Покажи контраст: как ощущается ситуация до и после того, как задача решена. 80–120 слов."},
        {"scene": True, "text": "Формат «момент из работы». Опиши момент, когда клиент понял, что нашёл нужное решение. Тепло, без пафоса. 80–120 слов."},
    ],
    "observation": [
        {"scene": False, "text": "Формат «закономерность». Начни с вывода, сделанного за годы работы, затем объясни, из чего он складывается. 90–130 слов."},
        {"scene": False, "text": "Формат «на что я смотрю». Перечисли 3–4 вещи, на которые обращаю внимание, и объясни зачем. 90–130 слов."},
        {"scene": False, "text": "Формат «неочевидное сравнение». Сопоставь два варианта или два подхода и покажи разницу, которую обычно не замечают. 90–130 слов."},
        {"scene": False, "text": "Формат «как это работает». Объясни внутреннюю механику процесса, которую клиент со стороны не видит. 90–130 слов."},
        {"scene": True, "text": "Формат «наблюдение из практики». Одна конкретная деталь, замеченная в работе, и общий вывод из неё. 90–130 слов."},
    ],
    "tips": [
        {"scene": False, "text": "Формат «один совет». Конкретное действие, зачем оно и что даст. 70–110 слов."},
        {"scene": False, "text": "Формат «три пункта». Короткое вступление и три практических пункта. 90–130 слов."},
        {"scene": False, "text": "Формат «частая ошибка». Ошибка, её последствие и как сделать правильно. 80–120 слов."},
        {"scene": False, "text": "Формат «что сделать заранее». Что стоит подготовить до начала процесса. 80–120 слов."},
        {"scene": True, "text": "Формат «совет из практики». Случай, который научил меня этому правилу, и сам совет. 90–130 слов."},
    ],
    "before_after": [
        {"scene": False, "text": "Формат «что было и что стало». Два абзаца контраста и один — за счёт чего получилось. 90–130 слов."},
        {"scene": False, "text": "Формат «главная деталь». Назови одно изменение, которое дало основной эффект, и объясни почему. 80–120 слов."},
        {"scene": False, "text": "Формат «запрос и решение». Что просил клиент и как это удалось сделать. 90–130 слов."},
        {"scene": False, "text": "Формат «что учитывали». Перечисли 3–4 вещи, которые пришлось учесть в этой работе. 90–130 слов."},
        {"scene": True, "text": "Формат «как это было». Короткий рассказ о ходе работы от начала до результата. 90–130 слов."},
    ],
    "promo": [
        {"scene": False, "text": "Формат «сначала польза». Абзац о ситуации, в которой это пригодится, затем само предложение и что сделать читателю. 80–120 слов."},
        {"scene": False, "text": "Формат «для кого». Опиши, кому именно подойдёт это предложение, затем условия простыми словами. 80–120 слов."},
        {"scene": False, "text": "Формат «что входит». Короткое вступление и 3–4 пункта, что получает клиент. 80–120 слов."},
        {"scene": False, "text": "Формат «почему сейчас». Объясни, чем этот момент удобен для клиента, затем предложение. 80–120 слов."},
        {"scene": True, "text": "Формат «повод из практики». Ситуация, после которой родилось это предложение, и само предложение. 90–130 слов."},
    ],
    "news": [
        {"scene": False, "text": "Формат «что появилось». Назови новинку в первой строке, затем чем она полезна и кому. 80–120 слов."},
        {"scene": False, "text": "Формат «чем отличается». Сравни новинку с тем, что было раньше. 80–120 слов."},
        {"scene": False, "text": "Формат «зачем мы это добавили». Объясни задачу клиента, ради которой появилась новинка. 80–120 слов."},
        {"scene": False, "text": "Формат «как пользоваться». Новинка и короткая инструкция, как ей воспользоваться. 80–120 слов."},
        {"scene": True, "text": "Формат «первые впечатления». Как новинку встретили клиенты. 80–120 слов."},
    ],
    "team": [
        {"scene": False, "text": "Формат «через дело». Покажи человека через то, что он делает каждый день. 90–130 слов."},
        {"scene": False, "text": "Формат «любимая часть работы». Что человеку нравится больше всего и почему. 80–120 слов."},
        {"scene": False, "text": "Формат «как мы работаем». Один принцип работы и как он проявляется на практике. 90–130 слов."},
        {"scene": False, "text": "Формат «чему научились». Что изменилось в подходе за время работы. 90–130 слов."},
        {"scene": True, "text": "Формат «рабочий день». Один эпизод из работы, показывающий характер. 90–130 слов."},
    ],
    "howto": [
        {"scene": False, "text": "Формат «пошагово». Короткое вступление и 3–5 шагов по порядку. 90–140 слов."},
        {"scene": False, "text": "Формат «что нельзя делать». Назови ошибку, объясни последствие и как правильно. 80–120 слов."},
        {"scene": False, "text": "Формат «как выбрать». Критерии выбора, по которым стоит ориентироваться. 90–130 слов."},
        {"scene": False, "text": "Формат «правило». Одно короткое правило и объяснение, откуда оно берётся. 80–120 слов."},
        {"scene": True, "text": "Формат «частый случай». Ситуация, в которой это правило спасает, и само правило. 90–130 слов."},
    ],
    "photo_case": [
        {"scene": False, "text": "Формат «разбор задачи». Назови задачу, которую решали в этой работе, затем объясни выбранный подход и к чему он привёл. 90–140 слов."},
        {"scene": False, "text": "Формат «главная деталь». Назови одну деталь, которая определила итог работы, и объясни, почему именно она. 80–120 слов."},
        {"scene": False, "text": "Формат «что учитывали». Перечисли 3–4 фактора, которые пришлось учесть в этой работе, и зачем. 90–130 слов."},
        {"scene": False, "text": "Формат «как это устроено». Объясни внутреннюю логику процесса, которую со стороны не видно. 90–130 слов."},
        {"scene": True, "text": "Формат «как проходила работа». Спокойный рассказ о ходе работы от запроса до завершения, по фактам. 90–130 слов."},
    ],
    "service": [
        {"scene": False, "text": "Формат «как проходит». Опиши этапы одной услуги от начала до результата. 90–140 слов."},
        {"scene": False, "text": "Формат «что входит». Разбери, что клиент получает и что остаётся за кадром. 90–130 слов."},
        {"scene": False, "text": "Формат «кому подойдёт». Опиши задачи, которые эта услуга закрывает. 80–120 слов."},
        {"scene": False, "text": "Формат «что влияет на результат». Назови 3–4 фактора, от которых зависит итог. 90–130 слов."},
        {"scene": True, "text": "Формат «на примере». Одна работа как иллюстрация того, как проходит услуга. 90–130 слов."},
    ],
}

# Общий набор — фолбэк для рубрик, тип которых не определился.
# Продублирован в crm/Code.gs (POST_FORMATS): правишь здесь — правь и там,
# иначе примеры в CRM и боевые посты разойдутся по манере подачи.
POST_FORMATS = [
    {"scene": False, "text": "Формат «неожиданный факт или наблюдение» (70–110 слов). Начни с того, что удивит читателя."},
    {"scene": False, "text": "Формат «совет эксперта» (80–120 слов). Конкретный, практичный, без воды."},
    {"scene": False, "text": "Формат «личное размышление» (80–120 слов). Разговорный тон, как будто делишься мыслью с другом."},
    {"scene": False, "text": "Формат «до и после» или сравнение (80–120 слов). Покажи контраст или изменение."},
    {"scene": True, "text": "Формат «короткая история из практики» (90–130 слов). Начни с конкретной ситуации, заверши выводом."},
]

# Варианты первого предложения. Крутятся по кругу вместе с форматами —
# именно это лечит однообразие «Ольга куда-то пришла и что-то заметила».
OPENER_MOVES = [
    "Заход: начни с прямого вопроса, который задаёт читатель.",
    "Заход: начни с утверждения-тезиса, с которым хочется поспорить.",
    "Заход: начни с распространённого заблуждения — назови его как чужое мнение.",
    "Заход: начни с термина, названия документа или этапа и сразу объясни его.",
    "Заход: начни с конкретного предмета или детали (подпись, дата, ключи, окно).",
    "Заход: начни с перечисления — назови три вещи, о которых пойдёт речь.",
    "Заход: начни с короткого сравнения двух вещей.",
    "Заход: начни с описания атмосферы или ощущения.",
]

SCENE_BAN = (
    "ЗАПРЕЩЕНО начинать пост со сцены из рабочего дня автора: «вчера», "
    "«на прошлой неделе», «на днях», «ко мне обратились», «клиент позвонил», "
    "«мне написали», «пришла клиентка и попросила», «на встрече он удивился» "
    "и любые их варианты. В этом посте автор не пересказывает случай — "
    "он объясняет, разбирает или показывает суть темы."
)

# Рубрика рассчитана на фото, но фото не приехало (папка пуста или vision
# отвалился). Без этого правила модель писала «в данном случае мы…» и
# сочиняла работу, которой не было — для медицинской ниши это прямой риск.
NO_PHOTO_RULE = (
    "Фотографии к этому посту НЕТ. ЗАПРЕЩЕНО описывать конкретный случай, "
    "работу, объект или человека так, будто они изображены на снимке: "
    "никаких «в данном случае», «на фото», «у этого клиента», «этой "
    "пациентке». Не выдумывай ни одной детали несуществующей работы. "
    "Напиши общий экспертный пост по теме рубрики."
)

NO_CONTACTS_RULE = (
    "ЗАПРЕЩЕНО писать в тексте телефоны, e-mail, адреса, ссылки, ники и "
    "названия каналов. Номера вида «+7 (999) 000-00-00» выдумывать нельзя ни "
    "в каком виде, даже как пример. Контакты приклеиваются автоматически "
    "после генерации."
)


def pick_from_cycle(items, state, client_id, key):
    """Берёт следующий элемент по кругу и запоминает позицию в состоянии.
    Ротация вместо random: гарантирует, что за N постов будут использованы
    все варианты, а не один и тот же три раза подряд."""
    if not items:
        return None
    if state is None or not client_id:
        import random
        return random.choice(items)
    cs = client_state(state, client_id)
    counters = cs.setdefault("cycles", {})
    idx = int(counters.get(key, 0)) % len(items)
    counters[key] = idx + 1
    return items[idx]


def pick_topic(rubric, state, client_id):
    """Следующая тема из списка тем рубрики (поле topics в конфиге клиента).
    Без topics поведение прежнее — тему выбирает сама модель."""
    topics = [str(t).strip() for t in (rubric.get("topics") or []) if str(t).strip()]
    if not topics:
        return ""
    key = "topic:" + str(rubric.get("name", ""))
    return pick_from_cycle(topics, state, client_id, key) or ""


# ---------- защита от выдуманных контактов ----------

CONTACT_PATTERNS = [
    # телефоны: +7 (999) 000-00-00, 8 999 0000000, 8-800-...
    re.compile(r"(?:\+7|\b8)[\s\-\u2013\u2014]*\(?\d{3}\)?[\s\-\u2013\u2014]*\d{3}[\s\-\u2013\u2014]*\d{2}[\s\-\u2013\u2014]*\d{2}\b"),
    re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Zа-яА-Я]{2,}"),
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"\b(?:t\.me|vk\.(?:com|ru)|max\.ru|wa\.me)/\S+", re.IGNORECASE),
    re.compile(r"(?<![\w@])@[A-Za-z][\w_]{3,}"),
]


def _allowed_contacts_blob(client):
    """Всё, что клиент указал сам (CTA, канал, ссылки) — это разрешено."""
    try:
        return json.dumps(client, ensure_ascii=False).lower()
    except Exception:
        return str(client).lower()


def _contact_is_allowed(fragment, blob):
    frag = fragment.strip().lower()
    if frag and frag in blob:
        return True
    digits = re.sub(r"\D", "", frag)
    if len(digits) >= 10:
        # телефон считаем своим, только если те же цифры есть в конфиге
        return digits[-10:] in re.sub(r"\D", "", blob)
    return False


def scrub_fabricated_contacts(text, client):
    """
    Вырезает из готового поста контакты, которых нет в карточке клиента.

    Модель периодически дописывает собственный призыв с выдуманным телефоном
    (реальный случай: «позвоните по телефону +7 (999) 000-00-00»), хотя в
    промпте это запрещено, а настоящий CTA приклеивается отдельно. Удаляем
    целиком предложение с фальшивым контактом: обрезать только номер —
    значит оставить в канале обрубок фразы.
    """
    src = text or ""
    blob = _allowed_contacts_blob(client)
    out_paragraphs = []
    removed = []

    for para in src.split("\n"):
        if not para.strip():
            out_paragraphs.append(para)
            continue
        sentences = re.split(r"(?<=[.!?…])\s+", para)
        keep = []
        for s in sentences:
            bad = False
            for pat in CONTACT_PATTERNS:
                for m in pat.findall(s):
                    frag = m if isinstance(m, str) else (m[0] if m else "")
                    if frag and not _contact_is_allowed(frag, blob):
                        bad = True
                        removed.append(frag.strip())
                        break
                if bad:
                    break
            if not bad:
                keep.append(s)
        joined = " ".join(x.strip() for x in keep if x.strip())
        if joined:
            out_paragraphs.append(joined)

    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out_paragraphs)).strip()
    if removed:
        print("Из текста убраны выдуманные контакты: " + ", ".join(removed[:5]))
    # Страховка: если чистка съела почти весь пост, лучше отдать исходник —
    # пустой пост в канале хуже лишней строки.
    if len(cleaned) < 150 or len(cleaned) < len(src.strip()) * 0.35:
        print("Чистка контактов убрала слишком много — оставляю исходный текст.")
        return src.strip()
    return cleaned or src.strip()


def recent_openings(rubric, state, client_id, limit=6):
    """Начала последних постов этой рубрики — чтобы модель не писала
    по третьему разу об одном и том же. Работает у любого клиента,
    даже если список тем в конфиге не заполнен."""
    if state is None or not client_id:
        return []
    cs = client_state(state, client_id)
    seen = cs.setdefault("recent", {})
    return list(seen.get(str(rubric.get("name", "")), []))[-limit:]


def remember_opening(rubric, state, client_id, text, keep=8):
    """Запоминает первые слова опубликованного поста. Первое предложение —
    достаточный отпечаток темы: если модель заходит на тот же сюжет,
    оно почти всегда совпадает по смыслу."""
    if state is None or not client_id or not text:
        return
    fingerprint = " ".join(str(text).split()[:14])
    if not fingerprint:
        return
    cs = client_state(state, client_id)
    seen = cs.setdefault("recent", {})
    key = str(rubric.get("name", ""))
    lst = seen.setdefault(key, [])
    lst.append(fingerprint)
    seen[key] = lst[-keep:]


def rubric_output_ok(kind, text):
    """Грубая проверка, что пост годится к публикации.

    Универсальная часть — длина: обрубок в две строки в канал уходить не
    должен. Жанровая часть пока нужна только для FAQ: пост без вопроса в
    начале — это не разбор вопроса, а обычный текст, и именно так ломалась
    рубрика у Ольги."""
    body = (text or "").strip()
    if not body:
        return False
    if len(body.split()) < 40:
        return False
    # Оборванный на полуслове текст — не пост, каким бы длинным он ни был.
    if not text_is_complete(body):
        return False
    if kind == "faq":
        head = " ".join(body.split()[:45])
        return "?" in head
    return True


# Маркеры «Вопрос:» / «Ответ:». Делим по границе слова, а не по пробелу:
# клиенты часто вставляют FAQ склеенным куском вида «...восстановления.Вопрос:»,
# и требование пробела перед маркером ломало разбор целиком.
FAQ_Q_SPLIT = re.compile(r"(?=\b(?:вопрос(?:\s+клиента)?|question|q)\s*[:.\u2014-])", re.IGNORECASE)
FAQ_Q_LEAD = re.compile(r"^\s*(?:вопрос(?:\s+клиента)?|question|q)\s*[:.\u2014-]\s*", re.IGNORECASE)
FAQ_A_SPLIT = re.compile(r"\b(?:ответ|answer|a)\s*[:.\u2014-]\s*", re.IGNORECASE)


def parse_faq(raw):
    """Разбирает FAQ клиента на пары (вопрос, ответ).

    Клиент вставляет FAQ как придётся: с переносами строк, одним куском,
    с маркерами «Вопрос:/Ответ:» или без них. Из CRM поле приезжает уже
    порезанным по \n, поэтому сначала склеиваем обратно.

    Три стратегии по убыванию надёжности:
      1. есть маркеры «Вопрос:» — режем по ним, внутри ищем «Ответ:»;
      2. маркеров нет — строка со знаком вопроса считается вопросом,
         всё до следующего вопроса — ответом;
      3. ничего не распозналось — возвращаем пустой список, и рубрика
         работает по-старому, генерацией по нише.
    """
    if isinstance(raw, (list, tuple)):
        text = "\n".join(str(x) for x in raw if str(x).strip())
    else:
        text = str(raw or "")
    text = text.replace("\r", "").strip()
    if not text:
        return []

    pairs = []

    # Стратегия 1 — по маркеру «Вопрос:».
    if FAQ_Q_SPLIT.search(text):
        for chunk in FAQ_Q_SPLIT.split(text):
            chunk = FAQ_Q_LEAD.sub("", chunk).strip()
            if not chunk:
                continue
            parts = FAQ_A_SPLIT.split(chunk, maxsplit=1)
            question = parts[0].strip(" \n\t.\u2014-")
            answer = parts[1].strip() if len(parts) > 1 else ""
            if question:
                pairs.append((question, answer))

    # Стратегия 2 — по знаку вопроса в конце строки.
    if not pairs:
        question, buf = "", []
        for line in [l.strip() for l in text.split("\n")]:
            if not line:
                continue
            if line.endswith("?"):
                if question:
                    pairs.append((question, " ".join(buf).strip()))
                question, buf = line, []
            elif question:
                buf.append(line)
        if question:
            pairs.append((question, " ".join(buf).strip()))

    # Отсекаем мусор: вопрос короче трёх слов — почти наверняка обрывок.
    return [(q, a) for q, a in pairs if len(q.split()) >= 3]


def build_post(client, rubric, photo_path=None, state=None, client_id=""):
    forbidden = client.get("forbidden") or []
    lines = [
        f"Бизнес: {client.get('business', '')}",
        f"Город: {client.get('city', '')}",
        f"Тон: {client.get('tone', '')}",
        f"Рубрика: {rubric.get('name', '')}",
    ]
    rubric_prompt = str(rubric.get("prompt") or "").strip()
    if forbidden:
        lines.append("Запрещено упоминать: " + "; ".join(forbidden))
    # CTA намеренно не уходит в промпт: он приклеивается дословно
    # постобработкой в append_cta. Модель его только портила.
    lines.append(
        "Не пиши в конце поста призыв к действию, контакты, ссылки и приглашение "
        "подписаться — они добавляются автоматически после генерации."
    )
    lines.append(NO_CONTACTS_RULE)

    style = (
        f"строго в стиле, описанном ниже:\n{client['style_prompt']}\n"
        if client.get("style_prompt")
        else "простым разговорным языком.\n"
    )

    # vision включается только для рубрик, где анализ реально нужен:
    # «Отзывы» (скриншоты/фото) и «Фото работ» (результаты работы).
    # tips, faq, ideas — заглушки без анализа.
    uses_vision = rubric_uses_vision(rubric)  # передаём словарь чтобы учитывался kind

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
                "Ты получаешь клиническое фото из практики пластического хирурга. "
                "Это коллаж «до / после» или фото результата на определённом сроке "
                "после операции.",
                "ШАГ 1 — определи по изображению:",
                "— тип операции (например: блефаропластика, маммопластика, "
                "абдоминопластика, подтяжка лица, липосакция, ринопластика и т.д.)",
                "— зону вмешательства (лицо, грудь, живот, бока и т.д.)",
                "— что уже видно на фото сейчас: изменения контура, чёткость "
                "линий, состояние рубцов, отёк, симметрия — только то, что "
                "реально различимо на снимке.",
                "Если на фото видна надпись с датой или сроком — используй её. "
                "Если нет — не выдумывай срок.",
                "ШАГ 2 — напиши пост по этой структуре:",
                "1. Первая строка: название операции + срок после операции "
                "(если виден). Коротко, без вступления.",
                "2. Что видно на фото прямо сейчас: конкретные изменения, "
                "которые можно увидеть. Говори только о том, что действительно "
                "различимо — контур, рубец, симметрия, отёк и т.д.",
                "3. Анатомический или технический комментарий: почему именно "
                "такой подход, что это даёт в долгосрочной перспективе.",
                "4. Вывод: что это значит для пациента — естественность, "
                "долговечность, качество восстановления.",
                "ЗАПРЕЩЕНО: выдумывать срок, имя, возраст пациента, конкретные "
                "цифры результата (кг, см, %), обещать гарантии. "
                "Не пиши «на фото изображено». Не начинай с «Сегодня я хочу».",
                f"Объём: 100–160 слов. Пиши {style}",
                "Ответь только готовым текстом поста."
            ]
            if photo_instruction:
                context_parts.insert(1,
                    "Специальная инструкция для этой работы: " + str(photo_instruction)
                )
            if photo_topics:
                topic_text = ", ".join(str(x) for x in photo_topics if str(x).strip())
                if topic_text:
                    context_parts.insert(2,
                        "Направления работы в этой практике: " + topic_text + "."
                    )

        user = "\n".join(lines) + "\n\n" + "\n".join(context_parts)
        if rubric_prompt:
            user += "\n\nИНСТРУКЦИЯ РУБРИКИ (выполнить целиком):\n" + rubric_prompt
        data_url = image_to_data_url(photo_path)
        if data_url:
            try:
                vision_text = ai_text([
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
                # Раньше vision-ветка выходила из функции здесь же, и зачин
                # не попадал в recent: антиповтор для «Фото работ» не копился
                # вообще, пока в папке лежали фото.
                vision_text = trim_to_sentence(vision_text)
                remember_opening(rubric, state, client_id, vision_text)
                return vision_text
            except Exception as e:
                # Если vision-провайдер временно недоступен, не теряем публикацию:
                # пробуем старую текстовую генерацию без изображения.
                print(f"Vision-анализ фото не удался — пробую обычную генерацию: {e}")

    kind = rubric_kind(rubric)

    # Формат подачи берём из набора СВОЕЙ рубрики и крутим по кругу.
    # Если в конфиге клиента задан post_format — он жёстче всего остального.
    fixed_format = (client.get("post_format") or "").strip()
    if fixed_format:
        fmt_text, fmt_scene = fixed_format, True
    else:
        pool = RUBRIC_FORMATS.get(kind) or POST_FORMATS
        fmt = pick_from_cycle(pool, state, client_id, "fmt:" + kind) or pool[0]
        fmt_text, fmt_scene = fmt["text"], fmt["scene"]

    opener = pick_from_cycle(OPENER_MOVES, state, client_id, "opener") or OPENER_MOVES[0]
    topic = pick_topic(rubric, state, client_id)

    # FAQ клиента — источник правды. Если он заполнен, вопрос НЕ выдумывается:
    # берём одну пару по кругу, чтобы за N постов прошли все вопросы и только
    # потом начался второй круг. Раньше client["faq"] в промпт вообще не
    # уходил, и модель сочиняла вопросы от себя.
    faq_pair = None
    if kind == "faq":
        faq_pairs = parse_faq(client.get("faq"))
        if faq_pairs:
            faq_pair = pick_from_cycle(faq_pairs, state, client_id, "faq")

    if faq_pair:
        question, answer = faq_pair
        anchor_text = (
            "Это рубрика вопросов и ответов, и вопрос для этого поста задан "
            "клиентом. Менять его тему, объединять с другими вопросами или "
            "придумывать свой — запрещено.\n"
            f"ВОПРОС ПОСТА (сформулируй его в первых двух предложениях, можно "
            f"своими словами, но смысл сохрани дословно): {question}"
        )
        if answer:
            anchor_text += (
                "\nОТВЕТ КЛИЕНТА — это фактура, единственный допустимый источник "
                "содержания. Перескажи его в стиле автора, можешь менять порядок "
                "и формулировки, но не добавляй фактов, которых в нём нет, и "
                "не выбрасывай смысловые части:\n" + answer
            )
        else:
            anchor_text += (
                "\nГотового ответа клиент не дал — отвечай по существу вопроса, "
                "не выходя за рамки ниши и ограничений."
            )
        task_lines = ["ГЛАВНОЕ ТРЕБОВАНИЕ: " + anchor_text]
    elif kind == "faq":
        task_lines = [
            "ГЛАВНОЕ ТРЕБОВАНИЕ: " + RUBRIC_ANCHOR["faq"] +
            " Список вопросов клиент не заполнил, поэтому вопрос выбери сам — "
            "но такой, который реальные клиенты этого бизнеса действительно "
            "задают перед покупкой или обращением. Не бери общие вопросы "
            "уровня «а что это такое»: нужен живой практический вопрос, "
            "на который человек ищет ответ прямо сейчас."
        ]
    else:
        task_lines = [
            "ГЛАВНОЕ ТРЕБОВАНИЕ: " + RUBRIC_ANCHOR.get(kind, RUBRIC_ANCHOR["default"]),
        ]

    # Тема из списка клиента не должна подменять заданный вопрос FAQ.
    if topic and not faq_pair:
        task_lines.append(
            f"Тема этого поста: {topic}. Раскрой именно её и не подменяй другой."
        )
    # Сюда мы попадаем либо из обычной текстовой рубрики, либо из фото-рубрики,
    # у которой фото не оказалось. Второй случай нужно проговорить явно.
    if uses_vision:
        task_lines.append(NO_PHOTO_RULE)
    task_lines.append(fmt_text)
    if not fmt_scene:
        task_lines.append(SCENE_BAN)
        task_lines.append(opener)
    # Антиповтор для клиентов без списка тем: показываем модели начала
    # последних постов этой же рубрики и просим не идти по тому же кругу.
    previous = recent_openings(rubric, state, client_id)
    if previous:
        task_lines.append(
            "В этой рубрике уже выходили посты, начинавшиеся так:\n"
            + "\n".join("— " + p for p in previous)
            + "\nНовый пост должен быть про ДРУГОЕ: другая тема, другой угол, "
            "другое первое предложение."
        )

    task_lines.append("Пиши " + style.strip())
    task_lines.append(
        "Не выдумывай цены, сроки, проценты и гарантии. "
        "Ответь только текстом поста, без черновиков и пометок о проверке."
    )
    # Инструкция рубрики стоит последней сознательно: модель сильнее всего
    # держит то, что ближе к концу промпта. Раньше последним был формат —
    # и он перебивал рубрику (рубрика ЧЗВ выдавала историю из практики).
    if rubric_prompt:
        task_lines.append(
            "ИНСТРУКЦИЯ РУБРИКИ — она главнее формата, выполни её целиком:\n"
            + rubric_prompt
        )

    style_instruction = (
        f"СТИЛЬ АВТОРА (соблюдать обязательно): {client['style_prompt']}\n\n"
        if client.get("style_prompt") else ""
    )

    user = style_instruction + "\n".join(lines) + "\n\n" + "\n".join(task_lines)
    system = (
            "Ты пишешь посты для соцсетей малого бизнеса от лица владельца. "
            "Если задан style_prompt — это описание реальной манеры речи этого человека, "
            "её нужно точно воспроизвести: длина предложений, обращение, любимые обороты. "
            "Порядок приоритетов при конфликте инструкций: "
            "1) требование рубрики, 2) стиль автора, 3) формат подачи. "
            "Формат — это только способ подачи, он никогда не отменяет задачу рубрики. "
            "Текст должен быть конкретным: одна мысль, доведённая до конца, "
            "вместо трёх общих фраз. Никакой воды, канцелярита и оборотов "
            "вроде «в современном мире», «играет важную роль», «не секрет, что». "
            + openers_rule() +
            "В ответе — только готовый текст поста, ничего больше: ни черновиков, "
            "ни заметок о проверке длины, ни заголовков вроде «Пост:» или «Draft». "
            "Не показывай ход рассуждений — только финальный результат."
    )

    text = ai_text([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

    # Причины перегенерации: дежурный зачин из стоп-листа, слишком короткий
    # текст и пост, промахнувшийся мимо жанра рубрики.
    problem = ""
    if has_forbidden_opener(text):
        problem = ("Предыдущая попытка начиналась с дежурной формулы: «"
                   + " ".join((text or "").split()[:6]) + "…». Начни совершенно иначе.")
    elif len((text or "").split()) < 40:
        problem = ("Предыдущая попытка получилась слишком короткой и общей. "
                   "Напиши полноценный пост нужной длины, раскрыв тему до конца.")
    elif not text_is_complete(text):
        problem = ("Предыдущая попытка оборвалась на полуслове: последнее "
                   "предложение не закончено. Напиши пост целиком и доведи "
                   "последнюю мысль до точки.")
    elif not rubric_output_ok(kind, text):
        problem = ("Предыдущая попытка не выполнила требование рубрики: в начале "
                   "поста нет сформулированного вопроса читателя. Перепиши так, "
                   "чтобы первое или второе предложение было самим вопросом.")

    if problem:
        print("Перегенерирую пост: " + problem.split(":")[0])
        try:
            retry = ai_text([
                {"role": "system", "content": system},
                {"role": "user", "content": user + "\n\n" + problem},
            ])
            if retry and not has_forbidden_opener(retry) and rubric_output_ok(kind, retry):
                text = retry
        except Exception as e:
            print(f"Повторная генерация не удалась, оставляю первый вариант: {e}")

    # Если и перегенерация вернула обрубок — отрезаем незаконченный хвост.
    # Лучше пост на предложение короче, чем оборванный на полуслове.
    text = trim_to_sentence(text)

    remember_opening(rubric, state, client_id, text)
    return text


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
        "«Последний день перед [название праздником].», "
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
        "«Сегодня [название праздника].», "
        "«Вот и наступил [название праздника]…». "
        "После вступления — поздравление и тёплые слова подписчикам, "
        "без официоза и без пересказа истории праздника. "
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
    # CTA добавляется дословно после генерации (append_cta), в промпт не идёт.
    lines.append(
        "Не пиши в конце поста призыв к действию, контакты и ссылки — "
        "они добавляются автоматически после генерации."
    )
    # Примеры вступительных фраз выше заданы в форме «мы». Если стиль
    # клиента требует единственного числа, приоритет у стиля: иначе
    # модель копирует «поздравляем» дословно и ломает голос автора.
    if client.get("style_prompt"):
        lines.append(
            "ВАЖНО: примеры вступительных фраз выше задают только структуру "
            "начала, а не лицо повествования. Местоимения, обращение и форму "
            "глаголов бери строго из стиля клиента. Если стиль требует «я», "
            "не пиши «мы», «наш», «наши», «поздравляем» — переформулируй "
            "в единственном числе."
        )

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
    text = ai_text(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
    )
    # Праздничные посты короткие, и обрыв на них заметнее всего: поздравление,
    # оборванное на полуслове, выглядит хуже, чем его отсутствие.
    return trim_to_sentence(text)


# Какая подпапка праздника отвечает за какой пост: анонсы за неделю и за
# день берут фото из before/, поздравление в сам день — из day/.
HOLIDAY_PHOTO_SUBFOLDER = {
    "before7": "before",
    "before1": "before",
    "day": "day",
}


def holiday_photo(client, holiday, kind, state=None, client_id=""):
    """
    Фото к празднику из собственной библиотеки на Яндекс.Диске.

    У каждого праздника две подпапки: before/ — предпраздничные картинки
    для анонсов за 7 и за 1 день, day/ — праздничные, для поздравления в
    сам день. Если нужной подпапки нет, скрипт берёт фото из корня папки
    праздника — так можно обойтись одним набором на всё.

    Полный порядок поиска (первая непустая папка выигрывает):
      1. {yandex_folder}/holidays/{key}/{before|day} — личные фото клиента
      2. {yandex_folder}/holidays/{key}
      3. {HOLIDAYS_FOLDER}/{key}/{before|day}        — общая библиотека
      4. {HOLIDAYS_FOLDER}/{key}

    Файлы не расходуются: перебираются по кругу по алфавиту, как рубрики
    tips/faq, — один раз положил несколько штук и больше за папкой не
    следишь. Нигде ничего не нашлось — праздничный пост уходит текстом.

    Фотосток здесь сознательно не используется: поиск по запросу выдаёт
    случайные кадры, проверить их перед публикацией нечем.
    """
    key = holiday["key"]
    sub = HOLIDAY_PHOTO_SUBFOLDER.get(kind, "day")

    roots = []
    own = (client.get("yandex_folder") or "").strip()
    if own:
        roots.append(f"{own}/holidays/{key}")
    if HOLIDAYS_FOLDER:
        roots.append(f"{HOLIDAYS_FOLDER}/{key}")

    sources = []
    for root in roots:
        sources.append((f"{root}/{sub}", f"{key}_{sub}"))
        sources.append((root, key))

    for source, loop_id in sources:
        files = list_folder(source, quiet=True)
        if not files:
            continue
        names = sorted(f["name"] for f in files)
        chosen_name = names[0]
        if state is not None and client_id:
            # у before/ и day/ очереди раздельные: свой ключ на каждую папку
            cs = client_state(state, client_id)
            loop_key = f"holiday_loop_{loop_id}"
            last_name = cs.get(loop_key, "")
            if last_name in names:
                chosen_name = names[(names.index(last_name) + 1) % len(names)]
            cs[loop_key] = chosen_name
        chosen = next(f for f in files if f["name"] == chosen_name)
        try:
            local_path = download_yandex_file(chosen["path"])
        except Exception as e:
            print(f"Не удалось скачать {chosen['path']}: {e}")
            return None
        print(f"Фото к празднику «{holiday['name']}» ({HOLIDAY_KINDS[kind]}): "
              f"{source}/{chosen_name}")
        return local_path

    print(f"Нет фото для праздника «{holiday['name']}» ({HOLIDAY_KINDS[kind]}): "
          f"пусто в {', '.join(src for src, _ in sources) or '—'}. "
          "Пост уйдёт текстом.")
    return None


# ---------- Telegram (с фолбэками — пост должен уйти всегда) ----------

def post_to_telegram(channel, text, photo_path=None):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    CAPTION_LIMIT = 1024

    def send_message(body, parse_mode=None):
        payload = {"chat_id": channel, "text": body}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return requests.post(f"{base_url}/sendMessage", json=payload, timeout=30)

    def send_photo(path, caption, parse_mode=None):
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
    это одновременно канун Нового года и неделя до Рождества, и получить два
    поста в один день клиент не должен.
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
    Расписание по дням недели (поле days[] у рубрики).

    При двух и более слотах рубрики делятся по типу:
      - kind == "photo_case" / "before_after" — это «фото работ» клиента.
        Такие рубрики идут в РАННИЙ слот. Обычно стоят «ежедневно».
      - все остальные — идут в ПОЗДНИЙ слот и чередуются между собой
        по назначенным дням.

    В каждом слоте берутся только рубрики, назначенные на сегодня.
    Если на сегодня ничего не назначено — слот пропускается (None).
    Рубрика с пустым days[] считается ежедневной.

    Если у клиента вообще нет фото-рубрик, ранний слот берёт из общего
    пула сегодняшних рубрик — чтобы не пропадал впустую.

    Один слот — обычный pick_rubric() по дням.
    Тест — по кругу по всем рубрикам.
    """
    if test_mode:
        return pick_rubric(client, today_abbr, state, test_mode)

    slot_names = [s.get("name") for s in client.get("slots", []) if s.get("name")]
    ordered_slots = [s for s in SLOT_ORDER if s in slot_names]

    if len(ordered_slots) <= 1:
        return pick_rubric(client, today_abbr, state, test_mode)

    rubrics = client.get("rubrics", [])
    if not rubrics:
        return None

    def scheduled_today(r):
        days = r.get("days") or []
        return (not days) or (today_abbr in [d.strip() for d in days])

    def is_photo_rubric(r):
        return str(r.get("kind") or "").strip().lower() in ("photo_case", "before_after")

    photo_pool = [r for r in rubrics if is_photo_rubric(r)]
    text_pool  = [r for r in rubrics if not is_photo_rubric(r)]

    cs = client_state(state, client["client_id"])
    is_early = (slot == ordered_slots[0])

    if is_early:
        pool = [r for r in photo_pool if scheduled_today(r)]
        if not pool:
            # У клиента нет фото-рубрик (или сегодня они не назначены) —
            # не оставляем слот пустым, берём из общего расписания дня.
            pool = [r for r in rubrics if scheduled_today(r)]
        if not pool:
            return None
        key = "photo_tie_index"
    else:
        pool = [r for r in text_pool if scheduled_today(r)]
        if not pool:
            return None
        key = "text_tie_index"

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
            # к празднику картинка берётся из библиотеки праздничных фото
            # и в posted/ не переезжает (photo_meta = None)
            photo_path, photo_meta = holiday_photo(client=client, holiday=holiday,
                                                   kind=kind, state=state,
                                                   client_id=cid), None
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
                text = build_post(client, rubric, photo_path,
                                  state=state, client_id=cid)
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

        # Последний рубеж: модель могла дописать свой телефон или ссылку
        # вопреки запрету в промпте — в канал такое уходить не должно.
        text = scrub_fabricated_contacts(text, client)

        # Второй рубеж: проверяем ТЕЛО поста до приклеивания CTA и тегов.
        # После них текст всегда заканчивается «правильно», и обрыв внутри
        # тела становится невидимым — именно так обрубок и попал в канал.
        text = trim_to_sentence(text)
        if not text_is_complete(text) or len(text.split()) < 40:
            head = " ".join(text.split()[:12])
            print(f"{cid}: текст неполный после всех попыток — публикацию отменяю. "
                  f"Начало: «{head}…»")
            report_post_status(
                cid, "Ошибка: неполный текст",
                today.strftime("%d.%m.%Y") + " " +
                __import__("datetime").datetime.now().strftime("%H:%M"),
            )
            # фото не тратим: очередь не двигаем, оно достанется следующему посту
            if photo_path and os.path.exists(photo_path):
                try:
                    os.unlink(photo_path)
                except Exception:
                    pass
            continue

        # У торжественных праздников призыв к действию неуместен.
        if not (holiday and holiday.get("solemn")):
            text = append_cta(text, client)
        text = append_hashtags(text, client, holiday)
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
