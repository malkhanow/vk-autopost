#!/usr/bin/env python3
"""Очередь фото и видео: пропуск битых файлов, видео, нейтральная подпись.

Диск и Telegram подменены. ffmpeg и Pillow — настоящие: перекодирование
и проверка «открывается ли файл» проверяются на реальных файлах.
Если ffmpeg в окружении нет, видео-проверки пропускаются с пометкой.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Checks, Response, load_module  # noqa: E402

cp, http = load_module("clients_post")
c = Checks("очередь фото и видео")


def eq(name, got, want):
    c.ok(name, got == want, got)


TMP = tempfile.mkdtemp(prefix="media-test-")


def make_jpeg(name):
    from PIL import Image
    p = os.path.join(TMP, name)
    Image.new("RGB", (64, 48), (200, 30, 30)).save(p, "JPEG")
    return p


def make_video(name, seconds=1, size="160x120"):
    """Настоящее видео из тестового генератора ffmpeg."""
    p = os.path.join(TMP, name)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=15",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", p],
        check=True)
    return p


def item(folder, name, size=1000):
    return {"name": name, "path": f"disk:/{folder}/{name}", "size": size}


def fresh_state():
    return {}


# ---------- расширения и разрешение на видео ----------

eq("mp4 — видео", cp.is_video_path("a/b/clip.MP4"), True)
eq("mov — видео", cp.is_video_path("clip.mov"), True)
eq("jpg — не видео", cp.is_video_path("photo.jpg"), False)
eq("пустой путь — не видео", cp.is_video_path(None), False)

eq("по умолчанию видео выключено", cp.video_enabled({"client_id": "x"}), False)
eq("явный post_video=true включает", cp.video_enabled({"client_id": "x", "post_video": True}), True)
cp.VIDEO_CLIENTS = {"x"}
eq("client_id из VIDEO_CLIENTS включает", cp.video_enabled({"client_id": "x"}), True)
eq("явный post_video=false главнее переменной",
   cp.video_enabled({"client_id": "x", "post_video": False}), False)
eq("чужой client_id не включается", cp.video_enabled({"client_id": "y"}), False)
cp.VIDEO_CLIENTS = set()

eq("to_post принимает видео", cp.rubric_accepts_video({"name": "Фото", "kind": "photo_case"}), True)
eq("своя подпапка принимает видео",
   cp.rubric_accepts_video({"name": "Видео", "kind": "photo_case", "photo_folder": "video"}), True)
eq("tips (круговая) не принимает видео", cp.rubric_accepts_video({"name": "x", "kind": "tips"}), False)
eq("ideas не принимает видео", cp.rubric_accepts_video({"name": "x", "kind": "inspiration"}), False)
eq("reviews не принимает видео", cp.rubric_accepts_video({"name": "x", "kind": "results"}), False)
eq("photo_loop=true отключает видео",
   cp.rubric_accepts_video({"name": "x", "kind": "photo_case", "photo_loop": True}), False)


# ---------- list_folder: видео только по разрешению ----------

listing = {"_embedded": {"items": [
    {"name": "a.jpg", "path": "disk:/f/a.jpg", "type": "file", "created": "1"},
    {"name": "b.MOV", "path": "disk:/f/b.MOV", "type": "file", "created": "2", "size": 5},
    {"name": "c.mp4", "path": "disk:/f/c.mp4", "type": "file", "created": "3"},
    {"name": "d.heic", "path": "disk:/f/d.heic", "type": "file", "created": "4"},
    {"name": "sub", "path": "disk:/f/sub", "type": "dir", "created": "5"},
]}}
http.routes["https://cloud-api.yandex.net/v1/disk/resources"] = Response(payload=listing)
eq("без разрешения — только картинки",
   [i["name"] for i in cp.list_folder("f")], ["a.jpg"])
eq("с разрешением — картинки и видео, регистр не важен",
   [i["name"] for i in cp.list_folder("f", allow_video=True)], ["a.jpg", "b.MOV", "c.mp4"])


# ---------- пропуск битых файлов ----------

def with_queue(files, downloads, allow_video=False, rubric=None, state=None, client_id="t"):
    """Подменяет Диск: files — список, downloads — {путь: локальный файл | Exception}."""
    calls = []

    def fake_list(path, quiet=False, allow_video=False):
        return list(files)

    def fake_download(path):
        calls.append(path)
        res = downloads[path]
        if isinstance(res, Exception):
            raise res
        return res

    cp.list_folder, cp.download_yandex_file = fake_list, fake_download
    rubric = rubric or {"name": "Фото работ", "kind": "photo_case"}
    out = cp.next_photo_for_client("Autopost WORK/t", rubric, state=state,
                                   client_id=client_id, allow_video=allow_video)
    return out, calls


a, b, d = (item("Autopost WORK/t/to_post", n) for n in ("a.jpg", "b.jpg", "d.jpg"))

# 1. первый файл не скачивается — берём второй
good = make_jpeg("good_b.jpg")
st = fresh_state()
(path, meta), calls = with_queue([a, b, d], {a["path"]: RuntimeError("504"), b["path"]: good, d["path"]: "-"}, state=st)
eq("битый первый — опубликован второй", path, good)
eq("в posted уйдёт именно второй файл", meta[1], "b.jpg")
eq("до третьего не дошли", calls, [a["path"], b["path"]])
eq("сбой записан в state", st["t"]["file_fails"].get(a["path"]), 1)

# 2. на следующем запуске провалившийся файл идёт в конец и не тратит попытку
good2 = make_jpeg("good_b2.jpg")
(path, meta), calls = with_queue([a, b, d], {a["path"]: RuntimeError("504"), b["path"]: good2, d["path"]: "-"}, state=st)
eq("повторно битый файл первым не берётся", calls, [b["path"]])

# 3. скачался, но не картинка (страница ошибки вместо снимка)
junk = os.path.join(TMP, "junk.jpg")
open(junk, "wb").write(b"<html>504 Gateway Timeout</html>")
good3 = make_jpeg("good_d.jpg")
(path, meta), calls = with_queue([a, d], {a["path"]: junk, d["path"]: good3}, state=fresh_state())
eq("файл-пустышка отсеян, взят следующий", path, good3)
eq("пустышка удалена с диска раннера", os.path.exists(junk), False)

# 4. обрезанный JPEG
cut = os.path.join(TMP, "cut.jpg")
raw = open(good, "rb").read()
open(cut, "wb").write(raw[: len(raw) // 2])
eq("обрезанный JPEG не считается рабочим", cp.image_is_readable(cut), False)
eq("целый JPEG рабочий", cp.image_is_readable(good), True)
eq("пустой файл не рабочий", cp.image_is_readable(os.path.join(TMP, "nope.jpg")), False)

# 5. все файлы битые — (None, None), попыток не больше лимита
many = [item("Autopost WORK/t/to_post", f"{i}.jpg") for i in range(20)]
(path, meta), calls = with_queue(many, {m["path"]: RuntimeError("x") for m in many}, state=fresh_state())
eq("все битые — фото нет", (path, meta), (None, None))
eq("попыток не больше лимита", len(calls), cp.MAX_FILE_ATTEMPTS)

# 6. побитые файлы тонут: на следующем запуске пробуем ещё не пробованные
st = fresh_state()
with_queue(many, {m["path"]: RuntimeError("x") for m in many}, state=st)
good4 = make_jpeg("good_late.jpg")
dl = {m["path"]: RuntimeError("x") for m in many}
dl[many[10]["path"]] = good4
(path, meta), calls = with_queue(many, dl, state=st)
eq("после сбоев доходим до годного файла из хвоста", path, good4)

# 7. круговая очередь (tips): битый файл не останавливает цикл
loop_files = [item("Autopost WORK/t/rubrics/tips", n) for n in ("1.jpg", "2.jpg", "3.jpg")]
good5 = make_jpeg("loop_2.jpg")
st = fresh_state()
(path, meta), calls = with_queue(
    loop_files,
    {loop_files[0]["path"]: RuntimeError("x"), loop_files[1]["path"]: good5, loop_files[2]["path"]: "-"},
    rubric={"name": "Советы", "kind": "tips"}, state=st)
eq("круг: битый первый пропущен", path, good5)
eq("круг: файл не переезжает в posted", meta, None)
eq("круг: позиция запомнена на рабочем файле", st["t"]["loop_rubrics_tips"], "2.jpg")

# 8. без state (старые вызовы) тоже работает
good6 = make_jpeg("nostate.jpg")
(path, meta), calls = with_queue([a, b], {a["path"]: RuntimeError("x"), b["path"]: good6}, state=None, client_id="")
eq("без state: тоже пропускаем битый", path, good6)

# 9. записи о ушедших файлах чистятся
st = fresh_state()
st["t"] = {"tie_index": 0, "used_photos": [], "file_fails": {b["path"]: 3, "disk:/other/x.jpg": 1}}
good7 = make_jpeg("clean.jpg")
with_queue([a], {a["path"]: good7}, state=st)
eq("сбой удалённого файла забыт", b["path"] in st["t"]["file_fails"], False)
eq("сбой файла из другой папки не тронут", "disk:/other/x.jpg" in st["t"]["file_fails"], True)


# ---------- видео в очереди ----------

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
if not HAVE_FFMPEG:
    print("skip видео-проверки: в окружении нет ffmpeg")
else:
    mp4 = make_video("ok.mp4")
    info = cp.probe_video(mp4)
    eq("probe: кодек и размер определены", (info["codec"], info["width"], info["height"]),
       ("h264", 160, 120))
    eq("probe: длительность около секунды", info.get("duration") in (0, 1), True)
    eq("мета для Telegram", cp.video_meta(mp4).get("width"), 160)

    # обычный mp4 в лимите уходит как есть
    eq("готовый mp4 не перекодируется", cp.prepare_video(mp4), mp4)

    # .mov перекодируется в mp4, исходник удалён
    mov = make_video("clip.mov")
    out = cp.prepare_video(mov)
    eq("mov превращён в mp4", bool(out) and out.endswith(".mp4") and cp.probe_video(out) is not None, True)
    eq("исходный mov удалён", os.path.exists(mov), False)
    if out:
        os.unlink(out)

    # обрезанный файл — не видео
    cutv = os.path.join(TMP, "cut.mp4")
    rawv = open(mp4, "rb").read()
    open(cutv, "wb").write(rawv[:200])
    eq("обрезанное видео отсеяно", cp.prepare_video(cutv), None)
    garbage = os.path.join(TMP, "garbage.mp4")
    open(garbage, "wb").write(b"not a video at all" * 100)
    eq("мусор под видом mp4 отсеян", cp.prepare_video(garbage), None)

    # не влезает в лимит даже при минимальном качестве
    big = make_video("big.mp4", seconds=2)
    saved = cp.MAX_VIDEO_MB
    cp.MAX_VIDEO_MB = 0.00001
    eq("не влезает в лимит — отказ, а не битый файл", cp.prepare_video(big), None)
    cp.MAX_VIDEO_MB = saved

    # очередь: битое видео пропущено, следующее взято
    v1 = {"name": "v1.mp4", "path": "disk:/Autopost WORK/t/to_post/v1.mp4", "size": 100}
    v2 = {"name": "v2.mp4", "path": "disk:/Autopost WORK/t/to_post/v2.mp4", "size": 100}
    okv = make_video("v2_local.mp4")
    (path, meta), calls = with_queue([v1, v2], {v1["path"]: garbage, v2["path"]: okv},
                                     allow_video=True, state=fresh_state())
    eq("битое видео пропущено, взято следующее", path, okv)
    eq("видео уйдёт в posted после публикации", meta[1], "v2.mp4")

    # клиенту без разрешения видео из очереди не берётся
    seen = {}

    def spy_list(path, quiet=False, allow_video=False):
        seen["allow_video"] = allow_video
        return [a]

    cp.list_folder = spy_list
    cp.download_yandex_file = lambda p: make_jpeg("spy.jpg")
    cp.next_photo_for_client("Autopost WORK/t", {"name": "Фото", "kind": "photo_case"},
                             state=fresh_state(), client_id="t", allow_video=False)
    eq("без разрешения видео в список не просим", seen["allow_video"], False)
    cp.next_photo_for_client("Autopost WORK/t", {"name": "Фото", "kind": "photo_case"},
                             state=fresh_state(), client_id="t", allow_video=True)
    eq("с разрешением просим", seen["allow_video"], True)
    cp.next_photo_for_client("Autopost WORK/t", {"name": "Советы", "kind": "tips"},
                             state=fresh_state(), client_id="t", allow_video=True)
    eq("в круговой папке видео не просим даже по разрешению", seen["allow_video"], False)

    # слишком тяжёлый исходник даже не скачивается
    huge = {"name": "huge.mp4", "path": "disk:/Autopost WORK/t/to_post/huge.mp4",
            "size": (cp.MAX_VIDEO_SOURCE_MB + 1) * 1024 * 1024}
    (path, meta), calls = with_queue([huge], {huge["path"]: "-"}, allow_video=True, state=fresh_state())
    eq("гигантский ролик не скачивается", (path, calls), (None, []))


# ---------- нейтральная подпись ----------

NEUTRAL = ("Спокойный профессиональный подход начинается с внимательного разговора. "
           "Мы сначала слушаем задачу, уточняем детали и только потом предлагаем "
           "решение, которое подходит именно вам, без спешки и без лишних обещаний. "
           "Это даёт ощущение опоры. Когда понятны шаги и причины каждого "
           "решения, появляется уверенность, что всё идёт так, как должно идти.")

captured = []


def fake_ai(messages, **kw):
    captured.append(messages)
    return fake_ai.replies.pop(0)


cp.ai_text = fake_ai
client = {"client_id": "t", "business": "Клиника", "city": "СПб", "tone": "Экспертный",
          "style_prompt": "Пиши от первого лица.", "forbidden": ["цены"], "faq": [],
          "rubrics": []}
rubric = {"name": "Фото работ", "kind": "photo_case", "prompt": "Опиши случай по приложенному фото"}

# видео -> нейтральная подпись, vision не вызывается, промпт рубрики не используется
vid_path = os.path.join(TMP, "caption.mp4")
open(vid_path, "wb").write(b"x")
seen_vision = []
cp.image_to_data_url = lambda p: seen_vision.append(p)
fake_ai.replies = [NEUTRAL]
captured.clear()
st = fresh_state()
text = cp.build_post(client, rubric, vid_path, state=st, client_id="t")
sent = "\n".join(m["content"] if isinstance(m["content"], str) else "" for m in captured[0])
eq("подпись к видео получена", text.startswith("Спокойный профессиональный"), True)
eq("vision для видео не вызывался", seen_vision, [])
eq("в промпте есть запрет описывать ролик", "НЕИЗВЕСТНО" in sent, True)
eq("промпт рубрики про фото в запрос не попал", "приложенному фото" in sent, False)
eq("запреты клиента переданы", "цены" in sent, True)
eq("зачин запомнен для антиповтора", "__video__" in st["t"]["recent"], True)

# модель упомянула ролик — перегенерируем и берём чистый вариант
fake_ai.replies = ["Посмотрите, как на видео мы работаем спокойно и без спешки. " * 6, NEUTRAL]
captured.clear()
text = cp.build_post(client, rubric, vid_path, state=fresh_state(), client_id="t")
eq("упоминание видео -> перегенерация", len(captured), 2)
eq("в итоге чистый текст", "видео" in text.lower(), False)

# слишком короткий текст -> перегенерация
fake_ai.replies = ["Коротко.", NEUTRAL]
captured.clear()
text = cp.build_post(client, rubric, vid_path, state=fresh_state(), client_id="t")
eq("короткий текст -> перегенерация", (len(captured), text.startswith("Спокойный")), (2, True))

# ротация тем подписи: подряд разные
fake_ai.replies = [NEUTRAL, NEUTRAL, NEUTRAL]
captured.clear()
st = fresh_state()
for _ in range(3):
    cp.build_post(client, rubric, vid_path, state=st, client_id="t")
angles = [m[1]["content"].split("общий взгляд на работу — ")[1].split(".")[0] for m in captured]
eq("тема подписи меняется от поста к посту", len(set(angles)), 3)

# обычное фото по-прежнему идёт старым путём (vision)
cp.image_to_data_url = lambda p: "data:image/jpeg;base64,AAAA"
photo_called = []
fake_ai.replies = ["Коррекция формы. На снимке видна работа. " * 12]
captured.clear()
cp.build_post(client, rubric, make_jpeg("regular.jpg"), state=fresh_state(), client_id="t")
eq("фото: по-прежнему vision-запрос", isinstance(captured[0][1]["content"], list), True)


# ---------- отправка в Telegram ----------

posts = []


def fake_post_ok(url, **kw):
    posts.append((url.rsplit("/", 1)[-1], kw))
    return Response(payload={"result": {"message_id": 7}})


http.post = fake_post_ok
cp.video_meta = lambda p: {"width": 160, "height": 120, "duration": 1}

v = os.path.join(TMP, "send.mp4")
open(v, "wb").write(b"0" * 2048)
posts.clear()
cp.post_to_telegram("@ch", "Короткая подпись", v)
eq("видео уходит через sendVideo", [p[0] for p in posts], ["sendVideo"])
eq("подпись приклеена к видео", posts[0][1]["data"].get("caption"), "Короткая подпись")
eq("потоковое воспроизведение включено", posts[0][1]["data"].get("supports_streaming"), "true")
eq("размеры переданы", posts[0][1]["data"].get("width"), "160")
eq("временный файл убран", os.path.exists(v), False)

# длинная подпись: видео без подписи, потом текст
open(v, "wb").write(b"0" * 2048)
posts.clear()
cp.post_to_telegram("@ch", "я" * 1500, v)
eq("длинный текст: видео, затем сообщение", [p[0] for p in posts], ["sendVideo", "sendMessage"])
eq("видео ушло без подписи", "caption" in posts[0][1]["data"], False)

# sendVideo отклонён -> один повтор без размеров, потом ошибка (не текст без видео!)
open(v, "wb").write(b"0" * 2048)
posts.clear()


def fake_post_reject(url, **kw):
    posts.append((url.rsplit("/", 1)[-1], kw))
    return Response(status=400, body="Bad Request: wrong file")


http.post = fake_post_reject
raised = False
try:
    cp.post_to_telegram("@ch", "Подпись", v)
except Exception:
    raised = True
eq("отказ Telegram по видео -> ошибка публикации", raised, True)
eq("текст без видео не отправлялся", [p[0] for p in posts], ["sendVideo", "sendVideo"])
eq("повтор был без размеров", "width" in posts[1][1]["data"], False)
eq("временный файл убран и после ошибки", os.path.exists(v), False)

# видео тяжелее лимита не отправляется вовсе
http.post = fake_post_ok
open(v, "wb").write(b"0" * 2048)
saved = cp.MAX_VIDEO_MB
cp.MAX_VIDEO_MB = 0.0001
posts.clear()
raised = False
try:
    cp.post_to_telegram("@ch", "Подпись", v)
except Exception:
    raised = True
cp.MAX_VIDEO_MB = saved
eq("видео сверх лимита -> ошибка без отправки", (raised, posts), (True, []))

# фото: поведение прежнее
photo = make_jpeg("tg_photo.jpg")
posts.clear()
cp.post_to_telegram("@ch", "Подпись к фото", photo)
eq("фото уходит через sendPhoto", [p[0] for p in posts], ["sendPhoto"])

shutil.rmtree(TMP, ignore_errors=True)
sys.exit(c.finish())
