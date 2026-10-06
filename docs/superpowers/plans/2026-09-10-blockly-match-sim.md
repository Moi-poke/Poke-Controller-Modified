# Blockly照合シミュレーション Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** テンプレートが画像内でどれくらいのthresholdで見つかるかをBlockly編集画面のmodal内で試せるようにする。

**Architecture:** サーバ側cv2で実行時（`isContainTemplate`）と同一条件で照合する。新設は`services/blockly_match.py`だけ。画像源は最新frame再取得とブラウザ送付の二系統。JS側の純粋計算は増やさず、既存modalへ区画追加のみ。

**Tech Stack:** Python 3.12 / OpenCV（cv2）・numpy・loguru / http.server（既存 `_Handler`）/ pytest・ruff・mypy・bounds・userapi

**Spec:** `docs/superpowers/specs/2026-09-10-blockly-match-sim-design.md`

## Global Constraints

- Python >= 3.12（`X | None`・builtin generics可）。
- `SerialController/` 起点の絶対import、相対import禁止。
- コメント・利用者文面は日本語。4スペース。CDN禁止。
- 資源パスは `WindowUtils.APP_DIR` 起点。`services/` で `os.chdir` しない。
- サーバスレッドからtkinterを触らない（`print` はLogPane queue経由のため可）。
- `core/` 一式・`Commands.*` 公開面・既存エンドポイントの形・`settings*.ini` は不変。
- Gate: `ruff check`＋`ruff format --check`＋`mypy`＋`bounds`＋`userapi`＋`pytest` が全緑。
- stepsにcommitを含めない（repo rule。reviewはworking-tree diffで行う）。
- 本planと `2026-09-10-blockly-preview.md` は `editor.html` と `tests/test_blockly_editor.py` を共有するため逐次実行する（本planが先）。

---

## File Structure

- Create: `SerialController/services/blockly_match.py` — threshold検査・PNGデコード・実行時同一条件の照合・`MatchResult`。GUI非依存。
- Modify: `SerialController/ui/blockly_editor.py` — `POST /match` 受け口の追加のみ。
- Modify: `SerialController/assets/blockly/editor.html` — modal内へthreshold行・照合ボタン・結果重ね描き・一致度表示・画像アップロードの追加のみ。
- Modify: `docs/BLOCKLY_EDITOR.md` — シミュレーションの追記。
- Create: `tests/test_blockly_match.py` — Task 1の受け口。
- Modify: `tests/test_blockly_editor.py` — `/match` の実HTTP追加分。

---

### Task 1: 照合サービス `blockly_match.py`

**Files:**
- Create: `SerialController/services/blockly_match.py`
- Test: `tests/test_blockly_match.py`

**Interfaces:**
- Consumes: `core.CommandVision._imread_or_raise(template_path, flags)`（存在・破損の文言を実行時と同一にする）、`core.CommandVision.VisionMixin._checkTemplate(src, template, mask)`（staticmethodのためインスタンス不要）、`services.blockly_capture.validate_template_name`（拒否規則）、`services.blockly_capture.FRAME_FAIL_MESSAGE`（frameデコード失敗時の文言流用はしない。本文参照）
- Produces（Task 2・3が使う正確な名前と型）:
  - `match_template(app_dir: str | Path, frame_png: bytes, template: str, threshold: object, use_gray: bool = True) -> MatchResult`
  - `decode_frame_png(png: bytes) -> np.ndarray`（デコードできないとき ValueError「画像を読み込めませんでした（取り直してください）」）
  - `parse_threshold(value: object) -> float`（数値0〜1以外は ValueError「しきい値は0〜1の数値で書いてください」）
  - `decode_upload_image(b64: str) -> bytes`（10MB超・base64破損は ValueError）
  - `MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024`
  - `MatchResult(status: str, message: str, score: float = 0.0, matched: bool = False, rect: dict[str, int] = ...)`（statusは `ok` / `failed`。`rect` は実画像画素の `{x, y, width, height}`）

