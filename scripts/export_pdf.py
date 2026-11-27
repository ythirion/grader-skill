#!/usr/bin/env python3
"""Imprime des rapports HTML en PDF (A4) avec un navigateur Chromium headless local.

Usage:
    python3 -I export_pdf.py <rendu>/correction/index.html [...] [--name rapport.pdf]

Pour chaque page, écrit <dossier de la page>/<name> (par défaut rapport.pdf), avec la
feuille d'impression de report.css. Rien n'est téléchargé : Chrome, Chromium, Edge ou Brave
installé localement (CHROME_PATH pour en imposer un).

Code de sortie : 0 tout est écrit, 1 au moins un échec, 2 aucun navigateur trouvé.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge", "brave-browser", "chrome",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str | None:
    for cand in [os.environ.get("CHROME_PATH"), *BROWSERS]:
        if not cand:
            continue
        path = cand if os.path.isabs(cand) else shutil.which(cand)
        if path and os.path.exists(path):
            return path
    return None


def print_pdf(browser: str, page: Path, pdf: Path) -> str | None:
    with tempfile.TemporaryDirectory() as profile:
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions",
               f"--user-data-dir={profile}", "--allow-file-access-from-files",
               "--run-all-compositor-stages-before-draw", "--virtual-time-budget=10000",
               "--no-pdf-header-footer", f"--print-to-pdf={pdf.resolve()}", page.resolve().as_uri()]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            return "headless browser timed out"
    if not pdf.exists() or pdf.stat().st_size == 0:
        return (proc.stderr.strip().splitlines() or ["PDF non écrit"])[-1]
    return None


def main() -> int:
    args = sys.argv[1:]
    name = "rapport.pdf"
    if "--name" in args:
        i = args.index("--name")
        name = args[i + 1]
        del args[i:i + 2]
    if not args:
        print(__doc__, file=sys.stderr)
        return 1
    browser = find_browser()
    if browser is None:
        print("Aucun navigateur Chromium headless trouvé (CHROME_PATH)", file=sys.stderr)
        return 2
    status = 0
    for a in args:
        page = Path(a)
        pdf = page.parent / name
        if pdf.exists():
            pdf.unlink()
        error = print_pdf(browser, page, pdf)
        if error:
            print(f"FAIL {page}: {error}", file=sys.stderr)
            status = 1
            continue
        pages = len(re.findall(rb"/Type\s*/Page(?!s)", pdf.read_bytes()))
        print(f"OK   {pdf} ({pages} pages)")
    return status


if __name__ == "__main__":
    sys.exit(main())
