from pathlib import Path

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
