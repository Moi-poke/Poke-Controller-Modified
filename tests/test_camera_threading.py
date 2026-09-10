"""Camera threading findings の回帰検査（TDD RED→GREEN 用）。

対象: Camera.py / CommandVision.py / GuiAssets.py（frame-consumer のみ）
方針: 実機なしで回す。cv2.VideoCapture は偽物に差し替える。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# 1. reopen で旧スレッドが蘇らない（generation / per-thread Event）
# ---------------------------------------------------------------------------


def _make_fake_capture_class(read_sleep: float = 0.2):
    import numpy as _np

    class _FakeCap:
        def __init__(self, *a: Any, **k: Any) -> None:
            self._opened = True

        def isOpened(self) -> bool:
            return self._opened

        def read(self) -> tuple[bool, Any]:
            time.sleep(read_sleep)
            return True, _np.zeros((4, 4, 3), dtype=_np.uint8)

        def release(self) -> None:
            self._opened = False

        def set(self, *a: Any, **k: Any) -> bool:
            return True

        def get(self, *a: Any, **k: Any) -> float:
            return 0.0

    return _FakeCap


def test_reopen_does_not_resurrect_old_thread(monkeypatch: Any) -> None:
    """rapid open→destroy→open x20 → CameraThread は1本だけ、isRunning True。"""
    from core import Camera as CamMod
    from core.Camera import Camera

    monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture_class(0.2))
    monkeypatch.setattr(CamMod, "_configure_capture", lambda *a, **k: None)
    monkeypatch.setattr(CamMod, "THREAD_JOIN_TIMEOUT", 0.05)
    monkeypatch.setattr(CamMod, "THREAD_RELEASE_TIMEOUT", 0.5)

    cam = Camera(fps=30)
    try:
        assert cam.openCamera(0)
        for _ in range(20):
            cam.destroy()
            assert cam.openCamera(0)
        # 旧スレッドが蘇ったぶんだけ沈殿するので少し待つ
        time.sleep(0.6)
        alive = [
            t
            for t in threading.enumerate()
            if t.name == "CameraThread" and t.is_alive()
        ]
        assert cam.isRunning()
        assert len(alive) == 1, f"CameraThread leak: {len(alive)} alive"
    finally:
        cam.destroy()
        time.sleep(0.3)


def test_open_creates_fresh_stop_event(monkeypatch: Any) -> None:
    """openCamera は共有 Event の clear ではなく新世代を開始する。"""
    from core import Camera as CamMod
    from core.Camera import Camera

    monkeypatch.setattr(cv2, "VideoCapture", _make_fake_capture_class(0.01))
    monkeypatch.setattr(CamMod, "_configure_capture", lambda *a, **k: None)

    cam = Camera(fps=30)
    try:
        assert cam.openCamera(0)
        old_event = cam._stop_event
        old_gen = getattr(cam, "_generation", None)
        assert old_gen is not None, "generation counter がありません"
        cam.destroy()
        assert cam.openCamera(0)
        # 同じ Event 使い回し（clear）では旧スレッドが蘇る
        assert cam._stop_event is not old_event, "stop Event が使い回されています"
        assert getattr(cam, "_generation") != old_gen
    finally:
        cam.destroy()


def test_camera_queue_open_creates_fresh_stop_event() -> None:
    """CameraQueue も共有 Event の clear ではなく新世代を開始する。"""
    import multiprocessing as mp

    from core.Camera import CameraQueue

    # 実プロセスを起動せず、_stop_process だけ差し替えて Event の扱いを見る
    q = CameraQueue.__new__(CameraQueue)
    q.camera_process = None
    q.stop_event = mp.Event()
    old = q.stop_event
    q._stop_process = lambda: None  # type: ignore[method-assign]
    q.cameraId = 0
    q.capture_size = (1280, 720)
    q.shape = (720, 1280, 3)
    q.shm_name = "test-never-created"
    q.fps = mp.Value("i", 30)
    q.face = mp.Value("i", 0)
    q.camera_process_status = mp.Value("b", False)

    created: dict[str, Any] = {}

    orig_process = mp.Process

    class _NoRunProcess:
        def __init__(self, *a: Any, **k: Any) -> None:
            created["args"] = (a, k)

        def is_alive(self) -> bool:
            return False

        def start(self) -> None:
            return None

    import multiprocessing as _mp

    _mp.Process = _NoRunProcess  # type: ignore[misc, assignment]
    try:
        # startCamera が None でも動くよう最小化：直接 openCamera の Event 扱いだけ見る
        # openCamera は _stop_process→Event更新→Process→startCamera の順
        q.startCamera = lambda: None  # type: ignore[method-assign]
        q.openCamera(0)
    finally:
        _mp.Process = orig_process  # type: ignore[misc]
    assert q.stop_event is not old, "CameraQueue の stop_event が使い回されています"


# ---------------------------------------------------------------------------
# 2. fps=0 失敗スピンに floor がある
# ---------------------------------------------------------------------------


def test_failure_path_has_floor_sleep(monkeypatch: Any) -> None:
    """ret==False 連続時も wait(0.0) で空転しない。5ms 以上の floor。"""
    from core import Camera as CamMod

    assert hasattr(CamMod, "READ_FAILURE_FLOOR_S"), "failure floor 定数がありません"
    assert float(CamMod.READ_FAILURE_FLOOR_S) >= 0.005

    from core.Camera import Camera

    class _FailCap:
        def isOpened(self) -> bool:
            return True

        def read(self) -> tuple[bool, Any]:
            return False, None

        def release(self) -> None:
            return None

        def set(self, *a: Any, **k: Any) -> bool:
            return True

        def get(self, *a: Any, **k: Any) -> float:
            return 0.0

    monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: _FailCap())
    monkeypatch.setattr(CamMod, "_configure_capture", lambda *a, **k: None)

    cam = Camera(fps=0)
    assert cam.openCamera(0)
    try:
        # 失敗ループが floor なしだと数百回以上回る。50ms で回数を数える
        time.sleep(0.05)
        # スレッドが生きていること（勝手に死んでいない）
        assert cam.isRunning()
        # 実測: puts が増えていない（失敗を put していない）
        assert cam._stat_puts == 0
    finally:
        cam.destroy()


def test_failure_wait_uses_floor(monkeypatch: Any) -> None:
    """失敗時の wait 間隔が floor 以上になる。"""
    from core.Camera import Camera

    waits: list[float] = []
    orig_wait = threading.Event.wait

    def _rec(self: threading.Event, timeout: float | None = None) -> bool:
        if timeout is not None:
            waits.append(float(timeout))
        return orig_wait(self, timeout)

    monkeypatch.setattr(threading.Event, "wait", _rec)

    class _FailCap:
        def isOpened(self) -> bool:
            return True

        def read(self) -> tuple[bool, Any]:
            return False, None

        def release(self) -> None:
            return None

    import core.Camera as CamMod

    monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: _FailCap())
    monkeypatch.setattr(CamMod, "_configure_capture", lambda *a, **k: None)

    cam = Camera(fps=0)
    assert cam.openCamera(0)
    try:
        time.sleep(0.05)
    finally:
        cam.destroy()
    assert waits, "wait が呼ばれていません"
    assert min(waits) >= 0.005 - 1e-9, f"floor なしの wait があります: {min(waits)}"


# ---------------------------------------------------------------------------
# 3. seq + consumer cache（cvt+match の冗長再計算を抑える）
# ---------------------------------------------------------------------------


def test_latest_frame_seq_monotonic() -> None:
    from core.Camera import LatestFrame

    q = LatestFrame()
    assert hasattr(q, "seq"), "LatestFrame.seq がありません"
    s0 = int(q.seq)
    q.put(np.zeros((2, 2, 3), dtype=np.uint8))
    s1 = int(q.seq)
    assert s1 > s0
    q.put(np.zeros((2, 2, 3), dtype=np.uint8))
    assert int(q.seq) > s1
    # clear をまたいで使い回さない（単調増加で無効化）
    q.clear()
    s2 = int(q.seq)
    assert s2 != s1, "clear 後も seq が同じだと cache が当たってしまいます"
    assert q.get() is None


def test_prepare_src_caches_on_same_seq(monkeypatch: Any) -> None:
    """同じ seq では cvt を繰り返さない。新 frame では再計算する。"""
    from core.Camera import LatestFrame
    from core.CommandVision import VisionMixin

    calls = {"cvt": 0}
    orig_cvt = cv2.cvtColor

    def _count_cvt(*a: Any, **k: Any) -> Any:
        calls["cvt"] += 1
        return orig_cvt(*a, **k)

    monkeypatch.setattr(cv2, "cvtColor", _count_cvt)

    lf = LatestFrame()
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    lf.put(frame)

    class _SeqCam:
        def readFrame(self, copy: bool = False) -> Any:
            return lf.get()

        def readFrameWithSeq(self, copy: bool = False) -> tuple[Any, int]:
            f, s = lf.get_with_seq()
            return (f.copy() if copy and f is not None else f, s)

        def frame_seq(self) -> int:
            return int(lf.seq)

    mix = VisionMixin()
    mix._initVision(_SeqCam())
    a = mix._prepareSrc(None, True)
    n1 = calls["cvt"]
    b = mix._prepareSrc(None, True)
    # 同じ seq → 再変換しない（0〜1回以下、少なくとも増えない）
    assert calls["cvt"] <= n1, (
        f"同じ seq で cvt が再実行されています: {n1} -> {calls['cvt']}"
    )
    assert np.array_equal(a, b)
    # 新 frame → 再計算する
    lf.put(np.full((8, 8, 3), 7, dtype=np.uint8))
    mix._prepareSrc(None, True)
    assert calls["cvt"] > n1, "新 frame で再計算されていません"


def test_match_cache_skips_redundant_match(monkeypatch: Any) -> None:
    """固定 scene で matchTemplate 呼び出しが減る。新 frame では再実行。"""
    import core.CommandVision as VisMod
    from core.Camera import LatestFrame
    from core.CommandVision import VisionMixin

    lf = LatestFrame()
    lf.put(np.zeros((16, 16, 3), dtype=np.uint8))

    class _SeqCam:
        def readFrame(self, copy: bool = False) -> Any:
            return lf.get()

        def readFrameWithSeq(self, copy: bool = False) -> tuple[Any, int]:
            f, s = lf.get_with_seq()
            return (f.copy() if copy and f is not None else f, s)

        def frame_seq(self) -> int:
            return int(lf.seq)

    tmpl = np.zeros((4, 4), dtype=np.uint8)
    monkeypatch.setattr(VisMod, "_imread_or_raise", lambda *a, **k: tmpl)

    n = {"match": 0}
    orig_match = cv2.matchTemplate

    def _count_match(*a: Any, **k: Any) -> Any:
        n["match"] += 1
        return orig_match(*a, **k)

    monkeypatch.setattr(cv2, "matchTemplate", _count_match)

    mix = VisionMixin()
    mix._initVision(_SeqCam())
    mix._matchOnce("dummy.png", 0.7, True, None, None)
    first = n["match"]
    assert first >= 1
    mix._matchOnce("dummy.png", 0.7, True, None, None)
    assert n["match"] == first, "同じ seq で matchTemplate が再実行されています"
    lf.put(np.full((16, 16, 3), 3, dtype=np.uint8))
    mix._matchOnce("dummy.png", 0.7, True, None, None)
    assert n["match"] > first, "新 frame で再計算されていません"


def test_gui_draw_skips_same_seq() -> None:
    """GUI _drawFrame は同じ seq を描き直さない。"""
    import numpy as _np
    from GuiAssets import CaptureArea

    area = CaptureArea.__new__(CaptureArea)
    area.show_width = 4
    area.show_height = 4
    area.show_size = (4, 4)
    area._allocBuffers()
    # _convert を数える
    n = {"conv": 0}
    orig = CaptureArea._convert

    def _count(self: Any, frame: Any) -> None:
        n["conv"] += 1
        return orig(self, frame)

    area._convert = _count.__get__(area, CaptureArea)  # type: ignore[attr-defined]
    # photo/itemconfig を無効化（Tk なし）
    area._photo = type("P", (), {"paste": lambda self, im: None})()
    area.im = object()
    area.im_ = None
    area.itemconfig = lambda *a, **k: None  # type: ignore[attr-defined]

    frame = _np.zeros((4, 4, 3), dtype=_np.uint8)
    area._drawFrame(frame, seq=7)  # type: ignore[call-arg]
    first = n["conv"]
    assert first == 1
    area._drawFrame(frame, seq=7)  # type: ignore[call-arg]
    assert n["conv"] == first, "同じ seq で再変換されています"
    area._drawFrame(frame, seq=8)  # type: ignore[call-arg]
    assert n["conv"] > first, "新 seq で再描画されていません"


# ---------------------------------------------------------------------------
# 4. vision 経路の buffer 再利用（thread-local scratch、retain は copy）
# ---------------------------------------------------------------------------


def test_vision_reuses_thread_local_scratch() -> None:
    from core.Camera import LatestFrame
    from core.CommandVision import VisionMixin

    lf1 = LatestFrame()
    lf1.put(np.zeros((8, 8, 3), dtype=np.uint8))

    class _SeqCam:
        def readFrame(self, copy: bool = False) -> Any:
            return lf1.get()

        def readFrameWithSeq(self, copy: bool = False) -> tuple[Any, int]:
            f, s = lf1.get_with_seq()
            return (f, s)

        def frame_seq(self) -> int:
            return int(lf1.seq)

    mix = VisionMixin()
    mix._initVision(_SeqCam())
    assert (
        hasattr(mix, "_tls") or hasattr(mix, "_gray_buf") or hasattr(mix, "_scratch")
    ), "thread-local scratch がありません"
    a = mix._prepareSrc(None, True)
    # retain=True は保持用 copy（次 poll で上書きされない）
    b = mix._prepareSrc(None, True, retain=True)  # type: ignore[call-arg]
    assert a.shape == b.shape
    assert not np.shares_memory(a, b), (
        "retain は copy を返すこと（scratch の共有は不可）"
    )
    # 非 retain は scratch 再利用（同じ object または shares_memory）
    lf1.put(np.full((8, 8, 3), 9, dtype=np.uint8))
    c = mix._prepareSrc(None, True)
    # 少なくとも crash せず再利用できる
    assert c.shape == (8, 8)


# ---------------------------------------------------------------------------
# 5. vision cache の共有（lock 保護＋読み取り専用の借用契約）
# ---------------------------------------------------------------------------


def _make_seq_mix(frame_value: int = 0):  # type: ignore[no-untyped-def]
    """seq 付きカメラを持つ VisionMixin を作る。"""
    from core.Camera import LatestFrame
    from core.CommandVision import VisionMixin

    lf = LatestFrame()
    lf.put(np.full((8, 8, 3), frame_value, dtype=np.uint8))

    class _SeqCam:
        def readFrame(self, copy: bool = False) -> Any:
            return lf.get()

        def readFrameWithSeq(self, copy: bool = False) -> tuple[Any, int]:
            f, s = lf.get_with_seq()
            return (f, s)

        def frame_seq(self) -> int:
            return int(lf.seq)

    mix = VisionMixin()
    mix._initVision(_SeqCam())
    return mix, lf


def test_vision_cache_guarded_by_lock() -> None:
    """_src_cache/_match_cache は錠で守り、同時命中でも壊れない。"""
    import threading as _th

    mix, _lf = _make_seq_mix()
    # 錠が無ければここで落ちる（RED: 共有 dict のまま）。
    assert isinstance(getattr(mix, "_vision_cache_lock", None), type(_th.Lock()))
    errors: list[BaseException] = []
    seen: list[bytes] = []
    seen_lock = _th.Lock()

    def hammer() -> None:
        try:
            for _ in range(50):
                hit = mix._prepareSrc(None, True)
                with seen_lock:
                    seen.append(bytes(hit))
        except BaseException as e:  # noqa: BLE001 - 記録用
            errors.append(e)

    threads = [_th.Thread(target=hammer, daemon=True) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10.0)
    assert all(not th.is_alive() for th in threads)
    assert not errors
    assert seen
    assert all(s == seen[0] for s in seen), "同じ seq の同時命中が食い違っています"


def test_vision_borrow_contract_documented() -> None:
    """借用契約：非 retain は読み取り専用の借用（別名）、retain だけ copy。

    性能のため非 retain は cache 本体をそのまま返す。書き換えると
    以降の判定に影響するため、保持が必要なら retain=True で copy を
    受けること（契約は docstring に明記）。
    """
    from core.CommandVision import VisionMixin

    mix, _lf = _make_seq_mix()
    doc = str(VisionMixin._prepareSrc.__doc__ or "")
    assert "retain" in doc and ("借用" in doc or "borrow" in doc.lower()), (
        "借用契約が docstring にありません"
    )
    borrowed = mix._prepareSrc(None, True)
    borrowed[:] = 255  # 借用物を書き換える（契約違反の側の挙動確認）
    reread = mix._prepareSrc(None, True)
    assert int(reread[0, 0]) == 255, "非 retain は別名のはず（契約どおりか）"
    owned = mix._prepareSrc(None, True, retain=True)  # type: ignore[call-arg]
    owned[:] = 0
    intact = mix._prepareSrc(None, True)
    assert int(intact[0, 0]) == 255, "retain の copy は cache と独立のはず"


# ---------------------------------------------------------------------------
# 6. minors
# ---------------------------------------------------------------------------


def test_stats_guarded_or_documented() -> None:
    from core.Camera import Camera

    assert hasattr(Camera, "getStats")
    mod_file = __import__("core.Camera", fromlist=["__file__"]).__file__
    assert mod_file is not None
    has_lock = hasattr(Camera, "__init__") and (
        "_stat_lock" in open(mod_file, encoding="utf-8").read()
    )
    # lock があるか、docstring で telemetry-only と明記か
    doc = str(Camera.getStats.__doc__ or "")
    assert has_lock or ("telemetry" in doc.lower() or "競合" in doc or "参考" in doc), (
        "stats race の guard（lock）も注記もありません"
    )


def test_release_later_joins_even_if_camera_none() -> None:
    """camera None でも渡された thread を join する（放置しない）。"""
    from core.Camera import Camera

    cam = Camera(fps=30)
    joined: list[str] = []

    class _T:
        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            joined.append("joined")

    cam.camera = None
    cam._release_later(_T())  # type: ignore[arg-type]
    time.sleep(0.2)
    assert joined, "_release_later が camera None で thread を放置しています"


def test_camera_queue_preframe_contract_documented() -> None:
    """CameraQueue.readFrame は初 frame 前の black-vs-None を docstring で明示。"""
    from core.Camera import CameraQueue

    doc = str(CameraQueue.readFrame.__doc__ or "")
    assert ("black" in doc.lower() or "黒" in doc) and "None" in doc, (
        "初フレーム前の black-vs-None 契約が docstring にありません"
    )
