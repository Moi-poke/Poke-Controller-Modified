# Blockly画像認識ブロック Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Blocklyに画像認識6ブロック・Template一覧・自動基底切替・zip配布対応を加え、保存→反映→実行の往復を保つ。

**Architecture:** 生成の真実はvendored JSが持ち、Python側は検証・保存・一覧だけを持つ（現行踏襲）。新設は`services/blockly_templates.py`（一覧＋参照検査）のみ。`.blockly.json`はmanifestに書かない派生成果物としてpackに同梱する。

**Tech Stack:** Python 3.12 / 標準ライブラリ（ast・http.server・pathlib）/ Blockly 13.2.1 vendored / pytest・ruff・mypy・bounds・userapi

**Spec:** `docs/superpowers/specs/2026-09-08-blockly-vision-design.md`

## Global Constraints

- `SerialController/`を`sys.path`前提の絶対import、相対import禁止。
- `core/`はtkinter・アプリ層 import禁止。新規`services/blockly_templates.py`は標準ライブラリのみ。
- `services/`はtkinter・画面部品 import禁止。`core.*`・`services.*`・loguruは可。
- `ui/`は`Window`本体 import禁止。`WindowUtils`は既存patternとして可。
- `Commands.*`の公開面を変えない。`settings*.ini`は触らない。
- Python floor 3.12。Ruff `I`＋`UP006/007/008/035`。コメントと利用者向け文面は日本語。4スペース字下げ。
- Blockly資産はCDN禁止。生成コードはダブルクォート＋4スペース＋末尾改行。
- 資源パスは`WindowUtils.APP_DIR`起点。`services/`で`os.chdir`しない。
- コミットはしない（ユーザーの明示指示があるときのみ。本planのstepにcommitを含めない）。
- Gate: `ruff check`＋`ruff format --check`＋`mypy`＋`bounds`＋`userapi`＋`pytest`が全緑。

## File Structure

- `SerialController/services/blockly_templates.py`（新規）— `list_image_templates`＋`validate_template_refs`＋`warn_template_refs`。FS走査はここだけ。tkinterなし。
- `SerialController/services/blockly_save.py`（追記）— `SaveResult.warnings`新設＋保存時の参照検査呼出し。既存の検証・原子保存は不変。
- `SerialController/core/pack_zip.py`（追記）— `create_pack`で同名`.blockly.json`を同梱。manifest検証は不変。
- `SerialController/services/script_pack.py`（追記）— `install_zip`の導入・記録に対象があれば同梱`.blockly.json`を含める。`uninstall`は記録駆動のため変更なし。
- `SerialController/assets/blockly/pokecon_blocks.js`（追記）— vision 6ブロック＋生成器＋program自動切替。既存3ブロックは不変。
- `SerialController/assets/blockly/editor.html`（追記）— vision分類＋テンプレ選択欄＋警告表示。既存の保存・開く・削除は不変。
- `SerialController/ui/blockly_editor.py`（追記）— `GET /templates`＋`/save`応答に`warnings`。既存経路は不変。
- `docs/BLOCKLY_EDITOR.md`（追記）— vision・テンプレ・配布の説明。
- Tests: `tests/test_blockly_templates.py`（新規）、`tests/test_pack_blockly.py`（新規）、`tests/test_blockly_editor.py`（新規）、`tests/test_blockly_browser.py`・`tests/test_blockly_save.py`（拡張）。
---

### Task 1: `services/blockly_templates.py`＋保存統合＋テスト

**Files:**
- Create: `SerialController/services/blockly_templates.py`
- Modify: `SerialController/services/blockly_save.py`（`SaveResult.warnings`＋検査呼出し）
- Test: `tests/test_blockly_templates.py`（新規）、`tests/test_blockly_save.py`（2件追加）

**Interfaces:**
- Consumes: `core/blockly_validate.py`の既存検証（Task 1は呼ばない。`save_blockly`が従来通り呼ぶ）。
- Produces: `list_image_templates(app_dir) -> list[str]`／`validate_template_refs(python_code) -> list[str]`／`warn_template_refs(python_code) -> list[str]`。Task 4の`/templates`が`list_image_templates`を使う。`SaveResult.warnings`をTask 4の`/save`応答が使う。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_blockly_templates.py`を新規作成する。内容は以下全文：

```python
"""blockly_templates（一覧＋参照検査）の検証。実機・GUIなしで回す。"""

from __future__ import annotations

from pathlib import Path

from services import blockly_templates


def make_app(base: Path) -> Path:
    app = base / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    (app / "Template" / "my-pack").mkdir(parents=True)
    return app


