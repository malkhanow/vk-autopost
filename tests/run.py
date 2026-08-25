#!/usr/bin/env python3
"""Все проверки автопостинга: без сети, без ключей, без Яндекс.Диска.

    python3 tests/run.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SUITES = [
    ("test_images.py", "картинки из интернета: форматы и повторы"),
    ("test_holiday_photos.py", "праздничные фото: сначала Яндекс.Диск"),
]

failed = 0
for filename, title in SUITES:
    print(f"\n──── {title} ({filename})")
    if subprocess.run([sys.executable, os.path.join(HERE, filename)]).returncode:
        failed += 1

print(f"\nПровалено наборов: {failed}" if failed else "\nВсе наборы прошли")
sys.exit(1 if failed else 0)
