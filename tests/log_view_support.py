"""ログ欄 E2E の支え（test_log_view_e2e / test_log_follow_e2e が共有）.

LogPanelMixin を混ぜた最小の宿主を本物の Tk 上に組み立てる。Window 全体を
起こすとカメラ・シリアル・音声まで動くため、ログ欄が触る属性だけを持たせる。
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "SerialController"))


def reset_queues() -> Any:
    """キューを作り直した LogPane を返す（前のシナリオの残りを持ち込まない）。"""
    import LogPane

    LogPane.text_queue = LogPane.DropOldestQueue()
    LogPane.sub_log_queue = LogPane.ResultForwardQueue()
    LogPane.input_log_queue = LogPane.DropOldestQueue()
    return LogPane


def make_host(root: tk.Tk) -> Any:
    """ログ欄を組み立てた宿主を返す。"""
    from ui.log_panel import LogPanelMixin

    class _Host(LogPanelMixin):
        pass

    host: Any = _Host()
    host.root = root
    host.frame_1 = tk.Frame(root)
    host.frame_1.pack(fill="both", expand=True)
    host.settings = SimpleNamespace(
        log_sash_ratio=tk.DoubleVar(master=root, value=0.6),
        input_log_enabled=tk.BooleanVar(master=root, value=True),
        log_show_time=tk.BooleanVar(master=root, value=True),
        log_wrap=tk.BooleanVar(master=root, value=True),
        log_group_similar=tk.BooleanVar(master=root, value=True),
        log_collect_problems=tk.BooleanVar(master=root, value=True),
    )
    host.serial = SimpleNamespace(
        is_open=lambda: False, set_input_log_enabled=lambda enabled: None
    )
    host._on_setting_changed = lambda: None
    host._closing = False
    host._display_after_id = None
    host._sash_after_id = None
    host._sash_restore_attempts = 0
    host._log_video_stats = lambda: None
    # Window と同じ順序：組み立てが先、設定の読み込みが後。
    settings = host.settings
    del host.settings
    host._build_log_area()
    host.settings = settings
    host._load_log_view_settings(settings)
    host.log_area.pack(fill="both", expand=True)
    root.update()
    return host


def pump(host: Any, root: tk.Tk) -> None:
    """GUI の取り出しを1周回す。自己再予約は取り消す（テストが周期を握る）。"""
    host.display_text()
    after_id = host._display_after_id
    if after_id is not None:
        host.logArea.after_cancel(after_id)
        host._display_after_id = None
    root.update()
