"""Camera CAPTURE_DIR が BASE_DIR 基準の検証。

起動場所（cwd）に寄らず、常に SerialController/Captures を指すこと。
"""

from __future__ import annotations

import os


def _expected_base() -> str:
    from core import Camera as cam_mod

    return os.path.dirname(os.path.dirname(os.path.abspath(cam_mod.__file__)))


def test_capture_dir_is_absolute_under_base() -> None:
    """CAPTURE_DIR は絶対パスで BASE_DIR/Captures を指す。"""
    from core import Camera as cam_mod

    assert os.path.isabs(cam_mod.CAPTURE_DIR)
    want = os.path.normpath(os.path.join(_expected_base(), "Captures"))
    assert os.path.normpath(cam_mod.CAPTURE_DIR) == want


def test_save_filespec_ignores_cwd(tmp_path: object) -> None:
    """相対名は CAPTURE_DIR 基準、絶対はそのまま。cwd を見ない。"""
    import pathlib

    from core import Camera as cam_mod

    base = cam_mod._get_save_filespec("a.png")
    assert os.path.isabs(base)
    assert os.path.normpath(base) == os.path.normpath(
        os.path.join(cam_mod.CAPTURE_DIR, "a.png")
    )
    abs_in = str(pathlib.Path(str(tmp_path)) / "x.png")
    assert cam_mod._get_save_filespec(abs_in) == abs_in


def test_camera_instances_follow_module_dir() -> None:
    """Camera / CameraQueue の既定も cwd 相対ではない。"""
    from core.Camera import Camera

    cam = Camera(fps=60)
    assert os.path.isabs(cam.capture_dir)
    assert os.path.normpath(cam.capture_dir) == os.path.normpath(
        os.path.join(_expected_base(), "Captures")
    )
