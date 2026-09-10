from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from services import blockly_capture, blockly_templates


def test_name_rejects_bad_names() -> None:
    assert blockly_capture.validate_template_name("") is not None
    assert blockly_capture.validate_template_name("   ") is not None
    assert blockly_capture.validate_template_name("../evil") is not None
    assert blockly_capture.validate_template_name("/abs") is not None
    assert blockly_capture.validate_template_name("C:/win") is not None
    assert blockly_capture.validate_template_name("a:b") is not None


def test_name_allows_plain_and_subdir() -> None:
    assert blockly_capture.validate_template_name("mark") is None
    assert blockly_capture.validate_template_name("boss/hp") is None


def test_parse_rect_rejects_garbage() -> None:
    for bad in [
        None,
        [0, 0, 1, 1],
        {"x": 0, "y": 0},
        {"x": "a", "y": 0, "width": 1, "height": 1},
        {"x": 0, "y": 0, "width": 0, "height": 1},
        {"x": 0, "y": 0, "width": -1, "height": 1},
        {"x": 2, "y": 2, "width": 1, "height": 1},
    ]:
        with pytest.raises(ValueError):
            blockly_capture.parse_rect(bad)


def make_png(width: int = 200, height: int = 100) -> bytes:
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (60, 120, 200)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return bytes(buf)


def test_crop_maps_normalized_to_pixels() -> None:
    out = blockly_capture.crop_png(
        make_png(), {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5}
    )
    arr = np.frombuffer(out, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None
    assert img.shape == (50, 100, 3)


def test_crop_clamps_overflow() -> None:
    out = blockly_capture.crop_png(
        make_png(), {"x": -0.1, "y": 0.1, "width": 0.5, "height": 0.5}
    )
    arr = np.frombuffer(out, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None
    assert img.shape == (50, 80, 3)


def test_crop_rejects_small_side() -> None:
    with pytest.raises(ValueError):
        blockly_capture.crop_png(
            make_png(), {"x": 0, "y": 0, "width": 0.01, "height": 0.5}
        )


def test_save_numbers_duplicates(tmp_path: Path) -> None:
    png = make_png()
    full = {"x": 0, "y": 0, "width": 1, "height": 1}
    r1 = blockly_capture.save_template(tmp_path, "mark", png, full)
    r2 = blockly_capture.save_template(tmp_path, "mark", png, full)
    assert (r1.status, r1.rel) == ("saved", "blockly/mark.png")
    assert (r2.status, r2.rel) == ("saved", "blockly/mark_2.png")
    assert "blockly/mark.png" in blockly_templates.list_image_templates(tmp_path)
    assert "blockly/mark_2.png" in blockly_templates.list_image_templates(tmp_path)


def test_save_failure_leaves_no_file(tmp_path: Path) -> None:
    res = blockly_capture.save_template(
        tmp_path, "../evil", make_png(), {"x": 0, "y": 0, "width": 1, "height": 1}
    )
    assert res.status == "failed"
    target = Path(tmp_path) / "Template" / "blockly"
    assert not target.is_dir() or list(target.iterdir()) == []


def test_get_frame_refreshes_after_clear_with_seq() -> None:
    """clear() 直後の即時要求は古い PNG を返さない（seq を鍵に含める）。

    時刻基準だけだと clear→put しても 200ms 以内は前世代の PNG が
    当たる。frame_seq を出すカメラでは seq を鍵にし、新世代を符号化する。
    """
    red = np.zeros((10, 10, 3), dtype=np.uint8)
    red[:] = (0, 0, 255)
    blue = np.zeros((10, 10, 3), dtype=np.uint8)
    blue[:] = (255, 0, 0)
    state: dict[str, Any] = {"img": red, "seq": 0}

    class SeqCam:
        def readFrame(self, copy: bool = False) -> object:
            return state["img"]

        def frame_seq(self) -> int:
            return state["seq"]

    get = blockly_capture.build_get_frame(lambda: SeqCam())
    p1 = get()
    assert p1 is not None
    # clear 相当：seq を進めて絵を差し替える（200ms 以内の即時再要求）。
    state["seq"] += 1
    state["img"] = blue
    p2 = get()
    assert p2 is not None
    assert p2 != p1, "clear 後の即時要求が前世代の PNG を返しています"
    arr = np.frombuffer(p2, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None
    assert tuple(int(v) for v in img[0, 0]) == (255, 0, 0)


def test_get_frame_seq_key_expiry_bounded(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """seq 鍵でも陳腐化は FRAME_CACHE_SEC に有界（期限切れは符号化し直す）。"""
    import services.blockly_capture as bc

    red = np.zeros((10, 10, 3), dtype=np.uint8)
    red[:] = (0, 0, 255)
    state: dict[str, Any] = {"img": red, "seq": 7}

    class SeqCam:
        def readFrame(self, copy: bool = False) -> object:
            return state["img"]

        def frame_seq(self) -> int:
            return state["seq"]

    encodes = {"n": 0}
    orig_encode = cv2.imencode

    def _count_encode(*a, **k):  # type: ignore[no-untyped-def]
        encodes["n"] += 1
        return orig_encode(*a, **k)

    monkeypatch.setattr(cv2, "imencode", _count_encode)
    now = {"t": 1000.0}
    monkeypatch.setattr(bc.time, "monotonic", lambda: now["t"])
    get = bc.build_get_frame(lambda: SeqCam())
    assert get() is not None
    assert encodes["n"] == 1
    # 同じ seq の即時再要求は使い回す（符号化しない）。
    assert get() is not None
    assert encodes["n"] == 1
    # 期限を過ぎたら同じ seq でも符号化し直す（有界）。
    now["t"] += bc.FRAME_CACHE_SEC
    assert get() is not None
    assert encodes["n"] == 2


def test_build_get_frame() -> None:
    img = np.zeros((10, 10, 3), dtype=np.uint8)

    class FakeCam:
        def readFrame(self, copy: bool = False) -> object:
            return img

    ok = blockly_capture.build_get_frame(lambda: FakeCam())()
    assert ok is not None and ok[:8] == b"\x89PNG\r\n\x1a\n"
    assert blockly_capture.build_get_frame(lambda: None)() is None

    class DeadCam:
        def readFrame(self, copy: bool = False) -> object:
            return None

    assert blockly_capture.build_get_frame(lambda: DeadCam())() is None
