# スクリプト配布の土台（manifest v0＋テンプレ名前空間＋CI）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自作スクリプトのzip配布の土台として、共通書式 `pokecon.json` v0 の検証器とテンプレ名前空間規約とCIを導入する。

**Architecture:** 検証の実体は tkinter-free の `core/pack_manifest.py` に閉じる（`task bounds` 対象）。`core/CommandVision.py` の変更はエラー文の例示1か所のみで挙動を変えない（サブフォルダは既に動作する）。zip梱包・導入GUIは次planの仕事で、ここでは含めない。

**Tech Stack:** Python 3.12 / 標準ライブラリのみ（json・pathlib・re・dataclasses）/ pytest, ruff, mypy, bounds, userapi / GitHub Actions＋uv

**Spec:** チャット合意（2026-09-06）— (1) manifestはJSON `pokecon.json`、(2) 新規テンプレは `Template/<pkg>/` 必須・既存の素の名前はレガシー維持、(3) Blockly資産は別planでベンダー同梱。本planは配布土台のみ。

## Global Constraints

- `SerialController/` を `sys.path` 前提の絶対 import、相対 import 禁止。
- `core/` は tkinter・pygubu・アプリ層（Commands・Settings・Window・GuiAssets 等）import 禁止 (`task bounds`)。新モジュールは標準ライブラリのみ使う。
- `Commands.*` の公開面（Keys / PythonCommandBase / McuCommandBase / WakeLink / CommandVision）を変えない (`task userapi`)。利用者スクリプトの既存importを壊さない。
- `settings*.ini` の書式は凍結。本planでは触らない。
- Python floor 3.12（pin 3.12.10）。`X | None`・builtin generics・`super()` 可。Ruff は `I`＋`UP006/007/008/035`。
- Windows-firstだがPOSIXでも壊れない（zip内パス区切り・改行の扱いに注意し、OS依存で落とさない）。
- Gate: `ruff check`＋`ruff format --check`＋`mypy`＋`bounds`＋`userapi`＋`pytest` が全緑。
- コミットはユーザーの明示指示があるときのみ（本planのstepにcommitを含めない）。

---

### Task 1: CIワークフロー（`task ci` 相当をActions化）

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `AGENTS.md`（「No tests, no CI」1行の更新のみ）

**Interfaces:**
- Consumes: `taskfile.yml` の `ci` 定義（lint / format --check / typecheck / bounds / userapi / test）。
- Produces: mainへのpush・PRで自動実行されるCI。以降のTaskの検証基盤。

- [ ] **Step 1: CIファイルを作成する**

`.github/workflows/ci.yml` を以下の内容で新規作成する（taskランナー自体には依存せず、`taskfile.yml` の各 `cmds` と同等の `uv run --frozen` を直接呼ぶ）：

```yaml
name: ci
on:
  push:
  pull_request:
jobs:
  ci:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up uv
        uses: astral-sh/setup-uv@v5
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12.10"
      - name: Sync deps
        run: uv sync --frozen
      - name: ruff check
        run: uv run --frozen ruff check SerialController tools tests
      - name: ruff format check
        run: uv run --frozen ruff format --check SerialController tools tests
      - name: mypy
        run: uv run --frozen mypy SerialController tools tests
      - name: bounds
        run: uv run --frozen python tools/check_core.py
      - name: userapi
        run: uv run --frozen python tools/check_user_api.py
      - name: pytest
        run: uv run --frozen pytest tests -q
```

- [ ] **Step 2: AGENTS.md の古い記述を直す**

`AGENTS.md` の `- No tests, no CI.` の1行を以下に置き換える：

```markdown
- Tests in `tests/` run via `task test`; CI (`.github/workflows/ci.yml`) runs `task ci` equivalent.
```

他の行には触らない。

- [ ] **Step 3: YAMLと gate を検証する**

Run: `uv run --frozen ruff check SerialController tools tests`（CI自体はpush後に回るため、ローカルでは最低でも lint＋bounds が緑であることを見る）
Expected: PASS。`python tools/check_core.py` も緑（`.github/` は検査対象外のため影響なし）。