- [ ] **Step 1: 検査系＋ヒット系の失敗テストを書く**

```python
from pathlib import Path

import cv2
import numpy as np
import pytest
from services import blockly_match


def make_frame() -> np.ndarray:
    """200x100のグレー地に白矩形(60,30,80x40)。テンプレはここから切り出す。"""
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[:] = (60, 60, 60)
    img[30:70, 60:140] = (255, 255, 255)
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
```

- [ ] **Step 2: テストを走らせ、失敗を確認する**

Run: `uv run --frozen pytest tests/test_blockly_match.py -q`
Expected: FAIL（`services.blockly_match` が無いためcollection error）。

- [ ] **Step 3: サービスの最小実装を書く**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""テンプレート照合の試し打ち（GUI非依存）。

実行時（`core.CommandVision.isContainTemplate`）と同一条件で
`TM_CCOEFF_NORMED` の最大一致度を求める。テンプレ読込は core の
`_imread_or_raise` を使い、存在・破損の文言も実行時と同じにする。
"""

from __future__ import annotations

import base64
import binascii
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from loguru import logger
from services import blockly_capture

#: upload画像の上限（localhostのため緩めの安全弁）。
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@dataclass
class MatchResult:
    """照合の結果。statusは ok / failed。"""

    status: str
    message: str
    score: float = 0.0
    matched: bool = False
    rect: dict[str, int] = field(default_factory=dict)


def parse_threshold(value: object) -> float:
    """しきい値（0〜1の数値）を検査する。おかしければ ValueError。"""
    try:
        threshold = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("しきい値は0〜1の数値で書いてください") from None
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("しきい値は0〜1の数値で書いてください")
    return threshold


def decode_frame_png(png: bytes) -> np.ndarray:
    """PNGバイト列をBGR画像にする。読めなければ ValueError。"""
    arr = np.frombuffer(png, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("画像を読み込めませんでした（取り直してください）")
    return img


def decode_upload_image(b64: str) -> bytes:
    """base64の画像送付をPNGバイト列に戻す。おかしければ ValueError。"""
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("画像を読み込めませんでした（取り直してください）") from None
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("画像が大きすぎます（10MBまで）")
    decode_frame_png(raw)
    return raw


def match_template(
    app_dir: str | Path,
    frame_png: bytes,
    template: str,
    threshold: object,
    use_gray: bool = True,
) -> MatchResult:
    """1枚の画像内でテンプレの最大一致度を求める。検証に落ちたらfailedで返す。"""
    name = str(template).strip()
    reason = blockly_capture.validate_template_name(name)
    if reason is not None:
        return MatchResult(status="failed", message=f"照合できません: {reason}")
    try:
        limit = parse_threshold(threshold)
    except ValueError as e:
        return MatchResult(status="failed", message=f"照合できません: {e}")
    try:
        src_img = decode_frame_png(frame_png)
    except ValueError as e:
        return MatchResult(status="failed", message=f"照合できません: {e}")
    from core.CommandVision import VisionMixin, _imread_or_raise

    flags = cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR
    try:
        src = cv2.cvtColor(src_img, cv2.COLOR_BGR2GRAY) if use_gray else src_img
        template_img = _imread_or_raise(name, flags)
        VisionMixin._checkTemplate(src, template_img, None)
    except (ValueError, FileNotFoundError, cv2.error) as e:
        logger.warning(f"テンプレ照合の準備に失敗: {e}")
        return MatchResult(status="failed", message=f"照合できません: {e}")
    res = cv2.matchTemplate(src, template_img, cv2.TM_CCOEFF_NORMED)
    res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    score = float(max_val)
    height, width = int(template_img.shape[0]), int(template_img.shape[1])
    rect = {"x": int(max_loc[0]), "y": int(max_loc[1]), "width": width, "height": height}
    matched = score >= limit
    tail = "ヒット" if matched else "ミス"
    logger.info(f"テンプレ照合: {name} 一致度={score:.3f} しきい値={limit} → {tail}")
    return MatchResult(
        status="ok",
        message=f"一致度 {score * 100:.1f}%（しきい値 {limit * 100:.1f}%）→ {tail}",
        score=score,
        matched=matched,
        rect=rect,
    )
```

`_checkTemplate` は `VisionMixin` のstaticmethodのためインスタンスなしで呼べる。`_imread_or_raise` の `FileNotFoundError` は実行時と同じ文言（置き場所付き案内）でそのまま返す。`float(value)` の `# type: ignore[arg-type]` はobject受けの既定形（`blockly_capture.parse_rect` と同方向）。

- [ ] **Step 4: テストを走らせ、通ることを確認する**

Run: `uv run --frozen pytest tests/test_blockly_match.py -q`
Expected: PASS（7 passed）。

- [ ] **Step 5: Task 1の所定gateを走らせる**

Run: `uv run --frozen pytest tests/test_blockly_match.py tests/test_blockly_templates.py -q`
Expected: PASS。

Run: `uv run --frozen ruff check SerialController/services/blockly_match.py tests/test_blockly_match.py`
Expected: PASS。

Run: `uv run --frozen ruff format --check SerialController/services/blockly_match.py tests/test_blockly_match.py`
Expected: PASS。落ちたら本Taskのファイルに限定して `ruff format` で直す。

Run: `uv run --frozen mypy SerialController/services/blockly_match.py`
Expected: PASS。

### Task 2: UI配線（`POST /match`）

**Files:**
- Modify: `SerialController/ui/blockly_editor.py`（`do_POST /match` 分岐の追加のみ）
- Test: `tests/test_blockly_editor.py`（5件追加）

**Interfaces:**
- Consumes: Task 1の `blockly_match.match_template/decode_upload_image`、`blockly_capture` の `NO_CAMERA_MESSAGE`・`FRAME_FAIL_MESSAGE`
- Produces: `POST /match`（要求 `{"template","threshold","use_gray","source","image?"}`／応答 `{"ok","message","score?","matched?","rect?"}`）

- [ ] **Step 1: `/match` の失敗テストを書く**

`tests/test_blockly_editor.py` へ追記する。同ファイルの `server` fixture（`APP_DIR` 差し替え＋`_GET_FRAME` 既定）はそのまま使う。`make_png` は単色のため照合には使わず、本Taskのヘルパを足す。

```python
def make_match_frame() -> bytes:
    import cv2
    import numpy as np

    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[:] = (60, 60, 60)
    img[30:70, 60:140] = (255, 255, 255)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return bytes(buf)


def test_match_frame_hit(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cv2
    import numpy as np

    from services import blockly_match

    frame = make_match_frame()
    arr = np.frombuffer(frame, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None
    tpl = img[30:70, 60:140]
    ok, buf = cv2.imencode(".png", tpl)
    assert ok
    app = Path(WindowUtils.APP_DIR)
    dest = app / "Template" / "pack"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "part.png").write_bytes(bytes(buf))
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", lambda: frame)
    data = post_json(
        server,
        "/match",
        {"template": "pack/part.png", "threshold": 0.7, "source": "frame"},
    )
    assert data["ok"] is True
    assert data["matched"] is True
    assert data["rect"] == {"x": 60, "y": 30, "width": 80, "height": 40}
    assert blockly_match.parse_threshold(data["score"]) >= 0.0


def test_match_upload_hit(server: str) -> None:
    import base64

    import cv2
    import numpy as np

    frame = make_match_frame()
    arr = np.frombuffer(frame, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None
    ok, buf = cv2.imencode(".png", img[30:70, 60:140])
    assert ok
    app = Path(WindowUtils.APP_DIR)
    dest = app / "Template" / "pack"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "part.png").write_bytes(bytes(buf))
    data = post_json(
        server,
        "/match",
        {
            "template": "pack/part.png",
            "threshold": 0.7,
            "source": "upload",
            "image": base64.b64encode(frame).decode(),
        },
    )
    assert data["ok"] is True
    assert data["matched"] is True


def test_match_missing_template_fails(server: str) -> None:
    data = post_json(
        server,
        "/match",
        {"template": "pack/none.png", "threshold": 0.7, "source": "frame"},
    )
    assert data["ok"] is False


def test_match_bad_threshold_fails(server: str) -> None:
    data = post_json(
        server,
        "/match",
        {"template": "pack/part.png", "threshold": "high", "source": "frame"},
    )
    assert data["ok"] is False


def test_match_no_camera_fails(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", None)
    data = post_json(
        server,
        "/match",
        {"template": "pack/part.png", "threshold": 0.7, "source": "frame"},
    )
    assert data["ok"] is False
```

先頭importへ `base64` は関数内importのため足さない。`Path`・`pytest`・`WindowUtils`・`blockly_editor`・`post_json` は既存のため足さない。

- [ ] **Step 2: テストを走らせ、失敗を確認する**

Run: `uv run --frozen pytest tests/test_blockly_editor.py -q -k match`
Expected: FAIL（`/match` が404のため `post_json` の `assert resp.status == 200` で落ちる）。

- [ ] **Step 3: `/match` 受け口を書く**

`do_POST` の `/template` 分岐の直後（`/save` 分岐の直前）へ足す。import行を `from services import blockly_capture, blockly_match, blockly_save, blockly_templates` に変える。

```python
        if urllib.parse.urlsplit(self.path).path == "/match":
            payload = self._read_json()
            if payload is None:
                return
            source = str(payload.get("source", "frame"))
            png: bytes | None
            if source == "upload":
                try:
                    png = blockly_match.decode_upload_image(
                        str(payload.get("image", ""))
                    )
                except ValueError as e:
                    self._reply(False, f"照合できません: {e}")
                    return
            else:
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
            res = blockly_match.match_template(
                WindowUtils.APP_DIR,
                png,
                str(payload.get("template", "")),
                payload.get("threshold", 0.7),
                bool(payload.get("use_gray", True)),
            )
            print(res.message)
            extra = (
                {"score": res.score, "matched": res.matched, "rect": res.rect}
                if res.status == "ok"
                else {}
            )
            self._reply(res.status == "ok", res.message, extra)
            return
```

`source` が `frame` でも `upload` でもない値は `frame` 扱いにする（else側に落ちる）。`use_gray` の `bool(...)` 化は JSON の真偽値をそのまま通すため。

- [ ] **Step 4: テストを走らせ、通ることを確認する**

Run: `uv run --frozen pytest tests/test_blockly_editor.py tests/test_blockly_match.py -q`
Expected: PASS。

- [ ] **Step 5: Task 2の所定gateを走らせる**

Run: `uv run --frozen ruff check SerialController/ui/blockly_editor.py tests/test_blockly_editor.py`
Expected: PASS。

Run: `uv run --frozen ruff format --check SerialController/ui/blockly_editor.py tests/test_blockly_editor.py`
Expected: PASS。落ちたら本Taskのファイルに限定して直す。

Run: `uv run --frozen mypy SerialController/ui/blockly_editor.py`
Expected: PASS。

Run: `uv run --frozen python tools/check_core.py`
Expected: PASS。

### Task 3: modal内UI＋文書＋全体gate

**Files:**
- Modify: `SerialController/assets/blockly/editor.html`（modal内へthreshold行・照合ボタン・結果重ね描き・一致度表示・画像アップロードの追加のみ）
- Modify: `docs/BLOCKLY_EDITOR.md`（1段落追記）
- Test: `tests/test_blockly_browser.py`（静的markup検査1件。node不要）

**Interfaces:**
- Consumes: Task 2の `POST /match` の形
- Produces: なし（末端）

- [ ] **Step 1: markup存在検査の失敗テストを書く**

node不要のPythonテストとして末尾へ足す（`test_modal_hint_is_static_markup` と同形）。

```python
def test_match_section_markup_exists() -> None:
    """照合区画の要素がeditor.htmlにあること。"""
    html = (BLOCKLY / "editor.html").read_text(encoding="utf-8")
    for token in [
        'id="capth"',
        'id="caprth"',
        'id="capmatch"',
        'id="capmatchrect"',
        'id="capmatchstatus"',
        'id="capupload"',
        'id="capuseupload"',
    ]:
        assert token in html
```

- [ ] **Step 2: テストを走らせ、失敗を確認する**

Run: `uv run --frozen pytest tests/test_blockly_browser.py::test_match_section_markup_exists -q`
Expected: FAIL（要素が無いため最初のassertで落ちる）。

- [ ] **Step 3: modal内区画と配線を書く**

数値欄のdiv（`capx`〜`caprh` の行）の直後へ挿入する。

```html
<div style="display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap;">
<span style="display: inline-flex; gap: 4px; align-items: center; white-space: nowrap;">しきい値 <input id="capth" type="number" step="0.01" min="0" max="1" value="0.7" style="width: 5em;"><input id="caprth" type="range" min="0" max="1" step="0.01" value="0.7" style="width: 120px;"></span>
<button id="capmatch">照合テスト</button>
<span id="capmatchstatus"></span>
</div>
<div style="display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap;">
<label>画像ファイル <input id="capupload" type="file" accept="image/*"></label>
<button id="capuseupload">この画像で試す</button>
<span id="capuploadname" style="color: #666;"></span>
</div>
```

`capbigwrap` 内、`capbigrect` の直後へ結果枠を足す。

```html
<div id="capmatchrect" style="position: absolute; border: 3px solid #00f; display: none; pointer-events: none;"></div>
```

`capModalClose` の定義直後へ配線一式を足す。

```js
  var capUploadDataUrl = null;
  function capMatchSetStatus(s, isErr) {
    var el = document.getElementById('capmatchstatus');
    el.textContent = s;
    el.style.color = isErr ? '#c00' : '#333';
    el.style.fontWeight = isErr ? 'bold' : 'normal';
  }
  function capMatchDraw(rect, matched) {
    var box = document.getElementById('capmatchrect');
    var img = document.getElementById('capbigimg');
    if (!rect || !img.offsetWidth || !img.naturalWidth) { box.style.display = 'none'; return; }
    var sx = img.offsetWidth / img.naturalWidth, sy = img.offsetHeight / img.naturalHeight;
    box.style.display = 'block';
    box.style.borderColor = matched ? '#00f' : '#f00';
    box.style.left = (rect.x * sx) + 'px';
    box.style.top = (rect.y * sy) + 'px';
    box.style.width = (rect.width * sx) + 'px';
    box.style.height = (rect.height * sy) + 'px';
  }
  function capMatchHide() {
    document.getElementById('capmatchrect').style.display = 'none';
  }
  function capMatch() {
    var tpl = document.getElementById('tpllist').value;
    if (!tpl) { capMatchSetStatus('画像を選んでください', true); return; }
    var th = parseFloat(document.getElementById('capth').value);
    var body = { template: tpl, threshold: th, use_gray: true,
      source: capUploadDataUrl ? 'upload' : 'frame' };
    if (capUploadDataUrl) { body.image = capUploadDataUrl.split(',', 2)[1]; }
    capMatchSetStatus('照合しています…', false);
    fetch('./match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (!j.ok) { capMatchHide(); capMatchSetStatus(j.message || '照合できません', true); return; }
      capMatchDraw(j.rect, j.matched);
      capMatchSetStatus(j.message || ('一致度 ' + (j.score * 100).toFixed(1) + '%'), !j.matched);
    }).catch(function (e) { capMatchHide(); capMatchSetStatus('照合できません: ' + e, true); });
  }
  document.getElementById('capmatch').addEventListener('click', capMatch);
  document.getElementById('capth').addEventListener('input', function () {
    document.getElementById('caprth').value = this.value;
  });
  document.getElementById('caprth').addEventListener('input', function () {
    document.getElementById('capth').value = this.value;
  });
  document.getElementById('capupload').addEventListener('change', function () {
    var f = this.files && this.files[0];
    if (!f) { return; }
    var reader = new FileReader();
    reader.onload = function () {
      var big = document.getElementById('capbigimg');
      big.src = reader.result;
      capUploadDataUrl = reader.result;
      document.getElementById('capuploadname').textContent = f.name + ' で試します';
      capModalSetStatus('手動画像を表示しました', false);
    };
    reader.readAsDataURL(f);
  });
  document.getElementById('capuseupload').addEventListener('click', function () {
    if (!capUploadDataUrl) { capMatchSetStatus('画像ファイルを選んでください', true); return; }
    capMatch();
  });
```

`capFetch` の成功分岐（`capModalOpen();` の直前）へ2行足す（frame取り直しでupload指定と前回結果を消す）。

```js
      capUploadDataUrl = null;
      document.getElementById('capuploadname').textContent = '';
      capMatchHide();
```

`capModalClose` へは足さない（閉じるだけ。開き直し時は `capFetch` 経由でなければ前回画像のまま。前回結果の重ね描きは `capMatchHide()` を `capModalOpen` の末尾にも1行足して消す）。

```js
  function capModalOpen() {
```

の末尾（`capModalSetStatus('', false);` の直後）へ足す。

```js
    capMatchHide();
```

既存の `tplapply`・保存・開く・削除・切出し保存の配線には触らない。probeスタブ（`PROBE_TPLAPPLY_JS`・`MODAL_PROBE_JS` のid一覧）へ新規id（`capth`・`caprth`・`capmatch`・`capmatchrect`・`capmatchstatus`・`capupload`・`capuseupload`・`capuploadname`）を足す（テストのみの変更。足さないとinline script読込で落ちる）。

- [ ] **Step 4: テストを走らせ、通ることを確認する**

Run: `uv run --frozen pytest tests/test_blockly_browser.py tests/test_blockly_editor.py -q`
Expected: PASS。

- [ ] **Step 5: `docs/BLOCKLY_EDITOR.md` に1段落追記する**

テンプレ作成の段落の直後へ挿入する。

```markdown
照合テスト： modal内のしきい値（既定0.7）を決めて`[照合テスト]`を押すと、候補画像（`画像`欄の選択値）を最新キャプチャ内で探し、一致度％と見つかった場所をmodal画像に重ねて出す（青＝ヒット・赤＝ミス、実行時と同一条件）。手持ちのスクショで試すときは画像ファイルを選んで`[この画像で試す]`。保存物は変わらない。
```

- [ ] **Step 6: 全体gateを順に走らせる**

Run: `uv run --frozen ruff check SerialController tools tests`
Expected: PASS。

Run: `uv run --frozen ruff format --check SerialController tools tests`
Expected: PASS（落ちたら本planのファイルに限定して直す）。

Run: `uv run --frozen mypy SerialController tools tests`
Expected: PASS。

Run: `uv run --frozen python tools/check_core.py`
Expected: PASS。

Run: `uv run --frozen python tools/check_user_api.py`
Expected: PASS（件数のみ微増）。

Run: `uv run --frozen pytest tests -q`
Expected: PASS（151＋新規12＝約163 passed。node無しではbrowser系がskip）。

- [ ] **Step 7: 手動確認を行う（ユーザ環境、カメラ隣接）**

1. modalを開き、候補を選んで`[照合テスト]`→一致度と重ね描きが出る。
2. しきい値を上げ下げしてヒット/ミスが切り替わる。
3. 画像ファイルを選び`[この画像で試す]`→同上で試せる。
4. `[取り直す]`で手動指定が消える。
5. 旧フロー（切出し保存・選択中反映・保存・開く・削除）が不変。
