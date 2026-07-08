#!/usr/bin/env python3
"""Local Tkinter GUI for the Windows PKU Treehole grade watcher."""

from __future__ import annotations

import contextlib
import io
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Any, Callable

from environment_check import collect_environment, render_environment
from grade_alert import (
    APP_DIR,
    CONFIG_PATH,
    DIAGNOSTICS_PATH,
    GradeAlertError,
    LoginRequired,
    auto_login_treehole,
    browser_page,
    build_diagnostics,
    check_once,
    fetch_score_payload,
    init_config,
    load_config,
    normalize_courses,
    now_text,
    process_score_payload,
    send_serverchan,
    write_json,
)
from local_secrets import (
    SecretStorageError,
    delete_credentials,
    load_credentials,
    save_credentials,
)


CREDENTIALS_PATH = APP_DIR / "credentials.dat"
LOG_PATH = APP_DIR / "data" / "grade_alert.log"
LOG_LIMIT_BYTES = 1_000_000


class QueueWriter(io.TextIOBase):
    def __init__(
        self,
        messages: queue.Queue[str],
        log_path: Path | None = None,
    ) -> None:
        self.messages = messages
        self.log_path = log_path
        self.log_lock = threading.Lock()

    def _append_log(self, value: str) -> None:
        if self.log_path is None:
            return
        try:
            with self.log_lock:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                if (
                    self.log_path.exists()
                    and self.log_path.stat().st_size >= LOG_LIMIT_BYTES
                ):
                    backup = self.log_path.with_name(
                        f"{self.log_path.stem}.1{self.log_path.suffix}"
                    )
                    if backup.exists():
                        backup.unlink()
                    self.log_path.replace(backup)
                with self.log_path.open("a", encoding="utf-8") as file:
                    file.write(value)
        except OSError:
            # Logging must never stop grade monitoring.
            return

    def write(self, value: str) -> int:
        if value:
            self.messages.put(value)
            self._append_log(value)
        return len(value)

    def flush(self) -> None:
        return None


