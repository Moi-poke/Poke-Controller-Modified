# Blocklyブロック内プレビュー Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** TEMPLATE欄を持つvision系4ブロックに指定中画像の縮小表示を付け、名前の打ち間違いに気づけるようにする。

**Architecture:** 画像配信の受け口（`GET /template_image`）を足し、ブロック側はダミー`FieldImage`＋TEMPLATE欄validatorで更新する。表示のみで保存物・生成コードは不変。

**Tech Stack:** Python 3.12 / 標準ライブラリ（http.server・pathlib）/ Blockly 13.2.1 vendored / pytest・ruff・mypy・bounds・userapi

**Spec:** `docs/superpowers/specs/2026-09-10-blockly-preview-design.md`

## Global Constraints

- Python >= 3.12（`X | None`・builtin generics可）。
- `SerialController/` 起点の絶対import、相対import禁止。
- コメント・利用者文面は日本語。4スペース。CDN禁止。
- 資源パスは `WindowUtils.APP_DIR` 起点。`services/` で `os.chdir` しない。
- サーバスレッドからtkinterを触らない（`print` はLogPane queue経由のため可）。
- `core/` 一式・`Commands.*` 公開面・既存ブロックの生成内容・既存エンドポイントの形・`settings*.ini` は不変。
- Gate: `ruff check`＋`ruff format --check`＋`mypy`＋`bounds`＋`userapi`＋`pytest` が全緑。
- stepsにcommitを含めない（repo rule。reviewはworking-tree diffで行う）。
- 本planと `2026-09-10-blockly-match-sim.md` は `editor.html` と `tests/test_blockly_editor.py` を共有するため逐次実行する（match-simが先）。

---

## File Structure

- Modify: `SerialController/ui/blockly_editor.py` — `do_GET /template_image` 分岐の追加のみ。
- Modify: `SerialController/assets/blockly/pokecon_blocks.js` — 4ブロックへダミー`PREVIEW`欄＋`pokecon_template_preview`拡張の追加のみ。
- Modify: `SerialController/assets/blockly/editor.html` — 開く直後の張り直しの追加のみ。
- Modify: `docs/BLOCKLY_EDITOR.md` — プレビューの追記。
- Modify: `tests/test_blockly_editor.py` — `/template_image` の実HTTP追加分。
- Modify: `tests/test_blockly_browser.py` — validator・生成コード不変のnode probe追加分。

---

### Task 1: 画像配信（`GET /template_image`）

**Files:**
- Modify: `SerialController/ui/blockly_editor.py`（`do_GET` へ1分岐）
- Test: `tests/test_blockly_editor.py`（3件追加）

**Interfaces:**
- Consumes: `services.blockly_capture.validate_template_name`（拒否規則）
- Produces: `GET /template_image?name=...`（成功: 画像バイト＋拡張子別のContent-Type／失敗: `{"ok": false, "message"}`）

- [ ] **Step 1: 失敗テストを書く**

`tests/test_blockly_editor.py` へ追記する。`server` fixtureの `make_app` は `Template/my-pack/a.png`（中身はPNG署名＋αのダミーバイト）を既に作るため、そのまま往復に使う。バイト取得の素振りは同ファイルの `get_bytes(host, path) -> tuple[int, str, bytes]` を使う（Task 2で追加済み）。

```python
def test_template_image_returns_bytes(server: str) -> None:
    status, ctype, body = get_bytes(server, "/template_image?name=my-pack/a.png")
    assert status == 200
    assert "image/png" in ctype
    assert body[:8] == b"\x89PNG\r\n\x1a\n"


def test_template_image_missing_fails(server: str) -> None:
    data = get_json(server, "/template_image?name=pack/none.png")
    assert data["ok"] is False


def test_template_image_traversal_fails(server: str) -> None:
    data = get_json(server, "/template_image?name=../evil.png")
    assert data["ok"] is False
```

- [ ] **Step 2: テストを走らせ、失敗を確認する**

Run: `uv run --frozen pytest tests/test_blockly_editor.py -q -k template_image`
Expected: FAIL（`/template_image` が静的配信に落ち、ダミーバイトでも404になるか、JSONでなく落ちる）。

- [ ] **Step 3: `/template_image` 分岐を書く**

`do_GET` の `/frame` 分岐の直後へ足す。拡張子→Content-Type対応表は同所に置く（`blockly_templates` の拡張子集合との micro-dup。意図的で4行のため共通化しない）。

```python
        if path == "/template_image":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            name = query.get("name", [""])[0]
            reason = blockly_capture.validate_template_name(name.strip())
            if reason is not None:
                self._reply(False, f"画像を出せません: {reason}")
                return
            base = Path(WindowUtils.APP_DIR) / "Template"
            target = (base / Path(str(name).replace("\\", "/"))).resolve()
            try:
                target.relative_to(base.resolve())
            except ValueError:
                self._reply(False, "画像を出せません: `..` は使えません")
                return
            content_types = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".bmp": "image/bmp",
            }
            ctype = content_types.get(target.suffix.lower())
            if ctype is None or not target.is_file():
                self._reply(False, f"画像を出せません: Template/{name} がありません")
                return
            try:
                body = target.read_bytes()
            except OSError as e:
                self._reply(False, f"画像を出せません: {e}")
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
```

