# Blockly照合UX改善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 照合シミュレーションを自動再照合・常設結果・modal内完結にし、しきい値決めを回せるようにする。

**Architecture:** 照合条件は実行時と同一のまま。件数とcrop限定をサービスへ足し、UIは自動実行スケジューラ（デバウンス＋陳腐応答破棄）＋常設結果欄＋modal内テンプレ選択に組み替える。`[照合テスト]`ボタンは撤去する。

**Tech Stack:** Python 3.12 / OpenCV（cv2）・numpy・loguru / http.server（既存 `_Handler`）/ pytest・ruff・mypy・bounds・userapi

**Spec:** `docs/superpowers/specs/2026-09-10-blockly-match-sim-design.md`（本roundの追記済み）

## Global Constraints

- Python >= 3.12（`X | None`・builtin generics可）。
- `SerialController/` 起点の絶対import、相対import禁止。
- コメント・利用者文面は日本語。4スペース。CDN禁止。
- 資源パスは `WindowUtils.APP_DIR` 起点。`services/` で `os.chdir` しない。
- サーバスレッドからtkinterを触らない（`print` はLogPane queue経由のため可）。
- `core/` 一式・`Commands.*` 公開面・既存エンドポイントの形・`settings*.ini` は不変。ただし `/match` の要求に `crop?`・応答に `count` を足す（追加のみ。既存キーは不変）。
- Gate: `ruff check`＋`ruff format --check`＋`mypy`＋`bounds`＋`userapi`＋`pytest` が全緑。
- stepsにcommitを含めない（repo rule。reviewはworking-tree diffで行う）。

---

## File Structure

- Modify: `SerialController/services/blockly_match.py` — `count_hits`＋`parse_crop`＋`MatchResult.count`＋`match_template(..., crop=None)`。GUI非依存。
- Modify: `SerialController/ui/blockly_editor.py` — `/match` の `crop` 受付＋`count` 返却の追加のみ。
- Modify: `SerialController/assets/blockly/editor.html` — 自動実行・常設結果・modal内選択・グレー切替・寸法表示・ROI限定・ボタン撤去。切出し・ブロック反映の配線には触らない。
- Modify: `docs/BLOCKLY_EDITOR.md` — 照合段落の書き換え。
- Modify: `tests/test_blockly_match.py` — 件数・cropの追加。
- Modify: `tests/test_blockly_editor.py` — `/match` のcrop・count追加分。
- Modify: `tests/test_blockly_browser.py` — 静的markup検査の更新＋自動実行probe追加分。

---

### Task 1: 件数とcrop限定（サービス）

**Files:**
- Modify: `SerialController/services/blockly_match.py`
- Test: `tests/test_blockly_match.py`（5件追加）

**Interfaces:**
- Consumes: `core.CommandVision`（変更なし。件数則の出典 `findAllTemplates` のみ参照）
- Produces: `count_hits(res, threshold, tw, th, max_count=20) -> int`、`parse_crop(value, width, height) -> list[int] | None`、`MatchResult.count: int`、`match_template(..., crop: object = None)`（`crop` は実画像画素 `[x1, y1, x2, y2]`、`rect` は全画面系に戻す）

- [ ] **Step 1: 失敗テストを書く**

`tests/test_blockly_match.py` へ追記する。同ファイルの `make_frame`（200x100・白矩形＋黒印あり）・`png_of`・`make_app_with_template` を使う。

```python
def test_count_single_on_hit(tmp_path: Path) -> None:
    frame = make_frame()
    app = make_app_with_template(tmp_path, frame)
    res = blockly_match.match_template(app, png_of(frame), "pack/part.png", 0.7)
    assert res.status == "ok"
    assert res.count == 1


def test_count_two_on_twin_rects(tmp_path: Path) -> None:
    import cv2
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
```

- [ ] **Step 2: テストを走らせ、失敗を確認する**

Run: `uv run --frozen pytest tests/test_blockly_match.py -q`
Expected: FAIL（`count_hits`・`parse_crop`・`count`・`crop` 引数が無いため。`test_crop_excluding_template_fails` はcrop無視で通る可能性があるが、他が落ちる）。

- [ ] **Step 3: サービスを拡張する**

`MatchResult` へ1行足す。

```python
@dataclass
class MatchResult:
    """照合の結果。statusは ok / failed。"""

    status: str
    message: str
    score: float = 0.0
    matched: bool = False
    count: int = 0
    rect: dict[str, int] = field(default_factory=dict)
```

