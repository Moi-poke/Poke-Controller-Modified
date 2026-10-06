# Blocklyテンプレ画像作成（Phase B） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Blockly編集画面でキャプチャ画像から矩形を切り出して `Template/blockly/` にテンプレ画像を保存し、そのままvisionブロックのテンプレ欄へ反映できるようにする。

**Architecture:** 編集画面に同居・サーバ側crop・取得関数を注入（spec案A）。Python側は検証＋切出し＋保存＋配信のみで新設は `services/blockly_capture.py` だけ。`core/` は不変。矩形の純粋計算はDOMなしJS `assets/blockly/pokecon_capture.js` に切り出し、node vmで検証する。

**Tech Stack:** Python 3.12 / 標準ライブラリ＋OpenCV（cv2）・numpy・loguru / http.server（既存 `_Handler`）/ Blockly 13.2.1 vendored / pytest・ruff・mypy・bounds・userapi

**Spec:** `docs/superpowers/specs/2026-09-09-blockly-template-capture-design.md`

## Global Constraints

- Python >= 3.12（`X | None`・builtin generics可）。
- `SerialController/` 起点の絶対import、相対import禁止。
- コメント・利用者文面は日本語。4スペース。CDN禁止。
- 資源パスは `WindowUtils.APP_DIR` 起点。`services/` で `os.chdir` しない。
- サーバスレッドからtkinterを触らない（`print` はLogPane queue経由のため可）。
- `core/` 一式・`Commands.*` 公開面・既存エンドポイントの形・`settings*.ini` は不変。
- Gate: `ruff check`＋`ruff format --check`＋`mypy`＋`bounds`＋`userapi`＋`pytest` が全緑。
- stepsにcommitを含めない（repo rule。reviewはworking-tree diffで行う）。

---

## File Structure

- Create: `SerialController/services/blockly_capture.py` — 保存名検査・正規化矩形の検査と画素換算・PNG切出し・連番つき原子保存・`get_frame` 組立。GUI非依存。
- Modify: `SerialController/ui/blockly_editor.py` — モジュール大域 `_GET_FRAME`＋`open_blockly_editor(..., get_frame=None)`＋`GET /frame`（PNG直返し／失敗時JSON）＋`POST /template`。
- Modify: `SerialController/Menubar.py` — `OpenBlocklyEditor` で `build_get_frame(lambda: self.camera)` を注入する3行程度。
- Create: `SerialController/assets/blockly/pokecon_capture.js` — DOMなし純粋部（`PokeconCapture`）。node vm検証のためだけに独立ファイル化する（静的配信の変更は不要、同ディレクトリのため）。
- Modify: `SerialController/assets/blockly/editor.html` — 「テンプレ作成」区画＋配線。`refreshTemplates` はpromiseを返す形に変える（後方互換あり）。
- Modify: `docs/BLOCKLY_EDITOR.md` — テンプレ作成の追記。
- Create: `tests/test_blockly_capture.py` — Task 1の受け口。
- Modify: `tests/test_blockly_editor.py` — `/frame`・`/template` の実HTTP追加分。
- Modify: `tests/test_blockly_browser.py` — `pokecon_capture.js` のnode vm検証追加分。

---

### Task 1: 切出しサービス `blockly_capture.py`

**Files:**
- Create: `SerialController/services/blockly_capture.py`
- Test: `tests/test_blockly_capture.py`

**Interfaces:**
- Consumes: `core.CommandVision.clear_template_cache`（遅延import、CPU側のみ。GPU側はパス別エントリのため新規保存では古くならない。連番で上書きしないためCPU側の無効化で足りる）、`core.CommandVision._readFrameOrRaise` の文言2件（文字列を複写、importしない）
- Produces（Task 2・3が使う正確な名前と型）:
  - `BLOCKLY_TEMPLATE_DIR_REL: str = "Template/blockly"`
  - `MIN_SIDE_PX: int = 8`
  - `FRAME_FAIL_MESSAGE: str`（`"カメラから画像を取得できません（未接続 / Disable / 取得スレッド停止）。"`）
  - `NO_CAMERA_MESSAGE: str`（`"カメラが割り当てられていません。"`）
  - `validate_template_name(name: str) -> str | None`
  - `parse_rect(rect: Any) -> tuple[float, float, float, float]`
  - `rect_to_pixels(x: float, y: float, w: float, h: float, width: int, height: int) -> tuple[int, int, int, int]`
  - `crop_png(png: bytes, rect: Any) -> bytes`
  - `save_template(app_dir: str | Path, name: str, png: bytes, rect: Any) -> CaptureResult`
  - `build_get_frame(camera_getter: Callable[[], Any]) -> Callable[[], bytes | None]`
  - `CaptureResult(status: str, message: str, rel: str = "")`（statusは `saved` / `failed`。`rel` は `Template/` からの相対posix、例 `blockly/mark.png`）

