"""WindowGeometry.py - ウィンドウ位置と仕切り位置の保存・復元.

Window.py から切り出した。触るのは root（ウィンドウ）と settings の2つ
だけなので、引数で受け取れば画面の他の部分と関わらずに済む。

ここで扱う値には、素直に保存すると事故る性質が2つある。

1. 最大化・最小化中の geometry を保存すると、次回もその状態で開く
   → 通常状態(normal)のときだけ控える。

2. モニタを外した後に画面外の座標を復元すると、ウィンドウがどこにも
   見えず操作できなくなる
   → 左上が画面内に残っているかを見て、外れていれば復元しない。

仕切り位置も同じで、画素数のまま持つとウィンドウの高さが変わった
瞬間に意味が変わる。割合(0.0〜1.0)で持ち、実際の高さを掛けて戻す。
"""

from __future__ import annotations

import re
import tkinter as tk
from collections.abc import Callable
from typing import Any, Final

from loguru import logger

# 仕切りの割合。片方が潰れて操作できなくならないよう範囲を限る。
SASH_MIN = 0.1
SASH_MAX = 0.9
SASH_DEFAULT = 0.6

# 位置の復元を許す余白。タイトルバーが掴める程度は画面内に残すこと。
SCREEN_MARGIN = 100

ASPECT_RATIO_WIDTH: Final[int] = 16
ASPECT_RATIO_HEIGHT: Final[int] = 9


