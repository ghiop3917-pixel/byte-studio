"""Поиск локального браузера для необязательных smoke-проверок."""

import os
import shutil
import sys
import unittest
from pathlib import Path


def find_browser():
    explicit = os.environ.get("CHROME_PATH")
    if explicit:
        path = Path(os.path.expandvars(explicit)).expanduser()
        if path.is_file():
            return str(path)
    for name in ("chrome", "chromium", "google-chrome", "msedge"):
        path = shutil.which(name)
        if path:
            return path
    if sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if not root:
                continue
            for suffix in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"):
                path = Path(root) / suffix
                if path.is_file():
                    return str(path)
    return None


def require_browser():
    path = find_browser()
    if path is None:
        raise unittest.SkipTest("Браузер не найден: укажите CHROME_PATH или установите Chrome/Chromium/Edge в PATH.")
    return path