`name` の空文字は `validate_template_name` が弾く。`target.is_file()` が偽のときの文面は存在・形式の両方を「ありません」にまとめる（探り防止のため詳細を出さない）。

- [ ] **Step 4: テストを走らせ、通ることを確認する**

Run: `uv run --frozen pytest tests/test_blockly_editor.py -q`
Expected: PASS。

- [ ] **Step 5: Task 1の所定gateを走らせる**

Run: `uv run --frozen ruff check SerialController/ui/blockly_editor.py tests/test_blockly_editor.py`
Expected: PASS。

Run: `uv run --frozen ruff format --check SerialController/ui/blockly_editor.py tests/test_blockly_editor.py`
Expected: PASS。落ちたら本Taskのファイルに限定して直す。

Run: `uv run --frozen mypy SerialController/ui/blockly_editor.py`
Expected: PASS。

Run: `uv run --frozen python tools/check_core.py`
Expected: PASS。

### Task 2: ブロック表示＋張り直し＋文書＋全体gate

**Files:**
- Modify: `SerialController/assets/blockly/pokecon_blocks.js`（4ブロックのJSON＋拡張登録）
- Modify: `SerialController/assets/blockly/editor.html`（開く直後の張り直し）
- Modify: `docs/BLOCKLY_EDITOR.md`（1段落追記）
- Test: `tests/test_blockly_browser.py`（probe追加）

**Interfaces:**
- Consumes: Task 1の `GET /template_image?name=...`
- Produces: なし（末端）

- [ ] **Step 1: probeの失敗テストを書く**

`tests/test_blockly_browser.py` の末尾へ足す。既存の `PROBE_VISION_JS` とは別建てのprobeにする（visionの生成検証と混ぜない）。実Blocklyを読み、ブロックを作ってTEMPLATE欄を書き換え、PREVIEW欄を見る。

```python
PREVIEW_PROBE_JS = """\
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const root = process.argv[1];
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const sandbox = { console, setTimeout, clearTimeout };
vm.createContext(sandbox);
for (const f of [
  'blockly_compressed.js',
  'blocks_compressed.js',
  'python_compressed.js',
  'msg/ja.js',
]) {
  vm.runInContext(read(f), sandbox, { filename: f });
}
vm.runInContext(read('pokecon_blocks.js'), sandbox, { filename: 'pokecon_blocks.js' });
const fail = (msg) => { console.error('PREVIEW-FAIL: ' + msg); process.exit(1); };
const B = sandbox.Blockly;
if (!B || typeof B.Python !== 'object') { fail('Blockly.Python が無い'); }
const ws = new B.Workspace();
const state = { blocks: { languageVersion: 0, blocks: [
  { type: 'pokecon_vision_contains',
    fields: { TEMPLATE: 'my-pack/a.png', THRESHOLD: 0.7, CROP: '' } },
] } };
B.serialization.workspaces.load(state, ws);
const blk = ws.getAllBlocks(false)[0];
if (typeof blk.getField !== 'function' || !blk.getField('PREVIEW')) {
  fail('PREVIEW欄が無い');
}
blk.setFieldValue('blockly/mark.png', 'TEMPLATE');
const shown = blk.getFieldValue('PREVIEW');
if (shown !== './template_image?name=' + encodeURIComponent('blockly/mark.png')) {
  fail('TEMPLATE変更でpreviewが更新されない: ' + shown);
}
blk.setFieldValue('', 'TEMPLATE');
if (blk.getFieldValue('PREVIEW').indexOf('data:image/png;base64,') !== 0) {
  fail('空欄でplaceholderに戻らない: ' + blk.getFieldValue('PREVIEW'));
}
const code = B.Python.workspaceToCode(ws);
ws.dispose();
if (code.indexOf('template_image') !== -1 || code.indexOf('PREVIEW') !== -1) {
  fail('生成コードにpreviewが混入した');
}
if (code.indexOf('self.isContainTemplate("blockly/mark.png"') === -1) {
  fail('生成コードのTEMPLATEが変わった');
}
console.log('PREVIEW-PROBE-OK');
"""


@NEEDS_NODE
def test_preview_follows_template_field() -> None:
    proc = subprocess.run(
        ["node", "-e", PREVIEW_PROBE_JS, str(BLOCKLY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"probe失敗:\n{proc.stderr}\n{proc.stdout}"
    assert "PREVIEW-PROBE-OK" in proc.stdout
```

`encodeURIComponent('blockly/mark.png')` は `'blockly%2Fmark.png'` になるため、等価比較ではなく `'./template_image?name=' + encodeURIComponent(...)` の厳密一致で書いている（両辺とも同じ式のため2進exact問題なし）。

- [ ] **Step 2: テストを走らせ、失敗を確認する**