- [ ] **Step 1: 検査系の失敗テストを書く**

```python
from pathlib import Path

import pytest
from services import blockly_capture


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
```

- [ ] **Step 2: テストを走らせ、失敗を確認する**

Run: `uv run --frozen pytest tests/test_blockly_capture.py -q`
Expected: FAIL（`services.blockly_capture` が無いためcollection error）。

- [ ] **Step 3: 検査系の最小実装を書く**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""キャプチャ画像からのテンプレート切出し（GUI非依存）。

正規化矩形（配信フレーム相対 0〜1）を受け、サーバ側で元画像に
戻して切り出し、`Template/blockly/` にPNG保存する。拒否規則は
Phase A（services/blockly_templates）の書式検査と同方向にする。
"""

from __future__ import annotations

import math
from pathlib import Path, PurePosixPath

BLOCKLY_TEMPLATE_DIR_REL = "Template/blockly"

#: 実画像換算でこの未満の辺を持つ矩形は無効。
MIN_SIDE_PX = 8

FRAME_FAIL_MESSAGE = (
    "カメラから画像を取得できません"
    "（未接続 / Disable / 取得スレッド停止）。"
)
NO_CAMERA_MESSAGE = "カメラが割り当てられていません。"


def validate_template_name(name: str) -> str | None:
    """保存名が不正なら理由、正常なら None を返す。"""
    if not name.strip():
        return "テンプレート名を書いてください"
    probe = name.replace("\\", "/")
    if probe.startswith("/") or (len(probe) > 1 and probe[1] == ":"):
        return "絶対パスは使えません（`Template/` からの相対で書いてください）"
    if ".." in PurePosixPath(probe).parts:
        return "`..` は使えません"
    if ":" in name:
        return "`:` は使えません"
    return None


def parse_rect(rect: object) -> tuple[float, float, float, float]:
    """`{x, y, width, height}`（0〜1）を検査して4要素にする。おかしければ ValueError。"""
    if not isinstance(rect, dict):
        raise ValueError("矩形は {x, y, width, height} で送ってください")
    try:
        x = float(rect["x"])
        y = float(rect["y"])
        w = float(rect["width"])
        h = float(rect["height"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            "矩形は {x, y, width, height} の数値で送ってください"
        ) from None
    if any(not math.isfinite(v) for v in (x, y, w, h)):
        raise ValueError("矩形に数値でない値があります")
    if w <= 0 or h <= 0:
        raise ValueError("矩形の幅または高さが0以下です")
    if x >= 1 or y >= 1 or x + w <= 0 or y + h <= 0:
        raise ValueError("矩形が画像の外を指しています")
    return (x, y, w, h)


def rect_to_pixels(
    x: float, y: float, w: float, h: float, width: int, height: int
) -> tuple[int, int, int, int]:
    """正規化矩形を画素へ戻す。はみ出しは画像内に丸める。JSの Math.round 寄せにする。"""
    x1 = min(width, max(0, int(math.floor(x * width + 0.5))))
    y1 = min(height, max(0, int(math.floor(y * height + 0.5))))
    x2 = min(width, max(0, int(math.floor((x + w) * width + 0.5))))
    y2 = min(height, max(0, int(math.floor((y + h) * height + 0.5))))
    if (x2 - x1) < MIN_SIDE_PX or (y2 - y1) < MIN_SIDE_PX:
        raise ValueError("矩形が小さすぎます（実画像で8px未満の辺があります）")
    return (x1, y1, x2, y2)
```

- [ ] **Step 4: テストを走らせ、通ることを確認する**

Run: `uv run --frozen pytest tests/test_blockly_capture.py -q`
Expected: PASS。

- [ ] **Step 5: 切出し・保存の失敗テストを書く**

Step 1の `tests/test_blockly_capture.py` へ追記する。先頭importは以下の完成形に置き換える（isort順）。

```python
from pathlib import Path

import cv2
import numpy as np
import pytest
from services import blockly_capture, blockly_templates
```

```python
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
```

- [ ] **Step 6: テストを走らせ、失敗を確認する**

Run: `uv run --frozen pytest tests/test_blockly_capture.py -q`
Expected: FAIL（`crop_png`・`save_template` が無いため5件失敗）。

- [ ] **Step 7: 切出し・保存の最小実装を書く**

Step 3のファイル末尾へ追記する。先頭importは以下の完成形に置き換える（isort順）。

```python
from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import cv2
import numpy as np
from loguru import logger
```

```python
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from loguru import logger


@dataclass
class CaptureResult:
    """切出し保存の結果。statusは saved / failed。"""

    status: str
    message: str
    rel: str = ""


def crop_png(png: bytes, rect: object) -> bytes:
    """PNGをデコード→矩形で切出し→PNGで返す。おかしければ ValueError。"""
    arr = np.frombuffer(png, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("画像を読み込めませんでした（取り直してください）")
    height, width = int(img.shape[0]), int(img.shape[1])
    x, y, w, h = parse_rect(rect)
    x1, y1, x2, y2 = rect_to_pixels(x, y, w, h, width, height)
    ok, buf = cv2.imencode(".png", img[y1:y2, x1:x2])
    if not ok:
        raise ValueError("画像の切出しに失敗しました（取り直してください）")
    return bytes(buf)


def _next_free(path: Path) -> Path:
    """重複時は `_2`・`_3` を挿した空き名を返す。"""
    if not path.exists():
        return path
    for i in range(2, 10000):
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
    raise FileExistsError(f"空き名がありません: {path}")


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """一時ファイル経由で書く。書けたらのみ置き換える。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=".tmp_bcapture_"
    )
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_template(
    app_dir: str | Path, name: str, png: bytes, rect: object
) -> CaptureResult:
    """切出し1枚を `Template/blockly/<名>.png` に保存する。検証に落ちたら何も書かない。"""
    stem = name.strip()
    reason = validate_template_name(stem)
    if reason is not None:
        return CaptureResult(status="failed", message=f"保存できません: {reason}")
    base = stem if stem.lower().endswith(".png") else stem + ".png"
    try:
        dest = _next_free(
            Path(app_dir) / PurePosixPath(BLOCKLY_TEMPLATE_DIR_REL).as_posix()
            / PurePosixPath(base).as_posix()
        )
    except (OSError, FileExistsError) as e:
        logger.warning(f"テンプレ保存に失敗: {e}")
        return CaptureResult(status="failed", message=f"保存できません: {e}")
    try:
        data = crop_png(png, rect)
    except ValueError as e:
        return CaptureResult(status="failed", message=f"保存できません: {e}")
    try:
        _atomic_write_bytes(dest, data)
    except OSError as e:
        logger.warning(f"テンプレ保存に失敗: {e}")
        return CaptureResult(status="failed", message=f"保存できません: {e}")
    try:
        from core.CommandVision import clear_template_cache

        clear_template_cache()
    except Exception as e:
        logger.warning(f"テンプレ cache 無効化に失敗: {e}")
    rel = dest.relative_to(Path(app_dir) / "Template").as_posix()
    logger.info(f"テンプレ保存: Template/{rel}")
    return CaptureResult(
        status="saved", message=f"保存しました: Template/{rel}", rel=rel
    )


