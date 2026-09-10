"""音声スレッド改善の回帰検証（実機なし・偽物＋計数＋時間で確認）。"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any

import numpy as np
import pytest
from core import AudioCapture as AC


class FakeStream:
    """sounddevice ストリームの偽物。fire() で受信を再現する。"""

    def __init__(self, **kwargs: Any) -> None:
        self.callback: Any = kwargs["callback"]
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True

    def fire(self, data: np.ndarray) -> None:
        assert self.started
        self.callback(data, len(data), None, None)


def _open_cap(
    jitter: int = 12,
    box: dict[str, FakeStream] | None = None,
) -> tuple[AC.AudioCapture, dict[str, FakeStream]]:
    holder: dict[str, FakeStream] = box if box is not None else {}

    def in_factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        holder["in"] = stream
        return stream

    def out_factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        holder["out"] = stream
        return stream

    cap = AC.AudioCapture(
        input_factory=in_factory,  # type: ignore[arg-type]
        output_factory=out_factory,  # type: ignore[arg-type]
        jitter_chunks=jitter,
    )
    assert cap.openInput("dummy") is True
    return cap, holder


def test_1_input_callback_minimal_48k() -> None:
    """48k原生でもコールバック内で重い変換・記録をしない（消費者側へ）。"""
    cap, box = _open_cap()
    # 48k専用機のふり（自レート48k・内部44.1k）
    cap._in_rate = 48000  # type: ignore[attr-defined]
    calls: list[str] = []
    real_to_internal = AC.to_internal

    def counting_to_internal(mono: np.ndarray, src_rate: int) -> np.ndarray:
        calls.append("to_internal")
        return real_to_internal(mono, src_rate)

    logged: list[str] = []
    real_debug = AC.logger.debug
    AC.logger.debug = lambda *a, **k: logged.append(str(a))  # type: ignore[assignment]
    AC.to_internal = counting_to_internal  # type: ignore[assignment]
    try:
        chunk48 = np.full(512, 0.3, dtype=np.float32)
        t0 = time.perf_counter()
        box["in"].fire(chunk48)
        dt = time.perf_counter() - t0
        # コールバック内では変換しない（消費者側の readWindow で行う）
        assert calls == []
        # 実時間スレッドでログを出さない（計数のみ）
        assert logged == []
        assert dt < 0.010
        # 消費者側では内部レートへ直る（0.05秒ぶん溜めてから読む）
        for _ in range(4):
            box["in"].fire(chunk48)
        calls.clear()
        window = cap.readWindow(0.05)
        assert window is not None
        assert len(calls) >= 1
        # 内部レート換算で件数が合う（48k→44.1k）
        assert abs(window.size - int(44100 * 0.05)) <= 4
    finally:
        AC.to_internal = real_to_internal  # type: ignore[assignment]
        AC.logger.debug = real_debug  # type: ignore[assignment]
        cap.close()


def test_1_input_overflow_counts_without_log() -> None:
    """あふれは計数のみ（ログは消費者側）。2回目の複製を作らない。"""

    class Status:
        input_overflow = True

    class OtherStatus:
        input_overflow = False

        def __str__(self) -> str:
            return "dummy-status"

    cap, box = _open_cap()
    real_debug = AC.logger.debug
    logged: list[str] = []
    AC.logger.debug = lambda *a, **k: logged.append(str(a))  # type: ignore[assignment]
    try:
        before = cap.getMonitorStats()["in_overflow"]
        box["in"].callback(np.full(512, 0.1, dtype=np.float32), 512, None, Status())
        after = cap.getMonitorStats()["in_overflow"]
        assert after == before + 1
        #  overflow以外の状態でも実時間スレッドでは記録しない
        box["in"].callback(
            np.full(512, 0.1, dtype=np.float32), 512, None, OtherStatus()
        )
        assert logged == []
    finally:
        AC.logger.debug = real_debug  # type: ignore[assignment]
        cap.close()


def test_2_pipe_gated_when_monitor_off() -> None:
    """監視OFFでは送らない（幻の欠落を数えない）。"""
    cap, box = _open_cap(jitter=2)
    assert cap.isMonitorEnabled() is False
    for _ in range(5):
        box["in"].fire(np.full(512, 0.2, dtype=np.float32))
    stats = cap.getMonitorStats()
    assert stats["drops"] == 0
    assert cap._pipe.qsize() == 0  # type: ignore[attr-defined]
    cap.close()


def test_3_output_nonblocking() -> None:
    """出力は待たない（空でも即無音＋計数）。"""
    cap, box = _open_cap(jitter=4)
    assert cap.setMonitorEnabled(True) is True
    # 管を空にする（前置分があれば捨てる）
    try:
        while True:
            cap._pipe.get_nowait()  # type: ignore[attr-defined]
    except queue.Empty:
        pass
    cap._mon_stats["silence"] = 0  # type: ignore[attr-defined]
    buf = np.zeros((512, 1), dtype=np.float32)
    t0 = time.perf_counter()
    box["out"].callback(buf, 512, None, None)
    dt = time.perf_counter() - t0
    assert dt < 0.010
    assert float(np.max(np.abs(buf))) == 0.0
    assert cap.getMonitorStats()["silence"] >= 1
    cap.close()


def test_3b_prebuffer_small_on_enable() -> None:
    """有効化時は直近少量だけ前置（古い溜まりは捨てる）。"""
    cap, box = _open_cap(jitter=12)
    for i in range(10):
        box["in"].fire(np.full(512, 0.05 * (i + 1), dtype=np.float32))
    # OFFの間は管へ送らないため、有効化前の管は空のはず
    assert cap._pipe.qsize() == 0  # type: ignore[attr-defined]
    assert cap.setMonitorEnabled(True) is True
    qsize = cap._pipe.qsize()  # type: ignore[attr-defined]
    # 少量の前置（2段程度）。12段ぶんの古い遅延は持たない。
    assert 0 < qsize <= 3
    cap.close()


def test_4_readwindow_concatenate_outside_lock() -> None:
    """長い窓の結合は錠の外（呼び側の待ちで欠落させない）。"""
    small, sbox = _open_cap()
    # 環を小さくして巻かせる（試験用に直接縮める）
    small._ring = np.zeros(1024, dtype=np.float32)  # type: ignore[attr-defined]
    small._pos = 0  # type: ignore[attr-defined]
    small._filled = 0  # type: ignore[attr-defined]
    small._total = 0  # type: ignore[attr-defined]
    for _ in range(3):
        sbox["in"].fire(np.full(512, 0.4, dtype=np.float32))
    real_concat = np.concatenate
    held: list[bool] = []

    def checking_concat(seq: Any, *a: Any, **k: Any) -> np.ndarray:
        # 同じ糸が錠を持っていれば再取得は失敗する（非再入）
        ok = small._lock.acquire(blocking=False)  # type: ignore[attr-defined]
        held.append(ok)
        if ok:
            small._lock.release()  # type: ignore[attr-defined]
        return real_concat(seq, *a, **k)

    np.concatenate = checking_concat  # type: ignore[assignment]
    try:
        held.clear()
        window = small.readWindow(1024 / 44100)
        assert window is not None
        assert window.size == 1024
        # 結合があれば錠なしで呼ばれた
        assert held and all(held)
    finally:
        np.concatenate = real_concat  # type: ignore[assignment]
        small.close()


def test_4_wraparound_logic() -> None:
    """環の巻き戻り（非巻・巻・満杯）を取りこぼさない。"""
    cap, box = _open_cap()
    # 小さな環で巻かせる
    cap._ring = np.zeros(1024, dtype=np.float32)  # type: ignore[attr-defined]
    cap._pos = 0  # type: ignore[attr-defined]
    cap._filled = 0  # type: ignore[attr-defined]
    cap._total = 0  # type: ignore[attr-defined]
    cap._tap.clear()  # type: ignore[attr-defined]
    # 512ずつ3回＝1536件（1024で巻く）
    box["in"].fire(np.full(512, 0.1, dtype=np.float32))
    box["in"].fire(np.full(512, 0.2, dtype=np.float32))
    box["in"].fire(np.full(512, 0.3, dtype=np.float32))
    # 直近1024件は [0.2×512後半256？] ではなく、時系列で 0.2の後半＋0.3
    # 単純に末尾が0.3で先頭側に0.2が残ることを見る
    window = cap.readWindow(1024 / 44100)
    assert window is not None
    assert window.size == 1024
    assert float(window[-1]) == pytest.approx(0.3)
    assert float(window[0]) == pytest.approx(0.2)
    # 複製であること
    window[:] = 0.0
    again = cap.readWindow(1024 / 44100)
    assert again is not None
    assert float(again[-1]) == pytest.approx(0.3)
    cap.close()


def test_6_stats_lock_and_reset_before_start() -> None:
    """計数は錠で守り、送りの残り・割合は開始前に整える。"""
    cap, box = _open_cap()
    assert hasattr(cap, "_stats_lock")
    # 開始前の残りが開始中に見えないこと（偽出力の start で確認）
    cap._out_carry = np.ones(10, dtype=np.float32)  # type: ignore[attr-defined]
    cap._out_rate = 99999  # type: ignore[attr-defined]
    seen: dict[str, Any] = {}

    orig_factory = cap._factory_out  # type: ignore[attr-defined]

    def checking_factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        box["checking"] = stream
        real_start = stream.start

        def start_and_check() -> None:
            seen["carry_size"] = int(cap._out_carry.size)  # type: ignore[attr-defined]
            seen["out_rate"] = int(cap._out_rate)  # type: ignore[attr-defined]
            real_start()

        stream.start = start_and_check  # type: ignore[assignment]
        return stream

    cap._factory_out = checking_factory  # type: ignore[attr-defined]
    try:
        assert cap.setMonitorEnabled(True) is True
        # 開始時点ですでに空の持ち越し・正しい割合
        assert seen["carry_size"] == 0
        assert seen["out_rate"] == cap._rate  # type: ignore[attr-defined]
    finally:
        cap._factory_out = orig_factory  # type: ignore[attr-defined]
        cap.close()


def test_6_stats_hammer() -> None:
    """入出力と表示の同時押しでも落とさず数える。"""
    cap, box = _open_cap(jitter=4)
    assert cap.setMonitorEnabled(True) is True
    errors: list[BaseException] = []

    def pound_input() -> None:
        try:
            for _ in range(200):
                box["in"].fire(np.full(16, 0.1, dtype=np.float32))
        except BaseException as e:  # pragma: no cover
            errors.append(e)

    def pound_output() -> None:
        try:
            for _ in range(200):
                buf = np.zeros((16, 1), dtype=np.float32)
                box["out"].callback(buf, 16, None, None)
        except BaseException as e:  # pragma: no cover
            errors.append(e)

    def pound_stats() -> None:
        try:
            for _ in range(200):
                _ = cap.getMonitorStats()
        except BaseException as e:  # pragma: no cover
            errors.append(e)

    threads = [
        threading.Thread(target=pound_input),
        threading.Thread(target=pound_output),
        threading.Thread(target=pound_stats),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    assert not errors
    stats = cap.getMonitorStats()
    assert stats["silence"] >= 0
    assert stats["drops"] >= 0
    cap.close()


def test_7_probe_keeps_old_on_failure() -> None:
    """絞り込みの片失敗で良い方を捨てない。"""
    from services import audio_service as svc_mod

    notes: list[str] = []
    svc = svc_mod.AudioService(notify_user=notes.append)
    svc._probe_cache = {True: [(7, "Ok Mic", 12.0)], False: [(3, "Ok Spk", 93.0)]}
    real_probe = svc_mod.probe_details

    def flaky(want_input: bool) -> list[tuple[int, str, float]]:
        if want_input:
            return [(7, "Ok Mic", 12.0)]
        raise RuntimeError("出力の試し開きに失敗")

    svc_mod.probe_details = flaky  # type: ignore[assignment]
    try:
        svc.refresh_probe_cache()
    finally:
        svc_mod.probe_details = real_probe  # type: ignore[assignment]
    # 入力の良品は残り、出力の良品も残る（失敗で空にしない）
    assert svc._probe_cache.get(True) == [(7, "Ok Mic", 12.0)]
    assert svc._probe_cache.get(False) == [(3, "Ok Spk", 93.0)]


def test_8_jitter_chunks_default() -> None:
    """遅延段数の既定は12（512×12≒139ms）。"""
    assert AC.JITTER_CHUNKS == 12
    cap, _ = _open_cap(jitter=12)
    try:
        assert cap._pipe.maxsize == 12  # type: ignore[attr-defined]
    finally:
        cap.close()
    cap2, _ = _open_cap(jitter=6)
    try:
        assert cap2._pipe.maxsize == 6  # type: ignore[attr-defined]
    finally:
        cap2.close()
