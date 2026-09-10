"""audio_latency（往復遅延の実測部品）の検証。実機なしで回す。"""

import numpy as np
from core import audio_latency as AL


def test_make_chirp_shape() -> None:
    chirp = AL.make_chirp(44100)
    assert chirp.size == int(44100 * AL.CHIRP_SECONDS)
    assert float(np.max(np.abs(chirp))) <= 0.8 + 1e-6
    # 両端は丸めてある（クリック防止）
    assert abs(float(chirp[0])) < 0.01
    assert abs(float(chirp[-1])) < 0.01


def test_find_impulse_at_offset() -> None:
    rng = np.random.RandomState(42)
    chirp = AL.make_chirp(44100)
    hay = rng.normal(0.0, 0.05, 44100).astype(np.float32)
    at = 20000
    hay[at : at + chirp.size] += chirp
    found = AL.find_impulse(hay, chirp)
    assert found is not None
    assert abs(found - at) <= 2


def test_find_impulse_absent() -> None:
    rng = np.random.RandomState(7)
    chirp = AL.make_chirp(44100)
    hay = rng.normal(0.0, 0.05, 44100).astype(np.float32)
    assert AL.find_impulse(hay, chirp) is None
    assert AL.find_impulse(np.zeros(1000, dtype=np.float32), chirp) is None
    assert AL.find_impulse(np.zeros(10, dtype=np.float32), chirp) is None


def test_find_impulse_start_skips_older() -> None:
    rng = np.random.RandomState(3)
    chirp = AL.make_chirp(44100)
    hay = rng.normal(0.0, 0.05, 44100).astype(np.float32)
    hay[5000 : 5000 + chirp.size] += chirp
    hay[25000 : 25000 + chirp.size] += chirp
    # start より前は探さない（等価な山が複数ある場合の除外用）
    found = AL.find_impulse(hay, chirp, start=15000)
    assert found is not None
    assert abs(found - 25000) <= 2


def test_time_math() -> None:
    assert abs(AL.play_time(100.0, 0.09, 441, 44100) - 100.1) < 1e-9
    tap = [(100.0, 44100), (101.0, 88200)]
    # 通算 45000 件目は、total_after が初めて超える (101.0, 88200) で内挿する
    got = AL.locate_played(tap, 45000, 0.02, 44100)
    assert got is not None
    assert abs(got - (101.0 - (88200 - 45000) / 44100 - 0.02)) < 1e-9
    assert AL.locate_played(tap, 999999, 0.02, 44100) is None


def test_summarize() -> None:
    empty = AL.summarize([])
    assert empty["detected"] == 0
    assert empty["median_ms"] == -1.0
    full = AL.summarize([30.0, 10.0, 20.0])
    assert full["detected"] == 3
    assert full["median_ms"] == 20.0


def _close(values: list[float], expected: list[float]) -> bool:
    return len(values) == len(expected) and all(
        abs(v - e) < 1e-6 for v, e in zip(values, expected)
    )


def test_pair_delays() -> None:
    # 正常系：順に組める
    assert _close(
        AL.pair_delays([1.0, 2.0, 3.0], [1.1, 2.05, 3.2]), [100.0, 50.0, 200.0]
    )
    # 見逃しがあっても残りは組める
    assert _close(AL.pair_delays([1.0, 2.0, 3.0], [1.1, 3.2]), [100.0, 200.0])
    # 窓外（古すぎ・未来・遠すぎ）は組まない
    assert AL.pair_delays([1.0, 2.0], [0.5, 9.0]) == []
    assert AL.pair_delays([], [1.1]) == []
    assert AL.pair_delays([1.0], []) == []


def test_emitter_sequence() -> None:
    chirp = AL.make_chirp(44100)[:100]
    emitter = AL.Emitter(chirp, 44100, 0.05, count=2, gap_s=0.01, prime_s=0.0)
    out = []
    for _ in range(20):
        buf = np.zeros(512, dtype=np.float32)
        emitter.callback(buf, 512, None, None)
        out.append(buf.copy())
    assert emitter.emitted == 2
    assert len(emitter.play_times) == 2
    assert emitter.done
    flat = np.concatenate(out)
    energy_chirp = float(np.sum(flat[:100] ** 2))
    energy_gap = float(np.sum(flat[100:541] ** 2))
    assert energy_chirp > 0.0
    assert energy_gap == 0.0
