#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""layout_panel.py - ウィンドウレイアウトの適用と状態バッジの配信。

PokeControllerApp に混ぜて使う（多重継承）。複数台を 1 画面に並べるため、
「どの部品を出すか」を設定 1 つで切り替える。どれを出すかはここで考えず、
core.display_mode.layout_plan が返した表（LayoutPlan）だけを見る。状態を
見分ける分岐が増えるたびに Window 側が太くなるため、判断は core 側 1 箇所に
閉じる。

プロファイルの色（帯・色チップ）と実行状態の 1 行（コンパクトバーと
プレビューのバッジ）もここから配る。状態が変わる経路は Window._update_title
が全部通るので、そこで 1 回だけ配る。

プレビューの要求サイズと伸び方は camera_panel 側の
_apply_preview_layout が持つ（実体は CaptureArea を知る必要があるため）。
このファイルは「いつ・どのレイアウトで呼ぶか」だけを決める。

Window 本体は import しない（循環になる）。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk
from typing import Any

from core.display_mode import LAYOUTS, layout_plan, normalize_profile_color
from core.pane_arrangement import arrangement_tree, normalize_arrangement
from core.status_view import BADGE_COLORS, status_view

# コンパクトバーの色チップの幅（px）。色つきなら一目で分かる細帯。
_COMPACT_CHIP_WIDTH = 12
# 色チップの左右の余白。
_CHIP_PAD_X = (4, 2)