`parse_threshold` の直後へ2関数を足す。

```python
def parse_crop(value: object, width: int, height: int) -> list[int] | None:
    """切出し範囲（実画像画素 `[x1, y1, x2, y2]`）を検査する。Noneは全体。おかしければ ValueError。"""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("範囲は [x1, y1, x2, y2] で送ってください")
    try:
        x1, y1, x2, y2 = (int(v) for v in value)
    except (TypeError, ValueError):
        raise ValueError("範囲は [x1, y1, x2, y2] の整数で送ってください") from None
    if x1 < 0 or y1 < 0 or x2 > width or y2 > height or x1 >= x2 or y1 >= y2:
        raise ValueError(f"範囲が画像({width}x{height})の外を指しています")
    return [x1, y1, x2, y2]


def count_hits(
    res: np.ndarray, threshold: float, tw: int, th: int, max_count: int = 20
) -> int:
    """`findAllTemplates` と同一の重複除去で件数を数える（中心座標は返さない）。"""
    ys, xs = np.where(res >= threshold)
    if len(xs) == 0:
        return 0
    order = np.argsort(res[ys, xs])[::-1]
    near_x, near_y = max(1, tw // 2), max(1, th // 2)
    found: list[tuple[int, int]] = []
    for i in order:
        x, y = int(xs[i]), int(ys[i])
        if any(abs(x - px) < near_x and abs(y - py) < near_y for px, py in found):
            continue
        found.append((x, y))
        if len(found) >= max_count:
            break
    return len(found)
```

`match_template` の引数へ `crop: object = None` を足し、デコード直後・テンプレ読込の前へ切出しを足す。`rect` に `dx, dy` を足し、`message` に件数を足す。

```python
def match_template(
    app_dir: str | Path,
    frame_png: bytes,
    template: str,
    threshold: object,
    use_gray: bool = True,
    crop: object = None,
) -> MatchResult:
    """1枚の画像内でテンプレの最大一致度を求める。検証に落ちたらfailedで返す。"""
```

```python
    try:
        src_img = decode_frame_png(frame_png)
    except ValueError as e:
        return MatchResult(status="failed", message=f"照合できません: {e}")
    height0, width0 = int(src_img.shape[0]), int(src_img.shape[1])
    try:
        crop_list = parse_crop(crop, width0, height0)
    except ValueError as e:
        return MatchResult(status="failed", message=f"照合できません: {e}")
    if crop_list is not None:
        x1, y1, x2, y2 = crop_list
        src_img = src_img[y1:y2, x1:x2]
        dx, dy = x1, y1
    else:
        dx, dy = 0, 0
```

```python
    matched = score >= limit
    count = count_hits(res, limit, width, height)
    tail = "ヒット" if matched else "ミス"
    logger.info(
        f"テンプレ照合: {name} 一致度={score:.3f} しきい値={limit} → {tail} {count}件"
    )
    return MatchResult(
        status="ok",
        message=f"一致度 {score * 100:.1f}%（しきい値 {limit * 100:.1f}%）→ {tail} {count}件",
        score=score,
        matched=matched,
        count=count,
        rect=rect,
    )
```

`rect` の組み立ては `{"x": int(max_loc[0]) + dx, "y": int(max_loc[1]) + dy, ...}` に変える（既存の3行のx/yへ `+ dx` / `+ dy` を足すだけ）。message文面の変更は既存テストに影響しない（既存assertはstatus・score・rect・拒否文言のみ）。

- [ ] **Step 4: テストを走らせ、通ることを確認する**

Run: `uv run --frozen pytest tests/test_blockly_match.py -q`
Expected: PASS（12 passed＝既存7＋新規5）。

- [ ] **Step 5: Task 1の所定gateを走らせる**

Run: `uv run --frozen pytest tests/test_blockly_match.py tests/test_blockly_templates.py -q`
Expected: PASS。

Run: `uv run --frozen ruff check SerialController/services/blockly_match.py tests/test_blockly_match.py`
Expected: PASS。

Run: `uv run --frozen ruff format --check SerialController/services/blockly_match.py tests/test_blockly_match.py`
Expected: PASS。落ちたら本Taskのファイルに限定して `ruff format` で直す。

Run: `uv run --frozen mypy SerialController/services/blockly_match.py`
Expected: PASS。

### Task 2: UI配線（`/match` のcrop・count）

