"""Camera.openCamera がバックエンド例外を bool 契約で吸収することの回帰検査。

対象: core/Camera.py の Camera.openCamera / CameraController.openCamera
方針: 実カメラは開かない。cv2.VideoCapture を偽物へ差し替え、monkeypatch で戻す。
"""

from __future__ import annotations

import time
from multiprocessing import Value
from typing import Any, cast

import numpy as np
import pytest
from core import Camera as CamMod
from core.Camera import Camera, CameraController


class _RaisingCapture:
    """構築だけで失敗するバックエンド。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("backend unavailable")


def test_openCamera_returns_false_when_video_capture_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """コンストラクタ例外は漏らさず False。実体もスレッドも残さない。"""
    monkeypatch.setattr(CamMod.cv2, "VideoCapture", _RaisingCapture)

    cam = Camera(fps=30)
    try:
        assert cam.openCamera(0) is False
        assert cam.camera is None
        assert cam._thread is None
        assert cam.isRunning() is False
    finally:
        cam.destroy()


def test_openCamera_returns_false_when_configure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """設定(set/get)の例外も False。開いた実体は release して残さない。"""
    released: list[bool] = []

    class _SetRaisingCapture:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._opened = True

        def isOpened(self) -> bool:
            return self._opened

        def set(self, *args: Any, **kwargs: Any) -> bool:
            raise RuntimeError("set failed")

        def get(self, *args: Any, **kwargs: Any) -> float:
            return 0.0

        def release(self) -> None:
            self._opened = False
            released.append(True)

    monkeypatch.setattr(CamMod.cv2, "VideoCapture", _SetRaisingCapture)

    cam = Camera(fps=30)
    try:
        assert cam.openCamera(0) is False
        assert cam.camera is None
        assert cam._thread is None
        assert released == [True]
    finally:
        cam.destroy()


def test_openCamera_returns_true_and_starts_thread_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正常に開ける場合は従来どおり True・実体・取得スレッド。"""

    class _WorkingCapture:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._opened = True

        def isOpened(self) -> bool:
            return self._opened

        def read(self) -> tuple[bool, np.ndarray]:
            time.sleep(0.01)
            return True, np.zeros((4, 4, 3), dtype=np.uint8)

        def set(self, *args: Any, **kwargs: Any) -> bool:
            return True

        def get(self, *args: Any, **kwargs: Any) -> float:
            return 0.0

        def release(self) -> None:
            self._opened = False

    monkeypatch.setattr(CamMod.cv2, "VideoCapture", _WorkingCapture)

    cam = Camera(fps=30)
    try:
        assert cam.openCamera(0) is True
        assert cam.camera is not None
        assert cam._thread is not None
        assert cam.isRunning() is True
    finally:
        cam.destroy()


def test_openCamera_returns_false_when_existing_handle_isOpened_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """壊れた既存ハンドルの isOpened() も漏らさず False。実体は残さない。"""
    constructed: list[bool] = []
    released: list[bool] = []

    class _UnexpectedCapture:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            constructed.append(True)

    class _BrokenCapture:
        def isOpened(self) -> bool:
            raise RuntimeError("broken handle")

        def release(self) -> None:
            released.append(True)

    monkeypatch.setattr(CamMod.cv2, "VideoCapture", _UnexpectedCapture)

    cam = Camera(fps=30)
    cam.camera = cast(Any, _BrokenCapture())
    try:
        assert cam.openCamera(0) is False
        assert constructed == []
        assert cam.camera is None
        assert released == [True]
    finally:
        cam.destroy()


def test_camera_controller_reports_false_when_existing_handle_isOpened_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """壊れた既存ハンドルでも status=False で返し、実体は残さない。"""
    constructed: list[bool] = []
    released: list[bool] = []

    class _UnexpectedCapture:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            constructed.append(True)

    class _BrokenCapture:
        def isOpened(self) -> bool:
            raise RuntimeError("broken handle")

        def release(self) -> None:
            released.append(True)

    monkeypatch.setattr(CamMod.cv2, "VideoCapture", _UnexpectedCapture)

    controller = CameraController.__new__(CameraController)
    controller.camera = cast(Any, _BrokenCapture())
    controller.cameraId = 0
    controller.capture_size = (1280, 720)
    controller.fps = Value("i", 30)
    controller.camera_process_status = Value("b", True)

    controller.openCamera()

    assert not controller.camera_process_status.value
    assert constructed == []
    assert controller.camera is None
    assert released == [True]


def test_camera_controller_reports_false_when_configure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """プロセス版も例外で落ちず、status=False で実体を残さない。"""
    released: list[bool] = []

    class _SetRaisingCapture:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._opened = True

        def isOpened(self) -> bool:
            return self._opened

        def set(self, *args: Any, **kwargs: Any) -> bool:
            raise RuntimeError("set failed")

        def get(self, *args: Any, **kwargs: Any) -> float:
            return 0.0

        def release(self) -> None:
            self._opened = False
            released.append(True)

    monkeypatch.setattr(CamMod.cv2, "VideoCapture", _SetRaisingCapture)

    controller = CameraController.__new__(CameraController)
    controller.camera = None
    controller.cameraId = 0
    controller.capture_size = (1280, 720)
    controller.fps = Value("i", 30)
    controller.camera_process_status = Value("b", True)

    controller.openCamera()

    assert not controller.camera_process_status.value
    assert controller.camera is None
    assert released == [True]
