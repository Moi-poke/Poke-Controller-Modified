"""RED: the GUI thread must never park forever on the camera-open lock.

``CameraPanelMixin.openCamera()`` runs on the tkinter main thread (Reload
button, camera-name combobox, Camera-ID entry Return/FocusOut -- the binds are
installed at startup). The background ``CameraOpener`` holds
``_camera_open_lock`` across the whole ``cv2.VideoCapture()`` constructor,
which is an unbounded driver call for a device that cannot be opened. With an
untimed acquire, the first FocusOut on the Camera ID entry parks the GUI thread
inside ``lock.acquire()`` with zero event processing: the app looks stuck at
startup. These tests pin that the calling thread always comes back.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
import ui.camera_panel as panel_module
from ui.camera_panel import CameraPanelMixin

# A lock held forever is indistinguishable from a driver that never returns,
# which is exactly the production failure. The holder is a daemon so a RED run
# cannot hang the suite: the test thread joins with a deadline and fails with
# a message instead of parking.
_HOLD_FOREVER_S = 60.0
_JOIN_DEADLINE_S = 25.0


class _StubCameraId:
    def __init__(self, value: int) -> None:
        self._value = value

    def get(self) -> int:
        return self._value


class _StubCamera:
    def __init__(self) -> None:
        self.open_calls: list[int] = []
        self.destroy_calls = 0
        self.open_result = True

    def openCamera(self, camera_id: int) -> bool:
        self.open_calls.append(camera_id)
        return self.open_result

    def destroy(self) -> bool:
        self.destroy_calls += 1
        return True


def _bare_panel(camera: Any, cam_id: int = 0) -> Any:
    """A CameraPanelMixin without Tk: only the attributes openCamera() reads."""
    panel: Any = object.__new__(CameraPanelMixin)
    panel.camera = camera
    panel.camera_id = _StubCameraId(cam_id)
    panel.camera_dic = None
    panel._camera_open_lock = threading.Lock()
    return panel


def _call_on_thread(panel: Any) -> tuple[list[bool], threading.Thread]:
    """Run panel.openCamera() off-test-thread so a hang becomes a deadline."""
    results: list[bool] = []
    worker = threading.Thread(
        target=lambda: results.append(panel.openCamera()),
        name="TestMainThread",
        daemon=True,
    )
    return results, worker


def test_openCamera_returns_false_quickly_when_another_open_holds_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the lock held by a stuck background open, and a short bound so
    # the suite never waits out a production-length timeout.
    monkeypatch.setattr(panel_module, "_CAMERA_OPEN_LOCK_TIMEOUT_S", 0.5, raising=False)
    panel = _bare_panel(_StubCamera())
    release = threading.Event()

    def hold_forever() -> None:
        panel._camera_open_lock.acquire()
        release.wait(_HOLD_FOREVER_S)

    holder = threading.Thread(target=hold_forever, name="StuckOpener", daemon=True)
    holder.start()
    assert panel._camera_open_lock.locked()

    # When: the calling (GUI) thread asks for a camera change.
    started = time.monotonic()
    results, worker = _call_on_thread(panel)
    worker.start()
    worker.join(timeout=_JOIN_DEADLINE_S)
    elapsed = time.monotonic() - started
    release.set()

    # Then: it comes back instead of parking, refusing the change loudly.
    assert not worker.is_alive(), (
        "(stranded) UNMET: openCamera() did not return within "
        f"{_JOIN_DEADLINE_S}s while the lock was held -- a GUI thread calling "
        "it parks inside lock.acquire() with zero event processing."
    )
    assert results == [False]
    assert elapsed < _JOIN_DEADLINE_S


def test_openCamera_proceeds_when_the_lock_is_free() -> None:
    # Given: no background open in flight.
    camera = _StubCamera()
    panel = _bare_panel(camera, cam_id=2)

    # When/Then: the request reaches the camera with its id and wins.
    assert panel.openCamera() is True
    assert camera.open_calls == [2]


def test_openCamera_disable_branch_also_refuses_quickly_when_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the id mapped to Disable, and the lock held elsewhere.
    monkeypatch.setattr(panel_module, "_CAMERA_OPEN_LOCK_TIMEOUT_S", 0.5, raising=False)
    camera = _StubCamera()
    panel = _bare_panel(camera, cam_id=4)
    panel.camera_dic = {4: "Disable"}
    release = threading.Event()

    def hold_forever() -> None:
        panel._camera_open_lock.acquire()
        release.wait(_HOLD_FOREVER_S)

    holder = threading.Thread(target=hold_forever, name="StuckOpener", daemon=True)
    holder.start()
    assert panel._camera_open_lock.locked()

    # When/Then: it comes back False and never touched the camera, because the
    # destroy it would have run requires the same lock.
    results, worker = _call_on_thread(panel)
    worker.start()
    worker.join(timeout=_JOIN_DEADLINE_S)
    release.set()
    assert not worker.is_alive(), (
        "(stranded) UNMET: the Disable branch parked inside lock.acquire()."
    )
    assert results == [False]
    assert camera.destroy_calls == 0