**Files:**
- Modify: `SerialController/ui/blockly_editor.py`（`/match` 分岐内の2か所）
- Test: `tests/test_blockly_editor.py`（3件追加）

**Interfaces:**
- Consumes: Task 1の `match_template(..., crop=None)`・`MatchResult.count`
- Produces: `/match` 応答に `count` 追加（既存キー不変）、要求に `crop?` 追加

- [ ] **Step 1: 失敗テストを書く**

```python
def test_match_crop_hit(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", lambda: frame)
    data = post_json(
        server,
        "/match",
        {
            "template": "pack/part.png",
            "threshold": 0.7,
            "source": "frame",
            "crop": [40, 10, 180, 90],
        },
    )
    assert data["ok"] is True
    assert data["rect"] == {"x": 60, "y": 30, "width": 80, "height": 40}
    assert data["count"] == 1


def test_match_bad_crop_fails(server: str) -> None:
    data = post_json(
        server,
        "/match",
        {
            "template": "pack/part.png",
            "threshold": 0.7,
            "source": "frame",
            "crop": [180, 10, 40, 90],
        },
    )
    assert data["ok"] is False


def test_match_response_has_count(server: str) -> None:
    data = post_json(
        server,
        "/match",
        {"template": "pack/part.png", "threshold": 0.7, "source": "frame"},
    )
    assert data["ok"] is True
    assert data["count"] >= 1
```

`test_match_response_has_count` は `make_app` の素名テンプレに依存しない。fixtureの `Template/my-pack/a.png`（ダミーバイト）ではデコードに失敗するため、直前2テストと同様に `make_match_frame` から切り出して `Template/pack/part.png` を作ってから叩く。以下の完成形で書く（`test_match_crop_hit` の先頭9行と同一の準備部を持つ）。

```python
def test_match_response_has_count(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", lambda: frame)
    data = post_json(
        server,
        "/match",
        {"template": "pack/part.png", "threshold": 0.7, "source": "frame"},
    )
    assert data["ok"] is True
    assert data["count"] >= 1
```

- [ ] **Step 2: テストを走らせ、失敗を確認する**

Run: `uv run --frozen pytest tests/test_blockly_editor.py -q -k "crop or count"`
Expected: FAIL（`crop` が無視され `count` キーが無いため。`test_match_bad_crop_fails` は逆転cropが無視されて通ってしまう点に注意＝これも失敗の一種。3件とも赤になる）。

- [ ] **Step 3: `/match` 分岐の2か所を直す**

`match_template(...)` 呼び出しへ `crop` を足す。

```python
            res = blockly_match.match_template(
                WindowUtils.APP_DIR,
                png,
                str(payload.get("template", "")),
                payload.get("threshold", 0.7),
                bool(payload.get("use_gray", True)),
                payload.get("crop", None),
            )
```

`extra` へ `count` を足す。

```python
            extra = (
                {
                    "score": res.score,
                    "matched": res.matched,
                    "count": res.count,
                    "rect": res.rect,
                }
                if res.status == "ok"
                else {}
            )
```

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

### Task 3: modal自動化＋常設結果（editor.html）

**Files:**
- Modify: `SerialController/assets/blockly/editor.html`（照合まわりのみ。切出し・ブロック反映の配線には触らない）
- Test: `tests/test_blockly_browser.py`（静的markup検査の更新＋自動実行probe追加分）

**Interfaces:**
- Consumes: Task 2の `/match`（`crop?`・`count` 付き）
- Produces: なし（末端）

- [ ] **Step 1: 自動実行probeの失敗テストを書く**

`MODAL_PROBE_JS` の末尾（`MODAL-PROBE-OK` の直前）へ足す。スタブの `fetch` は `/match` へのPOSTにも `{stems: []}` を返すため、`j.ok` は偽になり失敗系の表示が出る。それを利用し、配線（発火→描画指示→文言設定）だけを検証する。

```js
els.capmatchtpl.value = 'pack/part.png';
els.capth.value = '0.7';
els.capth.handlers.input();
setTimeout(function () {
  try {
    if (els.capmatchstatus.textContent !== '照合できません') { fail('自動実行されない: ' + els.capmatchstatus.textContent); }
    if (els.capmatchrect.style.display !== 'none') { fail('失敗時に重ね描きが残る'); }
    console.log('MODAL-PROBE-OK');
  } catch (e) { fail('例外: ' + (e && e.message)); }
}, 600);
```

