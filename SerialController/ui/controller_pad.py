#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""controller_pad.py - 仮想コントローラの操作盤（Canvas 1 枚に描く）。

並びと当たり判定は core.pad_layout、タブと別窓の出し入れは ui.controller_dock。
ここはマウスの押す・離す・ずらすを、送信側への押下・解放の申告に変える。
"""

from __future__ import annotations

import math
import re
import tkinter as tk
import tkinter.font as tkfont
import tkinter.ttk as ttk
from typing import Any

from Commands.Keys import Button, Hat
from GuiAssets import live_sender
from core.pad_layout import (
    FLOATING_MAX_ZOOM,
    PAD_H,
    PAD_W,
    SHAPES,
    PadShape,
    fit_scale,
    hit_test,
)
from loguru import logger


class ControllerGUI:
    """Switch コントローラを模した操作盤。

    ボタンを 1 枚の Canvas に描く。旧 UI は tk.Button を 600x300 の窓へ
    座標固定で並べていたため、場所を取るうえ DPI や窓の大きさに追従しなかった。
    並びと当たり判定は core.pad_layout に置き、ここでは倍率を掛けて描くだけ。
    """

    # 配色。周りの明るい画面から操作盤だと一目で分かる濃い本体に、
    # 押している間だけ青く光らせる（押しっぱなしが目で分かるように）。
    BODY = "#2f343b"
    BODY_EDGE = "#1f2328"
    KEY = "#4a515b"
    KEY_EDGE = "#5d6570"
    STICK_RING = "#23272d"
    KEY_TEXT = "#f2f4f7"
    BUTTON_ACTIVE_BG = "#3d8bfd"  # 押している間の色

    # 十字キーの押しっぱなしと同時押しの合成表。
    #   十字キーは「どれか一つの値」であって、ボタンのような
    #   ビットの組み合わせではない。
    #     ボタンのように OR で足せないため、押している方向の集合から
    #     どの値になるかを引く表を持つ。
    HAT_DIRS = ("UP", "RIGHT", "DOWN", "LEFT")
    HAT_COMBO = {
        (): "CENTER",
        ("UP",): "TOP",
        ("RIGHT",): "RIGHT",
        ("DOWN",): "BTM",
        ("LEFT",): "LEFT",
        ("RIGHT", "UP"): "TOP_RIGHT",
        ("DOWN", "RIGHT"): "BTM_RIGHT",
        ("DOWN", "LEFT"): "BTM_LEFT",
        ("LEFT", "UP"): "TOP_LEFT",
    }

    # 十字キーの向きの名前（core.pad_layout.DPAD_NAMES）から、押している向きへの対応。
    #   斜め（UP_RIGHT など）は2方向を同時に押したものとして扱う。
    HAT_NAME2DIRS = {
        "UP": ("UP",),
        "UP_RIGHT": ("UP", "RIGHT"),
        "RIGHT": ("RIGHT",),
        "DOWN_RIGHT": ("DOWN", "RIGHT"),
        "DOWN": ("DOWN",),
        "DOWN_LEFT": ("DOWN", "LEFT"),
        "LEFT": ("LEFT",),
        "UP_LEFT": ("UP", "LEFT"),
    }

    # 文字の大きさ（等倍時の px）。小さい丸の記号は大きめにして読めるようにする。
    _LABEL_PX = {"pill": 11, "circle": 13, "stick": 10, "square": 11}
    _DPAD_ARROWS = (
        ("UP", 0, -1, "▲"),
        ("RIGHT", 1, 0, "▶"),
        ("DOWN", 0, 1, "▼"),
        ("LEFT", -1, 0, "◀"),
    )

    def __init__(self, root: Any, ser: Any, container: Any = None) -> None:
        """container を渡すとその枠の中に組み立てる（メイン画面への埋め込み）。

        渡さなければ別ウィンドウ（Toplevel）を開く。

        ser には送り先そのものか、今の送り先を返す関数を渡す。送り先は起動時の
        組み立てより後に作られ、通信方式を変えると作り直されるので、埋め込みで
        長く置く場合は関数を渡し、押すたびに今の送り先を引く。
        """
        self._ser_source = ser
        # ボタンの押しっぱなしに対応するための保持。
        #   押下と解放を別々に受け取り、送信側の姿勢へ差分申告する。
        #     触っていない項目は保たれるので、他のボタンを消さない。
        self._held_btn: dict = {}  # 表示名 -> Keys.Button
        self._held_hat: set = set()  # 押している向き（"UP" など）
        # マウスで押さえている的（1 本のポインタで押せるのは 1 つ）。
        self._pointer: str | None = None
        # 論理座標 → 画面座標の倍率とずらし（_redraw で決まる）。
        self._scale = 1.0
        self._ox = 0.0
        self._oy = 0.0

        # 埋め込みは幅だけに合わせ、等倍より大きくしない（タブの場所を取らない）。
        # 別窓は窓の大きさに合わせて拡縮し、上下も中央に寄せる。
        self._embedded = container is not None
        if container is not None:
            self.window: Any = tk.Frame(container)
            self.window.pack(fill="x")
        else:
            self._open_window(root)
        self._build_pad()
        logger.debug("Create GUI controller")

    def _open_window(self, root: Any) -> None:
        """別ウィンドウとして開く。窓の大きさに合わせて操作盤も拡縮する。"""
        self.window = tk.Toplevel(root)
        self.window.title("仮想コントローラ")
        # 親ウィンドウの位置から少しずらして出す。geometry の座標は
        # マルチモニタで左/上の画面へ動かすと負になり '600x300-10+20'
        # の形になる。'+' で split すると要素が足りず IndexError で
        # 落ちるため、符号込みで正規表現から取り出す。
        pos = re.search(r"([+-]\d+)([+-]\d+)$", root.geometry())
        root_x = int(pos.group(1)) if pos else 0
        root_y = int(pos.group(2)) if pos else 0
        base = self._dpi_scale(self.window)
        width = round(PAD_W * base) + 16
        height = round(PAD_H * base) + 16
        self.window.geometry(
            "%dx%d%+d%+d" % (width, height, 250 + root_x, 125 + root_y)
        )
        self.window.minsize(width // 2, height // 2)

    @staticmethod
    def _dpi_scale(widget: Any) -> float:
        """96dpi を 1 とした画面の倍率（高 DPI で小さくなりすぎないように）。"""
        try:
            return max(1.0, float(widget.winfo_fpixels("1i")) / 96.0)
        except (tk.TclError, ValueError):
            return 1.0

    def _build_pad(self) -> None:
        """操作盤の Canvas を作り、押す・離す・ずらすを受ける。"""
        self._base = self._dpi_scale(self.window)
        # 画面の他の文字と同じ書体（Windows なら Yu Gothic UI / Segoe UI）。
        try:
            self._family = str(
                tkfont.nametofont("TkDefaultFont", root=self.window).actual("family")
            )
        except tk.TclError:
            self._family = "TkDefaultFont"
        try:
            bg = ttk.Style(self.window).lookup("TFrame", "background") or None
        except tk.TclError:
            bg = None
        width = round(PAD_W * self._base)
        height = round(PAD_H * self._base)
        # フォーカスを取らない。キーボード操作中に Space などで
        # 意図しない入力が飛ぶのを防ぐ（マウス専用の操作盤）。
        self.canvas = tk.Canvas(
            self.window,
            width=width,
            height=height,
            highlightthickness=0,
            takefocus=0,
            **({"bg": bg} if bg else {}),
        )
        if self._embedded:
            self.canvas.pack(fill="x")
        else:
            self.canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self.canvas.bind("<Configure>", self._onConfigure)
        self.canvas.bind("<ButtonPress-1>", self._pointerDown)
        self.canvas.bind("<B1-Motion>", self._pointerMove)
        self.canvas.bind("<ButtonRelease-1>", self._pointerUp)
        # 押さえたまま窓の外へ出たら離す（枠外で指を離しても残さない）。
        self.canvas.bind("<Leave>", self._pointerUp)
        self.canvas.bind("<Motion>", self._hover)
        self._redraw(width, height)

    # ------------------------------------------------------------------
    # 描画
    # ------------------------------------------------------------------

    def _onConfigure(self, event: Any) -> None:
        self._redraw(event.width, event.height)

    def _redraw(self, width: int, height: int) -> None:
        """置き場所の広さに合わせて描き直す。縦横比を保ち、横は中央に寄せる。"""
        if self._embedded:
            # 高さは描いた分だけにする。高さで倍率を決めると、一度縮めた後に
            # 幅を広げても元に戻らない（低い高さに引きずられる）。
            s = fit_scale(width, PAD_H * self._base, base=self._base, max_zoom=1.0)
            fitted = math.ceil(PAD_H * s)
            if int(self.canvas.cget("height")) != fitted:
                self.canvas.config(height=fitted)
            oy = 0.0
        else:
            s = fit_scale(width, height, base=self._base, max_zoom=FLOATING_MAX_ZOOM)
            oy = max(0.0, (height - PAD_H * s) / 2)
        self._scale = s
        self._ox = max(0.0, (width - PAD_W * s) / 2)
        self._oy = oy
        self.canvas.delete("all")
        self._round_rect(0, 0, PAD_W, PAD_H, 30, fill=self.BODY, outline=self.BODY_EDGE)
        for shape in SHAPES:
            self._draw_shape(shape)
        # 描き直しで色が戻らないよう、押しているものを塗り直す。
        for name in self._held_btn:
            self._paint(name, True)
        self._paintHat()

    def _xy(self, x: float, y: float) -> tuple[float, float]:
        return self._ox + x * self._scale, self._oy + y * self._scale

    def _font(self, px: float) -> tuple[str, int, str]:
        # 負の大きさは px 指定。倍率に合わせて文字も拡縮する。
        return (self._family, -max(6, round(px * self._scale)), "bold")

    def _round_rect(
        self, x0: float, y0: float, x1: float, y1: float, r: float, **kw: Any
    ) -> int:
        """角丸の四角（論理座標）。Canvas に角丸が無いので滑らかな多角形で描く。"""
        r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
        pts = [
            (x0 + r, y0), (x1 - r, y0), (x1, y0), (x1, y0 + r),
            (x1, y1 - r), (x1, y1), (x1 - r, y1), (x0 + r, y1),
            (x0, y1), (x0, y1 - r), (x0, y0 + r), (x0, y0),
        ]  # fmt: skip
        flat = [v for x, y in pts for v in self._xy(x, y)]
        return int(self.canvas.create_polygon(flat, smooth=True, **kw))

    def _oval(self, cx: float, cy: float, r: float, **kw: Any) -> int:
        x0, y0 = self._xy(cx - r, cy - r)
        x1, y1 = self._xy(cx + r, cy + r)
        return int(self.canvas.create_oval(x0, y0, x1, y1, **kw))

    def _text(self, cx: float, cy: float, text: str, px: float, tags: Any) -> None:
        x, y = self._xy(cx, cy)
        self.canvas.create_text(
            x, y, text=text, fill=self.KEY_TEXT, font=self._font(px), tags=tags
        )

    def _draw_shape(self, shape: PadShape) -> None:
        if shape.kind == "dpad":
            self._draw_dpad(shape)
            return
        name = shape.name
        key = ("key", f"key:{name}")
        face: dict[str, Any] = {
            "fill": self.KEY,
            "outline": self.KEY_EDGE,
            "tags": (*key, f"face:{name}"),
        }
        x0, y0 = shape.cx - shape.w / 2, shape.cy - shape.h / 2
        x1, y1 = shape.cx + shape.w / 2, shape.cy + shape.h / 2
        if shape.kind == "pill":
            self._round_rect(x0, y0, x1, y1, shape.h / 2, **face)
        elif shape.kind == "square":
            self._round_rect(x0, y0, x1, y1, 6, **face)
            # キャプチャの印（丸）。文字が無くても形で分かる。
            self._oval(
                shape.cx, shape.cy, shape.w * 0.22, outline=self.KEY_TEXT,
                width=max(1, round(1.5 * self._scale)), tags=key,
            )  # fmt: skip
        elif shape.kind == "stick":
            # 外側の溝と、押し込めるスティックの頭。
            self._oval(
                shape.cx, shape.cy, shape.w / 2, fill=self.STICK_RING,
                outline=self.BODY_EDGE, tags=key,
            )  # fmt: skip
            self._oval(shape.cx, shape.cy, shape.w * 0.34, **face)
        else:
            self._oval(shape.cx, shape.cy, shape.w / 2, **face)
        if shape.label:
            px = self._LABEL_PX.get(shape.kind, 11)
            # 小さい丸の記号（− + ⌂）は文字だけ大きくして読めるようにする。
            if shape.kind == "circle" and shape.w < 28:
                px = 15
            self._text(shape.cx, shape.cy, shape.label, px, key)

    def _draw_dpad(self, shape: PadShape) -> None:
        """十字キー。4 本の腕を別々に塗れるようにし、斜めは 2 本を光らせる。"""
        cx, cy = shape.cx, shape.cy
        reach = shape.w / 2 - 2
        half = 10.0
        key = ("key", "key:DPAD")
        self._round_rect(
            cx - half, cy - half, cx + half, cy + half, 2,
            fill=self.KEY, outline="", tags=key,
        )  # fmt: skip
        for direction, dx, dy, arrow in self._DPAD_ARROWS:
            if dx:
                ax0, ax1 = sorted((cx + dx * half, cx + dx * reach))
                ay0, ay1 = cy - half, cy + half
            else:
                ay0, ay1 = sorted((cy + dy * half, cy + dy * reach))
                ax0, ax1 = cx - half, cx + half
            self._round_rect(
                ax0, ay0, ax1, ay1, 4, fill=self.KEY, outline=self.KEY_EDGE,
                tags=(*key, f"face:{direction}"),
            )  # fmt: skip
            mid = (half + reach) / 2
            self._text(cx + dx * mid, cy + dy * mid, arrow, 8, key)

    def _paint(self, name: str, on: bool) -> None:
        try:
            self.canvas.itemconfigure(
                f"face:{name}", fill=self.BUTTON_ACTIVE_BG if on else self.KEY
            )
        except tk.TclError:
            pass

    def _paintHat(self) -> None:
        """十字キーの腕を、押している向きの集合どおりに塗る。"""
        for direction in self.HAT_DIRS:
            self._paint(direction, direction in self._held_hat)

    # ------------------------------------------------------------------
    # マウス
    # ------------------------------------------------------------------
    # 押下と解放を別々に受ける。旧 UI は tk.Button の command（離したときに
    #   1 回だけ呼ばれる）で押しっぱなしを表現できず、さらに input →
    #   sleep(0.1) → inputEnd を GUI スレッドで同期実行して画面を止めていた。
    #   押した瞬間と離した瞬間の 2 回だけ送るので sleep もスレッドも要らない。

    def _hit(self, x: float, y: float) -> str | None:
        return hit_test((x - self._ox) / self._scale, (y - self._oy) / self._scale)

    def _pointerDown(self, event: Any) -> None:
        name = self._hit(event.x, event.y)
        if name is None:
            return
        self._pointerUp()
        self._pointer = name
        self._onPress(name)

    def _pointerMove(self, event: Any) -> None:
        """押さえたまま動かした。的から外れたら離す。

        十字キーの上で向きを変えたときは、いったん中立へ戻さず新しい向きへ
        切り替える（実機の十字キーを指で転がすのと同じ）。
        """
        current = self._pointer
        if current is None:
            return
        name = self._hit(event.x, event.y)
        if name == current:
            return
        if name in self.HAT_NAME2DIRS and current in self.HAT_NAME2DIRS:
            dirs = set(self.HAT_NAME2DIRS[name])
            if self._announceHat(dirs):
                self._held_hat = dirs
                self._pointer = name
                self._paintHat()
                return
        self._pointerUp()

    def _pointerUp(self, _event: Any = None) -> None:
        current, self._pointer = self._pointer, None
        if current is not None:
            self._onRelease(current)

    def _hover(self, event: Any) -> None:
        """押せる所の上だけ指のカーソルにする（どこが押せるか分かるように）。"""
        cursor = "hand2" if self._hit(event.x, event.y) else ""
        if str(self.canvas.cget("cursor")) != cursor:
            self.canvas.config(cursor=cursor)

    @property
    def ser(self) -> Any:
        """今の送り先。関数を渡されていれば呼んで引く（古い送り先を掴まない）。"""
        source = self._ser_source
        # Sender 自体は呼び出し可能ではない。送り先を返す関数かどうかで分ける。
        if callable(source) and not hasattr(source, "pressButtons"):
            return source()
        return source

    # ------------------------------------------------------------------
    # 押下・解放の受け口
    # ------------------------------------------------------------------
    # どれも送信側の差分申告の操作口を通す。送り主として「操作画面」を付ける
    #   ので、入力の優先付けでは人の手入力として扱われる。
    # 申告してから sendPosture で1行にまとめて送る。項目ごとに送ると
    #   行数が増え、送信の遅延予算に響く。

    def _onPress(self, name: str) -> None:
        """ボタンまたは十字キーを押した。"""
        if name in self.HAT_NAME2DIRS:
            self._pressHat(name)
        else:
            self._pressButton(name)

    def _onRelease(self, name: str) -> None:
        """ボタンまたは十字キーを離した。"""
        if name in self.HAT_NAME2DIRS:
            self._releaseHat(name)
        else:
            self._releaseButton(name)

    def _pressButton(self, name: str) -> None:
        """押していないときだけ申告する（二重押下を無視）。

        同じボタンの ButtonPress が続けて来ても送信は1回で済む。
          イベントの取りこぼしや連打に強くするための保持。
        申告が受理されてから保持と点灯を行う。調停で棄却されたのに
          点灯すると「押しているのに効かない」表示になる。棄却時は
          Sender の通知が出るので、ここでは黙って何もしない。
        """
        if name in self._held_btn:
            return
        btn = getattr(Button, name, None)
        if btn is None:
            return
        ser = live_sender(self.ser)
        if ser is None:
            return
        if not ser.pressButtons([int(btn)], source="gui"):
            return
        self._held_btn[name] = btn
        self._setActive(name, True)
        ser.sendPosture(source="gui")

    def _releaseButton(self, name: str) -> None:
        """押していたものだけ解放する（二重解放を無視）。"""
        btn = self._held_btn.pop(name, None)
        if btn is None:
            return
        self._setActive(name, False)
        ser = live_sender(self.ser)
        if ser is None:
            return
        if ser.releaseButtons([int(btn)], source="gui"):
            ser.sendPosture(source="gui")

    def _pressHat(self, name: str) -> None:
        """十字キーを押す。斜めのボタンは2方向を同時に押したものとして扱う。

        申告が受理されてから保持と点灯を行う（_pressButton と同じ理由）。
        """
        dirs = self.HAT_NAME2DIRS.get(name, ())
        if self._held_hat.issuperset(dirs):
            return
        if not self._announceHat(self._held_hat | set(dirs)):
            return
        self._held_hat.update(dirs)
        self._setActive(name, True)

    def _releaseHat(self, name: str) -> None:
        """十字キーを離す。解放は拒否されないため、常に反映する。"""
        dirs = self.HAT_NAME2DIRS.get(name, ())
        if not self._held_hat.intersection(dirs):
            return
        self._held_hat.difference_update(dirs)
        self._setActive(name, False)
        self._announceHat(set(self._held_hat))

    def _hatValue(self) -> Any:
        """押している向きの集合から Hat の値を決める。

        十字キーは「どれか一つの値」であり、ボタンのようなビット列ではない。
          ボタンのように OR で足せないため、組み合わせを表から引く。
          上下同時・左右同時のように打ち消し合う組み合わせや、3つ以上の
          同時押しは表に無い。その場合は中立へ倒す（実機の十字キーでも
          相反する方向は同時に入らない）。
        """
        return self._hatValueFor(self._held_hat)

    @staticmethod
    def _hatValueFor(held: Any) -> Any:
        """向きの集合から Hat の値を決める（_hatValue の実体）。

        受理前の仮の集合でも値を求められるよう、引数で受ける形に
        分けた。受理されてから self._held_hat へ反映する。
        """
        keys = tuple(sorted(d for d in held if d in ControllerGUI.HAT_DIRS))
        name = ControllerGUI.HAT_COMBO.get(keys, "CENTER")
        return getattr(Hat, name)

    def _announceHat(self, held: Any) -> bool:
        """向きの集合を申告して送る。受理したら True を返す。

        押している間は holdHat で「押しっぱなし」として申告し、
          離すときは releaseHat で取り下げる。
          中立を「中央値」で送ると、別の操作元が押しっぱなしにしている
            十字キーまで中立へ戻してしまう。
          取り下げなら、他が押していればその向きへ戻るだけで済む。
        退避: 古い送信側（holdHat を持たない）なら従来どおり値で送る。
        """
        ser = live_sender(self.ser)
        if ser is None:
            return False
        hold = getattr(ser, "holdHat", None)
        release = getattr(ser, "releaseHat", None)
        if callable(hold) and callable(release):
            if held:
                ok = hold(int(self._hatValueFor(held)), source="gui")
            else:
                ok = release(source="gui")
        else:
            ok = ser.setHat(int(self._hatValueFor(held)), source="gui")
        if ok:
            ser.sendPosture(source="gui")
        return bool(ok)

    def _applyHat(self) -> None:
        """現在の向きを申告して送る（後方互換の入口）。

        受理の成否は見ない。点灯と保持の整合が必要な新しい呼び出しは
        _announceHat を直接使うこと。
        """
        self._announceHat(set(self._held_hat))

    def _setActive(self, name: str, on: bool) -> None:
        """押している間だけ色を変える（押しっぱなしが目で分かるように）。

        十字キーは向きの集合が正なので、名前ではなく集合から腕を塗り直す。
        """
        if name in self.HAT_NAME2DIRS:
            self._paintHat()
        else:
            self._paint(name, on)

    def releaseAllHeld(self) -> None:
        """押しているものをすべて離す。

        窓を閉じるときや切断時に必ず呼ぶ。押しっぱなしのまま閉じると、
          解放が届かず Switch 側でボタンが押されたままになる。
        """
        for name in list(self._held_btn):
            self._releaseButton(name)
        for name in list(self.HAT_NAME2DIRS):
            self._setActive(name, False)
        if self._held_hat:
            self._held_hat.clear()
            self._applyHat()
        self._paintHat()

    def bind(self, event: str, func: Any) -> None:
        self.window.bind(event, func)

    def protocol(self, event: str, func: Any) -> None:
        # 埋め込み（Frame）には窓の閉じるボタンが無い。
        if isinstance(self.window, tk.Toplevel):
            self.window.protocol(event, func)

    def focus_force(self) -> None:
        self.window.focus_force()

    def destroy(self) -> None:
        # 押しっぱなしのまま閉じると解放が届かず、
        # Switch 側でボタンが押されたままになる。必ず離してから閉じる。
        self.releaseAllHeld()
        flush = getattr(self.ser, "flushPending", None)
        if callable(flush):
            flush()
        self.window.destroy()
        logger.debug("GUI controller destroyed")