class WindowAspectLock:
    """通常状態のウィンドウを16:9の整数サイズへ戻すセッション限りのポリシー."""

    def __init__(
        self,
        root: tk.Tk,
        on_warning: Callable[[str], None] | None = None,
    ) -> None:
        self._root = root
        self._on_warning = on_warning
        self._enabled = False
        self._after_id: str | None = None
        self._binding_id: str | None = root.bind(
            "<Configure>", self._on_configure, add="+"
        )

    @property
    def enabled(self) -> bool:
        """現在の固定状態返す."""
        return self._enabled

    def set_enabled(self, enabled: bool) -> bool:
        """固定をセッション内で切り替え、実効状態を返す."""
        if not enabled:
            self._enabled = False
            self._cancel_pending()
            return False
        self._cancel_pending()
        self._enabled = True
        if self._root.state() != "normal":
            return True
        self._normalize()
        return self._enabled

    def cleanup(self) -> None:
        """Configure の予約とバインドを片付ける."""
        self._enabled = False
        self._cancel_pending()
        if self._binding_id is None:
            return
        try:
            self._root.unbind("<Configure>", self._binding_id)
        except tk.TclError:
            logger.debug("Failed to remove the window aspect Configure handler")
        self._binding_id = None

    def _on_configure(self, _event: tk.Event[tk.Misc]) -> None:
        if not self._enabled or self._root.state() != "normal":
            return
        if self._after_id is not None:
            return
        self._after_id = self._root.after_idle(self._normalize)

    def _normalize(self) -> None:
        self._after_id = None
        if not self._enabled or self._root.state() != "normal":
            return
        try:
            width = self._root.winfo_width()
            height = self._root.winfo_height()
            min_size = self._root.wm_minsize()
            max_size = self._root.wm_maxsize()
        except tk.TclError:
            self._disable_with_warning("ウィンドウ寸法を読み取れませんでした")
            return

        target = self._target_size(width, min_size, max_size)
        if target is None:
            self._disable_with_warning("16:9に固定できるウィンドウ寸法がありません")
            return
        target_width, target_height = target
        if (width, height) == (target_width, target_height):
            return
        try:
            self._root.geometry(f"{target_width}x{target_height}")
        except tk.TclError:
            self._disable_with_warning("16:9のウィンドウ寸法へ変更できませんでした")

    def _cancel_pending(self) -> None:
        if self._after_id is None:
            return
        try:
            self._root.after_cancel(self._after_id)
        except tk.TclError:
            logger.debug("Failed to cancel the window aspect Configure handler")
        self._after_id = None

    def _disable_with_warning(self, message: str) -> None:
        self._enabled = False
        if self._on_warning is None:
            logger.warning(message)
            return
        self._on_warning(message)

    @staticmethod
    def _target_size(
        width: int,
        min_size: tuple[int, int],
        max_size: tuple[int, int],
    ) -> tuple[int, int] | None:
        min_width, min_height = min_size
        max_width, max_height = max_size
        min_k = max(
            1,
            (max(1, min_width) + ASPECT_RATIO_WIDTH - 1) // ASPECT_RATIO_WIDTH,
            (max(1, min_height) + ASPECT_RATIO_HEIGHT - 1) // ASPECT_RATIO_HEIGHT,
        )
        max_k = min(
            max_width // ASPECT_RATIO_WIDTH,
            max_height // ASPECT_RATIO_HEIGHT,
        )
        if max_k < min_k:
            return None
        desired_k = max(
            1,
            (max(1, width) + ASPECT_RATIO_WIDTH // 2) // ASPECT_RATIO_WIDTH,
        )
        k = min(max(desired_k, min_k), max_k)
        return ASPECT_RATIO_WIDTH * k, ASPECT_RATIO_HEIGHT * k


def clampRatio(ratio: float) -> float:
    """仕切りの割合を扱える範囲へ収める。"""
    return min(SASH_MAX, max(SASH_MIN, ratio))


def rememberGeometry(root: tk.Tk, settings: Any) -> None:
    """ウィンドウの位置とサイズを設定へ控える。

    最大化・最小化された状態の geometry を保存すると、次回そのまま
    復元されて使いにくい。通常状態(normal)のときだけ控える。
    """
    try:
        if root.state() != "normal":
            return
        settings.window_geometry.set(root.geometry())
    except tk.TclError:
        # 破棄済みなど。位置の記憶は本質ではないので黙って諦める
        logger.debug("Failed to read the window geometry")


def restoreGeometry(root: tk.Tk, settings: Any) -> None:
    """前回のウィンドウ位置とサイズを復元する。"""
    if not settings.restore_geometry.get():
        return
    geometry = settings.window_geometry.get()
    if not geometry:
        return
    # 画面構成が変わって完全に画面外になっていたら復元しない
    if not isOnScreen(root, geometry):
        logger.warning(f"Saved geometry is off-screen. ignored: {geometry}")
        return
    try:
        root.geometry(geometry)
    except tk.TclError:
        logger.warning(f"Invalid geometry in settings: {geometry}")


def isOnScreen(root: tk.Misc, geometry: str) -> bool:
    """保存された位置が画面内に残っているかを判定する。

    モニタを外した後などに画面外の座標を復元すると、ウィンドウが
    どこにも見えず操作できなくなる。左上が画面内にあるかだけ見る
    （厳密な多画面判定は tkinter では取れないので、これで十分）。
    """
    m = re.search(r"([+-]\d+)([+-]\d+)$", geometry)
    if not m:
        return True  # サイズだけの指定なら位置は変わらない
    x, y = int(m.group(1)), int(m.group(2))
    return (
        -SCREEN_MARGIN <= x <= root.winfo_screenwidth() - SCREEN_MARGIN
        and -SCREEN_MARGIN <= y <= root.winfo_screenheight() - SCREEN_MARGIN
    )


def restoreSash(pane: Any, settings: Any, retry: Any = None) -> bool:
    """ログ欄の仕切り位置を前回の値へ戻す。

    sashpos は「上端からの画素数」なので、ウィンドウの高さが変わると
    意味が変わってしまう。割合(0.0〜1.0)で持っておき、実際の高さを
    掛けて戻す。極端な値だと片方が潰れて操作できなくなるため、
    1割〜9割の範囲に収める。

    まだ実寸が決まっていないときは False を返す。呼び出し側が
    after で呼び直す（ここで after を呼ぶと root を持つ必要が出る）。
    """
    try:
        ratio = float(settings.log_sash_ratio.get())
    except (TypeError, ValueError):
        ratio = SASH_DEFAULT
    ratio = clampRatio(ratio)
    try:
        height = pane.winfo_height()
        if height <= 1:
            return False
        pane.sashpos(0, int(height * ratio))
    except tk.TclError:
        logger.debug("Failed to restore the log sash position")
    return True


def rememberSash(pane: Any, settings: Any) -> bool:
    """仕切りを動かしたら割合として控える。

    書き出しが必要になったときだけ True を返す。誤差程度の変化で
    毎回ファイルへ書きに行かないための判定をここへ寄せている。
    """
    try:
        height = pane.winfo_height()
        if height <= 1:
            return False
        ratio = pane.sashpos(0) / height
    except (tk.TclError, ZeroDivisionError):
        return False
    ratio = clampRatio(ratio)
    if abs(ratio - float(settings.log_sash_ratio.get() or 0)) < 0.01:
        return False  # 誤差程度の変化で毎回書きに行かない
    settings.log_sash_ratio.set(round(ratio, 3))
    return True
