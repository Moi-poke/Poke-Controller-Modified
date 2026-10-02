"""プレビュー描画の契約層。tkinter にも PIL にも触れない。

設計: docs/superpowers/specs/2026-09-26-gdi-preview-renderer-design.md 5節。
Canvas の項目は廃止し、オーバーレイは「データ」として renderer へ渡す。
Tk も Win32 も知らないので core/ の境界内に置ける。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

import numpy as np

#: Tk の色名 → Win32 ``COLORREF`` (``0x00BBGGRR``)。
#:
#: Tk 内部形は ``0x00RRGGBB``、Win32 ``COLORREF`` は ``0x00BBGGRR`` で、
#: **バイト位置が逆**。Tk の値をそのまま pen や brush へ渡すと赤と青が
#: 入れ替わる（``"blue"`` が赤になる）。変換は必ずこの境界で一度行う。
#:
#: ``"blue"`` が ``0x00FF0000`` に見えるのはこの反転の帰結であって誤りでは
#: ない。認識経路が渡す名前は ``"blue"`` と ``"red"`` の2つだけ
#: （``core/CommandVision.py``）だが、範囲枠とスティックの色もここを通す。
TK_COLORREF: Final[dict[str, int]] = {
    "black": 0x00000000,
    "white": 0x00FFFFFF,
    "red": 0x000000FF,
    "green": 0x0000FF00,
    "blue": 0x00FF0000,
    "yellow": 0x0000FFFF,
    "cyan": 0x00FFFF00,
    "magenta": 0x00FF00FF,
}


@dataclass(frozen=True, slots=True)
class RenderResult:
    """1 回の描画操作の結果。例外にしないので mainloop は生き残る。"""

    ok: bool
    elapsed_ns: int
    # "ok" / "no_hwnd" / "frame_not_contiguous" / "dimension_mismatch"
    # "no_frame" / "getdc_failed" / "bitblt_failed"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class StickState:
    """スティックの外周円とノブ。押下中も外周円は動かない。"""

    active: bool = False
    center_x: int = 0
    center_y: int = 0
    radius: int = 0
    knob_x: int = 0  # ドラッグで動く
    knob_y: int = 0


@dataclass(frozen=True, slots=True)
class RectState:
    """範囲枠。Tk と同じ包含端の座標をそのまま持つ。"""

    x0: int = 0
    y0: int = 0
    x1: int = 0
    y1: int = 0
    visible: bool = False


@dataclass(frozen=True, slots=True)
class ImgRectState:
    """認識枠。白い外枠と認識色の内枠は別々の矩形なので両方持つ。

    ``color`` は Tk の色名ではなく Win32 ``COLORREF`` (0x00BBGGRR)。Tk 内部の
    0x00RRGGBB とバイト位置が逆なので、変換は境界で一度だけ行う。
    """

    outer: RectState = RectState()
    inner: RectState = RectState()
    visible: bool = False
    color: int = 0


@dataclass(frozen=True, slots=True)
class OverlayState:
    """1 フレーム分のオーバーレイ。不変なので中途半端な更新は現れない。"""

    left_stick: StickState = StickState()
    right_stick: StickState = StickState()
    guide: RectState = RectState()
    img_rect: ImgRectState = ImgRectState()


@runtime_checkable
class PreviewRenderer(Protocol):
    """合成と提示を分けるのは、合成時間と blit 時間を別々に測るため。"""

    def attach(self, parent_hwnd: int, size: tuple[int, int]) -> None: ...

    def resize(self, size: tuple[int, int]) -> None: ...

    def compose(self, frame: np.ndarray, overlay: OverlayState) -> RenderResult: ...

    def recompose(self, overlay: OverlayState) -> RenderResult: ...

    def present(self) -> RenderResult: ...

    def release(self) -> None: ...

    def client_size(self) -> tuple[int, int]: ...