def build_get_frame(
    camera_getter: Callable[[], Any],
) -> Callable[[], bytes | None]:
    """最新フレーム1枚をPNG化する関数を作る。撮れなければ None を返す。"""

    def get_frame() -> bytes | None:
        try:
            camera = camera_getter()
        except Exception:
            return None
        read = getattr(camera, "readFrame", None)
        if not callable(read):
            return None
        try:
            frame = read(copy=True)
        except Exception:
            return None
        if frame is None or getattr(frame, "size", 0) == 0:
            return None
        try:
            ok, buf = cv2.imencode(".png", frame)
        except Exception:
            return None
        if not ok:
            return None
        return bytes(buf)

    return get_frame
```

`_next_free` の10000回上限は現実には到達しない安全弁であり、到達時は上記の独立 `try` で `failed` 応答になる。

- [ ] **Step 8: `get_frame` 組立のテストを足して走らせる**

```python
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
```

Run: `uv run --frozen pytest tests/test_blockly_capture.py -q`
Expected: PASS（11 passed）。

- [ ] **Step 9: Task 1の所定gateを走らせる**

Run: `uv run --frozen pytest tests/test_blockly_capture.py tests/test_blockly_templates.py -q`
Expected: PASS。

Run: `uv run --frozen ruff check SerialController/services/blockly_capture.py tests/test_blockly_capture.py`
Expected: PASS。

Run: `uv run --frozen ruff format --check SerialController/services/blockly_capture.py tests/test_blockly_capture.py`
Expected: PASS。落ちたら本Taskのファイルに限定して `ruff format` で直す。

Run: `uv run --frozen mypy SerialController/services/blockly_capture.py`
Expected: PASS。

### Task 2: UI配線（`/frame`・`/template`・Menubar注入）

**Files:**
- Modify: `SerialController/ui/blockly_editor.py`（大域 `_GET_FRAME`＋`open_blockly_editor(..., get_frame=None)`＋`do_GET /frame`＋`do_POST /template`）
- Modify: `SerialController/Menubar.py`（`OpenBlocklyEditor` の注入3行）
- Test: `tests/test_blockly_editor.py`（fixtureに `_GET_FRAME` 設定＋6件追加）

**Interfaces:**
- Consumes: Task 1の `blockly_capture.save_template/build_get_frame/FRAME_FAIL_MESSAGE/NO_CAMERA_MESSAGE`
- Produces: `blockly_editor._GET_FRAME: Callable[[], bytes | None] | None`、`GET /frame`（成功: `image/png` 生バイト／失敗: `{"ok": false, "message"}`）、`POST /template`（要求 `{"name", "rect"}`／応答 `{"ok", "message", "path?"}`、`path` は `blockly/<名>.png` 形）

- [ ] **Step 1: `/frame` の失敗テストを書く**

`tests/test_blockly_editor.py` の `server` fixtureへ1行足す（`_GET_FRAME` の漏れ防止。`monkeypatch` は終了時に自動で戻す）。

```python
@pytest.fixture()
def server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
    app = make_app(tmp_path)
    monkeypatch.setattr(WindowUtils, "APP_DIR", str(app))
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", lambda: make_png())
    handler = functools.partial(blockly_editor._Handler)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5.0)
