#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""live_resize.py - 窓の縁をドラッグしている間は中身を並べ直さない。

Tk の部品はそれぞれが Windows の子窓で、ドラッグの 1 段ごとに全部を動かし
描き直す。本体の画面ではそれで 8fps 前後まで落ち、窓がカクカクと広がった
（部品を隠すと 18fps まで上がり、プレビューの描画を止めても変わらない）。

ドラッグ中は中身をドラッグ前の位置と大きさに止め（place で固定）、枠だけを
動かす。手を止めたとき（PAUSE_MS）と離したときに、元の置き方（pack）へ戻して
1 回だけ並べ直す。窓の処理の中では Tk を呼ばない（呼ぶと落ちた）。窓を動かすだけ（大きさが変わらない）のときは何もしない。
Windows 以外では何もしない（ウィンドウマネージャ側の挙動に任せる）。
"""

from __future__ import annotations

import time
import tkinter as tk
from typing import Any

from PIL import ImageTk
from loguru import logger
from ui import screen_grab
from ui.win32_subclass import WM_NCDESTROY, WindowSubclass

_WM_SIZING = 0x0214
_WM_EXITSIZEMOVE = 0x0232
_SUBCLASS_ID = 0x5126

# ドラッグ中に手を止めたら、この間隔（ms）で今の大きさに並べ直す。
PAUSE_MS = 150
# ドラッグの終わりと手を止めたことを見に行く間隔（ms）。ドラッグ中だけ回る。
WATCH_MS = 50


class ResizeFreeze:
    """root の縁のドラッグ中、content（root へ pack した中身）を固定する。

    窓の処理（WM_SIZING など）の中では Tk を呼ばない。ドラッグの途中で
    place / pack を呼ぶと Tk の中で access violation になり落ちた。窓の処理では
    印と時刻だけを残し、Tk の操作は Tk の側（<Configure> と after）で行う。
    """

    def __init__(self, root: Any, content: Any) -> None:
        self._root = root
        self._content = content
        self._pack: dict[str, Any] | None = None
        # 窓の処理が書き、Tk の側が読む印。
        self._sizing = False  # 縁を掴んで大きさを変えている
        self._last_sizing = 0.0  # 最後に WM_SIZING が来た時刻（monotonic）
        self._watch_id: str | None = None
        # 窓が壊れ始めたら何もしない。壊している最中の窓へ pack すると Tk の
        # 中で access violation になって落ちた（見張りの after が壊す途中で動く）。
        self._dead = False
        # ドラッグ中に中身の代わりに出す絵。
        self._shot: Any = None
        self._photo: Any = None
        self._subclass = WindowSubclass(root, _SUBCLASS_ID, self._on_message)
        root.bind("<Configure>", self._on_configure, add="+")
        root.bind("<Destroy>", self._on_destroy, add="+")
        # 外枠の窓は最初に表示されるまで無い。表示されてから付ける。
        if root.winfo_viewable():
            self._subclass.install()
        else:
            root.bind("<Map>", self._on_map, add="+")

    @property
    def frozen(self) -> bool:
        return self._pack is not None

    @property
    def stand_in(self) -> Any:
        """ドラッグ中に中身の代わりに出している絵。出していなければ None。"""
        return self._shot

    def _on_map(self, event: Any) -> None:
        if event.widget is self._root and not self._subclass.installed:
            self._subclass.install()

    def cleanup(self) -> None:
        """終了時。止めていれば戻し、割り込みを外す。"""
        self._sizing = False
        self._cancel_watch()
        self._thaw()
        self._subclass.remove()

    # ------------------------------------------------------------------
    # 窓の処理の中（Tk を呼ばない）

    def _on_message(self, msg: int, _wparam: int, _lparam: int) -> int | None:
        if msg == _WM_SIZING:
            # 大きさを変えるドラッグのときだけ来る（窓を動かすだけでは来ない）。
            self._sizing = True
            self._last_sizing = time.monotonic()
        elif msg == _WM_EXITSIZEMOVE:
            self._sizing = False
        elif msg == WM_NCDESTROY:
            self._sizing = False
            self._dead = True
        return None

    # ------------------------------------------------------------------
    # Tk の側

    def _on_destroy(self, event: Any) -> None:
        # 中身が先に壊されることもある（tkinter は子から壊す）。壊す途中で
        # Windows のメッセージが回り、見張りの after が動くので、どちらが
        # 壊れ始めても止める。
        if event.widget is not self._root and event.widget is not self._content:
            return
        self._dead = True
        self._sizing = False
        self._cancel_watch()

    def _on_configure(self, event: Any) -> None:
        if event.widget is not self._root or not self._sizing or self._dead:
            return
        if self._pack is None and not self._paused():
            self._freeze()
        self._ensure_watch()

    def _paused(self) -> bool:
        return time.monotonic() - self._last_sizing >= PAUSE_MS / 1000

    def _ensure_watch(self) -> None:
        if self._watch_id is None:
            self._watch_id = self._root.after(WATCH_MS, self._watch)

    def _cancel_watch(self) -> None:
        if self._watch_id is None:
            return
        try:
            self._root.after_cancel(self._watch_id)
        except tk.TclError:
            pass
        self._watch_id = None

    def _watch(self) -> None:
        """ドラッグの終わりと、掴んだまま手を止めたことを見張る。"""
        self._watch_id = None
        if self._dead:
            return
        if not self._sizing:
            self._thaw()  # 離した。
            return
        if self._paused():
            # 掴んだまま手を止めた。今の大きさで並べ直す（動けばまた止める）。
            self._thaw()
        elif self._pack is None:
            self._freeze()  # 手を止めた後にまた動いた。
        self._ensure_watch()

    def _freeze(self) -> None:
        if self._pack is not None or self._dead:
            return
        content = self._content
        try:
            if content.winfo_manager() != "pack":
                return  # 想定外の置き方なら手を出さない。
            info = dict(content.pack_info())
            slaves = list(self._root.pack_slaves())
            index = slaves.index(content)
            # 戻すときに並び順（下の状態表示との前後）を保つための目印。
            info.pop("in", None)
            if index + 1 < len(slaves):
                info["before"] = slaves[index + 1]
            elif index > 0:
                info["after"] = slaves[index - 1]
            x, y = content.winfo_x(), content.winfo_y()
            width, height = content.winfo_width(), content.winfo_height()
            shot = self._snapshot(content, width, height)
            if shot is not None:
                # 中身の絵 1 枚に差し替えて中身は隠す。子窓が残っていると、
                # 動かさなくても窓が 1 段広がるたびに全部の子窓の分だけ重い。
                shot.place(x=x, y=y, width=width, height=height)
                content.pack_forget()
            else:
                # 絵を撮れなければ、位置と大きさを止めるだけにする。
                content.place(x=x, y=y, width=width, height=height)
                content.pack_forget()
        except (tk.TclError, ValueError) as e:
            logger.debug(f"ドラッグ中の固定を見送りました: {e!r}")
            return
        self._pack = info
        # 絵を撮るのに時間がかかっても「手を止めた」と取り違えないよう、
        # 止めた時点から数え直す。
        self._last_sizing = max(self._last_sizing, time.monotonic())

    def _thaw(self) -> None:
        info = self._pack
        if info is None:
            return
        self._pack = None
        if self._dead:
            return  # 壊れていく窓は並べ直さない。
        try:
            try:
                self._content.pack(**info)
            except tk.TclError:
                # 目印の部品がドラッグ中に消えていた。並び順の指定を外して戻す。
                info.pop("before", None)
                info.pop("after", None)
                self._content.pack(**info)
            self._content.place_forget()
        except tk.TclError as e:
            logger.warning(f"ドラッグ後の並べ直しに失敗しました: {e!r}")
        if self._shot is not None:
            try:
                self._shot.destroy()
            except tk.TclError:
                pass
            self._shot = None
            self._photo = None

    def _snapshot(self, content: Any, width: int, height: int) -> Any:
        """中身の今の見た目を 1 枚の絵（Label）にする。撮れなければ None。"""
        try:
            x, y = content.winfo_rootx(), content.winfo_rooty()
            image = screen_grab.grab(x, y, width, height)
            if image is None:
                return None
            self._photo = ImageTk.PhotoImage(image, master=self._root)
            self._shot = tk.Label(
                self._root, image=self._photo, borderwidth=0, highlightthickness=0
            )
        except (OSError, ValueError, tk.TclError) as e:
            logger.debug(f"ドラッグ中の絵を撮れませんでした: {e!r}")
            self._shot = None
            self._photo = None
        return self._shot
