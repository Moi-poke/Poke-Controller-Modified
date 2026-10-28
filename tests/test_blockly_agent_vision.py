"""`tools/blockly_agent.py` の画像補助サブコマンド（frame/template/match/color）の確認。

CLI を subprocess で叩く。標準出力に JSON だけが出ること、失敗時に
既存ファイルを壊さないこと、テンプレ・照合・色割合が実装（services/）と
同じ数値を返すことを見る。画像はその場で作る（ダミー画像は置かない）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "blockly_agent.py"
ASSETS = REPO / "SerialController" / "assets" / "blockly"

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
BACKGROUND = (60, 60, 60)


def run_cli(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    """CLI を子プロセスで動かす。標準出力/エラーは UTF-8 で分けて受け取る。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


def make_frame() -> np.ndarray:
    """200x100 のグレー地に白矩形(60,30,80x40、中に黒の目印)。既存テストと同じ形。"""
    img = np.full((100, 200, 3), BACKGROUND, dtype=np.uint8)
    draw_object(img, 60, 30)
    return img


def draw_object(img: np.ndarray, x: int, y: int) -> None:
    """80x40 の白矩形＋内側の黒い目印を (x, y) に描く。"""
    img[y : y + 40, x : x + 80] = WHITE
    img[y + 10 : y + 30, x + 20 : x + 60] = BLACK


def write_png(path: Path, img: np.ndarray) -> None:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    path.write_bytes(bytes(buf))


def read_png(path: Path) -> np.ndarray:
    buf = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    assert img is not None
    return img


def make_app(tmp_path: Path) -> Path:
    """`assets/blockly` の複写と空の `Template/` を持つ一時アプリを作る。"""
    app = tmp_path / "app"
    shutil.copytree(ASSETS, app / "assets" / "blockly")
    (app / "Template").mkdir(parents=True)
    return app


def snapshot(app: Path) -> set[str]:
    """アプリ配下のファイル相対パス一覧（書込みの有無を見るため）。"""
    return {p.relative_to(app).as_posix() for p in app.rglob("*") if p.is_file()}


def cli_json(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """標準出力が JSON だけであることを確かめて読み取る。"""
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict)
    return payload


def template_args(app: Path, image: Path, name: str, rect: list[int]) -> list[str]:
    args = ["template", "--app-dir", str(app), "--image", str(image)]
    args += ["--name", name, "--rect", *map(str, rect), "--json"]
    return args


def match_args(app: Path, image: Path, template: str) -> list[str]:
    args = ["match", "--app-dir", str(app), "--image", str(image)]
    args += ["--template", template, "--json"]
    return args


def color_args(image: Path, lower: list[int], upper: list[int]) -> list[str]:
    args = ["color", "--image", str(image)]
    args += ["--lower", *map(str, lower), "--upper", *map(str, upper), "--json"]
    return args