```

`make_png` を同ファイルへ足す（Task 1と同名の小物。共通化しない）。

```python
def make_png(width: int = 200, height: int = 100) -> bytes:
    import cv2
    import numpy as np

    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (60, 120, 200)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return bytes(buf)


def get_bytes(host: str, path: str) -> tuple[int, str, bytes]:
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    return resp.status, resp.getheader("Content-Type", ""), resp.read()


def test_frame_returns_png(server: str) -> None:
    status, ctype, body = get_bytes(server, "/frame")
    assert status == 200
    assert "image/png" in ctype
    assert body[:8] == b"\x89PNG\r\n\x1a\n"


def test_frame_no_camera_fails(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", None)
    data = get_json(server, "/frame")
    assert data["ok"] is False
```

- [ ] **Step 2: テストを走らせ、失敗を確認する**

Run: `uv run --frozen pytest tests/test_blockly_editor.py -q`
Expected: FAIL（`/frame` が静的配信に落ちて画像バイトが返らないため）。

- [ ] **Step 3: `blockly_editor.py` に `/frame` 受け口を書く**

先頭importへ `from collections.abc import Callable` は既存のため足さない。`from services import blockly_save, blockly_templates` を `from services import blockly_capture, blockly_save, blockly_templates` に変える。`_server`・`_thread` の下へ大域を足す。

```python
_GET_FRAME: Callable[[], bytes | None] | None = None
```

`do_GET` の `/templates` 分岐の直後へ足す。

```python
        if path == "/frame":
            getter = _GET_FRAME
            if getter is None:
                self._reply(False, blockly_capture.NO_CAMERA_MESSAGE)
                return
            try:
                png = getter()
            except Exception:
                png = None
            if png is None:
                self._reply(False, blockly_capture.FRAME_FAIL_MESSAGE)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
            return
```

- [ ] **Step 4: テストを走らせ、通ることを確認する**

Run: `uv run --frozen pytest tests/test_blockly_editor.py -q`
Expected: PASS（既存3＋新規2）。

- [ ] **Step 5: `/template` の失敗テストを書く**

```python
def test_template_saves_and_lists(server: str) -> None:
    data = post_json(
        server,
        "/template",
        {"name": "mark", "rect": {"x": 0, "y": 0, "width": 1, "height": 1}},
    )
    assert data["ok"] is True
    assert data["path"] == "blockly/mark.png"
    listed = get_json(server, "/templates")
    assert "blockly/mark.png" in listed["templates"]


def test_template_bad_name_fails(server: str) -> None:
    data = post_json(
        server,
        "/template",
        {"name": "../evil", "rect": {"x": 0, "y": 0, "width": 1, "height": 1}},
    )
    assert data["ok"] is False


def test_template_bad_rect_fails(server: str) -> None:
    data = post_json(
        server,
        "/template",
        {"name": "mark", "rect": {"x": 0, "y": 0, "width": 0, "height": 1}},
    )
    assert data["ok"] is False


def test_template_no_camera_fails(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", None)
    data = post_json(
        server,
        "/template",
        {"name": "mark", "rect": {"x": 0, "y": 0, "width": 1, "height": 1}},
    )
    assert data["ok"] is False
```

- [ ] **Step 6: テストを走らせ、失敗を確認する**

Run: `uv run --frozen pytest tests/test_blockly_editor.py::test_template_saves_and_lists -q`
Expected: FAIL（`/template` が404のため `post_json` の `assert resp.status == 200` で落ちる）。

- [ ] **Step 7: `/template` 受け口＋`get_frame` 引数＋Menubar注入を書く**

`do_POST` の `/save` 分岐の直前へ足す。

```python
        if urllib.parse.urlsplit(self.path).path == "/template":
            payload = self._read_json()
            if payload is None:
                return
            getter = _GET_FRAME
            if getter is None:
                self._reply(False, blockly_capture.NO_CAMERA_MESSAGE)
                return
            try:
                png = getter()
            except Exception:
                png = None
            if png is None:
                self._reply(False, blockly_capture.FRAME_FAIL_MESSAGE)
                return
            res = blockly_capture.save_template(
                WindowUtils.APP_DIR,
                str(payload.get("name", "")),
                png,
                payload.get("rect"),
            )
            print(res.message)
            extra = {"path": res.rel} if res.status == "saved" else {}
            self._reply(res.status == "saved", res.message, extra)
            return
```

`open_blockly_editor` の引数へ足す。

```python
def open_blockly_editor(
    root: Any,
    *,
    is_busy: Callable[[], bool],
    reload_commands: Callable[[], None],
    get_frame: Callable[[], bytes | None] | None = None,
) -> None:
```

関数先頭（`_stop_server()` の前）へ1行足す。

```python
    global _GET_FRAME
    _GET_FRAME = get_frame
```

`Menubar.py` の `OpenBlocklyEditor` を書き換える。

```python
    def OpenBlocklyEditor(self) -> None:
        """Blocklyエディタを開く。実手順は ui.blockly_editor。"""
        from services import blockly_capture
        from ui import blockly_editor

        blockly_editor.open_blockly_editor(
            self.root,
            is_busy=lambda: self.app.runner.is_busy(),
            reload_commands=self.app.reloadCommands,
            get_frame=blockly_capture.build_get_frame(lambda: self.camera),
        )
```

`lambda: self.camera` は呼ぶたびにpropertyを評価するため、Window側でカメラが再生成されても古い参照を掴まない。

- [ ] **Step 8: テストを走らせ、通ることを確認する**

Run: `uv run --frozen pytest tests/test_blockly_editor.py tests/test_blockly_capture.py -q`
Expected: PASS（9＋11）。

- [ ] **Step 9: Task 2の所定gateを走らせる**

Run: `uv run --frozen ruff check SerialController/ui/blockly_editor.py SerialController/Menubar.py tests/test_blockly_editor.py`
Expected: PASS。

Run: `uv run --frozen ruff format --check SerialController/ui/blockly_editor.py SerialController/Menubar.py tests/test_blockly_editor.py`
Expected: PASS。落ちたら本Taskのファイルに限定して直す。

Run: `uv run --frozen mypy SerialController/ui/blockly_editor.py SerialController/Menubar.py`
Expected: PASS。

Run: `uv run --frozen python tools/check_core.py`
Expected: PASS（ui→services・Menubar内importは許可範囲のため）。

### Task 3: ブラウザ区画（純粋JS＋editor.html配線）

**Files:**
- Create: `SerialController/assets/blockly/pokecon_capture.js`
- Modify: `SerialController/assets/blockly/editor.html`
- Test: `tests/test_blockly_browser.py`（末尾へprobe追加）

**Interfaces:**
- Consumes: Task 2の `GET /frame`・`POST /template`・`GET /templates` の形
- Produces: `PokeconCapture.normalizeDrag01/normalizeDrag01` ではなく正確に `PokeconCapture.{clamp01, normalizeDrag01, toPixels, isValidPixels}`（JS大域 `var PokeconCapture`）

- [ ] **Step 1: node probeの失敗テストを書く**

`tests/test_blockly_browser.py` の末尾へ足す（既存の `NEEDS_NODE`・`BLOCKLY`・runner定型をそのまま使う）。

```python
CAPTURE_PROBE_JS = """\
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const root = process.argv[1];
const src = fs.readFileSync(path.join(root, 'pokecon_capture.js'), 'utf8');
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: 'pokecon_capture.js' });
const fail = (msg) => {
  console.error('CAPTURE-PROBE-FAIL: ' + msg);
  process.exit(1);
};
const c = sandbox.PokeconCapture;
if (!c) { fail('PokeconCapture が無い'); }
let r = c.normalizeDrag01(0.75, 0.75, 0.25, 0.25);
if (r.x !== 0.25 || r.y !== 0.25 || r.width !== 0.5 || r.height !== 0.5) {
  fail('逆転ドラッグの正規化: ' + JSON.stringify(r));
}
r = c.normalizeDrag01(-0.5, -0.5, 1.5, 1.5);
if (r.x !== 0 || r.y !== 0 || r.width !== 1 || r.height !== 1) {
  fail('はみ出し丸め: ' + JSON.stringify(r));
}
if (c.isValidPixels({ x: 0, y: 0, width: 0.5, height: 0.5 }, 200, 100) !== true) {
  fail('正常矩形を通さない');
}
if (c.isValidPixels({ x: 0, y: 0, width: 0.01, height: 0.5 }, 200, 100) !== false) {
  fail('8px未満を通した');
}
if (c.isValidPixels({ x: 0, y: 0, width: 0.04, height: 0.08 }, 200, 100) !== true) {
  fail('8px境界を通さない');
}
console.log('CAPTURE-PROBE-OK');
"""


@NEEDS_NODE
def test_capture_rect_helpers() -> None:
    proc = subprocess.run(
        ["node", "-e", CAPTURE_PROBE_JS, str(BLOCKLY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"probe失敗:\n{proc.stderr}\n{proc.stdout}"
    assert "CAPTURE-PROBE-OK" in proc.stdout
```

- [ ] **Step 2: テストを走らせ、失敗を確認する**

Run: `uv run --frozen pytest tests/test_blockly_browser.py::test_capture_rect_helpers -q`
Expected: FAIL（node有り環境では `pokecon_capture.js` が無いため読込で非ゼロ終了。node無し環境ではskipされ、Step 3以降の目視確認に切り替える）。

- [ ] **Step 3: `pokecon_capture.js` の最小実装を書く**

```js
// テンプレ切出しの純粋部（DOMなし。node vm検証用）。
// 画素換算は Python 側 rect_to_pixels と同方向（四捨五入・はみ出し丸め・8px）。
var PokeconCapture = (function () {
  'use strict';
  function clamp01(v) { return Math.min(1, Math.max(0, v)); }
  function normalizeDrag01(ax, ay, bx, by) {
    var x = clamp01(Math.min(ax, bx));
    var y = clamp01(Math.min(ay, by));
    return {
      x: x,
      y: y,
      width: clamp01(Math.max(ax, bx)) - x,
      height: clamp01(Math.max(ay, by)) - y,
    };
  }
  function toPixels(rect, natW, natH) {
    var x1 = Math.min(natW, Math.max(0, Math.round(rect.x * natW)));
    var y1 = Math.min(natH, Math.max(0, Math.round(rect.y * natH)));
    var x2 = Math.min(natW, Math.max(0, Math.round((rect.x + rect.width) * natW)));
    var y2 = Math.min(natH, Math.max(0, Math.round((rect.y + rect.height) * natH)));
    return { x1: x1, y1: y1, x2: x2, y2: y2 };
  }
  function isValidPixels(rect, natW, natH) {
    if (!natW || !natH) { return false; }
    var p = toPixels(rect, natW, natH);
    return (p.x2 - p.x1) >= 8 && (p.y2 - p.y1) >= 8;
  }
  return {
    clamp01: clamp01,
    normalizeDrag01: normalizeDrag01,
    toPixels: toPixels,
    isValidPixels: isValidPixels,
  };
})();
```

- [ ] **Step 4: テストを走らせ、通ることを確認する**

Run: `uv run --frozen pytest tests/test_blockly_browser.py -q`
Expected: PASS（node有りで既存2＋新規1、node無しで3 skip）。

- [ ] **Step 5: `editor.html` に区画と配線を書く**

`pokecon_blocks.js` の `<script>` の直後へ1行足す。

```html
<script src="./pokecon_capture.js"></script>
```

`bar2` の `</div>` の直後へ区画を足す。

```html
<div id="bar3" style="padding: 0 8px 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
<button id="capget">キャプチャ取得</button>
<span id="capwrap" style="position: relative; display: none;">
<img id="capimg" alt="キャプチャ" style="max-width: 320px; max-height: 180px; border: 1px solid #999; cursor: crosshair;">
<div id="caprect" style="position: absolute; border: 2px solid #f00; display: none; pointer-events: none;"></div>
</span>
<label>保存名 <input id="capname" value="" placeholder="例: mark"></label>
<button id="capsave" disabled>切出し保存</button>
<span id="capstatus"></span>
</div>
```

`refreshTemplates` をpromise返しに変える（呼び出し側はそのまま動く）。

```js
  function refreshTemplates() {
    return fetch('./templates').then(function (r) { return r.json(); }).then(function (j) {
      var sel = document.getElementById('tpllist');
      sel.textContent = '';
      (j.templates || []).forEach(function (name) {
        var opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        sel.appendChild(opt);
      });
    }).catch(function () { /* 候補なしでも保存はできる */ });
  }
```

`refreshFiles(); refreshTemplates();` の直前へ配線一式を足す。

```js
  var capRect01 = null;
  var dragStart = null;
  function capSetStatus(s, isErr) {
    var el = document.getElementById('capstatus');
    el.textContent = s;
    el.style.color = isErr ? '#c00' : '#333';
    el.style.fontWeight = isErr ? 'bold' : 'normal';
  }
  function capDraw() {
    var box = document.getElementById('caprect');
    var img = document.getElementById('capimg');
    if (!capRect01) { box.style.display = 'none'; return; }
    box.style.display = 'block';
    box.style.left = (capRect01.x * img.offsetWidth) + 'px';
    box.style.top = (capRect01.y * img.offsetHeight) + 'px';
    box.style.width = (capRect01.width * img.offsetWidth) + 'px';
    box.style.height = (capRect01.height * img.offsetHeight) + 'px';
  }
  document.getElementById('capget').addEventListener('click', function () {
    capSetStatus('取得しています…', false);
    fetch('./frame').then(function (r) {
      var ct = r.headers.get('Content-Type') || '';
      if (ct.indexOf('image/png') !== -1) {
        return r.blob().then(function (b) { return { img: b }; });
      }
      return r.json().then(function (j) { return { err: j.message || '取得できません' }; });
    }).then(function (res) {
      if (res.err) {
        capSetStatus(res.err, true);
        document.getElementById('capsave').disabled = true;
        return;
      }
      var img = document.getElementById('capimg');
      if (img.src && img.src.indexOf('blob:') === 0) { URL.revokeObjectURL(img.src); }
      img.src = URL.createObjectURL(res.img);
      document.getElementById('capwrap').style.display = 'inline-block';
      capRect01 = null; capDraw();
      document.getElementById('capsave').disabled = false;
      capSetStatus('画像をドラッグして範囲を選んでください', false);
    }).catch(function (e) { capSetStatus('取得できません: ' + e, true); });
  });
  document.getElementById('capimg').addEventListener('mousedown', function (ev) {
    var r = this.getBoundingClientRect();
    dragStart = {
      x: (ev.clientX - r.left) / r.width,
      y: (ev.clientY - r.top) / r.height,
    };
    capRect01 = null; capDraw();
    ev.preventDefault();
  });
  window.addEventListener('mousemove', function (ev) {
    if (!dragStart) { return; }
    var img = document.getElementById('capimg');
    var r = img.getBoundingClientRect();
    var cur = {
      x: (ev.clientX - r.left) / r.width,
      y: (ev.clientY - r.top) / r.height,
    };
    capRect01 = PokeconCapture.normalizeDrag01(dragStart.x, dragStart.y, cur.x, cur.y);
    capDraw();
  });
  window.addEventListener('mouseup', function () { dragStart = null; });
  document.getElementById('capsave').addEventListener('click', function () {
    var img = document.getElementById('capimg');
    var name = document.getElementById('capname').value;
    if (!capRect01) { capSetStatus('画像上でドラッグして範囲を選んでください', true); return; }
    if (!img.naturalWidth) { capSetStatus('画像の読込中です。取り直してください', true); return; }
    if (!PokeconCapture.isValidPixels(capRect01, img.naturalWidth, img.naturalHeight)) {
      capSetStatus('範囲が小さすぎます（実画像で8px未満の辺があります）', true);
      return;
    }
    if (!name.trim()) { capSetStatus('保存名を書いてください', true); return; }
    fetch('./template', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, rect: capRect01 }),
    }).then(function (r) { return r.json(); }).then(function (j) {
      capSetStatus(j.message || (j.ok ? '保存しました' : '保存できません'), !j.ok);
      if (j.ok) {
        capRect01 = null; capDraw();
        refreshTemplates().then(function () {
          if (j.path) { document.getElementById('tpllist').value = j.path; }
        });
      }
    }).catch(function (e) { capSetStatus('保存できません: ' + e, true); });
  });
```

既存の `tplapply`・保存・開く・削除の各配線には触らない。`setStatus('反映しました: ' + name, false)` の重複行（既存の写し間違い）にも触らない。

- [ ] **Step 6: ブラウザ資産の回帰を走らせる**

Run: `uv run --frozen pytest tests/test_blockly_browser.py tests/test_blockly_editor.py -q`
Expected: PASS。

### Task 4: 文書＋全体gate＋手動手順

**Files:**
- Modify: `docs/BLOCKLY_EDITOR.md`
- Test: 全体gate（`task ci` 相当を `uv run` で順に実行）

**Interfaces:**
- Consumes: Task 1〜3の全成果物
- Produces: 利用者向け手順＋手動確認の合否記録（ код変更なし）

- [ ] **Step 1: `docs/BLOCKLY_EDITOR.md` にテンプレ作成を追記する**

15行目（テンプレ画像の段落）の直後へ挿入する。

```markdown
テンプレ作成： 編集画面の「テンプレ作成」区画で`[キャプチャ取得]`→画像をドラッグで範囲選択→保存名を書いて`[切出し保存]`。保存先は`Template/blockly/<名>.png`固定で重複は`_2`・`_3`連番。保存後は候補に自動で入り、画像欄に選ばれた状態になるので`選択中へ反映`でそのまま使える。実画像で8px未満の辺がある範囲は無効。カメラが無いときは取得に理由が出て保存はできない。
```

- [ ] **Step 2: 全体gateを順に走らせる**

Run: `uv run --frozen ruff check SerialController tools tests`
Expected: PASS。

Run: `uv run --frozen ruff format --check SerialController tools tests`
Expected: PASS（落ちたら本planのファイルに限定して直す）。

Run: `uv run --frozen mypy SerialController tools tests`
Expected: PASS。

Run: `uv run --frozen python tools/check_core.py`
Expected: PASS。

Run: `uv run --frozen python tools/check_user_api.py`
Expected: PASS（生成コードのimportは凍結面内のため件数のみ微増）。

Run: `uv run --frozen pytest tests -q`
Expected: PASS（133＋新規約18＝約151 passed。node無しではbrowser 3件skip）。

- [ ] **Step 3: 手動確認を行う（ユーザ環境、カメラ隣接）**

1. メニュー→コマンド→「Blocklyエディタ...」→`[キャプチャ取得]`で画像が出る。
2. ドラッグ→`[切出し保存]`（名は `mark`）→`保存しました: Template/blockly/mark.png` と出て候補に入る。
3. 画像欄が `blockly/mark.png` になり、`選択中へ反映` でvisionブロックのテンプレ欄に入る。
4. `wait_appear`＋`press` を組んで保存→一覧反映→カメラあり実行で待ちが解ける。
5. 同名で再保存すると `mark_2.png` になる。
6. 旧フロー（保存・開く・削除・選択中反映）が不変。

成功基準: 自動Gate緑＋上記1〜6の往復確認。マッチ精度自体は対象外。
