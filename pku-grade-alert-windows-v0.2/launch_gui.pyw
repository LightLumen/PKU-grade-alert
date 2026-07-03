"""Start the GUI with diagnostics that remain available under pythonw.exe."""

from __future__ import annotations

import ctypes
import os
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
LOG_PATH = APP_DIR / "data" / "launcher.log"


def append_launcher_log(message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size >= 1_000_000:
            backup = LOG_PATH.with_name("launcher.1.log")
            if backup.exists():
                backup.unlink()
            LOG_PATH.replace(backup)
        with LOG_PATH.open("a", encoding="utf-8") as file:
            file.write(message.rstrip() + "\n")
    except OSError:
        return


def show_startup_error(error: BaseException) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"成绩提醒启动失败：{type(error).__name__}: {error}\n\n"
            f"详细信息已写入：\n{LOG_PATH}",
            "北大树洞成绩提醒",
            0x10,
        )
    except Exception:
        return


def main() -> int:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    append_launcher_log(
        f"[{timestamp}] bootstrap start | executable={sys.executable} | "
        f"prefix={sys.prefix} | base_prefix={sys.base_prefix} | "
        f"python={platform.python_version()} | cwd={os.getcwd()}"
    )
    try:
        from grade_alert_gui import main as gui_main

        return gui_main()
    except BaseException as error:
        append_launcher_log(
            f"[{timestamp}] startup failed\n{traceback.format_exc()}"
        )
        show_startup_error(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