既存末尾の `console.log('MODAL-PROBE-OK');` はこの非同期版に置き換える（削除して上記にする）。`setTimeout` はスタブのsandboxに既にある。`capmatchtpl`・`capgray`・`caproionly`・`capmodelabel`・`capmatchcrop` はスタブのid一覧に足す（テストのみの変更。足さないとinline script読込で落ちる）。

- [ ] **Step 2: テストを走らせ、失敗を確認する**

Run: `uv run --frozen pytest tests/test_blockly_browser.py::test_modal_drag_routing -q`
Expected: FAIL（`capmatchtpl` がスタブに無い段階では `handlers` 参照で落ちる。idを足した後は自動実行が無いため文言が出ず落ちる）。

- [ ] **Step 3: modalの照合区画を組み替える**

まず行（79〜83行目付近）を置き換える。

```html
<div style="display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap;">
<span style="display: inline-flex; gap: 4px; align-items: center; white-space: nowrap;">テンプレ <select id="capmatchtpl"></select><span id="capmatchsize" style="color: #666;"></span></span>
<span style="display: inline-flex; gap: 4px; align-items: center; white-space: nowrap;">しきい値 <input id="capth" type="number" step="0.01" min="0" max="1" value="0.7" style="width: 5em;"><input id="caprth" type="range" min="0" max="1" step="0.01" value="0.7" style="width: 120px;"></span>
<label style="white-space: nowrap;"><input type="checkbox" id="capgray" checked> グレー</label>
<label style="white-space: nowrap;"><input type="checkbox" id="caproionly"> 赤枠内のみ</label>
<span id="capmatchstatus"></span>
<span id="capmatchcrop" style="color: #666;"></span>
</div>
```

`[照合テスト]`ボタン（`capmatch`）と`[この画像で試す]`ボタン（`capuseupload`）の2行を消す。upload行は入力と名前表示だけ残す。

```html
<div style="display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap;">
<label>画像ファイル <input id="capupload" type="file" accept="image/*"></label>
<span id="capuploadname" style="color: #666;"></span>
</div>
```

ヘッダ行（`capmodalhint` の行）へモード表示を足す。

```html
<span id="capmodalhint">範囲を選択してください（枠内ドラッグで移動・赤い点で大きさ変更・数値でも指定可）</span>
<span id="capmodelabel" style="color: #666;"></span>
<button id="capmodalclose">× 閉じる</button>
```

- [ ] **Step 4: 自動実行スケジューラと配線を書く**

`capMatch` の定義直前へ足す（最終形）。

```js
  var capMatchSeq = 0;
  var capMatchTimer = null;
  function capMatchSoon(immediate) {
    if (capMatchTimer) { clearTimeout(capMatchTimer); capMatchTimer = null; }
    if (immediate) { capMatch(); return; }
    capMatchTimer = setTimeout(function () { capMatchTimer = null; capMatch(); }, 250);
  }
  function capSetModelabel(s) {
    document.getElementById('capmodelabel').textContent = s ? '［' + s + '］' : '';
  }
  function capMatchTplFill(preselect) {
    fetch('./templates').then(function (r) { return r.json(); }).then(function (j) {
      var sel = document.getElementById('capmatchtpl');
      var keep = preselect || sel.value;
      sel.textContent = '';
      (j.templates || []).forEach(function (name) {
        var opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        sel.appendChild(opt);
      });
      if (keep) { sel.value = keep; }
      capMatchSize();
    }).catch(function () { /* 候補なしでも枠だけは選べる */ });
  }
  function capMatchSize() {
    var sel = document.getElementById('capmatchtpl');
    var label = document.getElementById('capmatchsize');
    var v = sel.value;
    if (!v) { label.textContent = ''; return; }
    var im = new Image();
    im.onload = function () { label.textContent = im.naturalWidth + '×' + im.naturalHeight; };
    im.onerror = function () { label.textContent = ''; };
    im.src = './template_image?name=' + encodeURIComponent(v);
  }
```

`capMatch` を書き換える（要求組み立て＋連番ガード＋crop式表示）。

