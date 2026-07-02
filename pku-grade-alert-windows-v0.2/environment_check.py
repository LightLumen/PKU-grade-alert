"""Local environment diagnostics used by the launcher and GUI."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
EDGE_PATHS = (
    Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
    / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
    / "Microsoft/Edge/Application/msedge.exe",
)


def collect_environment() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, required: bool = True) -> None:
        checks.append(
            {"name": name, "ok": bool(ok), "detail": detail, "required": required}
        )

    add("Windows", os.name == "nt", platform.platform())
    version_ok = sys.version_info >= (3, 10)
    add("Python", version_ok, platform.python_version())
    edge = next((path for path in EDGE_PATHS if path.exists()), None)
    add("Microsoft Edge", edge is not None, str(edge) if edge else "not found")
    playwright_found = importlib.util.find_spec("playwright") is not None
    add("Playwright", playwright_found, "installed" if playwright_found else "not installed")

    config_path = APP_DIR / "config.local.json"
    config_ok = True
    config_detail = "will be created on first launch"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config_ok = isinstance(config, dict)
            config_detail = "valid local configuration" if config_ok else "invalid JSON root"
        except (OSError, json.JSONDecodeError) as error:
            config_ok = False
            config_detail = f"invalid: {error}"
    add("Local config", config_ok, config_detail)

    free_gb = shutil.disk_usage(APP_DIR).free / (1024**3)
    add("Free disk", free_gb >= 0.5, f"{free_gb:.1f} GB available")
    add(
        "Browser profile",
        True,
        "created" if (APP_DIR / ".browser-profile").exists() else "not created yet",
        required=False,
    )
    add(
        "Encrypted credentials",
        True,
        "saved locally" if (APP_DIR / "credentials.dat").exists() else "not enabled",
        required=False,
    )
    return checks


def render_environment(checks: list[dict[str, Any]]) -> str:
    lines = []
    for item in checks:
        marker = "OK" if item["ok"] else "FAIL"
        lines.append(f"[{marker}] {item['name']}: {item['detail']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Grade Alert local environment")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    checks = collect_environment()
    print(render_environment(checks))
    failed = any(item["required"] and not item["ok"] for item in checks)
    return 1 if args.strict and failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