---

### Task 2: manifest検証器 `core/pack_manifest.py`＋テスト

**Files:**
- Create: `SerialController/core/pack_manifest.py`
- Test: `tests/test_pack_manifest.py`

**Interfaces:**
- Consumes: なし（新規・標準ライブラリのみ）。
- Produces: `core.pack_manifest.PackManifest`（frozen dataclass）／`load_manifest(path) -> PackManifest`／`validate_manifest_data(data) -> list[str]`／`validate_package_layout(pkg_dir) -> list[str]`。次plan（梱包・導入）が使う。

manifest v0 の項目（すべて必須、空文字禁止）：
- `name`: パッケージID、`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`（先頭英数、最大64文字）
- `version`: `X.Y.Z`（各0〜999の数字のみ、例 `1.0.0`）
- `author`: 任意の非空文字（最大128文字）
- `description`: 任意の非空文字（最大1024文字）
- `entry`: zip内 `PythonCommands/` からの相対 `.py` パス（例 `MyPkg.py`）。絶対パス・`..`・`:`・先頭 `/` 禁止。末尾 `.py` 必須。
- `minAppVersion`: `X.Y.Z`（`version` と同じ書式）
- `templates`: 0件以上の一覧。各要素は zip内 `Template/` からの相対画像パス。絶対パス・`..`・`:` 禁止。拡張子は `.png`／`.jpg`／`.jpeg`／`.bmp` のみ（小文字比較）。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_pack_manifest.py` を新規作成する。内容は以下全文：

```python
"""pack_manifest（pokecon.json v0）の検証。"""

from __future__ import annotations

import json
from pathlib import Path

from core.pack_manifest import (
    load_manifest,
    validate_manifest_data,
    validate_package_layout,
)


def good_data() -> dict:
    return {
        "name": "my-pack",
        "version": "1.0.0",
        "author": "alice",
        "description": "sample",
        "entry": "MyPack.py",
        "minAppVersion": "4.0.0",
        "templates": ["my-pack/a.png"],
    }


def test_good_data_has_no_errors() -> None:
    assert validate_manifest_data(good_data()) == []


def test_missing_field_reports_name() -> None:
    data = good_data()
    del data["author"]
    errors = validate_manifest_data(data)
    assert any("author" in e for e in errors)


def test_bad_name_rejected() -> None:
    data = good_data()
    data["name"] = "../evil"
    assert validate_manifest_data(data) != []


def test_dotdot_entry_rejected() -> None:
    data = good_data()
    data["entry"] = "../evil.py"
    assert validate_manifest_data(data) != []


def test_absolute_template_rejected() -> None:
    data = good_data()
    data["templates"] = ["C:/abs/a.png"]
    assert validate_manifest_data(data) != []


def test_bad_extension_rejected() -> None:
    data = good_data()
    data["templates"] = ["my-pack/a.txt"]
    assert validate_manifest_data(data) != []


def test_load_manifest_reads_file(tmp_path: Path) -> None:
    p = tmp_path / "pokecon.json"
    p.write_text(json.dumps(good_data()), encoding="utf-8")
    m = load_manifest(p)
    assert m.name == "my-pack"
    assert m.templates == ["my-pack/a.png"]