```js
  function capMatch() {
    if (document.getElementById('capmodal').style.display === 'none') { return; }
    var seq = ++capMatchSeq;
    var tpl = document.getElementById('capmatchtpl').value;
    if (!tpl) { capMatchHide(); capMatchSetStatus('画像を選んでください', true); return; }
    var th = parseFloat(document.getElementById('capth').value);
    if (isNaN(th) || th < 0 || th > 1) {
      capMatchHide(); capMatchSetStatus('しきい値は0〜1で書いてください', true); return;
    }
    var body = { template: tpl, threshold: th,
      use_gray: document.getElementById('capgray').checked,
      source: capUploadDataUrl ? 'upload' : 'frame' };
    if (capUploadDataUrl) { body.image = capUploadDataUrl.split(',', 2)[1]; }
    if (document.getElementById('caproionly').checked) {
      var img = document.getElementById('capbigimg');
      if (!capModalRect || !img.naturalWidth) {
        capMatchHide(); capMatchSetStatus('赤枠を選んでください', true); return;
      }
      var px = PokeconCapture.rect01ToPx(capModalRect, img.naturalWidth, img.naturalHeight);
      body.crop = [px.x, px.y, px.x + px.width, px.y + px.height];
    }
    capMatchSetStatus('照合しています…', false);
    capSetModelabel('照合中');
    fetch('./match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (seq !== capMatchSeq) { return; }
      if (!j.ok) {
        capMatchHide(); capMatchSetStatus(j.message || '照合できません', true);
        capSetModelabel('通常'); return;
      }
      capMatchDraw(j.rect, j.matched);
      capMatchSetStatus(j.message || ('一致度 ' + (j.score * 100).toFixed(1) + '%'), !j.matched);
      var rc = j.rect || {};
      document.getElementById('capmatchcrop').textContent =
        'crop = img[' + rc.y + ':' + (rc.y + rc.height) + ', ' + rc.x + ':' + (rc.x + rc.width) + ']';
      capSetModelabel('通常');
    }).catch(function (e) {
      if (seq !== capMatchSeq) { return; }
      capMatchHide(); capMatchSetStatus('照合できません: ' + e, true);
      capSetModelabel('通常');
    });
  }
```

`document.getElementById('capmatch').addEventListener('click', capMatch);` の1行を消す。`capuseupload` のハンドラ（3〜4行）を消す。thresholdの2つの `input` ハンドラの末尾へ `capMatchSoon(false);` を足す。

```js
  document.getElementById('capth').addEventListener('input', function () {
    document.getElementById('caprth').value = this.value;
    capMatchSoon(false);
  });
  document.getElementById('caprth').addEventListener('input', function () {
    document.getElementById('capth').value = this.value;
    capMatchSoon(false);
  });
```

新規の配線を `caprth` ハンドラの直後へ足す。

```js
  document.getElementById('capmatchtpl').addEventListener('change', function () {
    capMatchSize();
    capMatchSoon(true);
  });
  document.getElementById('capgray').addEventListener('change', function () {
    capMatchSoon(true);
  });
  document.getElementById('caproionly').addEventListener('change', function () {
    capMatchSoon(true);
  });
```

`capModalOpen` の末尾（`capMatchSetStatus('', false);` の行）へ2行足す。

```js
    capMatchTplFill(capBlockMode ? null : document.getElementById('tpllist').value);
    capMatchSoon(true);
```

ブロック用モードのときは preselect を null にする（`capblockopen` ハンドラ側で `capmatchtpl` へブロック値を入れる処理を別途足すのではなく、ここでは開いた直後の block 値を読む。`capModalOpen` は切出し用経路のため block 値は無い。ブロック用経路（`capblockopen` ハンドラ）の末尾へ以下を足す）。

```js
    capMatchTplFill(
      (function () {
        var b = capBlockMode ? ws.getBlockById(capBlockMode) : null;
        var t = b && typeof b.getField === 'function' ? b.getField('TEMPLATE') : null;
        return t ? t.getValue() : null;
      })());
    capMatchSoon(true);
```

`capblockopen` ハンドラの末尾（`capMatchSetStatus('', false);` で終わる箇所。`capModalOpen()` を呼ばず自前で開く経路のためここに足す。`capModalOpen` 側には足さない。`capFetch` 成功時は `capModalOpen()` を呼ぶため自動実行される）。

`capModalClose` へ無効化の2行を足す。

```js
  function capModalClose() {
    capMatchSeq++;
    if (capMatchTimer) { clearTimeout(capMatchTimer); capMatchTimer = null; }
    document.getElementById('capmodal').style.display = 'none';
    capModalDrag = null;
```

mouseupのドラッグ確定（`capModalDrag = null;` の modal 用ハンドラ）へ即時実行を足す。

```js
  window.addEventListener('mouseup', function () {
    var wasDrag = !!capModalDrag;
    capModalDrag = null;
    document.getElementById('capbigimg').style.cursor = 'crosshair';
    if (wasDrag) { capMatchSoon(true); }
  });
```