def test_template_then_match_hit_miss_crop_and_preview(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    src = tmp_path / "frame.png"
    write_png(src, make_frame())

    proc = run_cli(*template_args(app, src, "part", [60, 30, 140, 70]))
    assert proc.returncode == 0, proc.stderr
    made = cli_json(proc)
    assert made["ok"] is True
    assert made["template"] == "blockly/part.png"
    saved = app / "Template" / "blockly" / "part.png"
    assert saved.is_file()
    saved_img = read_png(saved)
    assert (saved_img.shape[1], saved_img.shape[0]) == (80, 40)
    assert (made["width"], made["height"]) == (80, 40)
    assert made["rect"] == [60, 30, 140, 70]

    # 同じ物が別の場所にある画像。位置は元の座標系で返る。
    moved = np.full((200, 320, 3), BACKGROUND, dtype=np.uint8)
    draw_object(moved, 100, 120)
    moved_path = tmp_path / "moved.png"
    write_png(moved_path, moved)
    preview = tmp_path / "preview.png"
    args = match_args(app, moved_path, str(made["template"]))
    args += ["--preview", str(preview)]
    proc = run_cli(*args)
    assert proc.returncode == 0, proc.stderr
    hit = cli_json(proc)
    assert hit["ok"] is True
    assert hit["matched"] is True
    assert hit["rect"] == {"x": 100, "y": 120, "width": 80, "height": 40}
    assert (hit["width"], hit["height"]) == (320, 200)
    assert hit["template"] == "blockly/part.png"
    assert hit["threshold"] == pytest.approx(0.7)
    assert hit["use_gray"] is True
    assert hit["crop"] is None
    assert hit["preview"] == str(preview)
    assert isinstance(hit["count"], int) and hit["count"] >= 1
    preview_img = read_png(preview)
    assert preview_img.shape[:2] == (200, 320)
    # 元座標の一致位置に矩形（緑）が引かれている。上辺で確かめる。
    top = preview_img[120, 100:180]
    assert np.any(np.all(top == (0, 255, 0), axis=1))

    # 背景だけの画像はミス（終了コードは 0 のまま matched=false）。
    blank_path = tmp_path / "blank.png"
    write_png(blank_path, np.full((200, 320, 3), BACKGROUND, dtype=np.uint8))
    proc = run_cli(*match_args(app, blank_path, str(made["template"])))
    assert proc.returncode == 0, proc.stderr
    miss = cli_json(proc)
    assert miss["ok"] is True
    assert miss["matched"] is False
    assert miss["score"] < 0.7

    # 対象を外した範囲を指定したら、同じ画像でもミス。
    crop_args = match_args(app, moved_path, str(made["template"]))
    crop_args += ["--crop", "0", "0", "90", "200"]
    proc = run_cli(*crop_args)
    assert proc.returncode == 0, proc.stderr
    cropped = cli_json(proc)
    assert cropped["ok"] is True
    assert cropped["crop"] == [0, 0, 90, 200]
    assert cropped["matched"] is False


def test_duplicate_template_name_and_failures_write_nothing(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    src = tmp_path / "frame.png"
    write_png(src, make_frame())
    rect = [60, 30, 140, 70]

    first = cli_json(run_cli(*template_args(app, src, "part", rect)))
    assert first["template"] == "blockly/part.png"
    second_proc = run_cli(*template_args(app, src, "part", rect))
    assert second_proc.returncode == 0, second_proc.stderr
    second = cli_json(second_proc)
    assert second["template"] == "blockly/part_2.png"
    assert (app / "Template" / "blockly" / "part_2.png").is_file()

    before = snapshot(app)
    missing = tmp_path / "missing.png"
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not a png at all")

    failures: list[tuple[list[str], str]] = [
        (template_args(app, missing, "nope", [0, 0, 10, 10]), "画像を読めません"),
        (
            template_args(app, corrupt, "nope", [0, 0, 10, 10]),
            "画像を読み込めませんでした",
        ),
        (template_args(app, src, "tiny", [60, 30, 65, 35]), "小さすぎます"),
        (
            template_args(app, src, "outside", [0, 0, 500, 500]),
            "切出し範囲が不正",
        ),
        (match_args(app, src, "../escape.png"), ".."),
    ]
    for args, needle in failures:
        proc = run_cli(*args)
        assert proc.returncode == 1, (args, proc.returncode, proc.stderr)
        payload = cli_json(proc)
        assert payload["ok"] is False
        assert needle in str(payload["message"])
        assert payload["errors"]
        assert snapshot(app) == before, args


def make_color_image() -> np.ndarray:
    """赤（H=0）と H=175 の帯を持つ HSV 画像。0 またぎの範囲で測れる。"""
    hsv = np.zeros((90, 120, 3), dtype=np.uint8)
    hsv[:, :] = (0, 0, 40)
    hsv[10:40, 10:60] = (0, 255, 255)
    hsv[10:40, 60:90] = (175, 255, 255)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def independent_ratio(
    img: np.ndarray, lower: list[int], upper: list[int], crop: list[int] | None
) -> float:
    """実装に依存せず numpy/cv2 で数え直す（H の 0 またぎは2つに割る）。"""
    target = img if crop is None else img[crop[1] : crop[3], crop[0] : crop[2]]
    hsv = cv2.cvtColor(target, cv2.COLOR_BGR2HSV)
    lo = np.array([lower[0], lower[1], lower[2]], dtype=np.uint8)
    hi = np.array([upper[0], upper[1], upper[2]], dtype=np.uint8)
    upper_side = cv2.inRange(hsv, lo, np.array([179, hi[1], hi[2]], dtype=np.uint8))
    lower_side = cv2.inRange(hsv, np.array([0, lo[1], lo[2]], dtype=np.uint8), hi)
    mask = cv2.bitwise_or(upper_side, lower_side)
    return float(np.count_nonzero(mask)) / float(mask.size)


def test_color_ratio_matches_independent_count(tmp_path: Path) -> None:
    img = make_color_image()
    src = tmp_path / "colors.png"
    write_png(src, img)
    decoded = read_png(src)
    lower = [165, 100, 100]
    upper = [12, 255, 255]

    expected = independent_ratio(decoded, lower, upper, None)
    assert 0.1 < expected < 0.4

    low_ratio = expected - 0.05
    args = color_args(src, lower, upper)
    args += ["--ratio", f"{low_ratio:.4f}"]
    proc = run_cli(*args)
    assert proc.returncode == 0, proc.stderr
    payload = cli_json(proc)
    assert payload["ok"] is True
    assert payload["ratio"] == pytest.approx(expected, rel=0, abs=1e-12)
    assert payload["matched"] is True
    assert (payload["width"], payload["height"]) == (120, 90)
    assert payload["lower"] == lower
    assert payload["upper"] == upper
    assert payload["crop"] is None
    assert "preview" not in payload

    high_ratio = expected + 0.05
    args = color_args(src, lower, upper)
    args += ["--ratio", f"{high_ratio:.4f}"]
    proc = run_cli(*args)
    assert proc.returncode == 0, proc.stderr
    assert cli_json(proc)["matched"] is False


def test_color_roi_touching_edge_and_mask_preview(tmp_path: Path) -> None:
    img = make_color_image()
    src = tmp_path / "colors.png"
    write_png(src, img)
    decoded = read_png(src)
    lower = [165, 100, 100]
    upper = [12, 255, 255]
    crop = [0, 0, 60, 90]  # x1=0 で画像の端に接する

    expected = independent_ratio(decoded, lower, upper, crop)
    preview = tmp_path / "mask.png"
    args = color_args(src, lower, upper)
    args += ["--crop", *map(str, crop), "--ratio", f"{expected / 2:.4f}"]
    args += ["--preview", str(preview)]
    proc = run_cli(*args)
    assert proc.returncode == 0, proc.stderr
    payload = cli_json(proc)
    assert payload["ratio"] == pytest.approx(expected, rel=0, abs=1e-12)
    assert payload["matched"] is True
    assert payload["crop"] == crop
    assert payload["preview"] == str(preview)
    # crop 指定時のプレビューは ROI だけ（既存仕様のまま）。
    preview_img = read_png(preview)
    assert preview_img.shape[:2] == (90, 60)

    # 既存プレビューは上書きしない。
    preview.write_bytes(b"keep")
    args = color_args(src, lower, upper)
    args += ["--crop", *map(str, crop), "--preview", str(preview)]
    proc = run_cli(*args)
    assert proc.returncode == 1
    assert cli_json(proc)["ok"] is False
    assert preview.read_bytes() == b"keep"


class _FrameHandler(BaseHTTPRequestHandler):
    """`GET /frame` だけに PNG を返す最小の相手。"""

    png: bytes = b""

    def do_GET(self) -> None:  # noqa: N802 (http.server の規約)
        if self.path != "/frame":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(self.png)))
        self.end_headers()
        self.wfile.write(self.png)

    def log_message(self, *args: object) -> None:
        return