def test_validate_package_layout_checks_files(tmp_path: Path) -> None:
    (tmp_path / "PythonCommands").mkdir()
    (tmp_path / "Template" / "my-pack").mkdir(parents=True)
    (tmp_path / "pokecon.json").write_text(json.dumps(good_data()), encoding="utf-8")
    (tmp_path / "PythonCommands" / "MyPack.py").write_text("# ok\n", encoding="utf-8")
    (tmp_path / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert validate_package_layout(tmp_path) == []
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `uv run --frozen pytest tests/test_pack_manifest.py -q`
Expected: FAIL（`core.pack_manifest` が無いため collection error）。

- [ ] **Step 3: 最小実装を書く**

`SerialController/core/pack_manifest.py` を新規作成する。内容は以下全文（標準ライブラリのみ、tkinter・アプリ層importなし、相対importなし）：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pokecon.json v0 の読み込みと検証（GUI非依存）。

zip梱包・導入（次plan）の土台。ここでは検証だけを持ち、zip操作・
GUI・CommandLoader への組み込みは行わない。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MANIFEST_FILENAME = "pokecon.json"

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_VERSION_RE = re.compile(r"(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
_REQUIRED_FIELDS = (
    "name",
    "version",
    "author",
    "description",
    "entry",
    "minAppVersion",
    "templates",
)


@dataclass(frozen=True)
class PackManifest:
    """検証済み manifest。未検証の dict はこの型に入れない。"""

    name: str
    version: str
    author: str
    description: str
    entry: str
    minAppVersion: str
    templates: list[str]


def _is_bad_rel(value: str, *, need_py: bool = False) -> str | None:
    """zip内相対パスとして不正なら理由、正常なら None を返す。"""
    if not value or not value.strip():
        return "空です"
    if value.startswith("/") or value.startswith("\\"):
        return "絶対パスは使えません（相対で書いてください）"
    if ".." in PurePosixPath(value).parts:
        return "`..` は使えません"
    if ":" in value:
        return "`:` は使えません（ドライブ文字・代替 stream のため）"
    if need_py and not value.lower().endswith(".py"):
        return "末尾は `.py` にしてください"
    return None


def validate_manifest_data(data: object) -> list[str]:
    """未検証 dict を検査し、異常の一覧（日本語）を返す。空なら正常。"""
    if not isinstance(data, dict):
        return ["manifest は JSON オブジェクトで書いてください"]
    errors: list[str] = []
    for field in _REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"`{field}` がありません")
    if errors:
        return errors

    name = data["name"]
    if not isinstance(name, str) or _NAME_RE.fullmatch(name) is None:
        errors.append("`name` は英数始まりの `[A-Za-z0-9_-]`（最大64文字）にしてください")
    for field in ("version", "minAppVersion"):
        value = data[field]
        if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
            errors.append(f"`{field}` は `X.Y.Z`（各0〜999の数字）にしてください（例: 1.0.0）")
    for field in ("author", "description"):
        value = data[field]
        limit = 128 if field == "author" else 1024
        if not isinstance(value, str) or not value.strip():
            errors.append(f"`{field}` を1文字以上書いてください")
        elif len(value) > limit:
            errors.append(f"`{field}` は{limit}文字以内にしてください")
    entry = data["entry"]
    if not isinstance(entry, str):
        errors.append("`entry` は `PythonCommands/` からの相対 `.py` パスで書いてください")
    else:
        reason = _is_bad_rel(entry, need_py=True)
        if reason is not None:
            errors.append(f"`entry` が不正です（{reason}）: {entry!r}")

    templates = data["templates"]
    if not isinstance(templates, list):
        errors.append("`templates` は一覧で書いてください（0件なら `[]`）")
    else:
        for item in templates:
            if not isinstance(item, str):
                errors.append(f"`templates` の要素は文字にしてください: {item!r}")
                continue
            reason = _is_bad_rel(item)
            if reason is not None:
                errors.append(f"`templates` が不正です（{reason}）: {item!r}")
                continue
            if PurePosixPath(item).suffix.lower() not in _IMAGE_SUFFIXES:
                errors.append(
                    "`templates` は `.png`/`.jpg`/`.jpeg`/`.bmp` にしてください: "
                    f"{item!r}"
                )
    return errors


def load_manifest(path: str | Path) -> PackManifest:
    """`pokecon.json` を読み、検証済みなら返す。異常なら ValueError。"""
    filespec = Path(path)
    try:
        data: object = json.loads(filespec.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"manifest がありません: {filespec}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"manifest を読めません: {filespec}（{e}）") from None
    except json.JSONDecodeError as e:
        raise ValueError(f"manifest の JSON が壊れています: {filespec}（{e}）") from None
`SerialController/core/pack_manifest.py` の `validate_manifest_data` 内、`templates` 要素検査の末尾（拡張子検査の直後）に以下を追加する。`data["name"]` は未確定の場合があるため、`isinstance` で確認してから例示に使う：

```python
            if "/" not in item.replace("\\", "/"):
                pkg = data.get("name")
                example = f"Template/{pkg}/{item}" if isinstance(pkg, str) and pkg else "Template/<name>/..."
                errors.append(
                    "`templates` は `Template/<name>/...` の形にしてください "
                    f"（例: {example}）。"
                    "素の名前は既存配布との衝突のため新規では使えません"
                )
```
        manifest = load_manifest(base / MANIFEST_FILENAME)
    except ValueError as e:
        return [str(e)]
    errors: list[str] = []
    entry = base / "PythonCommands" / PurePosixPath(manifest.entry).as_posix()
    if not entry.is_file():
        errors.append(f"`entry` の実体がありません: PythonCommands/{manifest.entry}")
    for item in manifest.templates:
        if not (base / "Template" / PurePosixPath(item).as_posix()).is_file():
            errors.append(f"`templates` の実体がありません: Template/{item}")
    return errors
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run --frozen pytest tests/test_pack_manifest.py -q`
Expected: PASS（8 passed）。

- [ ] **Step 5: gate を確認する**

Run: `uv run --frozen ruff check SerialController/core/pack_manifest.py tests/test_pack_manifest.py`、次に `uv run --frozen ruff format --check SerialController/core/pack_manifest.py tests/test_pack_manifest.py`、次に `uv run --frozen mypy SerialController/core/pack_manifest.py tests/test_pack_manifest.py`
Expected: すべてPASS。直しが必要なら `ruff format` で整形してから再実行する。

---

### Task 3: テンプレ名前空間規約（`Template/<pkg>/`）の最小実装

**Files:**
- Modify: `SerialController/core/CommandVision.py`（エラー文の例示1か所のみ）
- Modify: `SerialController/core/pack_manifest.py`（`templates` の名前空間注意を追加）
- Test: `tests/test_pack_manifest.py`（1テスト追加）

**Interfaces:**
- Consumes: Task 2 の `validate_manifest_data`。
- Produces: 新規配布は `Template/<name>/...` に寄せる規約。以降の梱包・導入planがこの検査を使う。読み込み挙動（`_get_template_filespec`）は変えない。

- [ ] **Step 1: 失敗するテストを足す**

`tests/test_pack_manifest.py` の末尾に以下を追加する：

```python
def test_legacy_root_template_gets_namespace_hint() -> None:
    data = good_data()
    data["templates"] = ["a.png"]
    errors = validate_manifest_data(data)
    assert any("<name>" in e or "Template" in e for e in errors)
```

Run: `uv run --frozen pytest tests/test_pack_manifest.py -q`
Expected: FAIL（現状は素の `a.png` を通すため）。

- [ ] **Step 2: 名前空間の注意を実装する**

`SerialController/core/pack_manifest.py` の `validate_manifest_data` 内、`templates` 要素検査の末尾（拡張子検査の直後）に以下を追加する：

```python
            if "/" not in item.replace("\\", "/"):
                errors.append(
                    "`templates` は `Template/<name>/...` の形にしてください "
                    f"（例: Template/{data['name']}/{item}）。"
                    "素の名前は既存配布との衝突のため新規では使えません"
                )
```

`data["name"]` はこの時点で未確定の場合があるため、`isinstance(data.get("name"), str)` のときだけ例示に使い、そうでなければ `Template/<name>/...` の固定文にする。正確なコードは以下：

```python
            if "/" not in item.replace("\\", "/"):
                pkg = data.get("name")
                example = f"Template/{pkg}/{item}" if isinstance(pkg, str) and pkg else "Template/<name>/..."
                errors.append(
                    "`templates` は `Template/<name>/...` の形にしてください "
                    f"（例: {example}）。"
                    "素の名前は既存配布との衝突のため新規では使えません"
                )
```

- [ ] **Step 3: CommandVision の案内を新配置に寄せる**

`SerialController/core/CommandVision.py` の `_imread_or_raise` 内の hint：

```python
            hint = (
                f"画像を {TEMPLATE_PATH} からの相対で置いてください "
                f"（例: {path.join(TEMPLATE_PATH, 'shiny_mark.png')}）"
            )
```

を以下に置き換える（文言以外の挙動・戻り値・例外型は変えない）：

```python
            hint = (
                f"画像を {TEMPLATE_PATH} からの相対で置いてください "
                f"（例: {path.join(TEMPLATE_PATH, 'my-pack', 'a.png')}）。"
                "配布物は `Template/<パッケージ名>/` に置く規約です"
            )
```

- [ ] **Step 4: テストとgateを確認する**

Run: `uv run --frozen pytest tests/test_pack_manifest.py -q`
Expected: PASS（9 passed）。

Run: `uv run --frozen python tools/check_user_api.py`（CommandVision の公開名を変えていないため影響なしのはず）
Expected: `利用者API面 OK`。

---

### Task 4: サンプル配布物＋作者向け手順（ドキュメントのみ）

**Files:**
- Create: `docs/PACK_FORMAT_v0.md`
- Create: `docs/pack_sample/pokecon.json`
- Create: `docs/pack_sample/PythonCommands/MyPack.py`
- Create: `docs/pack_sample/Template/my-pack/a.png.txt`（配置例を示すダミー。バイナリは置かない）

**Interfaces:**
- Consumes: Task 2〜3 の規約。
- Produces: 作者が写経できる最小例。アプリ本体・テストには組み込まない。

- [ ] **Step 1: サンプル manifest を作る**

`docs/pack_sample/pokecon.json` を以下の内容で作成する：

```json
{
  "name": "my-pack",
  "version": "1.0.0",
  "author": "alice",
  "description": "配布サンプル",
  "entry": "MyPack.py",
  "minAppVersion": "4.0.0",
  "templates": ["my-pack/a.png"]
}
```

- [ ] **Step 2: サンプル script を作る**

`docs/pack_sample/PythonCommands/MyPack.py` を以下の内容で作成する：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.Keys import Button
from Commands.PythonCommandBase import PythonCommand


class MyPack(PythonCommand):
    NAME = "配布サンプル"

    def __init__(self) -> None:
        super().__init__()

    def do(self) -> None:
        self.press(Button.A)
```

このファイルは `task userapi` の検査対象外（`Commands/PythonCommands/` 配下ではない）なので、公開面の例示として成立する。

- [ ] **Step 3: テンプレ配置のダミーを置く**

`docs/pack_sample/Template/my-pack/a.png.txt` を以下の1行で作成する：

```text
ここに a.png を置く（バイナリはリポジトリに入れない。配置例: Template/my-pack/a.png）。
```

- [ ] **Step 4: 書式文書を書く**

`docs/PACK_FORMAT_v0.md` を以下の内容で作成する（内側の検証行はインデント記法にし、フェンスを入れ子にしない）：

    # 配布書式 v0（pokecon.json）

    zip内の配置：

    - `pokecon.json`（本書式）
    - `PythonCommands/<entry>`（自作スクリプト本体）
    - `Template/<name>/...`（テンプレ画像。素の名前は新規禁止）

    `pokecon.json` の項目は `core/pack_manifest.py` の検査が正とする。
    `entry`・`templates` は相対パスのみ（絶対・`..`・`:` 禁止）。
    画像拡張子は `.png`/`.jpg`/`.jpeg`/`.bmp`。

    検証： `uv run --frozen pytest tests/test_pack_manifest.py -q`

- [ ] **Step 5: サンプルが検査を通ることを確認する**

Run: `uv run --frozen pytest tests/test_pack_manifest.py -q`
Expected: PASS（本Taskはドキュメントのみのため回帰確認だけ）。