`capApplyPx` の末尾（`capModalDraw(); capModalSyncInputs();` の直後、`isValidPixels` の分岐の前）へ1行足す。

```js
    capModalDraw(); capModalSyncInputs();
    capMatchSoon(false);
```

uploadの `reader.onload` の末尾（`capModalSetStatus('手動画像を表示しました', false);` の直後）へ1行足す。

```js
      capModalSetStatus('手動画像を表示しました', false);
      capMatchSoon(true);
```

`capFetch` の成功分岐は `capModalOpen()` を呼ぶため追加不要。`capretake` は `capFetch` のため追加不要。

probeスタブ（`PROBE_TPLAPPLY_JS`・`MODAL_PROBE_JS` のid一覧）へ `capmatchtpl`・`capmatchsize`・`capgray`・`caproionly`・`capmatchcrop`・`capmodelabel` を足し、`capmatch`・`capuseupload` は残す（消した要素のidが一覧に残っても無害。削除漏れの方が読込落ちの原因になるため残す）。

静的markup検査（`test_match_section_markup_exists`）を書き換える。`id="capmatch"` のtokenを消し、新idを足す。

```python
def test_match_section_markup_exists() -> None:
    """照合区画の要素がeditor.htmlにあること。"""
    html = (BLOCKLY / "editor.html").read_text(encoding="utf-8")
    for token in [
        'id="capmatchtpl"',
        'id="capmatchsize"',
        'id="capgray"',
        'id="caproionly"',
        'id="capmatchrect"',
        'id="capmatchstatus"',
        'id="capmatchcrop"',
        'id="capmodelabel"',
        'id="capupload"',
        'id="capuploadname"',
    ]:
        assert token in html
    assert 'id="capmatch"' not in html
    assert 'id="capuseupload"' not in html
```

- [ ] **Step 5: テストを走らせ、通ることを確認する**

Run: `uv run --frozen pytest tests/test_blockly_browser.py tests/test_blockly_editor.py -q`
Expected: PASS。

- [ ] **Step 6: Task 3の所定gateを走らせる**

Run: `uv run --frozen ruff check SerialController tools tests`
Expected: PASS（`MyBlock.py` のF401は利用者の手元物。触らない。落ちたら本plan外と記録して進む）。

Run: `uv run --frozen ruff format --check SerialController tools tests`
Expected: PASS。落ちたら本planのファイルに限定して直す。

### Task 4: 文書＋全体gate＋手動確認

**Files:**
- Modify: `docs/BLOCKLY_EDITOR.md`（照合段落の書き換え）

- [ ] **Step 1: 照合段落を書き換える**

```markdown
照合テスト： modalを開くと自動で照合が走り、候補画像を最新キャプチャ内で探して一致度％・件数・見つかった場所を出す（青＝ヒット・赤＝ミス、実行時と同一条件）。しきい値・テンプレ・グレーを変える・枠を動かすたびに追従する（連続操作は少し待ってから1回）。テンプレの寸法も出る。手持ちのスクショは画像ファイルを選ぶと自動で試す。赤枠内だけ試すときはチェックを入れる。保存物は変わらない。
```

- [ ] **Step 2: 全体gateを順に走らせる**

Run: `uv run --frozen ruff check SerialController tools tests`
Expected: PASS（`MyBlock.py` 除外の扱いはTask 3 Step 6と同一）。

Run: `uv run --frozen ruff format --check SerialController tools tests`
Expected: PASS。

Run: `uv run --frozen mypy SerialController tools tests`
Expected: PASS。

Run: `uv run --frozen python tools/check_core.py`
Expected: PASS。

Run: `uv run --frozen python tools/check_user_api.py`
Expected: PASS。

Run: `uv run --frozen pytest tests -q`
Expected: PASS（168＋新規9＝約177 passed。node無しではbrowser系がskip）。

- [ ] **Step 3: 手動確認を行う（ユーザ環境、カメラ隣接）**

1. modalを開くと自動で一致度・件数・重ね描きが出る。
2. しきい値スライダを振ると追従し、件数が変わる。
3. テンプレを切り替えると寸法と結果が変わる。グレー切替でも変わる。
4. 赤枠を描いてチェックを入れると枠内限定になり、crop式が出る。
5. 画像ファイルを選ぶと自動で試せる。取り直すと戻る。
6. 旧フロー（切出し保存・ブロック反映・保存・開く・削除）が不変。
