from pathlib import Path

import cv2
import numpy as np
import pytest
from services import blockly_match


def make_frame() -> np.ndarray:
    """200x100のグレー地に白矩形(60,30,80x40、中に黒の目印)。テンプレはここから切り出す。"""
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[:] = (60, 60, 60)
    img[30:70, 60:140] = (255, 255, 255)
    # TM_CCOEFF_NORMEDは分散ゼロ（真っ白など）のテンプレだと全面1.0になり
    # 位置も成否も判定できないため、内側に黒の目印を入れる。
    # 切出し位置・大きさ（60,30,80x40）は変えない。
    img[40:60, 80:120] = (0, 0, 0)
    return img


def png_of(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return bytes(buf)


def make_app_with_template(base: Path, frame: np.ndarray) -> Path:
    app = base / "app"
    tpl_dir = app / "Template" / "pack"
    tpl_dir.mkdir(parents=True)
    tpl = frame[30:70, 60:140]
    (tpl_dir / "part.png").write_bytes(png_of(tpl))
    return app


def test_parse_threshold_rejects_garbage() -> None:
    for bad in ["high", None, float("nan"), -0.1, 1.5]:
        with pytest.raises(ValueError):
            blockly_match.parse_threshold(bad)
    assert blockly_match.parse_threshold(0.7) == 0.7
    assert blockly_match.parse_threshold(0) == 0.0
    assert blockly_match.parse_threshold(1) == 1.0


def test_match_hit_on_identical_crop(tmp_path: Path) -> None:
    frame = make_frame()
    app = make_app_with_template(tmp_path, frame)
    res = blockly_match.match_template(app, png_of(frame), "pack/part.png", 0.7)
    assert res.status == "ok"
    assert res.matched is True
    assert res.score >= 0.99
    assert res.rect == {"x": 60, "y": 30, "width": 80, "height": 40}


def test_match_rejects_bad_template_name(tmp_path: Path) -> None:
    frame = make_frame()
    app = make_app_with_template(tmp_path, frame)
    res = blockly_match.match_template(app, png_of(frame), "../evil.png", 0.7)
    assert res.status == "failed"


def test_match_missing_template_explains_like_runtime(tmp_path: Path) -> None:
    frame = make_frame()
    app = make_app_with_template(tmp_path, frame)
    res = blockly_match.match_template(app, png_of(frame), "pack/none.png", 0.7)
    assert res.status == "failed"
    assert "読み込めませんでした" in res.message


def test_match_miss_on_plain_frame(tmp_path: Path) -> None:
    frame = make_frame()
    app = make_app_with_template(tmp_path, frame)
    plain = np.zeros((100, 200, 3), dtype=np.uint8)
    plain[:] = (60, 60, 60)
    res = blockly_match.match_template(app, png_of(plain), "pack/part.png", 0.7)
    assert res.status == "ok"
    assert res.matched is False
    assert res.score < 0.7


def test_match_rejects_broken_frame(tmp_path: Path) -> None:
    frame = make_frame()
    app = make_app_with_template(tmp_path, frame)
    res = blockly_match.match_template(app, b"not-a-png", "pack/part.png", 0.7)
    assert res.status == "failed"


def test_decode_upload_image_roundtrip() -> None:
    import base64

    raw = png_of(make_frame())
    assert blockly_match.decode_upload_image(base64.b64encode(raw).decode()) == raw
    with pytest.raises(ValueError):
        blockly_match.decode_upload_image("!!! not base64 !!!")


def test_count_single_on_hit(tmp_path: Path) -> None:
    frame = make_frame()
    app = make_app_with_template(tmp_path, frame)
    res = blockly_match.match_template(app, png_of(frame), "pack/part.png", 0.7)
    assert res.status == "ok"
    assert res.count == 1


def test_count_two_on_twin_rects(tmp_path: Path) -> None:
    import numpy as np

    frame = np.zeros((100, 300, 3), dtype=np.uint8)
    frame[:] = (60, 60, 60)
    for x0 in (20, 200):
        frame[30:70, x0 : x0 + 80] = (255, 255, 255)
        frame[40:50, x0 + 10 : x0 + 20] = (0, 0, 0)
    app = tmp_path / "app"
    tpl_dir = app / "Template" / "pack"
    tpl_dir.mkdir(parents=True)
    (tpl_dir / "part.png").write_bytes(png_of(frame[30:70, 20:100]))
    res = blockly_match.match_template(app, png_of(frame), "pack/part.png", 0.7)
    assert res.status == "ok"
    assert res.matched is True
    assert res.count == 2


def test_crop_hit_returns_full_frame_coords(tmp_path: Path) -> None:
    frame = make_frame()
    app = make_app_with_template(tmp_path, frame)
    res = blockly_match.match_template(
        app, png_of(frame), "pack/part.png", 0.7, crop=[40, 10, 180, 90]
    )
    assert res.status == "ok"
    assert res.matched is True
    assert res.rect == {"x": 60, "y": 30, "width": 80, "height": 40}


def test_crop_excluding_template_fails(tmp_path: Path) -> None:
    frame = make_frame()
    app = make_app_with_template(tmp_path, frame)
    res = blockly_match.match_template(
        app, png_of(frame), "pack/part.png", 0.7, crop=[0, 0, 50, 50]
    )
    assert res.status == "failed"


def test_parse_crop_rejects_garbage() -> None:
    assert blockly_match.parse_crop(None, 200, 100) is None
    assert blockly_match.parse_crop([40, 10, 180, 90], 200, 100) == [40, 10, 180, 90]
    for bad in [
        [0, 0, 50],
        [0, 0, 50, 50, 1],
        [180, 10, 40, 90],
        [-1, 0, 50, 50],
        [0, 0, 201, 50],
        ["a", 0, 50, 50],
    ]:
        with pytest.raises(ValueError):
            blockly_match.parse_crop(bad, 200, 100)