def write_images(app: Path) -> None:
    (app / "Template" / "root.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (app / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (app / "Template" / "my-pack" / "b.JPG").write_bytes(b"\x89PNG\r\n\x1a\n")
    (app / "Template" / "my-pack" / "skip.txt").write_text("not image")
    (app / "Template" / "my-pack" / "c.gif").write_bytes(b"GIF89a")


def vision_code(tpl: str = "my-pack/a.png") -> str:
    return (
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n"
        "\n\n"
        "class BlocklyCmd(ImageProcPythonCommand):\n"
        '    NAME = "画像認識"\n'
        "\n"
        "    def __init__(self, cam, gui=None):\n"
        "        super().__init__(cam, gui)\n"
        "\n"
        "    def do(self) -> None:\n"
        f'        self.waitTemplate("{tpl}", timeout=10.0, threshold=0.7)\n'
    )


def test_list_returns_sorted_image_rels(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    write_images(app)
    assert blockly_templates.list_image_templates(app) == [
        "my-pack/a.png",
        "my-pack/b.JPG",
        "root.png",
    ]


def test_list_missing_dir_returns_empty(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    assert blockly_templates.list_image_templates(app) == []


def test_good_ref_passes() -> None:
    assert blockly_templates.validate_template_refs(vision_code()) == []
    assert blockly_templates.warn_template_refs(vision_code()) == []


def test_empty_ref_rejected() -> None:
    errors = blockly_templates.validate_template_refs(vision_code(""))
    assert errors != []


def test_dotdot_ref_rejected() -> None:
    errors = blockly_templates.validate_template_refs(vision_code("../evil.png"))
    assert any(".." in e for e in errors)


def test_absolute_ref_rejected() -> None:
    assert blockly_templates.validate_template_refs(vision_code("/abs.png")) != []
    assert blockly_templates.validate_template_refs(vision_code("C:/x.png")) != []


def test_bare_name_warns_but_passes() -> None:
    assert blockly_templates.validate_template_refs(vision_code("a.png")) == []
    warnings = blockly_templates.warn_template_refs(vision_code("a.png"))
    assert len(warnings) == 1
    assert "配布" in warnings[0]


def test_broken_code_is_safe() -> None:
    assert blockly_templates.validate_template_refs("def broken(:\n") == []
    assert blockly_templates.warn_template_refs("def broken(:\n") == []
```

- [ ] **Step 2: REDを確認する**

Run: `uv run --frozen pytest tests/test_blockly_templates.py -q`
Expected: FAIL（`services.blockly_templates`が無いためcollection error）。

- [ ] **Step 3: `services/blockly_templates.py`を作成する**

内容は以下全文（標準ライブラリのみ、tkinterなし、相対importなし）：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Template画像の一覧と生成コード内の参照検査（GUI非依存）。

一覧は配布zipの `Template/<名>/...` 規約と同方向の相対表示にする。
参照検査は書式のみを見て存在は見ない。存在の有無は実行時の
`FileNotFoundError` が置き場所付きで知らせるため。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import ast

TEMPLATE_DIR_REL = "Template"

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}

#: 第1引数がテンプレートパスのVision API名。
VISION_TEMPLATE_APIS = frozenset(
    {
        "isContainTemplate",
        "isContainTemplateDump",
        "waitTemplate",
        "waitTemplateGone",
        "getTemplatePosition",
        "findAllTemplates",
        "countTemplate",
    }
)

#: 素名への警告文。
BARE_TEMPLATE_HINT = (
    "共有画像のため配布zipに含まれません。"
    "配布する場合は `Template/<名>/...` に置いてください"
)


def list_image_templates(app_dir: str | Path) -> list[str]:
    """`Template/` 以下の画像を相対（posix）で名前順に返す。無ければ空。"""
    base = Path(app_dir) / TEMPLATE_DIR_REL
    if not base.is_dir():
        return []
    found: list[str] = []
    for path in base.rglob("*"):
        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES:
            rel = path.relative_to(base).as_posix()
            found.append(PurePosixPath(rel).as_posix())
    return sorted(found)


def _template_args(python_code: str) -> list[str]:
    """vision系API呼び出しの第1引数（文字定数のみ）を集める。"""
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return []
    args: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute) and func.attr in VISION_TEMPLATE_APIS
        ):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                args.append(value)
    return args


def _bad_reason(value: str) -> str | None:
    """テンプレ参照として不正なら理由、正常なら None を返す。"""
    if not value.strip():
        return "テンプレート名を書いてください"
    probe = value.replace("\\", "/")
    if probe.startswith("/") or (len(probe) > 1 and probe[1] == ":"):
        return "絶対パスは使えません（`Template/` からの相対で書いてください）"
    if ".." in PurePosixPath(probe).parts:
        return "`..` は使えません"
    if ":" in value:
        return "`:` は使えません"
    return None


def validate_template_refs(python_code: str) -> list[str]:
    """生成コード内のテンプレ参照を検査する。異常の一覧を返す。"""
    errors: list[str] = []
    for value in _template_args(python_code):
        reason = _bad_reason(value)
        if reason is not None:
            errors.append(f"テンプレート名が不正です（{reason}）: {value!r}")
    return errors


def warn_template_refs(python_code: str) -> list[str]:
    """配布に含まれない素名参照への注意を返す。保存は通す。"""
    warnings: list[str] = []
    seen: set[str] = set()
    for value in _template_args(python_code):
        if value in seen:
            continue
        seen.add(value)
        if _bad_reason(value) is not None:
            continue
        if "/" not in value.replace("\\", "/"):
            warnings.append(f"{value!r} は{BARE_TEMPLATE_HINT}")
    return warnings
```

- [ ] **Step 4: `blockly_save.py`へ統合する**

`SerialController/services/blockly_save.py`に以下の3点を加える（ほかは触らない）：

1. import追加（`from core import blockly_validate`の直後）：

```python
from services import blockly_templates
```

2. `SaveResult`に1行追加（`errors`の後）：

```python
    warnings: list[str] = field(default_factory=list)
```

3. `save_blockly`の既存検証ブロックの直後（`if errors:`の閉じの後、`app = Path(app_dir)`の前）へ挿入：

```python
    ref_errors = blockly_templates.validate_template_refs(python_code)
    if ref_errors:
        return SaveResult(
            status="failed",
            message="保存できません:\n- " + "\n- ".join(ref_errors),
            errors=ref_errors,
        )
```

4. 成功時の戻り値を以下へ置き換える：

```python
    warnings = blockly_templates.warn_template_refs(python_code)
    message = f"保存しました: {py_rel}"
    if warnings:
        message += "\n注意:\n- " + "\n- ".join(warnings)
    logger.info(f"Blockly保存: {py_rel}")
    return SaveResult(
        status="saved",
        message=message,
        py_rel=py_rel,
        json_rel=json_rel,
        warnings=warnings,
    )
```

- [ ] **Step 5: 保存統合のテストを足す**

`tests/test_blockly_save.py`の末尾へ以下2件を追加する：

```python
def test_vision_code_with_subdir_saves_without_warnings(tmp_path: Path) -> None:
    from services import blockly_templates

    app = make_app(tmp_path)
    code = (
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n"
        "\n\n"
        "class BlocklyCmd(ImageProcPythonCommand):\n"
        '    NAME = "画像認識"\n'
        "\n"
        "    def __init__(self, cam, gui=None):\n"
        "        super().__init__(cam, gui)\n"
        "\n"
        "    def do(self) -> None:\n"
        '        self.waitTemplate("my-pack/a.png", timeout=10.0, threshold=0.7)\n'
    )
    assert blockly_templates.validate_template_refs(code) == []
    res = blockly_save.save_blockly(app, "VisionOk", good_ws(), code)
    assert res.status == "saved"
    assert res.warnings == []


def test_dotdot_template_fails_without_files(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    code = (
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n"
        "\n\n"
        "class BlocklyCmd(ImageProcPythonCommand):\n"
        '    NAME = "画像認識"\n'
        "\n"
        "    def __init__(self, cam, gui=None):\n"
        "        super().__init__(cam, gui)\n"
        "\n"
        "    def do(self) -> None:\n"
        '        self.waitTemplate("../evil.png", timeout=10.0, threshold=0.7)\n'
    )
    res = blockly_save.save_blockly(app, "VisionNg", good_ws(), code)
    assert res.status == "failed"
    assert list((app / "Commands" / "PythonCommands").iterdir()) == []
```

- [ ] **Step 6: GREENとgateを確認する**

Run: `uv run --frozen pytest tests/test_blockly_templates.py tests/test_blockly_save.py tests/test_blockly_validate.py -q`
Expected: PASS（8＋11＋7＝26 passed）。

Run: `uv run --frozen ruff check SerialController/services/blockly_templates.py SerialController/services/blockly_save.py tests/test_blockly_templates.py tests/test_blockly_save.py`
Expected: PASS。

Run: `uv run --frozen ruff format --check SerialController/services/blockly_templates.py SerialController/services/blockly_save.py tests/test_blockly_templates.py tests/test_blockly_save.py`
Expected: PASS。落ちたら`ruff format`で直すのは本Taskのファイルに限定する。

Run: `uv run --frozen mypy SerialController/services/blockly_templates.py SerialController/services/blockly_save.py`
Expected: PASS。

---

### Task 2: `.blockly.json`のpack同梱

**Files:**
- Modify: `SerialController/core/pack_zip.py`（`create_pack`のみ）
- Modify: `SerialController/services/script_pack.py`（`install_zip`のrels組み立てのみ）
- Test: `tests/test_pack_blockly.py`（新規）

**Interfaces:**
- Consumes: Task 1なし。`core/pack_manifest.py`のmanifest検証（不変）。
- Produces: pack内`.blockly.json`同梱。Task 4の文書がこれを説明する。他Taskは使わない。

約束: manifest v0は不変（`.blockly.json`は記載しない派生成果物）。同梱物が無い旧配置は従来通り動く。`uninstall`は記録駆動のため変更なし。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_pack_blockly.py`を新規作成する。内容は以下全文：

```python
"""packにおける `.blockly.json` 同梱の検証。実機・GUIなしで回す。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from core import pack_zip
from services import script_pack

ENTRY_PY = (
    "from Commands.Keys import Button\n"
    "from Commands.PythonCommandBase import PythonCommand\n"
    "\n\n"
    "class PackCmd(PythonCommand):\n"
    '    NAME = "配布"\n'
    "\n"
    "    def do(self) -> None:\n"
    "        self.press(Button.A)\n"
)

WS_JSON = json.dumps({"blocks": {"languageVersion": 0, "blocks": []}})

MANIFEST = {
    "name": "my-pack",
    "version": "1.0.0",
    "author": "tester",
    "description": "blockly同梱の検証",
    "entry": "MyEntry.py",
    "minAppVersion": "4.0.0",
    "templates": ["my-pack/a.png"],
}


def make_staged(base: Path, *, with_json: bool) -> Path:
    staged = base / "staged"
    (staged / "Commands" / "PythonCommands").mkdir(parents=True)
    (staged / "Template" / "my-pack").mkdir(parents=True)
    (staged / "pokecon.json").write_text(
        json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8"
    )
    (staged / "Commands" / "PythonCommands" / "MyEntry.py").write_text(
        ENTRY_PY, encoding="utf-8"
    )
    if with_json:
        (staged / "Commands" / "PythonCommands" / "MyEntry.blockly.json").write_text(
            WS_JSON, encoding="utf-8"
        )
    (staged / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return staged


def test_create_includes_companion_json(tmp_path: Path) -> None:
    staged = make_staged(tmp_path, with_json=True)
    names = pack_zip.create_pack(staged, tmp_path / "out.zip")
    assert "Commands/PythonCommands/MyEntry.blockly.json" in names
    with zipfile.ZipFile(tmp_path / "out.zip") as zf:
        assert (
            zf.read("Commands/PythonCommands/MyEntry.blockly.json").decode("utf-8")
            == WS_JSON
        )


def test_create_without_json_stays_compatible(tmp_path: Path) -> None:
    staged = make_staged(tmp_path, with_json=False)
    names = pack_zip.create_pack(staged, tmp_path / "out.zip")
    assert "Commands/PythonCommands/MyEntry.blockly.json" not in names
    assert "Commands/PythonCommands/MyEntry.py" in names


def test_install_and_uninstall_carry_json(tmp_path: Path) -> None:
    staged = make_staged(tmp_path, with_json=True)
    pack_zip.create_pack(staged, tmp_path / "out.zip")
    app = tmp_path / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    res = script_pack.install_zip(app, tmp_path / "out.zip")
    assert res.status == "installed"
    assert (
        "Commands/PythonCommands/MyEntry.blockly.json" in res.files
    )
    assert (
        app / "Commands" / "PythonCommands" / "MyEntry.blockly.json"
    ).is_file()
    un = script_pack.uninstall_package(app, "my-pack")
    assert un.status == "removed"
    assert not (app / "Commands" / "PythonCommands" / "MyEntry.blockly.json").exists()
```

- [ ] **Step 2: REDを確認する**

Run: `uv run --frozen pytest tests/test_pack_blockly.py -q`
Expected: FAIL（同梱が無いため1件目・3件目が失敗）。

- [ ] **Step 3: `create_pack`を直す**

`SerialController/core/pack_zip.py`の`create_pack`内で、`targets = [...]`の直後へ以下を挿入する：

```python
    entry_rel = PurePosixPath("Commands/PythonCommands") / manifest.entry
    json_rel = entry_rel.with_suffix(".blockly.json")
    if (base / json_rel.as_posix()).is_file():
        targets.append(json_rel)
```

- [ ] **Step 4: `install_zip`を直す**

`SerialController/services/script_pack.py`の`install_zip`内で、既存の`rels = [...]`の2行を以下の5行で置き換える：

```python
        entry_rel = "Commands/PythonCommands/" + PurePosixPath(manifest.entry).as_posix()
        rels = [entry_rel] + [
            "Template/" + PurePosixPath(t).as_posix() for t in manifest.templates
        ]
        json_rel = PurePosixPath(entry_rel).with_suffix(".blockly.json").as_posix()
        if (staged / json_rel).is_file():
            rels.append(json_rel)
```

注意: 既存の`rels = [...]`の2行を上記の5行で置き換える。`entry_rel`という名前の既存変数は無いため衝突しない。

- [ ] **Step 5: GREENとgateを確認する**

Run: `uv run --frozen pytest tests/test_pack_blockly.py tests/test_pack_zip.py tests/test_script_pack.py tests/test_pack_manifest.py -q`
Expected: PASS。

Run: `uv run --frozen ruff check SerialController/core/pack_zip.py SerialController/services/script_pack.py tests/test_pack_blockly.py`
Expected: PASS。

Run: `uv run --frozen ruff format --check SerialController/core/pack_zip.py SerialController/services/script_pack.py tests/test_pack_blockly.py`
Expected: PASS。落ちたら本Taskのファイルに限定して直す。

Run: `uv run --frozen mypy SerialController/core/pack_zip.py SerialController/services/script_pack.py`
Expected: PASS。

---

### Task 3: visionブロックJS＋生成器＋browser検証

**Files:**
- Modify: `SerialController/assets/blockly/pokecon_blocks.js`（vision 6種＋生成器＋program自動切替）
- Modify: `SerialController/assets/blockly/editor.html`（vision分類＋テンプレ選択欄）
- Test: `tests/test_blockly_browser.py`（vision用probe＋1件追加。既存テストは不変）

**Interfaces:**
- Consumes: Task 1の`validate_template_refs`（browserテストの生成コード検査で使う）。
- Produces: vision混じりの生成コード（ImageProcヘッダ）。Task 4の`/save`警告表示が`warnings`付き応答を読む（無くても動く）。

約束: 既存3ブロックの生成形は変えない。vision無しのprogramは従来ヘッダのまま。`pythonGenerator.INDENT = "    "`は維持。cropは`x1,y1,x2,y2`の整数4つのときだけ付け、空・不正形は付けない（全体扱い）。

- [ ] **Step 1: ブロック定義を追加する**

`SerialController/assets/blockly/pokecon_blocks.js`の最初の`Blockly.defineBlocksWithJsonArray([...]);`の閉じの直後へ、以下を挿入する：

```js
  Blockly.defineBlocksWithJsonArray([
    {
      type: "pokecon_vision_contains",
      message0: "画像 %1 がある 閾値 %2 範囲 %3",
      args0: [
        { type: "field_input", name: "TEMPLATE", text: "my-pack/a.png" },
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_input", name: "CROP", text: "" },
      ],
      output: "Boolean",
      colour: 210,
    },
    {
      type: "pokecon_vision_wait_appear",
      message0: "画像 %1 が出るまで待つ 上限 %2 閾値 %3 範囲 %4",
      args0: [
        { type: "field_input", name: "TEMPLATE", text: "my-pack/a.png" },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_input", name: "CROP", text: "" },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 210,
    },
    {
      type: "pokecon_vision_wait_gone",
      message0: "画像 %1 が消えるまで待つ 上限 %2 閾値 %3 範囲 %4",
      args0: [
        { type: "field_input", name: "TEMPLATE", text: "my-pack/a.png" },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_input", name: "CROP", text: "" },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 210,
    },
    {
      type: "pokecon_vision_position",
      message0: "画像 %1 の位置 閾値 %2 範囲 %3",
      args0: [
        { type: "field_input", name: "TEMPLATE", text: "my-pack/a.png" },
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_input", name: "CROP", text: "" },
      ],
      output: null,
      colour: 210,
    },
    {
      type: "pokecon_vision_wait_stable",
      message0: "画面が止まるまで待つ 静止 %1 上限 %2",
      args0: [
        { type: "field_number", name: "QUIET", value: 0.5, min: 0, max: 60 },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 210,
    },
    {
      type: "pokecon_vision_color",
      message0: "色 下限 %1 %2 %3 上限 %4 %5 %6 割合 %7",
      args0: [
        { type: "field_number", name: "H1", value: 0, min: 0, max: 179 },
        { type: "field_number", name: "S1", value: 0, min: 0, max: 255 },
        { type: "field_number", name: "V1", value: 0, min: 0, max: 255 },
        { type: "field_number", name: "H2", value: 179, min: 0, max: 179 },
        { type: "field_number", name: "S2", value: 255, min: 0, max: 255 },
        { type: "field_number", name: "V2", value: 255, min: 0, max: 255 },
        { type: "field_number", name: "RATIO", value: 0.6, min: 0, max: 1 },
      ],
      output: "Boolean",
      colour: 210,
    },
  ]);
```

- [ ] **Step 2: 生成器を追加し、programを自動切替にする**

同じファイルの`pythonGenerator.forBlock["pokecon_wait"]`定義の直後へ、以下を挿入する：

```js
  function visionCrop(block) {
    var c = String(block.getFieldValue("CROP") || "").trim();
    var m = c.match(/^(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)$/);
    return m
      ? ", crop=[" + m[1] + "," + m[2] + "," + m[3] + "," + m[4] + "]"
      : "";
  }

  pythonGenerator.forBlock["pokecon_vision_contains"] = function (
    block,
    generator
  ) {
    var code =
      "self.isContainTemplate(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      ")";
    return [code, generator.ORDER_ATOMIC];
  };

  pythonGenerator.forBlock["pokecon_vision_wait_appear"] = function (block) {
    return (
      "self.waitTemplate(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", timeout=" +
      block.getFieldValue("TIMEOUT") +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      ")\n"
    );
  };

  pythonGenerator.forBlock["pokecon_vision_wait_gone"] = function (block) {
    return (
      "self.waitTemplateGone(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", timeout=" +
      block.getFieldValue("TIMEOUT") +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      ")\n"
    );
  };

  pythonGenerator.forBlock["pokecon_vision_position"] = function (
    block,
    generator
  ) {
    var code =
      "self.getTemplatePosition(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      ")";
    return [code, generator.ORDER_ATOMIC];
  };

  pythonGenerator.forBlock["pokecon_vision_wait_stable"] = function (block) {
    return (
      "self.waitStable(quiet=" +
      block.getFieldValue("QUIET") +
      ", timeout=" +
      block.getFieldValue("TIMEOUT") +
      ")\n"
    );
  };

  pythonGenerator.forBlock["pokecon_vision_color"] = function (
    block,
    generator
  ) {
    var code =
      "self.isSimilarColor([], [" +
      block.getFieldValue("H1") +
      "," +
      block.getFieldValue("S1") +
      "," +
      block.getFieldValue("V1") +
      "], [" +
      block.getFieldValue("H2") +
      "," +
      block.getFieldValue("S2") +
      "," +
      block.getFieldValue("V2") +
      "], ratio=" +
      block.getFieldValue("RATIO") +
      ")";
    return [code, generator.ORDER_ATOMIC];
  };
```

次に`pokecon_program`の生成器を以下へ置き換える（旧全文を anchor にする。旧文は現行ファイルの`pythonGenerator.forBlock["pokecon_program"] = function ...`から`};`まで）：

```js
  pythonGenerator.forBlock["pokecon_program"] = function (block, generator) {
    var name = block.getFieldValue("NAME");
    var body = generator.statementToCode(block, "DO");
    var inner = body ? generator.prefixLines(body, "    ") : "        pass\n";
    // statementToCodeだけ・prefixLinesだけの片方では字下げが壊れる（spike確定）。
    // 両方を使ってdo()の中に寄せる。
    var vision =
      /self\.(isContainTemplate|waitTemplate|waitTemplateGone|getTemplatePosition|waitStable|getColorRatio|isSimilarColor|isContainTemplateDump|findAllTemplates|countTemplate)\s*\(/.test(
        body
      );
    var head;
    if (vision) {
      // press系と混ぜても `Button` が未定義にならないよう、vision側にも付ける。
      head =
        "from Commands.Keys import Button\n" +
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n" +
        "\n\n" +
        "class BlocklyCmd(ImageProcPythonCommand):\n" +
        "    NAME = " +
        pyStr(name) +
        "\n\n" +
        "    def __init__(self, cam, gui=None):\n" +
        "        super().__init__(cam, gui)\n" +
        "\n" +
        "    def do(self) -> None:\n";
    } else {
      head =
        "from Commands.Keys import Button\n" +
        "from Commands.PythonCommandBase import PythonCommand\n" +
        "\n\n" +
        "class BlocklyCmd(PythonCommand):\n" +
        "    NAME = " +
        pyStr(name) +
        "\n\n" +
        "    def do(self) -> None:\n";
    }
    return head + inner;
  };
```

置き換え対象の旧文（現行 `pokecon_blocks.js` のprogram生成器全文。これ全体を上記の新文で置き換える）：

```js
  pythonGenerator.forBlock["pokecon_program"] = function (block, generator) {
    var name = block.getFieldValue("NAME");
    var body = generator.statementToCode(block, "DO");
    var inner = body ? generator.prefixLines(body, "    ") : "        pass\n";
    // statementToCodeだけ・prefixLinesだけの片方では字下げが壊れる（spike確定）。
    // 両方を使ってdo()の中に寄せる。
    return (
      "from Commands.Keys import Button\n" +
      "from Commands.PythonCommandBase import PythonCommand\n" +
      "\n\n" +
      "class BlocklyCmd(PythonCommand):\n" +
      "    NAME = " +
      pyStr(name) +
      "\n\n" +
      "    def do(self) -> None:\n" +
      inner
    );
  };
```

- [ ] **Step 3: `editor.html`に分類とテンプレ選択欄を足す**

toolboxのPokeCon分類の直後（`{ kind: 'block', type: 'pokecon_wait' },`の3行の後、`]},`の前ではない。正確には以下の旧文：

```js
            { kind: 'block', type: 'pokecon_program' },
            { kind: 'block', type: 'pokecon_press' },
            { kind: 'block', type: 'pokecon_wait' },
          ],
        },
```

を以下へ置き換える：

```js
            { kind: 'block', type: 'pokecon_program' },
            { kind: 'block', type: 'pokecon_press' },
            { kind: 'block', type: 'pokecon_wait' },
          ],
        },
        {
          kind: 'category', name: '画像認識',
          contents: [
            { kind: 'block', type: 'pokecon_vision_contains' },
            { kind: 'block', type: 'pokecon_vision_wait_appear' },
            { kind: 'block', type: 'pokecon_vision_wait_gone' },
            { kind: 'block', type: 'pokecon_vision_position' },
            { kind: 'block', type: 'pokecon_vision_wait_stable' },
            { kind: 'block', type: 'pokecon_vision_color' },
          ],
        },
```

`bar2`の削除ボタンの後へ選択欄を足す。旧文：

```html
<button id="del">削除</button>
```

新文：

```html
<button id="del">削除</button>
<label>画像 <select id="tpllist"></select></label>
<button id="tplapply">選択中へ反映</button>
```

`refreshFiles`定義の直後へ以下を挿入する：

```js
  function refreshTemplates() {
    fetch('./templates').then(function (r) { return r.json(); }).then(function (j) {
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
  document.getElementById('tplapply').addEventListener('click', function () {
    var name = document.getElementById('tpllist').value;
    if (!name) { setStatus('画像を選んでください', true); return; }
    var sel = (typeof Blockly.getSelected === 'function') ? Blockly.getSelected() : null;
    if (!sel || typeof sel.getField !== 'function' || !sel.getField('TEMPLATE')) {
      setStatus('テンプレ欄のあるブロックを選んでください', true);
      return;
    }
    sel.setFieldValue(name, 'TEMPLATE');
    setStatus('反映しました: ' + name, false);
  });
```

保存応答の表示を警告対応にする。旧文：

```js
      setStatus(j.message || (j.ok ? '保存しました' : '保存できません'), !j.ok);
      if (j.ok) { refreshFiles(stem); }
```

新文：

```js
      var msg = j.message || (j.ok ? '保存しました' : '保存できません');
      if (j.warnings && j.warnings.length) { msg += '\n' + j.warnings.join('\n'); }
      setStatus(msg, !j.ok);
      if (j.ok) { refreshFiles(stem); }
```

末尾の`refreshFiles();`を以下へ置き換える：

```js
  refreshFiles();
  refreshTemplates();
```

- [ ] **Step 4: browser検証を拡張する**

`tests/test_blockly_browser.py`の末尾へ以下を追加する（既存の`PROBE_JS`・既存テストは触らない）：

```python
PROBE_VISION_JS = """\
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
const fail = (msg) => {
  console.error('BROWSER-PROBE-FAIL: ' + msg);
  process.exit(1);
};
try {
  vm.runInContext(read('pokecon_blocks.js'), sandbox, { filename: 'pokecon_blocks.js' });
} catch (e) {
  fail('pokecon_blocks.js が投げた: ' + e.constructor.name + ': ' + e.message);
}
const gen = sandbox.Blockly.Python;
for (const t of [
  'pokecon_vision_contains',
  'pokecon_vision_wait_appear',
  'pokecon_vision_wait_gone',
  'pokecon_vision_position',
  'pokecon_vision_wait_stable',
  'pokecon_vision_color',
]) {
  if (!gen.forBlock || typeof gen.forBlock[t] !== 'function') {
    fail(t + ' が登録されていない');
  }
}
const state = {
  blocks: {
    languageVersion: 0,
    blocks: [
      {
        type: 'pokecon_program',
        fields: { NAME: '画像待機' },
        inputs: {
          DO: {
            block: {
              type: 'pokecon_vision_wait_appear',
              fields: { TEMPLATE: 'my-pack/a.png', TIMEOUT: 10, THRESHOLD: 0.7, CROP: '' },
              next: {
                block: {
                  type: 'controls_if',
                  inputs: {
                    IF0: {
                      block: {
                        type: 'pokecon_vision_contains',
                        fields: { TEMPLATE: 'my-pack/a.png', THRESHOLD: 0.7, CROP: '' },
                      },
                    },
                    DO0: {
                      block: {
                        type: 'pokecon_press',
                        fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 },
                      },
                    },
                  },
                },
              },
            },
          },
        },
      },
    ],
  },
};
const ws = new sandbox.Blockly.Workspace();
sandbox.Blockly.serialization.workspaces.load(state, ws);
const code = gen.workspaceToCode(ws);
ws.dispose();
console.log('=== GENERATED START ===');
console.log(code);
console.log('=== GENERATED END ===');
"""


@NEEDS_NODE
def test_browser_vision_codegen_switches_base() -> None:
    from core import blockly_validate
    from services import blockly_templates

    proc = subprocess.run(
        ["node", "-e", PROBE_VISION_JS, str(BLOCKLY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"probe失敗:\n{proc.stderr}\n{proc.stdout}"
    start = proc.stdout.index("=== GENERATED START ===\n") + len(
        "=== GENERATED START ===\n"
    )
    end = proc.stdout.index("=== GENERATED END ===")
    code = proc.stdout[start:end]
    assert "ImageProcPythonCommand" in code
    assert "def __init__(self, cam, gui=None):" in code
    assert 'self.waitTemplate("my-pack/a.png", timeout=10, threshold=0.7)' in code
    assert 'self.isContainTemplate("my-pack/a.png", threshold=0.7)' in code
    assert "self.press(Button.A" in code
    assert blockly_validate.validate_generated_code(code) == []
    assert blockly_templates.validate_template_refs(code) == []
    assert blockly_templates.warn_template_refs(code) == []
```

- [ ] **Step 5: GREENを確認する**

Run: `uv run --frozen pytest tests/test_blockly_browser.py tests/test_blockly_save.py tests/test_blockly_templates.py -q`
Expected: PASS（node有りで2＋11＋8、node無しで1 skip＋11＋8）。

注意: nodeが無い環境ではvisionの1件がskipになる。HEAD側の目視（`pokecon_blocks.js`の`visionCrop`と3分岐head、`editor.html`の分類・選択欄）で補う。

---

### Task 4: `/templates`配信＋警告表示＋文書＋全gate

**Files:**
- Modify: `SerialController/ui/blockly_editor.py`（`GET /templates`＋`/save`応答に`warnings`）
- Modify: `docs/BLOCKLY_EDITOR.md`（vision・テンプレ・配布の説明）
- Test: `tests/test_blockly_editor.py`（新規。実HTTPで`/templates`・`/save`を叩く）

**Interfaces:**
- Consumes: Task 1の`list_image_templates`・`SaveResult.warnings`、Task 3の`editor.html`（`warnings`が無くても動く）。
- Produces: 完成。手動確認手順は文書に載る。

- [ ] **Step 1: `blockly_editor.py`を直す**

1. import追加（`from services import blockly_save`の直後）：

```python
from services import blockly_templates
```

2. `do_GET`の`/load`分岐の直後（`super().do_GET()`の前）へ挿入：

```python
        if path == "/templates":
            names = blockly_templates.list_image_templates(WindowUtils.APP_DIR)
            self._reply(True, "", {"templates": names})
            return
```

3. `/save`の応答1行を置き換える。旧文：

```python
            print(res.message)
            self._reply(res.status == "saved", res.message)
            return
        self.send_error(404)
```

新文（`/save`分岐の2行だけ。`/delete`分岐は触らない）：

```python
            print(res.message)
            self._reply(res.status == "saved", res.message, {"warnings": res.warnings})
            return
        self.send_error(404)
```

- [ ] **Step 2: 失敗する配信テストを書く**

`tests/test_blockly_editor.py`を新規作成する。内容は以下全文（tkinterのimportのみで生成はしないためheadless可。無ければskip）：

```python
"""blockly_editorの配信受け口（/templates・/save）の検証。実HTTPで叩く。"""

from __future__ import annotations

import functools
import http.client
import http.server
import json
import threading
from pathlib import Path

import pytest

tkinter = pytest.importorskip("tkinter")

import WindowUtils
from ui import blockly_editor


def make_app(base: Path) -> Path:
    app = base / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    (app / "Template" / "my-pack").mkdir(parents=True)
    (app / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return app


def vision_code(tpl: str = "my-pack/a.png") -> str:
    return (
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n"
        "\n\n"
        "class BlocklyCmd(ImageProcPythonCommand):\n"
        '    NAME = "画像認識"\n'
        "\n"
        "    def __init__(self, cam, gui=None):\n"
        "        super().__init__(cam, gui)\n"
        "\n"
        "    def do(self) -> None:\n"
        f'        self.waitTemplate("{tpl}", timeout=10.0, threshold=0.7)\n'
    )


@pytest.fixture()
def server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    app = make_app(tmp_path)
    monkeypatch.setattr(WindowUtils, "APP_DIR", str(app))
    handler = functools.partial(blockly_editor._Handler)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5.0)


def get_json(host: str, path: str) -> dict:
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    assert resp.status == 200
    return json.loads(resp.read().decode("utf-8"))


def post_json(host: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request(
        "POST", path, body=body, headers={"Content-Type": "application/json"}
    )
    resp = conn.getresponse()
    assert resp.status == 200
    return json.loads(resp.read().decode("utf-8"))


def test_templates_lists_images(server: str) -> None:
    data = get_json(server, "/templates")
    assert data["ok"] is True
    assert data["templates"] == ["my-pack/a.png"]


def test_save_vision_ok_with_warnings(server: str) -> None:
    ws = json.dumps({"blocks": {"languageVersion": 0, "blocks": []}})
    data = post_json(
        server,
        "/save",
        {"stem": "VisionOk", "workspaceJson": ws, "pythonCode": vision_code("a.png")},
    )
    assert data["ok"] is True
    assert isinstance(data.get("warnings"), list)
    assert len(data["warnings"]) == 1


def test_save_dotdot_fails(server: str) -> None:
    ws = json.dumps({"blocks": {"languageVersion": 0, "blocks": []}})
    data = post_json(
        server,
        "/save",
        {
            "stem": "VisionNg",
            "workspaceJson": ws,
            "pythonCode": vision_code("../evil.png"),
        },
    )
    assert data["ok"] is False
```

- [ ] **Step 3: REDを確認する**

Run: `uv run --frozen pytest tests/test_blockly_editor.py -q`
Expected: FAIL（`/templates`が404のため1件目が失敗）。

- [ ] **Step 4: Step 1の実装を入れ、GREENを確認する**

Run: `uv run --frozen pytest tests/test_blockly_editor.py tests/test_blockly_templates.py tests/test_blockly_save.py -q`
Expected: PASS。

- [ ] **Step 5: 文書を追記する**

`docs/BLOCKLY_EDITOR.md`の以下の旧文：

```markdown
ブロック： program（NAME＋DO）、press（ボタン14種＋長さ＋待ち）、wait（秒）。繰り返し・条件は標準ブロックを使う。画像認識ブロックはTODO-22で後付け。
```

を以下へ置き換える：

```markdown
ブロック： program（NAME＋DO）、press（ボタン14種＋長さ＋待ち）、wait（秒）。繰り返し・条件は標準ブロックを使う。

画像認識： 「画像認識」分類に6種。`contains`（値・標準ifの条件に直結）と`position`（値）は値型、`wait_appear`・`wait_gone`・`wait_stable`は文型、`color`（値・HSV上下限＋割合）は値型。vision系が1つでも混ざると生成コードは`ImageProcPythonCommand`＋`__init__(cam, gui)`になり、カメラが自動で渡る（混ざらなければ従来通り）。テンプレ欄の`範囲`は空＝全体または`x1,y1,x2,y2`。

テンプレ画像： `Template/`以下の画像が候補に出る（`画像`欄→`選択中へ反映`で選択中ブロックへ入る）。素名（例: `a.png`）は保存できるが共有画像扱いで配布zipに含まれない。配布する場合は`Template/<名>/...`に置く。

配布： zipには`.py`と同名`.blockly.json`が対で入る（あれば再編集可）。manifest v0は不変。
```

検証行の旧文：

```markdown
検証： `uv run --frozen pytest tests/test_blockly_assets.py tests/test_blockly_validate.py tests/test_blockly_save.py -q`
```

を以下へ置き換える：

```markdown
検証： `uv run --frozen pytest tests/test_blockly_assets.py tests/test_blockly_validate.py tests/test_blockly_save.py tests/test_blockly_templates.py tests/test_blockly_browser.py tests/test_blockly_editor.py tests/test_pack_blockly.py -q`
```

- [ ] **Step 6: 全gateを確認する**

Run: `uv run --frozen ruff check SerialController tools tests`
Expected: PASS。

Run: `uv run --frozen ruff format --check SerialController tools tests`
Expected: PASS（132→約136ファイル。落ちたら本planのファイルに限定して直す）。

Run: `uv run --frozen mypy SerialController tools tests`
Expected: PASS。

Run: `uv run --frozen python tools/check_core.py`
Expected: PASS（core・services・uiの違反なし）。

Run: `uv run --frozen python tools/check_user_api.py`
Expected: PASS（33→34ファイル程度。生成コードのimportは凍結面内のため）。

Run: `uv run --frozen pytest tests -q`
Expected: PASS（115＋新規約15＝約130 passed。node無しではbrowser 2件skip）。

- [ ] **Step 7: 実機で手動確認する（GUI＋ブラウザ＋カメラのため自動化しない）**

手順と期待結果（報告に転記すること）：
1. `python SerialController/Window.py`を起動し、メニュー→コマンド→「Blocklyエディタ...」を開く
2. ブラウザ編集画面に「画像認識」分類（6種）と`画像`選択欄が出る。`Template/my-pack/a.png`を置くと候補に出る
3. `wait_appear`（my-pack/a.png）＋`press`（A）を組んで保存名VisionOkで保存→「保存しました」と出る
4. `Commands/PythonCommands/VisionOk.py`が`ImageProcPythonCommand`＋`__init__(cam, gui)`で、`ruff format --check`が通る
5. エディタ小窓を閉じずとも約1秒で一覧に載り、選んで実行できる（カメラありで待機、画像なしで置き場所付きエラー）
6. 素名（`a.png`）で保存すると注意文（配布zipに含まれない）が出るが保存は通る
7. 旧MyBlockを開いて再保存すると従来ヘッダのまま動く
