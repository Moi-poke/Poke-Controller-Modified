#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""エラー報告の小窓（tkinter）。

文面の組み立ては core.error_report が持ち、ここでは見せる・写す・
開くだけにする。設定の中身や webhook は扱わない（渡さない・読まない）。
未捕捉の例外は install_hooks で拾い、GUI スレッドへ戻してから開く。
"""

from __future__ import annotations

import platform
import sys
import threading
import tkinter as tk
import tkinter.ttk as ttk
import traceback
from collections.abc import Callable
from typing import Any

import WindowUtils
from core import error_report
from loguru import logger

# 二重に開かないための参照。破棄済みは掴み直す。
_open_win: Any | None = None


def _alive(window: Any) -> bool:
    """窓が生きているか。破棄済み参照の再利用を防ぐ。"""
    try:
        return window is not None and bool(window.winfo_exists())
    except Exception:
        return False


def collect_report(
    app_version: str,
    os_name: str,
    python_version: str,
    profile: str,
    transport: str,
    error_text: str,
    app_dir: str | None = None,
) -> tuple[str, str]:
    """報告文とログ置き場を返す。落ちない。"""
    base = app_dir if app_dir is not None else WindowUtils.APP_DIR
    log_dir = error_report.resolve_log_dir(base)
    log_path = error_report.latest_log_file(log_dir)
    tail = error_report.read_tail_lines(log_path) if log_path else []
    text = error_report.build_report(
        app_version=app_version,
        os_name=os_name,
        python_version=python_version,
        profile=profile,
        transport=transport,
        error_text=error_text,
        log_path=log_path,
        log_tail=tail,
    )
    return (text, log_dir)


def open_error_report(
    master: tk.Misc,
    app_version: str,
    os_name: str,
    python_version: str,
    profile: str,
    transport: str,
    error_text: str = "",
    app_dir: str | None = None,
) -> None:
    """報告の小窓を開く。二重に呼ばれたら手前へ出すだけにする。"""
    global _open_win
    if _alive(_open_win):
        try:
            existing = _open_win
            assert existing is not None
            existing.focus_force()
        except Exception:
            pass
        return
    _open_win = None

    try:
        text, log_dir = collect_report(
            app_version,
            os_name,
            python_version,
            profile,
            transport,
            error_text,
            app_dir,
        )
    except Exception:
        logger.error(traceback.format_exc())
        return

    win = tk.Toplevel(master)
    win.title("エラー報告をコピー")
    win.resizable(True, True)

    body = ttk.Frame(win, padding=8)
    body.pack(fill=tk.BOTH, expand=True)

    hint = ttk.Label(
        body,
        text="下の文面をコピーして Issue に貼ってください。"
        "ログファイル本体（log/ 直下）も添えると原因が追いやすくなります。",
        wraplength=560,
        justify="left",
    )
    hint.pack(fill=tk.X, pady=(0, 6))

    area = tk.Text(body, height=22, width=80, wrap="none")
    area.pack(fill=tk.BOTH, expand=True)
    area.insert("1.0", text)
    area.configure(state="disabled")

    bar = ttk.Frame(body)
    bar.pack(fill=tk.X, pady=(6, 0))

    def on_copy() -> None:
        try:
            win.clipboard_clear()
            win.clipboard_append(area.get("1.0", "end-1c"))
        except tk.TclError:
            pass

    def on_open_dir() -> None:
        WindowUtils.openDirectory(log_dir, platform.system())

    ttk.Button(bar, text="クリップボードにコピー", command=on_copy).pack(side=tk.LEFT)
    ttk.Button(bar, text="ログフォルダを開く", command=on_open_dir).pack(
        side=tk.LEFT, padx=(6, 0)
    )
    ttk.Button(bar, text="閉じる", command=win.destroy).pack(side=tk.RIGHT)

    def on_close() -> None:
        global _open_win
        _open_win = None
        try:
            if win.winfo_exists():
                win.destroy()
        except Exception:
            pass

    win.protocol("WM_DELETE_WINDOW", on_close)
    _open_win = win


def install_hooks(root: tk.Misc, provider: Callable[[], dict[str, str]]) -> None:
    """未捕捉の例外を報告窓へ回す。登録は何度でも安全にする。

    provider は app_version / os_name / python_version / profile /
    transport を返す。現値は開く直前に引く（古い参照を掴まないため）。
    ワーカー内の例外は従来どおり各所が握ってファイルへ書くため、
    ここでは未捕捉（GUI スレッド等）だけを扱う。
    """

    def show_later(error_text: str) -> None:
        try:
            info = provider()
        except Exception:
            logger.error(traceback.format_exc())
            return

        def show() -> None:
            try:
                open_error_report(
                    root,
                    app_version=info.get("app_version", ""),
                    os_name=info.get("os_name", platform.system()),
                    python_version=info.get(
                        "python_version", platform.python_version()
                    ),
                    profile=info.get("profile", ""),
                    transport=info.get("transport", ""),
                    error_text=error_text,
                )
            except Exception:
                logger.error(traceback.format_exc())

        try:
            root.after(0, show)
        except Exception:
            logger.error(traceback.format_exc())

    def handle_sys(exc_type: Any, exc_value: Any, exc_tb: Any) -> None:
        if exc_type is KeyboardInterrupt:
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            logger.critical(text)
        except Exception:
            pass
        show_later(text)

    def handle_thread(args: Any) -> None:
        exc_type = getattr(args, "exc_type", None)
        exc_value = getattr(args, "exc_value", None)
        exc_tb = getattr(args, "exc_traceback", None)
        if exc_type is None and exc_value is None:
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            logger.critical(text)
        except Exception:
            pass
        # tkinter を触るのは GUI スレッドだけにする。
        if threading.current_thread() is threading.main_thread():
            show_later(text)
            return
        try:
            root.after(0, lambda: show_later(text))
        except Exception:
            logger.error(traceback.format_exc())

    sys.excepthook = handle_sys
    threading.excepthook = handle_thread
