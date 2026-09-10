#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LogPane合流（行単位put＋flushで部分行）の検証。"""

from __future__ import annotations

import queue

from LogPane import DropOldestQueue, QueueStdoutRedirector


def _make_redirector() -> tuple[QueueStdoutRedirector, DropOldestQueue]:
    q: DropOldestQueue = DropOldestQueue(maxsize=10000)
    r = QueueStdoutRedirector()
    r.buffer = q  # type: ignore[assignment]
    return r, q


def _drain(q: queue.Queue) -> list[str]:
    out: list[str] = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            break
    return out


def test_coalesces_fragments_to_one_put_per_line() -> None:
    """断片化した書き込みは行単位で1putにまとめる（mutex往復を約半減）。"""
    r, q = _make_redirector()
    for _ in range(50):
        r.write("hello ")
        r.write("world\n")
    # 現状は100put、合流後は50putになるはず。
    assert q.qsize() == 50, f"合流されていません: qsize={q.qsize()}"
    lines = _drain(q)
    assert lines == ["hello world\n"] * 50


def test_partial_line_appears_after_flush() -> None:
    """改行なしの部分行はflush()で姿を現す。"""
    r, q = _make_redirector()
    r.write("partial-no-newline")
    # まだキューに出さず溜めるのが仕様。
    assert q.qsize() == 0
    r.flush()
    got = _drain(q)
    assert got == ["partial-no-newline"]


def test_multiline_single_write_splits() -> None:
    """1回のwriteに複数行が混ざっても行単位で出す。"""
    r, q = _make_redirector()
    r.write("a\nb\nc")
    # "a\n" "b\n" の2put、"c" は部分行として残る。
    assert q.qsize() == 2
    assert _drain(q) == ["a\n", "b\n"]
    r.flush()
    assert _drain(q) == ["c"]


def test_giant_partial_flushes_without_newline() -> None:
    """巨大な部分行は改行を待たずに姿を現す（stall 防止の size cap）。

    print(end="") の進捗表示などが溜まり続けると flush まで画面に
    出ない。cap（数KB）を超えたら1putで救済放出する。
    """
    r, q = _make_redirector()
    big = "x" * 9000
    r.write(big)
    got = _drain(q)
    assert got == [big]


def test_small_partial_still_waits_for_newline() -> None:
    """小さな部分行は従来どおり溜める（行単位1putの性能を保つ）。"""
    r, q = _make_redirector()
    r.write("abc")
    assert q.qsize() == 0
    r.write("def\n")
    assert _drain(q) == ["abcdef\n"]


def test_stale_partial_flushes_on_next_write() -> None:
    """古い部分行は次の write で救済放出する（stall 防止の age cap）。

    少しずつしか来ない表示が改行なしで止まると、flush まで出ない。
    最初に溜めた時刻から約1秒を超えたら、次の write で一緒に出す。
    """
    r, q = _make_redirector()
    r.write("abc")
    assert q.qsize() == 0
    assert r._partial_since is not None
    r._partial_since -= 2.0  # 1秒以上前に溜めたことにする
    r.write("d")
    assert _drain(q) == ["abcd"]
