#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""log_panel.py - ログ欄の組み立てと表示ポンプを受け持つMixin.

PokeControllerApp に混ぜて使う（多重継承）。状態は self 越しに
触るため、触る属性は下に宣言しておく（mypy のため。値は Window 側が持つ）。
描画の実体（キュー・間引き）は LogPane が持つ。ここは組み立てと
一定間隔の取り出しだけを行う。
"""

from __future__ import annotations

import gc
import time
import tkinter as tk
import tkinter.ttk as ttk
import traceback
from typing import Any

import LogPane
import WindowGeometry
import WindowUtils
from loguru import logger
from pygubu.widgets.scrollbarhelper import ScrollbarHelper
from services.serial_service import SerialService


class LogPanelMixin:
    """ログ欄Mixin。単体では使わない。"""

    frame_1: Any
    root: Any
    settings: Any
    serial: SerialService
    log_nb: Any
    log_pane: Any
    log_scroll: Any
    logArea: Any
    sub_scroll: Any
    subLogArea: Any
    input_scroll: Any
    inputLogArea: Any
    log_autoscroll: Any
    show_input_log: Any
    _closing: bool
    _display_after_id: Any
    _sash_after_id: Any
    _sash_restore_attempts: int
    _stat_ticks: int
    camera: Any
    preview: Any
    runner: Any
    _on_setting_changed: Any

    def _build_log_area(self) -> None:
        """ログ欄を組み立てる。

        入力ログは1操作で press と release の2行が出るため、コマンドの
        print やシステムメッセージと同じ欄に流すと、それらが押し流されて
        読めなくなる（テキストエリアの輻輳）。量の桁が違うものは欄を分ける。
        タブにしておけば場所を取らず、必要なときだけ見に行ける。

        さらに「ログ」タブの中身は上下2枚に分ける。1枚だと、常時流れる
        進捗と、残しておきたい結果が混ざって流れてしまう。上を主ログ
        （print と通常の出力）、下を副ログ（明示的に出した内容）とし、
        仕切りは ttk.PanedWindow でドラッグして高さを変えられるようにする。
        使い分けは Python コマンド側の print2 / log2 で行う。
        """
        self.log_nb = ttk.Notebook(self.frame_1)

        # 「ログ」= コマンドの出力とシステムメッセージ（上下2枚）
        self.log_pane = ttk.PanedWindow(self.log_nb, orient="vertical")

        self.log_scroll = ScrollbarHelper(self.log_pane, scrolltype="both")
        self.logArea = WindowUtils.makeLogText(self.log_scroll)
        # weight を付けておくと、ウィンドウを広げた分が両方へ配分される
        self.log_pane.add(self.log_scroll, weight=3)

        self.sub_scroll = ScrollbarHelper(self.log_pane, scrolltype="both")
        self.subLogArea = WindowUtils.makeLogText(self.sub_scroll)
        self.log_pane.add(self.sub_scroll, weight=2)

        self.log_nb.add(self.log_pane, text="ログ")

        # 「入力」= シリアルへ送った操作のログ
        self.input_scroll = ScrollbarHelper(self.log_nb, scrolltype="both")
        self.inputLogArea = WindowUtils.makeLogText(self.input_scroll)
        self.log_nb.add(self.input_scroll, text="入力")

        self.log_nb.grid(column=3, padx="5", pady="5", row=0, rowspan=3, sticky="nsew")

        self._build_log_toolbar()
        # 仕切り位置の復元は、ウィジェットの大きさが確定してからでないと
        # 効かない（構築直後は高さが1のため sashpos が無視される）。
        self._sash_after_id = self.root.after_idle(self._restore_sash)

    def _build_log_toolbar(self) -> None:
        """ログ欄の下に、表示の絞り込みと消去を置く。

        欄を分けても、コマンドが大量に print すれば「ログ」側は流れる。
        止めたいときにすぐ止められる口を用意しておく。
        """
        bar = ttk.Frame(self.frame_1)
        bar.grid(column=3, padx="5", row=3, sticky="ew")

        self.log_autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="追従", variable=self.log_autoscroll).pack(
            side="left"
        )

        self.show_input_log = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bar,
            text="入力ログ",
            variable=self.show_input_log,
            command=self._on_input_log_toggled,
        ).pack(side="left", padx=(8, 0))

        ttk.Button(bar, text="消去", command=self.clearLog).pack(side="right")

    def _on_input_log_toggled(self) -> None:
        """入力ログの ON/OFF。切ると Sender 側で出力そのものを止める。

        表示だけ止めても、行を作る処理とキューの往復は残る。元を止めた
        ほうが軽く、意図も分かりやすい。
        """
        enabled = bool(self.show_input_log.get())
        self.serial.set_input_log_enabled(enabled)
        self.settings.input_log_enabled.set(enabled)
        self._on_setting_changed()

    def _log_video_stats(self) -> None:
        """取込fpsと表示fpsを5秒ごとに1行出す（ラグの切り分け用）。

        取込 < 設定なら機器・帯域側、表示 < 取込なら描画・GUI側が
        遅い。呼ばれるたびの実測ではなく期間平均を見る。
        """
        ticks = getattr(self, "_stat_ticks", 0) + 1
        self._stat_ticks = ticks
        if ticks % 25 != 0:
            return
        try:
            camera = getattr(self, "camera", None)
            preview = getattr(self, "preview", None)
            cap = camera.getStats() if camera is not None else None
            shown = preview.getStats() if preview is not None else None
        except Exception as e:
            logger.debug(f"映像実測を取れませんでした: {e}")
            return
        if cap is None:
            cap_text = "-"
        else:
            cap_text = f"{cap.get('fps', 0.0)}fps(avg {cap.get('avg_ms', 0.0)}ms)"
        if shown is None:
            shown_text = "-"
        else:
            shown_text = (
                f"{shown.get('fps', 0.0)}fps"
                f"(draw {shown.get('draw_ms', 0.0)}ms/max {shown.get('draw_max_ms', 0.0)}ms)"
            )
        log_ms = round(getattr(self, "_log_flush_ms", 0.0), 1)
        log_max_ms = round(getattr(self, "_log_flush_max_ms", 0.0), 1)
        gap_max_ms = round(getattr(self, "_pump_gap_max_ms", 0.0), 1)
        self._log_flush_max_ms = 0.0
        self._pump_gap_max_ms = 0.0
        # 世代別GCの回収回数の差分。突発停止（スタッター）がGC由来かを見る。
        # 回収そのものは裏で走るため、ここでは回数だけ拾う（安い）。
        try:
            gc_now = [g["collections"] for g in gc.get_stats()[:3]]
        except Exception:
            gc_now = []
        gc_prev = getattr(self, "_gc_prev", None)
        self._gc_prev = gc_now
        if gc_prev is not None and len(gc_now) == 3 and len(gc_prev) == 3:
            gc_text = f"gc +{gc_now[0] - gc_prev[0]}/+{gc_now[1] - gc_prev[1]}/+{gc_now[2] - gc_prev[2]}"
        else:
            gc_text = "gc -/-/-"
        try:
            run_state = self.runner.state
        except Exception:
            run_state = "?"
        logger.debug(
            f"映像: 取込 {cap_text} / 表示 {shown_text} / "
            f"ログ {log_ms}ms(max {log_max_ms}ms) / ポンプ間隔max {gap_max_ms}ms"
            f" / {gc_text} [{run_state}]"
        )

    def clearLog(self) -> None:
        """いま見えているタブのログを消す。"""
        LogPane.clearAreas(self._active_log_areas())

    def _active_log_areas(self) -> list:
        """選択中のタブに対応する Text を返す（ログタブは2枚）。"""
        try:
            if self.log_nb.index("current") == 1:
                return [self.inputLogArea]
        except tk.TclError:
            pass
        return [self.logArea, self.subLogArea]

    def _active_log_area(self) -> tk.Text:
        """後方互換。主たる1枚を返す。"""
        return self._active_log_areas()[0]

    # -- 仕切り位置 ---------------------------------------------------------

    def _restore_sash(self) -> None:
        if self._closing:
            return
        self._sash_after_id = None
        self._sash_restore_attempts += 1
        try:
            restored = WindowGeometry.restoreSash(self.log_pane, self.settings)
            if not restored and self._sash_restore_attempts < 50:
                self._sash_after_id = self.root.after(100, self._restore_sash)
                return
        except (tk.TclError, RuntimeError):
            return
        if not restored:
            logger.warning("ログ欄の仕切り位置を復元できませんでした")
        self.log_pane.bind("<ButtonRelease-1>", self._remember_sash, add="+")

    def _remember_sash(self, *event: Any) -> None:
        if WindowGeometry.rememberSash(self.log_pane, self.settings):
            self._on_setting_changed()

    def display_text(self) -> None:
        self._display_after_id = None
        if self._closing:
            return
        # ポンプ自体の間隔の最大も見る。GUI スレッドが詰まると
        # 200ms 周期が崩れる。描画もログ流しも軽いのに間隔だけ
        # 開くなら、別の after 予約や重い処理が主犯である。
        now = time.perf_counter()
        last_at = getattr(self, "_pump_last_at", None)
        self._pump_last_at = now
        if last_at is not None:
            gap_ms = (now - last_at) * 1000.0
            if gap_ms > getattr(self, "_pump_gap_max_ms", 0.0):
                self._pump_gap_max_ms = gap_ms
        try:
            flush_began = time.perf_counter()
            follow = self.log_autoscroll.get()
            LogPane.flushQueue(LogPane.text_queue, self.logArea, follow)
            LogPane.flushQueue(LogPane.sub_log_queue, self.subLogArea, follow)
            if self.show_input_log.get():
                if self.serial.is_open():
                    self.serial.flush_input_log()
                LogPane.flushQueue(LogPane.input_log_queue, self.inputLogArea, follow)
            flush_ms = (time.perf_counter() - flush_began) * 1000.0
            prev = getattr(self, "_log_flush_ms", 0.0)
            self._log_flush_ms = (
                flush_ms if prev <= 0.0 else prev * 0.9 + flush_ms * 0.1
            )
            if flush_ms > getattr(self, "_log_flush_max_ms", 0.0):
                self._log_flush_max_ms = flush_ms
            self._log_video_stats()
        except tk.TclError:
            if not self._closing:
                logger.debug("ログWidget破棄中の更新を停止しました")
        except Exception:
            logger.error(traceback.format_exc())
        finally:
            if not self._closing:
                try:
                    self._display_after_id = self.logArea.after(
                        LogPane.FLUSH_INTERVAL_MS, self.display_text
                    )
                except (tk.TclError, RuntimeError):
                    self._display_after_id = None
