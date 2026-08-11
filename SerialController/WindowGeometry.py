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
from typing import Any

from loguru import logger

# 仕切りの割合。片方が潰れて操作できなくならないよう範囲を限る。
SASH_MIN = 0.1
SASH_MAX = 0.9
SASH_DEFAULT = 0.6

# 位置の復元を許す余白。タイトルバーが掴める程度は画面内に残すこと。
SCREEN_MARGIN = 100


def clampRatio(ratio: float) -> float:
    """仕切りの割合を扱える範囲へ収める。"""
    return min(SASH_MAX, max(SASH_MIN, ratio))


def rememberGeometry(root: tk.Misc, settings: Any) -> None:
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


def restoreGeometry(root: tk.Misc, settings: Any) -> None:
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