class CheckMark(tk.Frame):
    """Theme-independent checkbox that always renders a check mark."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str,
        variable: tk.BooleanVar,
        background: str,
        foreground: str,
        accent: str,
        border: str,
        command: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            master,
            background=background,
            cursor="hand2",
            takefocus=True,
            highlightthickness=0,
        )
        self.variable = variable
        self.command = command
        self.background_color = background
        self.accent_color = accent
        self.border_color = border

        self.canvas = tk.Canvas(
            self,
            width=18,
            height=18,
            background=background,
            highlightthickness=0,
        )
        self.canvas.pack(side="left")
        self.label = tk.Label(
            self,
            text=text,
            background=background,
            foreground=foreground,
            font=("Microsoft YaHei UI", 9),
            cursor="hand2",
        )
        self.label.pack(side="left", padx=(6, 0))

        for widget in (self, self.canvas, self.label):
            widget.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<FocusIn>", lambda _event: self._draw(focused=True))
        self.bind("<FocusOut>", lambda _event: self._draw(focused=False))
        self.variable.trace_add("write", self._variable_changed)
        self._draw()

    def _toggle(self, _event: tk.Event[Any] | None = None) -> str:
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()
        self.focus_set()
        return "break"

    def _variable_changed(self, *_args: Any) -> None:
        self._draw(focused=self.focus_get() is self)

    def _draw(self, focused: bool = False) -> None:
        self.canvas.delete("all")
        outline = self.accent_color if focused else self.border_color
        if self.variable.get():
            self.canvas.create_rectangle(
                2,
                2,
                16,
                16,
                fill=self.accent_color,
                outline=self.accent_color,
                width=1,
            )
            self.canvas.create_line(
                5,
                9,
                8,
                12,
                14,
                5,
                fill="#FFFFFF",
                width=2,
                capstyle="round",
                joinstyle="round",
            )
        else:
            self.canvas.create_rectangle(
                2,
                2,
                16,
                16,
                fill=self.background_color,
                outline=outline,
                width=2 if focused else 1,
            )


class GradeAlertApp:
    COLORS = {
        "background": "#F2F4F7",
        "surface": "#FFFFFF",
        "border": "#D7DDE5",
        "text": "#202936",
        "muted": "#667085",
        "red": "#8C1D2C",
        "red_hover": "#741521",
        "teal": "#0F766E",
        "teal_hover": "#0B5F59",
        "amber": "#B45309",
        "danger": "#B42318",
        "log_background": "#16202A",
        "log_foreground": "#DDE6EE",
    }

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("北大树洞成绩提醒")
        self.root.geometry("860x720")
        self.root.minsize(760, 650)
        self.root.configure(background=self.COLORS["background"])

        if not CONFIG_PATH.exists():
            init_config()
        self.config = load_config()
        self.messages: queue.Queue[str] = queue.Queue()
        self.writer = QueueWriter(self.messages, LOG_PATH)
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.active_task: str | None = None
        self.closing = False

        self.interval_var = tk.StringVar(
            value=str(max(1, int(self.config.get("poll_seconds", 900)) // 60))
        )
        self.include_score_var = tk.BooleanVar(
            value=bool(self.config.get("include_score_in_notification", True))
        )
        serverchan = self.config.get("serverchan", {})
        if not isinstance(serverchan, dict):
            serverchan = {}
        self.serverchan_enabled_var = tk.BooleanVar(
            value=bool(serverchan.get("enabled", False))
        )
        self.serverchan_sendkey_var = tk.StringVar(
            value=str(serverchan.get("sendkey", ""))
        )
        auto_login = self.config.get("auto_login", {})
        if not isinstance(auto_login, dict):
            auto_login = {}
        stored_credentials: dict[str, str] | None = None
        credential_load_error = ""
        try:
            stored_credentials = load_credentials(CREDENTIALS_PATH)
        except SecretStorageError as error:
            credential_load_error = str(error)
        self.auto_login_enabled_var = tk.BooleanVar(
            value=bool(auto_login.get("enabled", False))
        )
        self.trust_device_var = tk.BooleanVar(
            value=bool(auto_login.get("trust_device", True))
        )
        self.username_var = tk.StringVar(
            value=(stored_credentials or {}).get("username", "")
        )
        self.password_var = tk.StringVar(
            value=(stored_credentials or {}).get("password", "")
        )
        self.phone_token_var = tk.StringVar(value="")
        self.show_login_secrets_var = tk.BooleanVar(value=False)
        self.pending_phone_token = ""
        self.show_secrets_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="尚未运行")

        self._configure_style()
        self._build_ui()
        self.root.after(100, self._drain_messages)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.log("界面已就绪。第一次使用可手动登录，或在“自动登录”页完成本机设置。")
        self.log(f"运行日志保存在本机：{LOG_PATH}")
        if credential_load_error:
            self.log(f"本地登录信息无法读取：{credential_load_error}")

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        colors = self.COLORS
        base_font = ("Microsoft YaHei UI", 9)
        style.configure("App.TFrame", background=colors["background"])
        style.configure("Surface.TFrame", background=colors["surface"])
        style.configure("TNotebook", background=colors["background"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background="#E7EBF0",
            foreground=colors["muted"],
            font=base_font,
            padding=(14, 8),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", colors["surface"]), ("active", "#F7F8FA")],
            foreground=[("selected", colors["text"])],
        )
        style.configure(
            "SectionTitle.TLabel",
            background=colors["surface"],
            foreground=colors["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        style.configure(
            "Body.TLabel",
            background=colors["surface"],
            foreground=colors["text"],
            font=base_font,
        )
        style.configure(
            "Muted.TLabel",
            background=colors["surface"],
            foreground=colors["muted"],
            font=("Microsoft YaHei UI", 8),
        )
        style.configure(
            "Footer.TLabel",
            background=colors["background"],
            foreground=colors["muted"],
            font=("Microsoft YaHei UI", 8),
        )
        style.configure(
            "Primary.TButton",
            background=colors["red"],
            foreground="#FFFFFF",
            borderwidth=0,
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(14, 8),
        )
        style.map(
            "Primary.TButton",
            background=[("active", colors["red_hover"]), ("disabled", "#C7CDD4")],
            foreground=[("disabled", "#F8FAFC")],
        )
        style.configure(
            "Success.TButton",
            background=colors["teal"],
            foreground="#FFFFFF",
            borderwidth=0,
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(14, 8),
        )
        style.map(
            "Success.TButton",
            background=[("active", colors["teal_hover"]), ("disabled", "#C7CDD4")],
            foreground=[("disabled", "#F8FAFC")],
        )
        style.configure(
            "Secondary.TButton",
            background="#FFFFFF",
            foreground=colors["text"],
            bordercolor=colors["border"],
            borderwidth=1,
            font=base_font,
            padding=(12, 7),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#EEF1F4"), ("disabled", "#F3F4F6")],
            foreground=[("disabled", "#98A2B3")],
        )
        style.configure(
            "Danger.TButton",
            background="#FFFFFF",
            foreground=colors["danger"],
            bordercolor="#E8B4AE",
            borderwidth=1,
            font=base_font,
            padding=(12, 7),
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#FFF2F0"), ("disabled", "#F3F4F6")],
            foreground=[("disabled", "#98A2B3")],
        )
        style.configure(
            "App.TEntry",
            fieldbackground="#FBFCFD",
            foreground=colors["text"],
            bordercolor=colors["border"],
            padding=7,
        )
        style.configure(
            "App.TSpinbox",
            fieldbackground="#FBFCFD",
            foreground=colors["text"],
            bordercolor=colors["border"],
            arrowsize=14,
            padding=5,
        )

    def _build_ui(self) -> None:
        colors = self.COLORS
        header = tk.Frame(self.root, background=colors["red"], height=82)
        header.pack(fill="x")
        header.pack_propagate(False)

        heading = tk.Frame(header, background=colors["red"])
        heading.pack(side="left", padx=24, pady=10)
        tk.Label(
            heading,
            text="北大树洞成绩提醒",
            background=colors["red"],
            foreground="#FFFFFF",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            heading,
            text="本地查询 · 变化检测 · Server酱通知",
            background=colors["red"],
            foreground="#F1D7DB",
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(4, 0))

        status = tk.Frame(header, background=colors["red"])
        status.pack(side="right", padx=24)
        self.status_dot = tk.Canvas(
            status,
            width=12,
            height=12,
            background=colors["red"],
            highlightthickness=0,
        )
        self.status_dot.pack(side="left", padx=(0, 7))
        self.status_dot_id = self.status_dot.create_oval(2, 2, 10, 10, fill="#D0D5DD", outline="")
        self.status_label = tk.Label(
            status,
            textvariable=self.status_var,
            background=colors["red"],
            foreground="#FFFFFF",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.status_label.pack(side="left")

        outer = ttk.Frame(self.root, style="App.TFrame", padding=(22, 12, 22, 10))
        outer.pack(fill="both", expand=True)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="x")

        settings = ttk.Frame(notebook, style="Surface.TFrame", padding=18)
        notebook.add(settings, text="监控与推送")
        settings.columnconfigure(2, weight=1)
        ttk.Label(settings, text="监控设置", style="SectionTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w"
        )
        ttk.Label(
            settings,
            text="登录信息和 SendKey 仅保存在当前电脑。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(3, 14))

        ttk.Label(settings, text="查询间隔", style="Body.TLabel").grid(
            row=2, column=0, sticky="w"
        )
        interval_box = ttk.Frame(settings, style="Surface.TFrame")
        interval_box.grid(row=2, column=1, sticky="w", padx=(12, 28))
        ttk.Spinbox(
            interval_box,
            from_=1,
            to=1440,
            width=7,
            textvariable=self.interval_var,
            style="App.TSpinbox",
        ).pack(side="left")
        ttk.Label(interval_box, text="分钟", style="Muted.TLabel").pack(
            side="left", padx=(7, 0)
        )
        CheckMark(
            settings,
            text="通知中包含具体成绩",
            variable=self.include_score_var,
            background=colors["surface"],
            foreground=colors["text"],
            accent=colors["teal"],
            border=colors["border"],
        ).grid(row=2, column=2, columnspan=2, sticky="w")

        ttk.Separator(settings).grid(
            row=3, column=0, columnspan=4, sticky="ew", pady=14
        )

        ttk.Label(settings, text="Server酱", style="Body.TLabel").grid(
            row=4, column=0, sticky="w"
        )
        CheckMark(
            settings,
            text="启用",
            variable=self.serverchan_enabled_var,
            background=colors["surface"],
            foreground=colors["text"],
            accent=colors["teal"],
            border=colors["border"],
        ).grid(row=4, column=1, sticky="w", padx=(12, 28))
        self.serverchan_entry = ttk.Entry(
            settings,
            textvariable=self.serverchan_sendkey_var,
            show="*",
            style="App.TEntry",
        )
        self.serverchan_entry.grid(row=4, column=2, sticky="ew")
        CheckMark(
            settings,
            text="显示 SendKey",
            variable=self.show_secrets_var,
            command=self._toggle_secrets,
            background=colors["surface"],
            foreground=colors["text"],
            accent=colors["teal"],
            border=colors["border"],
        ).grid(row=4, column=3, sticky="w", padx=(10, 0))
        ttk.Label(
            settings,
            text="请粘贴 SendKey，不要粘贴完整请求网址。",
            style="Muted.TLabel",
        ).grid(row=5, column=2, sticky="w", pady=(5, 0))

        settings_actions = ttk.Frame(settings, style="Surface.TFrame")
        settings_actions.grid(row=6, column=2, columnspan=2, sticky="w", pady=(14, 0))
        ttk.Button(
            settings_actions,
            text="保存设置",
            style="Secondary.TButton",
            command=self.save_settings,
        ).pack(side="left")
        ttk.Button(
            settings_actions,
            text="测试 Server酱",
            style="Secondary.TButton",
            command=self.test_push,
        ).pack(side="left", padx=(8, 0))

        login_settings = ttk.Frame(notebook, style="Surface.TFrame", padding=18)
        notebook.add(login_settings, text="自动登录（可选）")
        login_settings.columnconfigure(1, weight=1)
        login_settings.columnconfigure(3, weight=1)
        ttk.Label(login_settings, text="自动登录", style="SectionTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w"
        )
        ttk.Label(
            login_settings,
            text="凭据经 Windows DPAPI 加密且只存本机；登录时仅提交给北大官方 IAAA。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(3, 12))
        CheckMark(
            login_settings,
            text="登录失效时使用本地账号密码自动恢复",
            variable=self.auto_login_enabled_var,
            background=colors["surface"],
            foreground=colors["text"],
            accent=colors["teal"],
            border=colors["border"],
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(0, 12))

        ttk.Label(login_settings, text="账号", style="Body.TLabel").grid(
            row=3, column=0, sticky="w"
        )
        self.username_entry = ttk.Entry(
            login_settings,
            textvariable=self.username_var,
            style="App.TEntry",
        )
        self.username_entry.grid(row=3, column=1, sticky="ew", padx=(10, 22))
        ttk.Label(login_settings, text="密码", style="Body.TLabel").grid(
            row=3, column=2, sticky="w"
        )
        self.password_entry = ttk.Entry(
            login_settings,
            textvariable=self.password_var,
            show="*",
            style="App.TEntry",
        )
        self.password_entry.grid(row=3, column=3, sticky="ew", padx=(10, 0))

        ttk.Label(login_settings, text="手机令牌", style="Body.TLabel").grid(
            row=4, column=0, sticky="w", pady=(10, 0)
        )
        self.phone_token_entry = ttk.Entry(
            login_settings,
            textvariable=self.phone_token_var,
            show="*",
            style="App.TEntry",
        )
        self.phone_token_entry.grid(
            row=4, column=1, sticky="ew", padx=(10, 22), pady=(10, 0)
        )
        CheckMark(
            login_settings,
            text="同设备登录免二次验证",
            variable=self.trust_device_var,
            background=colors["surface"],
            foreground=colors["text"],
            accent=colors["teal"],
            border=colors["border"],
        ).grid(row=4, column=2, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(
            login_settings,
            text="手机令牌是动态码，仅用于下一次登录尝试，使用后清空且不会写入磁盘。",
            style="Muted.TLabel",
        ).grid(row=5, column=1, columnspan=3, sticky="w", pady=(5, 0))

        login_actions = ttk.Frame(login_settings, style="Surface.TFrame")
        login_actions.grid(row=6, column=0, columnspan=4, sticky="w", pady=(12, 0))
        CheckMark(
            login_actions,
            text="显示密码和令牌",
            variable=self.show_login_secrets_var,
            command=self._toggle_login_secrets,
            background=colors["surface"],
            foreground=colors["text"],
            accent=colors["teal"],
            border=colors["border"],
        ).pack(side="left")
        ttk.Button(
            login_actions,
            text="保存本地登录设置",
            style="Secondary.TButton",
            command=self.save_settings,
        ).pack(side="left", padx=(16, 0))
        ttk.Button(
            login_actions,
            text="清除登录信息",
            style="Danger.TButton",
            command=self.clear_login_credentials,
        ).pack(side="left", padx=(8, 0))

        actions = ttk.Frame(outer, style="App.TFrame")
        actions.pack(fill="x", pady=(10, 10))
        self.login_button = ttk.Button(
            actions,
            text="首次登录 / 重新验证",
            style="Secondary.TButton",
            command=self.start_login,
            width=18,
        )
        self.login_button.pack(side="left")
        self.check_button = ttk.Button(
            actions,
            text="立即查询",
            style="Secondary.TButton",
            command=self.start_check,
            width=10,
        )
        self.check_button.pack(side="left", padx=(8, 0))
        self.start_button = ttk.Button(
            actions,
            text="开始监控",
            style="Success.TButton",
            command=self.start_monitor,
            width=11,
        )
        self.start_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(
            actions,
            text="停止监控",
            style="Danger.TButton",
            command=self.stop_monitor,
            state="disabled",
            width=10,
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        self.diagnose_button = ttk.Button(
            actions,
            text="生成诊断",
            style="Secondary.TButton",
            command=self.start_diagnose,
            width=10,
        )
        self.diagnose_button.pack(side="right")
        self.environment_button = ttk.Button(
            actions,
            text="环境检测",
            style="Secondary.TButton",
            command=self.check_environment,
            width=10,
        )
        self.environment_button.pack(side="right", padx=(0, 8))

        log_panel = ttk.Frame(outer, style="Surface.TFrame", padding=(16, 13, 16, 16))
        log_panel.pack(fill="both", expand=True)
        ttk.Label(log_panel, text="运行记录", style="SectionTitle.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            log_panel,
            text="这里只显示运行状态，不会输出密码、Cookie 或 SendKey。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(3, 9))
        self.log_text = scrolledtext.ScrolledText(
            log_panel,
            height=7,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
            background=colors["log_background"],
            foreground=colors["log_foreground"],
            insertbackground="#FFFFFF",
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=10,
        )
        self.log_text.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="显示器关闭或锁屏不会影响监控；电脑睡眠、休眠或关机会暂停。",
            style="Footer.TLabel",
        ).pack(anchor="w", pady=(10, 0))

    def _toggle_secrets(self) -> None:
        show = "" if self.show_secrets_var.get() else "*"
        self.serverchan_entry.configure(show=show)

    def _toggle_login_secrets(self) -> None:
        show = "" if self.show_login_secrets_var.get() else "*"
        self.password_entry.configure(show=show)
        self.phone_token_entry.configure(show=show)

    def log(self, message: str) -> None:
        self.writer.write(f"[{now_text()}] {message}\n")

    def _drain_messages(self) -> None:
        chunks: list[str] = []
        while True:
            try:
                chunks.append(self.messages.get_nowait())
            except queue.Empty:
                break
        if chunks:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "".join(chunks))
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        if not self.closing:
            self.root.after(100, self._drain_messages)

    def save_settings(self, quiet: bool = False) -> dict[str, Any] | None:
        try:
            minutes = int(self.interval_var.get().strip())
        except ValueError:
            if not quiet:
                messagebox.showerror("设置有误", "查询间隔必须是整数分钟。")
            return None
        if minutes < 1:
            if not quiet:
                messagebox.showerror("设置有误", "查询间隔不能少于 1 分钟。")
            return None
        sendkey = self.serverchan_sendkey_var.get().strip()
        if self.serverchan_enabled_var.get() and not sendkey:
            if not quiet:
                messagebox.showerror("设置有误", "启用 Server酱时必须填写 SendKey。")
            return None
        username = self.username_var.get().strip()
        password = self.password_var.get()
        auto_login_enabled = self.auto_login_enabled_var.get()
        if auto_login_enabled and (not username or not password):
            if not quiet:
                messagebox.showerror("设置有误", "启用自动登录时，账号和密码必须填写。")
            return None
        if auto_login_enabled:
            try:
                save_credentials(CREDENTIALS_PATH, username, password)
            except SecretStorageError as error:
                if not quiet:
                    messagebox.showerror("无法保存登录信息", str(error))
                return None
        self.pending_phone_token = self.phone_token_var.get().strip()

        config = dict(self.config)
        config["poll_seconds"] = minutes * 60
        config["include_score_in_notification"] = self.include_score_var.get()
        config["serverchan"] = {
            "enabled": self.serverchan_enabled_var.get(),
            "sendkey": sendkey,
        }
        config["auto_login"] = {
            "enabled": auto_login_enabled,
            "trust_device": self.trust_device_var.get(),
        }
        write_json(CONFIG_PATH, config)
        self.config = config
        if not quiet:
            self.log("设置已保存到本机。")
        return config

    def clear_login_credentials(self) -> None:
        if not messagebox.askyesno(
            "清除登录信息",
            "确定删除本机加密保存的账号和密码吗？浏览器登录会话不会在此步骤删除。",
        ):
            return
        try:
            delete_credentials(CREDENTIALS_PATH)
        except OSError as error:
            messagebox.showerror("删除失败", str(error))
            return
        self.username_var.set("")
        self.password_var.set("")
        self.phone_token_var.set("")
        self.pending_phone_token = ""
        self.auto_login_enabled_var.set(False)
        config = dict(self.config)
        config["auto_login"] = {
            "enabled": False,
            "trust_device": self.trust_device_var.get(),
        }
        write_json(CONFIG_PATH, config)
        self.config = config
        self.log("本机加密登录信息已清除。")

    def check_environment(self) -> None:
        self.log("开始本机环境检测。")
        for line in render_environment(collect_environment()).splitlines():
            self.log(line)

    def _set_status(self, value: str) -> None:
        def update() -> None:
            self.status_var.set(value)
            if value == "监控中":
                color = self.COLORS["teal"]
            elif value in {"需要登录", "运行失败"}:
                color = self.COLORS["danger"]
            elif value in {"正在查询", "等待登录", "测试推送", "生成诊断", "正在停止"}:
                color = self.COLORS["amber"]
            else:
                color = "#D0D5DD"
            self.status_dot.itemconfigure(self.status_dot_id, fill=color)

        self.root.after(0, update)

    def _set_busy(self, busy: bool, monitoring: bool = False) -> None:
        def update() -> None:
            normal = "disabled" if busy else "normal"
            self.login_button.configure(state=normal)
            self.check_button.configure(state=normal)
            self.start_button.configure(state=normal)
            self.diagnose_button.configure(state=normal)
            self.environment_button.configure(state=normal)
            self.stop_button.configure(state="normal" if monitoring else "disabled")

        self.root.after(0, update)

    def _run_worker(
        self,
        task_name: str,
        operation: Callable[[], None],
        monitoring: bool = False,
    ) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("任务正在运行", f"请先等待或停止当前任务：{self.active_task}")
            return
        self.stop_event.clear()
        self.active_task = task_name
        self._set_busy(True, monitoring=monitoring)
        self._set_status(task_name)

        def runner() -> None:
            try:
                with contextlib.redirect_stdout(self.writer), contextlib.redirect_stderr(self.writer):
                    operation()
            except LoginRequired as error:
                self.log(f"需要重新登录：{error}")
                self._set_status("需要登录")
            except GradeAlertError as error:
                self.log(f"任务失败：{error}")
                self._set_status("运行失败")
            except Exception as error:
                self.log(f"发生未预期错误：{type(error).__name__}: {error}")
                self._set_status("运行失败")
            else:
                if not monitoring or self.stop_event.is_set():
                    self._set_status("已停止" if monitoring else "就绪")
            finally:
                self.active_task = None
                self._set_busy(False)

        self.worker = threading.Thread(target=runner, name=task_name, daemon=True)
        self.worker.start()

    def _clear_phone_token(self) -> None:
        self.pending_phone_token = ""
        self.root.after(0, self.phone_token_var.set, "")

    def _recover_login(self, page: Any, config: dict[str, Any]) -> dict[str, Any]:
        auto_login = config.get("auto_login", {})
        if not isinstance(auto_login, dict) or not auto_login.get("enabled", False):
            raise LoginRequired("登录已失效，且未启用自动登录")
        try:
            credentials = load_credentials(CREDENTIALS_PATH)
        except SecretStorageError as error:
            raise LoginRequired(f"本地登录信息无法读取：{error}") from error
        if not credentials:
            raise LoginRequired("未找到本机加密保存的账号密码")

        phone_token = self.pending_phone_token
        self.log("检测到登录失效，正在使用本机加密凭据自动登录。")
        try:
            payload = auto_login_treehole(
                page,
                credentials["username"],
                credentials["password"],
                phone_token=phone_token,
                trust_device=bool(auto_login.get("trust_device", True)),
            )
        finally:
            self._clear_phone_token()
        courses = normalize_courses(payload)
        self.log(f"自动登录成功，接口返回 {len(courses)} 门课程。")
        return payload

    def _process_recovered_payload(
        self,
        page: Any,
        payload: dict[str, Any],
        config: dict[str, Any],
        show_courses: bool = False,
    ) -> None:
        try:
            process_score_payload(payload, config, show_courses=show_courses)
        except LoginRequired as error:
            self.log(f"自动登录返回的访问参数已失效，等待 3 秒后重新读取成绩：{error}")
            page.wait_for_timeout(3000)
            fresh_payload = fetch_score_payload(page)
            process_score_payload(fresh_payload, config, show_courses=show_courses)

    def _notify_login_required(self, config: dict[str, Any], error: Exception) -> None:
        serverchan = config.get("serverchan", {})
        if not isinstance(serverchan, dict) or not serverchan.get("enabled", False):
            return
        try:
            send_serverchan(
                config,
                "成绩提醒需要重新登录",
                f"自动登录未完成：{error}\n请打开本机 GUI 完成验证。",
            )
        except GradeAlertError as push_error:
            self.log(f"登录失效通知发送失败：{push_error}")

    def start_login(self) -> None:
        config = self.save_settings(quiet=True)
        if config is None:
            messagebox.showerror("设置有误", "请先检查并保存设置。")
            return

        def operation() -> None:
            self.log("正在打开专用 Edge 窗口。请完成北大登录和可能出现的手机令牌验证。")
            self.log("登录后进入成绩页并点击一次“查询”；验证成功后浏览器会自动关闭。")
            with browser_page(config, headed=True) as page:
                try:
                    payload = fetch_score_payload(page)
                    courses = normalize_courses(payload)
                    self.log(f"当前登录仍然有效，接口返回 {len(courses)} 门课程。")
                    return
                except GradeAlertError:
                    pass
                auto_login = config.get("auto_login", {})
                if isinstance(auto_login, dict) and auto_login.get("enabled", False):
                    try:
                        self._recover_login(page, config)
                        return
                    except GradeAlertError as error:
                        self.log(f"自动登录未完成：{error}。请在打开的浏览器中手动完成验证。")
                deadline = time.monotonic() + 15 * 60
                last_message = ""
                while not self.stop_event.is_set() and time.monotonic() < deadline:
                    try:
                        payload = fetch_score_payload(page)
                        courses = normalize_courses(payload)
                    except GradeAlertError as error:
                        message = str(error)
                        if message != last_message:
                            self.log(message)
                            last_message = message
                        self.stop_event.wait(5)
                        continue
                    self.log(f"登录验证成功，接口返回 {len(courses)} 门课程。")
                    return
                if self.stop_event.is_set():
                    self.log("登录验证已取消。")
                    return
                raise GradeAlertError("等待登录超过 15 分钟，请重新尝试")

        self._run_worker("等待登录", operation)

    def start_check(self) -> None:
        config = self.save_settings(quiet=True)
        if config is None:
            messagebox.showerror("设置有误", "请先检查并保存设置。")
            return

        def operation() -> None:
            self.log("开始单次查询。")
            with browser_page(config, headed=False) as page:
                try:
                    check_once(page, config, show_courses=True)
                except LoginRequired:
                    payload = self._recover_login(page, config)
                    self._process_recovered_payload(
                        page,
                        payload,
                        config,
                        show_courses=True,
                    )

        self._run_worker("正在查询", operation)

    def start_monitor(self) -> None:
        initial_config = self.save_settings(quiet=True)
        if initial_config is None:
            messagebox.showerror("设置有误", "请先检查并保存设置。")
            return
        initial_interval = int(initial_config["poll_seconds"])

        def operation() -> None:
            self.log(f"持续监控已开始，每 {initial_interval // 60} 分钟查询一次。")
            previous_cycle_started: float | None = None
            with browser_page(initial_config, headed=False) as page:
                while not self.stop_event.is_set():
                    current_config = self.config
                    interval = int(current_config.get("poll_seconds", initial_interval))
                    cycle_started = time.time()
                    if previous_cycle_started is not None:
                        elapsed = cycle_started - previous_cycle_started
                        if elapsed > interval + 120:
                            self.log(
                                f"检测到约 {round(elapsed / 60)} 分钟未执行查询，"
                                "电脑可能刚从睡眠或休眠恢复；现已继续监控。"
                            )
                    previous_cycle_started = cycle_started
                    try:
                        check_once(page, current_config)
                    except LoginRequired:
                        try:
                            payload = self._recover_login(page, current_config)
                            self._process_recovered_payload(page, payload, current_config)
                        except GradeAlertError as error:
                            self._notify_login_required(current_config, error)
                            raise
                    except GradeAlertError as error:
                        self.log(f"本轮查询失败：{error}")
                    if self.stop_event.wait(interval):
                        break
            self.log("持续监控已停止。")

        self._run_worker("监控中", operation, monitoring=True)

    def stop_monitor(self) -> None:
        self.stop_event.set()
        self._set_status("正在停止")
        self.log("正在停止当前任务，请稍候。")

    def start_diagnose(self) -> None:
        config = self.save_settings(quiet=True)
        if config is None:
            messagebox.showerror("设置有误", "请先检查并保存设置。")
            return

        def operation() -> None:
            with browser_page(config, headed=False) as page:
                payload = fetch_score_payload(page)
            write_json(DIAGNOSTICS_PATH, build_diagnostics(payload))
            self.log(f"脱敏诊断已生成：{DIAGNOSTICS_PATH}")

        self._run_worker("生成诊断", operation)

    def test_push(self) -> None:
        config = self.save_settings(quiet=True)
        if config is None:
            messagebox.showerror("设置有误", "请填写有效的 Server酱配置。")
            return
        serverchan_enabled = config.get("serverchan", {}).get("enabled", False)
        if not serverchan_enabled:
            messagebox.showinfo("尚未启用", "请勾选“启用 Server酱”并填写 SendKey。")
            return

        def operation() -> None:
            send_serverchan(
                config,
                "成绩提醒测试",
                "这是一条来自本机成绩提醒的测试消息。",
            )
            self.log("Server酱测试请求已发送。")

        self._run_worker("测试推送", operation)

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("退出程序", "当前任务仍在运行。确定停止并退出吗？"):
                return
            self.stop_event.set()
        self.closing = True
        self.root.destroy()


def main() -> int:
    if "--smoke-test" in sys.argv:
        root = tk.Tk()
        root.withdraw()
        app = GradeAlertApp(root)
        root.update_idletasks()
        print(
            "GUI init OK: "
            f"title={root.title()!r}, size={root.winfo_reqwidth()}x{root.winfo_reqheight()}"
        )
        app.closing = True
        root.destroy()
        return 0
    root = tk.Tk()
    GradeAlertApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