class LayoutPanelMixin:
    """レイアウト適用Mixin。単体では使わない。"""

    root: Any
    frame_1: Any
    camera_lf: Any
    preview: Any
    profile: str
    serial: Any
    runner: Any
    # Window 側が _init_state / _apply_settings_to_widgets で用意する
    layout_mode: Any
    arrangement: Any
    show_tabs_pane: Any
    show_log_pane: Any
    _pane_arranger: Any
    profile_color: Any
    startButton: Any
    pauseButton: Any
    openCommandPalette: Any
    show_mode: Any
    show_size: Any
    _running_command: str
    _paused: bool
    _on_setting_changed: Any
    _apply_content_minsize: Any
    _apply_preview_layout: Any

    # 組み立て後に出来上がる部品。まだ無い間に _publish_status が来ても
    # 落ちないよう、宣言だけして参照は getattr で避ける。
    profile_band: Any
    compact_bar: Any
    # コンパクト/プレビューのみで隠したカメラ欄の操作部（標準へ戻すときに使う）
    _hidden_camera_controls: list[Any]
    compact_chip: Any
    compact_profile_label: Any
    compact_status: Any
    _compact_status_label: Any
    compact_start: Any
    transport_name: Any
    status_bar: Any
    status_state: Any
    status_device: Any
    status_fps: Any
    compact_pause: Any

    # ------------------------------------------------------------------
    # 組み立て
    # ------------------------------------------------------------------

    def _build_layout_widgets(self) -> None:
        """レイアウト切替の対象になる部品を 2 つ作る。

        _build_ui の最後で呼ぶ。作時点では preview も startButton も揃って
        いるが、適用（_apply_layout）は preview 確定後に Window 側が呼ぶ。
        ここではどちらも配置しない（標準レイアウトのまま見せる）。
        """
        # プロファイル色の帯。色がある時だけ root の上端へ乗せる。
        self.profile_band = tk.Frame(self.root, height=6)
        self.profile_band.pack_propagate(False)

        # コンパクトバー。1 行で「誰の・今どこ・操作」だけを示す。
        self.compact_bar = ttk.Frame(self.camera_lf)

        # 左から: 色チップ / プロファイル名 / 状態 / 操作。
        self.compact_chip = tk.Frame(self.compact_bar, width=_COMPACT_CHIP_WIDTH)
        self.compact_chip.pack_propagate(False)
        self.compact_chip.pack(side="left", fill="y", padx=_CHIP_PAD_X)

        self.compact_profile_label = ttk.Label(self.compact_bar, text="")
        self.compact_profile_label.pack(side="left")

        self.compact_status = tk.StringVar(value="")
        self._compact_status_label = ttk.Label(
            self.compact_bar, textvariable=self.compact_status
        )
        self._compact_status_label.pack(side="left", padx=(6, 0))

        # Start / Pause は本体と同じ関数を invoke する。状態を二重に持たず、
        # 入口だけ並べて 1 画面で完結させる。
        ttk.Button(
            self.compact_bar, text="選択...", command=self.openCommandPalette
        ).pack(side="right", padx=2)
        self.compact_pause = ttk.Button(self.compact_bar, text="一時停止")
        self.compact_pause.config(command=lambda: self.pauseButton.invoke())
        self.compact_pause.pack(side="right")
        self.compact_start = ttk.Button(self.compact_bar, text="開始")
        self.compact_start.config(command=lambda: self.startButton.invoke())
        self.compact_start.pack(side="right")

        # 標準レイアウトの下端の 1 行。状態・接続先・表示 fps を常に見せる。
        # 出し入れは _apply_layout（root 直下に pack する）。
        self.status_bar = ttk.Frame(self.root, padding=(6, 1))
        self.status_state = tk.StringVar(value="")
        self.status_device = tk.StringVar(value="")
        self.status_fps = tk.StringVar(value="")
        ttk.Label(self.status_bar, textvariable=self.status_state).pack(side="left")
        ttk.Separator(self.status_bar, orient="vertical").pack(
            side="left", fill="y", padx=8
        )
        ttk.Label(self.status_bar, textvariable=self.status_device).pack(side="left")
        ttk.Label(self.status_bar, textvariable=self.status_fps).pack(side="right")

    # ------------------------------------------------------------------
    # レイアウト適用
    # ------------------------------------------------------------------

    def applyLayout(self, layout: str) -> None:
        """レイアウトを選び、画面へ適用して保存する。

        値は検証してから変数に入れる。未知の値がそのまま入ると、
        次回起動時に画面と設定が食い違うため。
        """
        self.layout_mode.set(layout if layout in LAYOUTS else LAYOUTS[0])
        self._apply_layout()
        # 要求サイズが変わるので最小サイズの制限も測り直す（タブの見切れ防止）。
        self._apply_content_minsize()
        self._on_setting_changed()

    def _apply_layout(self) -> None:
        """現在のレイアウトに合わせて、部品の出し入れとプレビューを直す。"""
        plan = self._layout_plan()

        # カメラ欄の操作部（見出し・入力・コンボ・ボタン）。
        # プレビューとコンパクトバーは残すので、grid_slaves から取り除く。
        # grid_remove は配置オプションを覚えているため、戻す時は grid() だけで
        # 元の位置と sticky へ戻る。再配置のコードを書き足さずに済む。
        if plan.show_camera_controls:
            for widget in getattr(self, "_hidden_camera_controls", []):
                widget.grid()
            self._hidden_camera_controls = []
        else:
            # grid_remove した部品は grid_slaves に出てこなくなるので、戻す
            # ときのために隠した物を覚えておく（覚えないと標準へ戻せない）。
            hidden = list(getattr(self, "_hidden_camera_controls", []))
            for widget in self._camera_control_widgets():
                widget.grid_remove()
                hidden.append(widget)
            self._hidden_camera_controls = hidden
        # 隠すときは枠の見出しも落とす（プレビューだけの枠にする）。
        self.camera_lf.config(text="カメラ" if plan.show_camera_controls else "")

        # 下端の状態の 1 行。本体より先に pack して、窓が小さいときに本体より
        # 先に詰められないようにする。
        if plan.show_status_bar:
            self.status_bar.pack(side="bottom", fill="x", before=self.frame_1)
        else:
            self.status_bar.pack_forget()

        # コンパクトバー
        if plan.show_compact_bar:
            self.compact_bar.grid(column=0, columnspan=10, row=4, sticky="ew")
        else:
            self.compact_bar.grid_remove()

        # プレビューの要求サイズと伸び方。実体は camera_panel 側。
        self._apply_preview_layout()

        # 3 つの欄（プレビュー・設定タブ・ログ）を仕切りに並べ直す。出すかどうかは
        # レイアウト（コンパクト等は隠す）と利用者の折りたたみの両方で決まる。
        # 伸びるプレビューにだけ余白を多めに配る（core.pane_arrangement）。
        tree = arrangement_tree(
            str(self.arrangement.get()),
            show_tabs=plan.show_tabs and bool(self.show_tabs_pane.get()),
            show_log=plan.show_log and bool(self.show_log_pane.get()),
            stretch=bool(plan.preview.stretch),
        )
        self._pane_arranger.apply(tree)

        # 色も設定値なので、ここでまとめて反映する。起動時の 1 回目も
        # _apply_layout を通るため、色反映の経路が 1 つで済む。
        self._apply_profile_color_widgets(str(self.profile_color.get()))

        # 実行状態の 1 行（コンパクトバー）とプレビューのバッジ
        self._publish_status()

    def applyArrangement(self, arrangement: str) -> None:
        """欄の並べ方を選び、画面へ適用して保存する。"""
        self.arrangement.set(normalize_arrangement(arrangement))
        self._relayout_and_save()

    def setPaneVisible(self, name: str, visible: bool) -> None:
        """設定タブ（tabs）かログ（log）を出す/畳む。保存もする。"""
        var = {"tabs": self.show_tabs_pane, "log": self.show_log_pane}.get(name)
        if var is None:
            return
        var.set(bool(visible))
        self._relayout_and_save()

    def resetPaneSashes(self) -> None:
        """仕切りを既定の位置へ戻して保存する。"""
        self._pane_arranger.reset_sashes()
        self._on_setting_changed()

    def _relayout_and_save(self) -> None:
        self._apply_layout()
        self._apply_content_minsize()
        self._on_setting_changed()

    def _layout_plan(self) -> Any:
        """今のレイアウト・表示モード・固定サイズから LayoutPlan を取る。"""
        return layout_plan(
            str(self.layout_mode.get()),
            str(self.show_mode.get()),
            str(self.show_size.get()),
        )

    def _camera_control_widgets(self) -> list[Any]:
        """カメラ欄の grid の子のうち、操作部だけを取り出す。

        プレビューとコンパクトバーは「操作部」ではないので残す。
        """
        keep = {id(getattr(self, "preview", None)), id(self.compact_bar)}
        return [w for w in self.camera_lf.grid_slaves() if id(w) not in keep]

    # ------------------------------------------------------------------
    # 状態表示の配信
    # ------------------------------------------------------------------

    def _publish_status(self) -> None:
        """実行状態の 1 行を、コンパクトバーとプレビューのバッジへ配る。

        Window._update_title の最後から呼ばれる（状態が変わる経路は全部そこを
        通るため）。UI 構築前に来ても落ちないよう、未作成の部品は getattr で
        避ける。
        """
        # ポート名は _update_title と同じく、UI 構築前は無いことがある。
        port_var = getattr(self, "com_port_name", None)
        device = port_var.get() if port_var is not None else ""
        state = {
            "running_command": str(getattr(self, "_running_command", "")),
            "paused": bool(getattr(self, "_paused", False)),
            "stop_waited_ms": int(getattr(self.runner, "stop_waited", 0)),
            "device": str(device),
            "opened": bool(self.serial.is_open()),
        }
        profile = str(getattr(self, "profile", ""))
        # バッジは単独で見えるので [名前] を付ける。コンパクトバーは隣の
        # ラベルが名前を出しているため、付けると二重になる。
        view = status_view(profile=profile, **state)
        bar_view = status_view(profile="", **state)

        # 下端の 1 行。名前はウィンドウタイトルにあるので付けない。
        if getattr(self, "status_state", None) is not None:
            self.status_state.set(bar_view.text)
            transport_var = getattr(self, "transport_name", None)
            transport = str(transport_var.get()) if transport_var is not None else ""
            if device and state["opened"]:
                suffix = f" ({transport})" if transport else ""
                self.status_device.set(f"{device}{suffix}")
            else:
                self.status_device.set("未接続")

        # コンパクトバーがあれば 1 行と操作ボタンの中身を写す。
        if getattr(self, "compact_status", None) is not None:
            self.compact_status.set(bar_view.text)
            self._mirror_button(self.compact_start, self.startButton)
            self._mirror_button(self.compact_pause, self.pauseButton)

        # プロファイル名のラベル。無名なら隠す（空欄のラベルが幅を取る）。
        profile_label = getattr(self, "compact_profile_label", None)
        if profile_label is not None:
            profile_label.config(text=profile)
            if profile:
                # 隠した後に pack し直すと左詰めの最後へ回るので、状態文より
                # 前（色チップの直後）へ明示して戻す。
                profile_label.pack(side="left", before=self._compact_status_label)
            else:
                profile_label.pack_forget()

        # プレビューのバッジ。出すのはバッジのあるレイアウトだけ。
        preview = getattr(self, "preview", None)
        if preview is None:
            return
        if self._layout_plan().show_badge:
            preview.setBadge(view.text, BADGE_COLORS[view.kind])
        else:
            preview.clearBadge()

    def publishVideoStats(self, shown_fps: float) -> None:
        """表示 fps の実測を下端の 1 行へ出す（log_panel の集計から呼ぶ）。"""
        if getattr(self, "status_fps", None) is not None:
            self.status_fps.set(f"表示 {shown_fps:.1f} fps")

    @staticmethod
    def _mirror_button(target: Any, source: Any) -> None:
        """コンパクトバーのボタンへ、本体側の文字と有効/無効を写す。

        状態を二重に持たないため、読むのは本体側だけ。写すのは text と state
        の 2 つ（command は本体を invoke するので写さない）。
        """
        if target is None or source is None:
            return
        for option in ("text", "state"):
            try:
                target[option] = source[option]
            except Exception:
                # 破棄済みなら黙る。状態表示の失敗で本体を止めない。
                pass

    # ------------------------------------------------------------------
    # プロファイルの色
    # ------------------------------------------------------------------

    def applyProfileColor(self, color: str) -> None:
        """プロファイルの色を検証して帯と色チップへ反映し、保存する。

        色は複数台を並べたときの識別子。窓が重なっても「どの台か」が、
        ウィンドウタイトルを見られない環境でも分かる。
        """
        value = normalize_profile_color(color)
        self.profile_color.set(value)
        self._apply_profile_color_widgets(value)
        self._on_setting_changed()

    def _apply_profile_color_widgets(self, color: str) -> None:
        """色つきの帯と色チップを更新する（空なら出さない）。

        applyProfileColor と _apply_layout の両方から呼ぶ。起動時の 1 回目も
        _apply_layout を通るため、色反映の経路が 1 つで済む。
        """
        band = getattr(self, "profile_band", None)
        if band is not None:
            if color:
                band.config(background=color)
                # 本体は pack 済みなので、順序を指定しないと帯が下に来る。
                band.pack(side="top", fill="x", before=self.frame_1)
            else:
                band.pack_forget()

        chip = getattr(self, "compact_chip", None)
        if chip is not None:
            if color:
                chip.config(background=color)
                # pack し直すと左詰めの最後（状態文の後ろ）へ回るため、
                # バーの先頭へ明示して戻す。無名プロファイルでは名前ラベルが
                # pack されておらず基準にできないので、状態文を基準にする。
                label = self.compact_profile_label
                anchor = label if label.winfo_manager() else self._compact_status_label
                chip.pack(side="left", fill="y", padx=_CHIP_PAD_X, before=anchor)
            else:
                chip.pack_forget()