Run: `uv run --frozen pytest tests/test_blockly_browser.py::test_preview_follows_template_field -q`
Expected: FAIL（`PREVIEW欄が無い` で落ちる）。

- [ ] **Step 3: ブロック定義と拡張を書く**

`pokecon_blocks.js` のvision定義4種（`pokecon_vision_contains`・`pokecon_vision_wait_appear`・`pokecon_vision_wait_gone`・`pokecon_vision_position`）へ、各 `message0` の末尾に `" %N"`（Nは既存の次番号）を足し、`args0` の末尾へ下記を足し、`"extensions": ["pokecon_template_preview"]` を足す。例（containsの場合。残り3種も `%4`→`%5` の番号だけ変えて同形）。

```js
    {
      type: "pokecon_vision_contains",
      message0: "画像 %1 がある 閾値 %2 範囲 %3 %4",
      args0: [
        { type: "field_input", name: "TEMPLATE", text: "my-pack/a.png" },
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_input", name: "CROP", text: "" },
        {
          type: "field_image",
          name: "PREVIEW",
          src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
          width: 120,
          height: 90,
          alt: "*",
        },
      ],
      extensions: ["pokecon_template_preview"],
      output: "Boolean",
      colour: 210,
    },
```

`wait_appear`・`wait_gone` は `"画像 %1 が出るまで待つ 上限 %2 閾値 %3 範囲 %4 %5"`（`%5` がPREVIEW）、`position` は `"画像 %1 の位置 閾値 %2 範囲 %3 %4"`。`wait_stable`・`color` には触らない。

`defineBlocksWithJsonArray` のvision配列の直後（生成器登録の前）へ拡張を足す。

```js
  // TEMPLATE欄の変更をダミーPREVIEW欄へ反映する。生成コードには触らない。
  // 欠損時は透明placeholderのままにする（保存は塞がない）。
  var PREVIEW_EMPTY =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
  function previewUrl(v) {
    return "./template_image?name=" + encodeURIComponent(v);
  }
  Blockly.Extensions.register("pokecon_template_preview", function () {
    var tpl = this.getField("TEMPLATE");
    var prev = this.getField("PREVIEW");
    if (!tpl || !prev) {
      return;
    }
    tpl.setValidator(function (v) {
      var b = this.getSourceBlock();
      if (b) {
        var p = b.getField("PREVIEW");
        if (p) {
          p.setValue(v ? previewUrl(v) : PREVIEW_EMPTY);
        }
      }
      return v;
    });
  });
```

`field_image` の `width: 120, height: 90` は固定枠（縦横比が崩れる画像もあるが preview のため許容）。validator内で別欄を `setValue` してもPREVIEW側にvalidatorが無いため循環しない。生成器は既知欄だけ読むため混入しない（probeが厳密比較で担保）。

- [ ] **Step 4: テストを走らせ、通ることを確認する**

Run: `uv run --frozen pytest tests/test_blockly_browser.py -q`
Expected: PASS（node有りで既存＋新規1、node無しでskip）。

- [ ] **Step 5: 開く直後の張り直しを書く**

`editor.html` の開くハンドラ内、`Blockly.serialization.workspaces.load(JSON.parse(j.workspaceJson), ws);` の直後へ足す（保存データのURL陳腐化対策。欠損時はplaceholder）。

```js
      ws.getAllBlocks(false).forEach(function (b) {
        if (typeof b.getField !== 'function') { return; }
        var t = b.getField('TEMPLATE'), p = b.getField('PREVIEW');
        if (t && p) {
          var v = t.getValue();
          p.setValue(v
            ? './template_image?name=' + encodeURIComponent(v)
            : 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==');
        }
      });
```

placeholderのdataURL重複は意図的（`pokecon_blocks.js` と2か所。共通化のための新ファイルを作らない）。

probeスタブ（`PROBE_TPLAPPLY_JS`・`MODAL_PROBE_JS` のid一覧）には触らない（新規idを使わないため）。

- [ ] **Step 6: テストを走らせ、通ることを確認する**

Run: `uv run --frozen pytest tests/test_blockly_browser.py tests/test_blockly_editor.py -q`
Expected: PASS。

- [ ] **Step 7: `docs/BLOCKLY_EDITOR.md` に1段落追記する**

画像認識の段落の直後へ挿入する。

```markdown
ブロック内プレビュー： TEMPLATE欄のある画像認識ブロックには指定中画像の縮小表示（幅120pxまで）が出る。存在しない名では透明のまま。表示のみで保存物・生成コードは変わらない。
```

- [ ] **Step 8: 全体gateを順に走らせる**

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
Expected: PASS（match-sim完了後の約163＋新規4＝約167。node無しではbrowser系がskip）。

- [ ] **Step 9: 手動確認を行う（ユーザ環境、カメラ隣接）**

1. TEMPLATE欄に既存画像名→縮小表示が出る。
2. 存在しない名→透明のまま（保存は通る）。
3. 保存物の `.py` が従来と同一。
4. 旧フロー（生成・保存・開く・削除・選択中反映）が不変。