def test_frame_from_local_editor_and_guards(tmp_path: Path) -> None:
    ok, buf = cv2.imencode(".png", make_frame())
    assert ok
    _FrameHandler.png = bytes(buf)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FrameHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_address[1])
        output = tmp_path / "captures" / "shot.png"
        args = ["frame", "--server", f"http://127.0.0.1:{port}"]
        args += ["--output", str(output), "--json"]
        proc = run_cli(*args)
        assert proc.returncode == 0, proc.stderr
        payload = cli_json(proc)
        assert payload["ok"] is True
        assert payload["image"] == str(output)
        assert (payload["width"], payload["height"]) == (200, 100)
        assert output.read_bytes() == _FrameHandler.png

        # 既存の出力は上書きしない。
        output.write_bytes(b"keep")
        proc = run_cli(*args)
        assert proc.returncode == 1
        refused = cli_json(proc)
        assert refused["ok"] is False
        assert "上書き" in str(refused["message"])
        assert output.read_bytes() == b"keep"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    # localhost 以外は受け付けない（理由は日本語で返す）。
    other = tmp_path / "other.png"
    args = ["frame", "--server", "http://10.0.0.1:5100"]
    args += ["--output", str(other), "--json"]
    proc = run_cli(*args)
    assert proc.returncode in (1, 2)
    rejected = cli_json(proc)
    assert rejected["ok"] is False
    assert "localhost" in str(rejected["message"])
    assert not other.exists()
