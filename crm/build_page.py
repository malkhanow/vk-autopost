#!/usr/bin/env python3
"""Собирает обычную страницу index.html из холста «Клиенты.dc.html».

Холст остаётся источником правды: правим его, потом

    python3 crm/build_page.py

Из него берётся разметка, стили и логика; вместо support.js в страницу
встраивается page_runtime.js — он и разбирает {{ … }}, sc-for и sc-if.
Результат — один самодостаточный файл, который работает на GitHub Pages.
"""

import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "Клиенты.dc.html")
RUNTIME = os.path.join(HERE, "page_runtime.js")
TARGET = os.path.join(HERE, "index.html")

TITLE = "SMM-сервис · CRM"


def slice_between(text, start_tag, end_tag, what):
    start = text.find(start_tag)
    end = text.find(end_tag)
    if start < 0 or end < 0 or end < start:
        sys.exit(f"Не нашёл {what} в {os.path.basename(SOURCE)}")
    return text[start + len(start_tag):end].strip("\n")


def main():
    with open(SOURCE, encoding="utf-8") as f:
        source = f.read()
    with open(RUNTIME, encoding="utf-8") as f:
        runtime = f.read().rstrip("\n")

    head = slice_between(source, "<helmet>", "</helmet>", "блок <helmet>")
    markup = slice_between(source, "</helmet>", "</x-dc>", "разметку внутри <x-dc>")

    script = re.search(r'<script type="text/x-dc"[^>]*>\n(.*)\n</script>', source, re.S)
    if not script:
        sys.exit("Не нашёл блок логики <script type=\"text/x-dc\">")
    logic = script.group(1)

    # значения по умолчанию для props берём оттуда же, где их держит холст
    props_attr = re.search(r'data-props="([^"]*)"', source)
    props = {}
    if props_attr:
        for name, spec in json.loads(html.unescape(props_attr.group(1))).items():
            if "default" in spec:
                props[name] = spec["default"]

    page = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITLE}</title>
<!--
  Файл собран из «Клиенты.dc.html» скриптом crm/build_page.py — руками не правьте.
  Меняйте холст и пересобирайте:  python3 crm/build_page.py
-->
{head}
</head>
<body>

<div id="crm-root"></div>

<template id="crm-template">
{markup}
</template>

<script>
{runtime}
</script>

<script>
{logic}

mountPage(
  document.getElementById('crm-template').content,
  Component,
  {json.dumps(props, ensure_ascii=False, indent=2)},
  document.getElementById('crm-root')
);
</script>

</body>
</html>
"""

    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(page)

    print(f"Готово: {os.path.relpath(TARGET)} — {len(page) // 1024} КБ, "
          f"props: {', '.join(props) or 'нет'}")


if __name__ == "__main__":
    main()
