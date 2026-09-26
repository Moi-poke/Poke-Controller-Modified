"""Non-Windows preview surface. Draws the same picture with PhotoImage + Canvas.

Design: docs/superpowers/specs/2026-09-26-gdi-preview-renderer-design.md 8.
Windows has no GDI child window here, so the same ``PreviewRenderer`` protocol
is met by this implementation. A platform requirement, not generalisation.

It touches tkinter, so it lives in ui/ (which may, unlike services/).
"""

from __future__ import annotations

import time
import tkinter as tk
from typing import Any, Final

import cv2
import numpy as np
from PIL import Image, ImageTk
from core.Camera import CAPTURE_SIZE
from core.preview_renderer import TK_COLORREF, OverlayState, RenderResult

# The reverse of the single authored table, derived rather than written out
# twice: this backend needs COLORREF -> name, and a second hand-kept table could
# drift from core's without anything failing. Derived at import, so it cannot.
# The eight values are distinct, so the mapping is 1:1.
_NAME_BY_COLORREF: Final[dict[int, str]] = {
    colorref: name for name, colorref in TK_COLORREF.items()
}


class PhotoImageSurface:
    """非 Windows 用の描画面。Canvas に PhotoImage を置いて同じ絵を描く。

    映像を Canvas の項目として持つので、設計 0節で問題にした「子窓が
    親より上に合成される」事情は起きない（そもそも子窓が無い）。
    60fps は出ない（設計 13節）。PreviewRenderer の形を満たすことが要件
    なので、毎フレーム PhotoImage を作り直す速度の我省略はしない。
    """

    def __init__(self, host: Any) -> None:
        self._host = host
        self._canvas: Any = None
        self._image_id: Any = None
        self._size = (0, 0)
        self._photo: Any = None
        self._overlay: OverlayState = OverlayState()

    def attach(self, parent_hwnd: int, size: tuple[int, int]) -> None:
        # 子窓を持たないので親 HWND は使わない（プロトコルの形は合わせる）。
        _ = parent_hwnd
        self._canvas = tk.Canvas(
            self._host, borderwidth=0, highlightthickness=0, cursor=""
        )
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._image_id = self._canvas.create_image(0, 0, anchor=tk.NW)
        self.resize(size)

    def resize(self, size: tuple[int, int]) -> None:
        self._size = (int(size[0]), int(size[1]))
        if self._canvas is not None:
            self._canvas.config(width=self._size[0], height=self._size[1])

    def compose(self, frame: np.ndarray, overlay: OverlayState) -> RenderResult:
        started = time.perf_counter_ns()
        if self._canvas is None:
            return RenderResult(False, time.perf_counter_ns() - started, "no_hwnd")
        frame_size = frame.shape[1::-1]
        # 判定の相手は _size でもなく canvas でもなく、カメラが返す映像の解像度
        # （CAPTURE_SIZE）である。GDI 面の core/gdi_surface.py と同じ規則。
        # _size を相手にすると、<Configure> で表示サイズが変わった途端に
        # 生きているフレームを 1 枚も描かなくなる。
        # 縮小も拡大もしない。寸法が違えば捨てる。
        if frame_size != CAPTURE_SIZE:
            return RenderResult(
                False, time.perf_counter_ns() - started, "dimension_mismatch"
            )
        if frame_size == self._size:
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        else:
            # 受け皿が映像と違う大きさなら 1:1 の範囲だけ描く。大きい時は
            # 残りを黒で埋め、小さい時は切る。換算しないので GDI 面と同じ絵になる。
            height = min(frame.shape[0], self._size[1])
            width = min(frame.shape[1], self._size[0])
            image = Image.new("RGB", self._size)
            image.paste(
                Image.fromarray(
                    cv2.cvtColor(frame[:height, :width], cv2.COLOR_BGR2RGB)
                ),
                (0, 0),
            )
        self._photo = ImageTk.PhotoImage(image)
        self._overlay = overlay
        return RenderResult(True, time.perf_counter_ns() - started, "ok")

    def present(self) -> RenderResult:
        started = time.perf_counter_ns()
        if self._canvas is None or self._photo is None:
            return RenderResult(False, time.perf_counter_ns() - started, "no_hwnd")
        self._canvas.itemconfig(self._image_id, image=self._photo)
        self._draw_overlay(self._overlay)
        return RenderResult(True, time.perf_counter_ns() - started, "ok")

    def release(self) -> None:
        canvas = self._canvas
        self._photo = None
        self._canvas = None
        self._image_id = None
        if canvas is not None:
            canvas.destroy()

    def client_size(self) -> tuple[int, int]:
        return self._size

    def _draw_overlay(self, overlay: OverlayState) -> None:
        # 項目は作り直さない。前回分を "overlay" タグでまとめて消す。
        self._canvas.delete("overlay")
        for stick, color in (
            (overlay.left_stick, "cyan"),
            (overlay.right_stick, "red"),
        ):
            if not stick.active:
                continue
            r = stick.radius
            k = r // 10
            self._canvas.create_oval(
                stick.center_x - r,
                stick.center_y - r,
                stick.center_x + r,
                stick.center_y + r,
                outline=color,
                tags="overlay",
            )
            self._canvas.create_oval(
                stick.knob_x - k,
                stick.knob_y - k,
                stick.knob_x + k,
                stick.knob_y + k,
                fill=color,
                tags="overlay",
            )
        guide = overlay.guide
        if guide.visible:
            self._canvas.create_rectangle(
                guide.x0,
                guide.y0,
                guide.x1,
                guide.y1,
                outline="red",
                dash=(4, 4),
                tags="overlay",
            )
        rect = overlay.img_rect
        if rect.visible:
            self._canvas.create_rectangle(
                rect.outer.x0,
                rect.outer.y0,
                rect.outer.x1,
                rect.outer.y1,
                width=4,
                outline="white",
                tags="overlay",
            )
            self._canvas.create_rectangle(
                rect.inner.x0,
                rect.inner.y0,
                rect.inner.x1,
                rect.inner.y1,
                width=2,
                outline=self._color_name(rect.color),
                tags="overlay",
            )

    @staticmethod
    def _color_name(colorref: int) -> str:
        """COLORREF を Tk の色名へ戻す（core の表を逆引きする）。"""
        return _NAME_BY_COLORREF.get(colorref, "white")
