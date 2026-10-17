#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui_inbox.py - 裏スレッドから GUI スレッドへの受け渡し箱（純粋層）。

Tk には一切触らない。ワーカーは積むだけ、GUI 側が定期的に取り出す。

なぜ要るか:
  ワーカースレッドから root.after を呼ぶのは _tkinter の公式な使い方では
  ない。mainloop が回っていれば main スレッドへ処理を依頼するが、その間
  **呼び出し側のスレッドは止まる**ので、GUI 側がそのスレッドの終了を待つ
  経路（終了処理の join など）と組み合わさると互いに待ち合う。mainloop の
  開始前・終了後は RuntimeError になる。
  積むだけなら待たないので、そのどちらも起きない。

2 種類の積み方:
  post         閉じるまでは必ず届ける（閉じた後に積まれた分は on_drop だけ
               実行する）。受け渡しや後始末の通知に使う。
  post_latest  同じ key は最新の 1 件だけ残す。表示の更新に使う。
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any

from loguru import logger


class UiInbox:
    """裏スレッドから GUI スレッドへの受け渡し。Tk には触らない。

    積んだ必達は閉じるまでは必ず届ける。閉じた後に積まれた分は実行せず、
    on_drop（後始末）だけをその場で走らせる。
    """

    def __init__(self) -> None:
        self._must: queue.SimpleQueue[
            tuple[Callable[..., Any], tuple[Any, ...], Callable[[], None] | None]
        ] = queue.SimpleQueue()
        self._latest: dict[str, tuple[Callable[..., Any], tuple[Any, ...]]] = {}
        self._lock = threading.Lock()
        self._closed = False

    def post(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_drop: Callable[[], None] | None = None,
    ) -> None:
        """必ず届ける 1 件を積む（閉じるまでは捨てない）。

        on_drop は「実行されずに捨てられたとき」の後始末（開き途中の
        カメラを閉じる等）。閉じた後に積まれた分は、その場で実行する。
        """
        with self._lock:
            if not self._closed:
                self._must.put((fn, args, on_drop))
                return
        if on_drop is not None:
            try:
                on_drop()
            except Exception:
                logger.exception("受け渡し箱を閉じた後の後始末で例外")

    def close(self) -> int:
        """以後の post を断り、残っている必達分の後始末を実行する。

        実行されなかった処理そのものは呼ばない（GUI が無いので触れない）。
        on_drop だけを、積んだ側の代わりにここで走らせる。
        """
        with self._lock:
            self._closed = True
        dropped = 0
        while True:
            try:
                _fn, _args, on_drop = self._must.get_nowait()
            except queue.Empty:
                return dropped
            if on_drop is not None:
                try:
                    on_drop()
                except Exception:
                    logger.exception("終了時の後始末で例外")
            dropped += 1

    def post_latest(self, key: str, fn: Callable[..., Any], *args: Any) -> None:
        """同じ key は最新の 1 件だけ残す（表示の更新向け）。

        閉じた後は積まない（破棄済みの widget を掴んだままにしない）。
        """
        with self._lock:
            if self._closed:
                return
            self._latest[key] = (fn, args)

    def drain(self, budget: int = 64) -> int:
        """溜まった分を実行する。GUI スレッドからだけ呼ぶ。

        必達（状態を変える処理）を先に、coalesce した表示の更新を後に流す。
        逆にすると、変わる前の状態を 1 周期ぶん表示してしまう。
        1 回あたりの件数に上限を置く（表示の周期を守るため）。戻り値は
        実行した件数で、残っていれば次の周期が拾う。
        """
        if self._closed:
            # 閉じた後は何もしない（破棄済みの widget を触ると TclError になる）。
            with self._lock:
                self._latest.clear()
            return 0
        jobs: list[tuple[Callable[..., Any], tuple[Any, ...]]] = []
        for _ in range(budget):
            try:
                fn, args, _on_drop = self._must.get_nowait()
                jobs.append((fn, args))
            except queue.Empty:
                break
        if not self._must.empty():
            # 積み残しが続くのは異常（毎周期 64 件を超えて積まれている）。
            logger.debug("GUI への受け渡しが滞留しています")
        with self._lock:
            latest, self._latest = self._latest, {}
        jobs.extend(latest.values())
        done = 0
        for fn, args in jobs:
            try:
                fn(*args)
            except Exception:
                # 1 件の失敗で残りを捨てない。規約どおりログは残す。
                logger.exception("GUI への受け渡し処理で例外")
            done += 1
        return done

    def pending(self) -> int:
        """残件数（最新側のみ。SimpleQueue は件数を公開しない）。"""
        with self._lock:
            return len(self._latest)


#: 逃げ道（inbox を持たない宿主）を通ったことを 1 回だけ知らせる。
_FALLBACK_WARNED = False


def _fallback_to_after(
    owner: Any,
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    on_drop: Callable[[], None] | None,
) -> None:
    """inbox を持たない宿主向けの逃げ道。通ったら 1 回だけ error を出す。

    別スレッドからは Tk を触らない（それが元の欠陥そのもの）。メイン
    スレッドのときだけ直接呼び、それ以外は on_drop に倒す。
    """
    global _FALLBACK_WARNED
    if not _FALLBACK_WARNED:
        _FALLBACK_WARNED = True
        logger.error("inbox を持たない宿主から GUI へ渡そうとしました（結線漏れ）")
    if threading.current_thread() is not threading.main_thread():
        logger.error("別スレッドから Tk を直接呼びそうになりました（inbox 未結線）")
        if on_drop is not None:
            try:
                on_drop()
            except Exception:
                logger.exception("逃げ道の後始末で例外")
        return
    # after に引数を渡せない実装（テストの偽物など）でも通るよう lambda で包む。
    owner.root.after(0, lambda: fn(*args))


def post_to_gui(
    owner: Any,
    fn: Callable[..., Any],
    *args: Any,
    on_drop: Callable[[], None] | None = None,
) -> None:
    """宿主の inbox へ積む（on_drop は捨てられたときの後始末）。"""
    inbox = getattr(owner, "inbox", None)
    if inbox is None:
        _fallback_to_after(owner, fn, args, on_drop)
        return
    inbox.post(fn, *args, on_drop=on_drop)


def post_latest_to_gui(
    owner: Any, key: str, fn: Callable[..., Any], *args: Any
) -> None:
    """表示の更新用。inbox が無ければ従来どおり after へ回す。"""
    inbox = getattr(owner, "inbox", None)
    if inbox is None:
        _fallback_to_after(owner, fn, args, None)
        return
    inbox.post_latest(key, fn, *args)
