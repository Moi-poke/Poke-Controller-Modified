# Blocklyエディタ基盤（vendored資産＋保存API＋メニュー起動）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ブラウザのBlocklyで組み立てた操作をPythonコマンド（`.py`＋`.blockly.json`）として保存し、既存の一覧に再読込できるようにする。

**Architecture:** 生成の実体はvendoredなBlockly JS（`assets/blockly/`）が持ち、Python側は検証（`core/blockly_validate.py`）と保存（`services/blockly_save.py`）だけを持つ。編集画面は標準ライブラリのlocalhostサーバで配信し、`/save`でファイルを書く。開閉は`ui/blockly_editor.py`に閉じ、実操作後に既存`reloadCommands()`を呼ぶ。生成はBlockly→Python片方向のみ。

**Tech Stack:** Python 3.12 / 標準ライブラリ（http.server・webbrowser・ast・json・pathlib）/ Blockly 13.2.1（vendored JS、CDN禁止）/ tkinter（uiのみ）/ pytest, ruff, mypy, bounds, userapi

**Spec:** チャット合意＋handoff（`C:\Users\moilo\AppData\Local\Temp\opencode\pokecon-handoff.md`）— (1)資産は`SerialController/assets/blockly/`にvendored＋VERSION、(2)Blockly 13.2.1ピン、(3)生成はBlockly→Python片方向のみ、(4)localhostサーバで編集＋`/save`で`PythonCommands/`へ`.blockly.json`＋`.py`保存→`reloadCommands()`、(5)画像認識ブロックはTODO-22で後付け。Spike証跡（throwaway、リポジトリ外）: `C:\Users\moilo\AppData\Local\Temp\opencode\blockly-spike\probe.js`＋`spike_out.py`。Spike確定事項：`pythonGenerator.INDENT='    '`と`prefixLines(body,'    ')`の両方が要る（片方だけでは字下げが壊れる）、Node headlessでは`Object.assign(Blockly.Msg, require('msg/ja.js'))`が要る（ブラウザはscriptタグで自動）、ダブルクォート＋末尾改行で`ruff format`を通す。

## Global Constraints

- `SerialController/`を`sys.path`前提の絶対import、相対import禁止。
- `core/`はtkinter・pygubu・アプリ層 import禁止。新規は標準ライブラリ＋`core.*`のみ。
- `services/`はtkinter・画面部品 import禁止。`core.*`・標準ライブラリ・loguruは可。
- `ui/`は`Window`本体 import禁止。tkinter・`services`・`WindowUtils`（既存pattern）可。
- `Commands.*`の公開面を変えない。生成`.py`のimportは凍結面（Keys/PythonCommandBase/McuCommandBase/WakeLink/CommandVision＋既知サードパーティ）のみ。
- `settings*.ini`は触らない。
- Python floor 3.12（pin 3.12.10）。`X | None`・builtin generics可。Ruff `I`＋`UP006/007/008/035`。
- コメントと利用者向け文面は日本語。生成`.py`も4スペース字下げ。
- Blockly資産はCDN禁止、リポジトリ内vendored。Blockly 13.2.1ピン。生成はBlockly→Python片方向のみ（逆変換なし）。
- 実行中の保存後リロード・編集起動は断る（既存`reloadCommands`と`runner.is_busy()`と同じ規則）。
- Gate: `ruff check`＋`ruff format --check`＋`mypy`＋`bounds`＋`userapi`＋`pytest`が全緑。
- コミットはユーザーの明示指示があるときのみ（本planのstepにcommitを含めない）。
- `Window.py`は起動時に`os.chdir(BASE_DIR)`する。資源パスは`WindowUtils.APP_DIR`（＝`SerialController/`）起点で組み立て、cwd相対にしない。`services/`で`os.chdir`しない。
- `.blockly.json`は`Utility.getModuleNames`（`ext=".py"`のみ走査）のため`CommandLoader`を壊さない。生成`.py`1件が壊れても残りは読める既存挙動を保つ。

## File Structure

- `SerialController/assets/blockly/`（vendored静的資産）— `blockly_compressed.js`＋`blocks_compressed.js`＋`python_compressed.js`＋`msg/ja.js`＋`media/*`＋`VERSION`（中身`13.2.1`）＋`editor.html`＋`pokecon_blocks.js`。ブラウザが読むだけ。Pythonからは触らない。
- `SerialController/core/blockly_validate.py`（新規）— ファイル名・生成コード・ワークスペースJSONの検証。tkinterなし。
- `SerialController/services/blockly_save.py`（新規）— `.py`＋`.blockly.json`の原子保存・一覧。UIなし。
- `SerialController/ui/blockly_editor.py`（新規）— localhost配信＋`/save`＋ブラウザ起動＋終了時リロード。実保存はservices。
- `SerialController/Menubar.py`（追記）— 「Blocklyエディタ...」1項目＋対応メソッド＋`closeAll`でのサーバ停止。`closeAll`以外は触らない。
- `docs/BLOCKLY_EDITOR.md`（新規）— 使い方・vendoring手順・ブロック一覧・TODO-22予告。
- Tests: `tests/test_blockly_assets.py`（新規）、`tests/test_blockly_validate.py`（新規）、`tests/test_blockly_save.py`（新規）。UIの自動テストは書かない（既存`script_pack_dialogs`と同様、手動手順で確認）。

---

### Task 1: vendored資産＋VERSION＋存在テスト

**Files:**
- Create: `SerialController/assets/blockly/VERSION`
- Vendor: `SerialController/assets/blockly/blockly_compressed.js`、`blocks_compressed.js`、`python_compressed.js`、`msg/ja.js`、`media/*`（バイナリ含む、変換禁止）
- Test: `tests/test_blockly_assets.py`（新規）

**Interfaces:**
- Consumes: なし（静的資産）。
- Produces: ブラウザが相対パスで読める資産一式。Task 4の`editor.html`・`pokecon_blocks.js`が使う。`VERSION`は`13.2.1`固定。

約束（verbatim）: 入手元は`https://registry.npmjs.org/blockly/-/blockly-13.2.1.tgz`（spikeの`blockly-13.2.1.tgz`と同一版）。抜き出すのは`blockly_compressed.js`（約619KB）・`blocks_compressed.js`（約70KB）・`python_compressed.js`（約27KB）・`msg/ja.js`（約55KB）・`media/`のみ。`.map`・`.d.ts`・`.mjs`は入れない。合計約800KB。`script`タグはCDN禁止、すべて相対（`./blockly_compressed.js`等）。

- [ ] **Step 1: 失敗する存在テストを書く**

`tests/test_blockly_assets.py`を新規作成する。内容は以下全文：

```python
"""Blockly vendored資産の存在検証。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCKLY = ROOT / "SerialController" / "assets" / "blockly"


def test_version_pinned() -> None:
    assert (BLOCKLY / "VERSION").read_text(encoding="utf-8").strip() == "13.2.1"


def test_vendored_files_exist() -> None:
    for rel in (
        "blockly_compressed.js",
        "blocks_compressed.js",
        "python_compressed.js",
        "msg/ja.js",
    ):
        path = BLOCKLY / rel
        assert path.is_file(), f"missing: {rel}"
        assert path.stat().st_size > 10 * 1024, f"too small: {rel}"


def test_media_has_sound_and_icons() -> None:
    media = BLOCKLY / "media"
    assert media.is_dir()
    names = {p.name for p in media.iterdir()}
    assert "click.mp3" in names
    assert "delete-icon.svg" in names
```

- [ ] **Step 2: REDを確認する**

Run: `uv run --frozen pytest tests/test_blockly_assets.py -q`
Expected: FAIL（資産が無いため`missing`系で失敗）。

- [ ] **Step 3: 資産を配置する**

手順（バイナリ変換なしで写すこと）：
1. `https://registry.npmjs.org/blockly/-/blockly-13.2.1.tgz`を取得する（spikeで取得済みの`C:\Users\moilo\AppData\Local\Temp\opencode\blockly-spike\blockly-13.2.1.tgz`を再利用してよい）。
2. 展開した`package/`から`blockly_compressed.js`・`blocks_compressed.js`・`python_compressed.js`・`msg/ja.js`・`media/`だけを`SerialController/assets/blockly/`へ写す。`msg/`直下は`ja.js`のみ置く（`msg/`フォルダ自体は作る）。
3. `SerialController/assets/blockly/VERSION`を以下の1行で作る：

```text
13.2.1
```

4. エディタ本体（`editor.html`・`pokecon_blocks.js`）はTask 4で作る。ここでは置かない。

- [ ] **Step 4: GREENを確認する**

Run: `uv run --frozen pytest tests/test_blockly_assets.py -q`
Expected: PASS（3 passed）。

---

### Task 2: `core/blockly_validate.py`＋テスト

**Files:**
- Create: `SerialController/core/blockly_validate.py`
- Test: `tests/test_blockly_validate.py`（新規）

**Interfaces:**
- Consumes: `tools/check_user_api.py`の許可集合と同値（正本はあちら。変えたらこちらも合わせる）。
- Produces: `validate_stem(stem)->list[str]`／`validate_generated_code(code)->list[str]`／`validate_workspace_json(text)->list[str]`。Task 3が使う。

約束: ファイル名は`[A-Za-z_][A-Za-z0-9_]*`（`pack_manifest.validate_entry_segments`と同規則、先頭英字・`_`、ハイフン禁止）。生成コードは`ast.parse`必須、importは凍結面のみ、クラスに空でない`NAME`と`def do`必須。ワークスペースJSONは`{"blocks": {"blocks": [...]}}`形必須。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_blockly_validate.py`を新規作成する。内容は以下全文：

```python
"""blockly_validate（ファイル名・生成コード・JSON）の検証。"""

from __future__ import annotations

import json

from core import blockly_validate


def good_code() -> str:
    return (
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        'class BlocklyCmd(PythonCommand):\n'
        '    NAME = "ブロック作成"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.press(Button.A)\n"
        "        self.wait(0.5)\n"
    )


def test_good_code_passes() -> None:
    assert blockly_validate.validate_generated_code(good_code()) == []
    assert blockly_validate.validate_stem("MyBlock") == []
    ws = {"blocks": {"languageVersion": 0, "blocks": []}}
    assert blockly_validate.validate_workspace_json(json.dumps(ws)) == []


def test_bad_stem_rejected() -> None:
    assert blockly_validate.validate_stem("my-pack") != []
    assert blockly_validate.validate_stem("../evil") != []
    assert blockly_validate.validate_stem("") != []


def test_relative_import_rejected() -> None:
    code = good_code() + "from . import foo\n"
    assert blockly_validate.validate_generated_code(code) != []


def test_unknown_commands_rejected() -> None:
    code = "from Commands.Unknown import X\n" + good_code()
    assert blockly_validate.validate_generated_code(code) != []


def test_missing_name_rejected() -> None:
    code = good_code().replace('NAME = "ブロック作成"', "NAME = ")
    assert blockly_validate.validate_generated_code(code) != []


def test_missing_do_rejected() -> None:
    code = good_code().replace("    def do(self) -> None:\n", "")
    assert blockly_validate.validate_generated_code(code) != []


def test_broken_json_rejected() -> None:
    assert blockly_validate.validate_workspace_json("{broken") != []
    assert blockly_validate.validate_workspace_json(json.dumps({"a": 1})) != []
```

- [ ] **Step 2: REDを確認する**

Run: `uv run --frozen pytest tests/test_blockly_validate.py -q`
Expected: FAIL（`core.blockly_validate`が無いためcollection error）。

- [ ] **Step 3: `core/blockly_validate.py`を作成する**

内容は以下全文（標準ライブラリ＋`core.*`なし、tkinterなし、相対importなし）：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blockly生成物の検証（GUI非依存）。

編集画面（JS）が作ったPythonコードとワークスペースJSONを、保存前に
検査する。許可するimportの集合は `tools/check_user_api.py` と同じ値
（あちらが正本。公開面が変わったらこちらも合わせる）。
"""

from __future__ import annotations

import ast
import json
import re
import sys

_STEM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# 正本は tools/check_user_api.py。利用者スクリプトの公開面が変わったら合わせる。
_ALLOWED_COMMANDS_SUBS = {
    "Keys",
    "PythonCommandBase",
    "McuCommandBase",
    "WakeLink",
    "CommandVision",
}
_THIRD_PARTY = {
    "cv2",
    "numpy",
    "PIL",
    "pandas",
    "scipy",
    "requests",
    "yaml",
    "loguru",
    "icecream",
    "deprecated",
    "pynput",
    "serial",
    "pygubu",
    "matplotlib",
    "pyaudio",
}


def validate_stem(stem: str) -> list[str]:
    """保存名（拡張子なし）を検査する。異常の一覧を返す。空なら正常。"""
    if not isinstance(stem, str) or not stem.strip():
        return ["保存名を書いてください"]
    if "/" in stem or "\\" in stem or "." in stem:
        return ["保存名に区切り文字は使えません（例: MyBlock）"]
    if _STEM_RE.fullmatch(stem) is None:
        return ["保存名は英字・数字・`_`にしてください（例: MyBlock）"]
    return []


def _check_imports(tree: ast.AST) -> list[str]:
    """import経路の検査。公開面外は異常、未知トップレベルは注意も異常扱い。"""
    allowed_top = set(sys.stdlib_module_names) | _THIRD_PARTY | {"Commands"}
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top not in allowed_top:
                    errors.append(f"許可外の import です: import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                errors.append("相対 import は使えません（絶対 import にしてください）")
            elif node.module:
                top = node.module.split(".")[0]
                if top == "Commands":
                    parts = node.module.split(".")
                    sub = parts[1] if len(parts) > 1 else ""
                    if sub not in _ALLOWED_COMMANDS_SUBS:
                        errors.append(
                            "公開API面外の Commands 経路です: "
                            f"from {node.module} import ..."
                        )
                elif top not in allowed_top:
                    errors.append(
                        f"許可外の import です: from {node.module} import ..."
                    )
    return errors


def validate_generated_code(code: str) -> list[str]:
    """生成Pythonコードを検査する。異常の一覧を返す。空なら正常。"""
    if not isinstance(code, str) or not code.strip():
        return ["生成コードが空です"]
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"生成コードの文法が壊れています（{e}）"]
    errors = _check_imports(tree)
    found_name = False
    found_do = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for t in item.targets:
                        if isinstance(t, ast.Name) and t.id == "NAME":
                            if isinstance(item.value, ast.Constant):
                                value = item.value.value
                                if isinstance(value, str) and value.strip():
                                    found_name = True
                if isinstance(item, ast.FunctionDef) and item.name == "do":
                    found_do = True
    if not found_name:
        errors.append("`NAME = \"...\"`（空でない文字）がありません")
    if not found_do:
        errors.append("`def do(self)` がありません")
    return errors


def validate_workspace_json(text: str) -> list[str]:
    """ワークスペースJSONを検査する。異常の一覧を返す。空なら正常。"""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return [f"ワークスペースJSONが壊れています（{e}）"]
    if not isinstance(data, dict):
        return ["ワークスペースJSONはオブジェクトで書いてください"]
    blocks = data.get("blocks")
    if not isinstance(blocks, dict) or not isinstance(blocks.get("blocks"), list):
        return ["ワークスペースJSONに `blocks.blocks` がありません"]
    return []
```

- [ ] **Step 4: GREENとgateを確認する**

Run: `uv run --frozen pytest tests/test_blockly_validate.py tests/test_blockly_assets.py -q`
Expected: PASS（7＋3＝10 passed）。

Run: `ruff check`＋`format --check`＋`mypy`を`SerialController/core/blockly_validate.py tests/test_blockly_validate.py tests/test_blockly_assets.py`に掛ける
Expected: すべてPASS。直しは本Taskのファイルに限定する。

---

### Task 3: `services/blockly_save.py`＋テスト

**Files:**
- Create: `SerialController/services/blockly_save.py`
- Test: `tests/test_blockly_save.py`（新規）

**Interfaces:**
- Consumes: Task 2の`blockly_validate.validate_stem/validate_generated_code/validate_workspace_json`。
- Produces: `SaveResult(status/message/py_rel/json_rel)`／`save_blockly(app_dir,stem,workspace_json,python_code)->SaveResult`／`list_blockly(app_dir)->list[str]`。Task 4が使う。

約束（verbatim）: 保存先は`<app>/Commands/PythonCommands/<stem>.py`と同名`.blockly.json`。`status`は`"saved"`／`"failed"`の2値。`py_rel`・`json_rel`はappからの`/`区切り相対。上書きは許す（作者本人の下書きのため退避は作らない）。末尾改行が無ければ足す。`os.chdir`しない。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_blockly_save.py`を新規作成する。内容は以下全文：

```python
"""blockly_save（.py＋.blockly.json保存）の検証。実機・GUIなしで回す。"""

from __future__ import annotations

import json
from pathlib import Path

from services import blockly_save


def good_code() -> str:
    return (
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        'class BlocklyCmd(PythonCommand):\n'
        '    NAME = "ブロック作成"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.press(Button.A)\n"
    )


def good_ws() -> str:
    return json.dumps({"blocks": {"languageVersion": 0, "blocks": []}})


def make_app(base: Path) -> Path:
    app = base / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    return app


def test_save_roundtrip(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    res = blockly_save.save_blockly(app, "MyBlock", good_ws(), good_code())
    assert res.status == "saved"
    assert res.py_rel == "Commands/PythonCommands/MyBlock.py"
    assert res.json_rel == "Commands/PythonCommands/MyBlock.blockly.json"
    text = (app / res.py_rel).read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert (app / res.json_rel).is_file()
    assert blockly_save.list_blockly(app) == ["MyBlock"]


def test_overwrite_allowed(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    assert blockly_save.save_blockly(app, "MyBlock", good_ws(), good_code()).status == "saved"
    res = blockly_save.save_blockly(app, "MyBlock", good_ws(), good_code())
    assert res.status == "saved"


def test_bad_stem_fails_without_files(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    res = blockly_save.save_blockly(app, "my-pack", good_ws(), good_code())
    assert res.status == "failed"
    assert list((app / "Commands" / "PythonCommands").iterdir()) == []


def test_bad_code_fails_without_files(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    res = blockly_save.save_blockly(app, "MyBlock", good_ws(), "from . import foo\n")
    assert res.status == "failed"
    assert list((app / "Commands" / "PythonCommands").iterdir()) == []
```

- [ ] **Step 2: REDを確認する**

Run: `uv run --frozen pytest tests/test_blockly_save.py -q`
Expected: FAIL（`services.blockly_save`が無いためcollection error）。

- [ ] **Step 3: `services/blockly_save.py`を作成する**

内容は以下全文（tkinterなし。画面への通知は戻り値のmessageに載せ、呼び出し側がprintする）：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blockly編集結果の保存手順（GUI非依存）。

`.py`と`.blockly.json`を対で書く。`.blockly.json`は
`Utility.getModuleNames`（`.py`のみ走査）のため一覧を壊さない。
上書きは許す（作者本人の下書きのため退避は作らない）。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from core import blockly_validate
from loguru import logger

PY_DIR_REL = "Commands/PythonCommands"


@dataclass
class SaveResult:
    """保存の結果。statusは saved / failed。"""

    status: str
    message: str
    py_rel: str = ""
    json_rel: str = ""
    errors: list[str] = field(default_factory=list)


def _atomic_write(target: Path, text: str) -> None:
    """一時ファイル経由で書く。書けたらのみ置き換える。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp_blockly_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fp:
            fp.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_blockly(
    app_dir: str | Path,
    stem: str,
    workspace_json: str,
    python_code: str,
) -> SaveResult:
    """編集結果を保存する。検証に落ちたら何も書かずfailedで返す。"""
    errors = blockly_validate.validate_stem(stem)
    errors.extend(blockly_validate.validate_workspace_json(workspace_json))
    errors.extend(blockly_validate.validate_generated_code(python_code))
    if errors:
        return SaveResult(
            status="failed",
            message="保存できません:\n- " + "\n- ".join(errors),
            errors=errors,
        )
    app = Path(app_dir)
    py_rel = (PurePosixPath(PY_DIR_REL) / f"{stem}.py").as_posix()
    json_rel = (PurePosixPath(PY_DIR_REL) / f"{stem}.blockly.json").as_posix()
    code = python_code if python_code.endswith("\n") else python_code + "\n"
    try:
        _atomic_write(app / PurePosixPath(py_rel).as_posix(), code)
        _atomic_write(app / PurePosixPath(json_rel).as_posix(), workspace_json)
    except OSError as e:
        logger.warning(f"Blockly保存に失敗: {e}")
        return SaveResult(status="failed", message=f"保存できません: {e}", errors=[str(e)])
    logger.info(f"Blockly保存: {py_rel}")
    return SaveResult(
        status="saved",
        message=f"保存しました: {py_rel}",
        py_rel=py_rel,
        json_rel=json_rel,
    )


def list_blockly(app_dir: str | Path) -> list[str]:
    """`.blockly.json`を持つ保存名の一覧を名前順で返す。"""
    base = Path(app_dir) / PurePosixPath(PY_DIR_REL).as_posix()
    if not base.is_dir():
        return []
    return sorted(p.name[: -len(".blockly.json")] for p in base.glob("*.blockly.json"))
```

- [ ] **Step 4: GREENとgateを確認する**

Run: `uv run --frozen pytest tests/test_blockly_save.py -q`
Expected: PASS（4 passed）。

Run: `ruff check`＋`format --check`＋`mypy`を`SerialController/services/blockly_save.py tests/test_blockly_save.py`に掛ける
Expected: すべてPASS。直しは本Taskのファイルに限定する。

---

### Task 4: 編集画面（JS＋HTML）＋`ui/blockly_editor.py`＋メニュー配線＋文書

**Files:**
- Create: `SerialController/assets/blockly/pokecon_blocks.js`
- Create: `SerialController/assets/blockly/editor.html`
- Create: `SerialController/ui/blockly_editor.py`
- Modify: `SerialController/Menubar.py`（「Blocklyエディタ...」1項目＋2メソッド＋`closeAll`での停止。ほかは触らない）
- Create: `docs/BLOCKLY_EDITOR.md`

**Interfaces:**
- Consumes: Task 1の資産、Task 3の`save_blockly`、既存`reloadCommands`（選択復元・実行中拒否を持つ）・`runner.is_busy()`。
- Produces: ブラウザ編集→`/save`保存→終了時リロードの往復。headlessテストは書かない。手動手順（Step 5）で確認する。

約束: サーバスレッドからtkinterを触らない（保存ハンドラはファイル書きのみ。リロードはエディタを閉じた後のGUIスレッドで行う）。`webbrowser.open`は標準ライブラリ。待ち受けは`127.0.0.1`の空きポートのみ。生成コードはダブルクォート＋末尾改行（`ruff format`用）。字下げは`pythonGenerator.INDENT='    '`と`prefixLines(body,'    ')`の両方を使う（spike確定）。

- [ ] **Step 1: `pokecon_blocks.js`を作成する**

`SerialController/assets/blockly/pokecon_blocks.js`を以下の内容で新規作成する（spikeの`probe.js`からブラウザ用に起こしたもの。`require`は使わず、`<script>`で読んだ大域`Blockly`・`pythonGenerator`を使う）：

```js
// PokeCon用ブロック定義と生成器（ブラウザ用）。
// 対応：program（NAME＋DO）、press（ボタン＋長さ＋待ち）、wait（秒）。
// 繰り返し・条件は標準ブロック（controls_repeat等）を使う。
(function () {
  'use strict';

  function pyStr(s) {
    return '"' + String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
  }

  Blockly.defineBlocksWithJsonArray([
    {
      type: 'pokecon_program',
      message0: 'プログラム %1 %2',
      args0: [
        { type: 'field_input', name: 'NAME', text: 'ブロック作成' },
        { type: 'input_statement', name: 'DO' },
      ],
      colour: 230,
    },
    {
      type: 'pokecon_press',
      message0: '%1 を押す 長さ %2 待ち %3',
      args0: [
        {
          type: 'field_dropdown',
          name: 'BUTTON',
          options: [
            ['Y', 'Y'], ['B', 'B'], ['A', 'A'], ['X', 'X'],
            ['L', 'L'], ['R', 'R'], ['ZL', 'ZL'], ['ZR', 'ZR'],
            ['MINUS', 'MINUS'], ['PLUS', 'PLUS'],
            ['LCLICK', 'LCLICK'], ['RCLICK', 'RCLICK'],
            ['HOME', 'HOME'], ['CAPTURE', 'CAPTURE'],
          ],
        },
        { type: 'field_number', name: 'DURATION', value: 0.1, min: 0, max: 10 },
        { type: 'field_number', name: 'WAIT', value: 0.1, min: 0, max: 60 },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 160,
    },
    {
      type: 'pokecon_wait',
      message0: '%1 秒待つ',
      args0: [{ type: 'field_number', name: 'SEC', value: 0.5, min: 0, max: 3600 }],
      previousStatement: null,
      nextStatement: null,
      colour: 160,
    },
  ]);

  // リポジトリは4スペース字下げ（ruff format）。既定の2スペースのままでは通らない。
  pythonGenerator.INDENT = '    ';

  pythonGenerator.forBlock['pokecon_program'] = function (block, generator) {
    var name = block.getFieldValue('NAME');
    var body = generator.statementToCode(block, 'DO');
    // statementToCodeだけ・prefixLinesだけの片方では字下げが壊れる（spike確定）。
    // statementToCodeはINDENT分（4）付きで返すため、さらに4を足してdo()の中（8）に寄せる。
    // 空のときはpassを8字下げで補う（空のdefは文法違反のため）。
    var inner = body ? generator.prefixLines(body, '    ') : '        pass\n';
    return (
      'from Commands.Keys import Button\n' +
      'from Commands.PythonCommandBase import PythonCommand\n' +
      '\n\n' +
      'class BlocklyCmd(PythonCommand):\n' +
      '    NAME = ' +
      pyStr(name) +
      '\n\n' +
      '    def do(self) -> None:\n' +
      inner
    );
  };
  };

  pythonGenerator.forBlock['pokecon_press'] = function (block) {
    var btn = block.getFieldValue('BUTTON');
    var dur = block.getFieldValue('DURATION');
    var wait = block.getFieldValue('WAIT');
    return 'self.press(Button.' + btn + ', duration=' + dur + ', wait=' + wait + ')\n';
  };

  pythonGenerator.forBlock['pokecon_wait'] = function (block) {
    return 'self.wait(' + block.getFieldValue('SEC') + ')\n';
  };
})();
```

`class BlocklyCmd`の名は固定でよい（`CommandLoader`はファイル内の`NAME`空でないクラスを拾うため、利用者が変えても動く）。

- [ ] **Step 2: `editor.html`を作成する**

`SerialController/assets/blockly/editor.html`を以下の内容で新規作成する（CDN禁止。同じフォルダの相対のみ読む。日本語UI）：

```html
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>Blocklyエディタ</title>
<script src="./blockly_compressed.js"></script>
<script src="./blocks_compressed.js"></script>
<script src="./python_compressed.js"></script>
<script src="./msg/ja.js"></script>
<script src="./pokecon_blocks.js"></script>
<style>
html, body { height: 100%; margin: 0; }
#bar { padding: 8px; display: flex; gap: 8px; align-items: center; }
#ws { height: calc(100% - 48px); width: 100%; }
#status { color: #333; }
</style>
</head>
<body>
<div id="bar">
<label>保存名 <input id="stem" value="MyBlock"></label>
<button id="save">保存</button>
<span id="status"></span>
</div>
<div id="ws"></div>
<script>
(function () {
  'use strict';
  var ws = Blockly.inject('ws', {
    toolbox: {
      kind: 'categoryToolbox',
      contents: [
        {
          kind: 'category', name: 'PokeCon',
          contents: [
            { kind: 'block', type: 'pokecon_program' },
            { kind: 'block', type: 'pokecon_press' },
            { kind: 'block', type: 'pokecon_wait' },
          ],
        },
        {
          kind: 'category', name: '標準',
          contents: [
            { kind: 'block', type: 'controls_repeat' },
            { kind: 'block', type: 'controls_if' },
            { kind: 'block', type: 'math_number' },
          ],
        },
      ],
    },
  });
  function setStatus(s) { document.getElementById('status').textContent = s; }
  document.getElementById('save').addEventListener('click', function () {
    var stem = document.getElementById('stem').value;
    var state = Blockly.serialization.workspaces.save(ws);
    var code = pythonGenerator.workspaceToCode(ws);
    fetch('./save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stem: stem, workspaceJson: JSON.stringify(state), pythonCode: code }),
    }).then(function (r) { return r.json(); }).then(function (j) {
      setStatus(j.message || (j.ok ? '保存しました' : '保存できません'));
    }).catch(function (e) { setStatus('保存できません: ' + e); });
  });
})();
</script>
</body>
</html>
```

- [ ] **Step 3: `ui/blockly_editor.py`を作成する**

内容は以下全文（サーバスレッドからtkinterを触らない。保存はservices、表示はprint＋messagebox、再読込は閉じた後のGUIスレッド）：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blocklyエディタの起動と保存受け口（tkinter）。

実保存は services.blockly_save が持ち、ここでは配信・起動・
終了時の作り直しだけにする。保存ハンドラ（サーバスレッド）では
tkinterを触らず、ファイル書きの結果だけをJSONで返す。再読込は
エディタを閉じた後のGUIスレッドで行う（実行中は reloadCommands
側も塞いでいるが、入口でも断つ）。
"""

from __future__ import annotations

import functools
import http.server
import json
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

import WindowUtils
from services import blockly_save

_BLOCKLY_DIR = Path(WindowUtils.APP_DIR) / "assets" / "blockly"

_server: http.server.ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None


class _Handler(http.server.SimpleHTTPRequestHandler):
    """静的配信＋`/save`受け口。ログは出さない。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(_BLOCKLY_DIR), **kwargs)

    def log_message(self, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        if self.path.rstrip("/").endswith("/save") or self.path == "/save":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                self._reply(False, f"保存できません: {e}")
                return
            res = blockly_save.save_blockly(
                WindowUtils.APP_DIR,
                str(payload.get("stem", "")),
                str(payload.get("workspaceJson", "")),
                str(payload.get("pythonCode", "")),
            )
            print(res.message)
            self._reply(res.status == "saved", res.message)
            return
        self.send_error(404)

    def _reply(self, ok: bool, message: str) -> None:
        body = json.dumps({"ok": ok, "message": message}, ensure_ascii=False).encode(
            "utf-8"
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _stop_server() -> None:
    """待ち受けを止める。何度呼んでもよい。"""
    global _server, _thread
    server, _server = _server, None
    if server is not None:
        server.shutdown()
        server.server_close()
    thread, _thread = _thread, None
    if thread is not None:
        thread.join(timeout=5.0)


def stop_blockly_editor() -> None:
    """Menubar.closeAllから呼ぶ。開きっぱなしでの終了を防ぐ。"""
    _stop_server()


def open_blockly_editor(
    root: Any,
    *,
    is_busy: Callable[[], bool],
    reload_commands: Callable[[], None],
) -> None:
    """エディタを開く。終わったら一覧を作り直す。"""
    import tkinter as tk
    import tkinter.messagebox as tkmsg

    if is_busy():
        print("実行中はBlocklyエディタを開けません")
        tkmsg.showwarning("Blockly", "実行中はBlocklyエディタを開けません", parent=root)
        return
    _stop_server()
    handler = functools.partial(_Handler)
    global _server, _thread
    _server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = _server.server_address[1]
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    url = f"http://127.0.0.1:{port}/editor.html"
    webbrowser.open(url)

    win = tk.Toplevel(root)
    win.title("Blocklyエディタ")
    win.resizable(False, False)
    tk.Label(win, text="ブラウザで編集し、保存ボタンで保存します。").pack(padx=10, pady=10)
    tk.Label(win, text=url).pack(padx=10, pady=(0, 10))

    def on_close() -> None:
        _stop_server()
        try:
            win.destroy()
        except Exception:
            pass
        if not is_busy():
            reload_commands()

    win.protocol("WM_DELETE_WINDOW", on_close)
```

`root: Any`にしているのは既存`script_pack_dialogs`と同様、`tk.Misc`のstub不整合を避けるため。

- [ ] **Step 4: Menubarへ1項目を足す**

`SerialController/Menubar.py`の`AssignMenuCommand`末尾（スクリプトの削除の後）へ以下を足す：

```python
        self.menu_command.add(
            "command", command=self.OpenBlocklyEditor, label="Blocklyエディタ..."
        )
```

次に`OpenScriptUninstall`の後へ以下を足す：

```python
    def OpenBlocklyEditor(self) -> None:
        """Blocklyエディタを開く。実手順は ui.blockly_editor。"""
        from ui import blockly_editor

        blockly_editor.open_blockly_editor(
            self.root,
            is_busy=lambda: self.app.runner.is_busy(),
            reload_commands=self.app.reloadCommands,
        )
```

次に`closeAll`の対象タプルへ停止を足す。`for name in (...)`の前に以下を足す：

```python
        try:
            from ui import blockly_editor

            blockly_editor.stop_blockly_editor()
        except Exception:
            pass
```

メソッド内の遅延importにするのは、Menubarのimport時にui配下を引き込んで循環にするのを避けるため（既存のOpenScriptInstallと同様）。

- [ ] **Step 5: 文書を書く**

`docs/BLOCKLY_EDITOR.md`を以下の内容で新規作成する（内側にフェンスを入れ子にしない。検証行は単行で書く）：

```markdown
# Blocklyエディタ（TODO-20基盤）

ブラウザで操作を組み立て、Pythonコマンドとして保存する。生成はBlockly→Python片方向のみ（逆変換なし）。

使い方： メニュー→コマンド→「Blocklyエディタ...」→ブラウザで編集→保存ボタン→エディタ小窓を閉じると一覧に反映（実行中は開けない・作り直さない）。

保存物： `Commands/PythonCommands/<保存名>.py`と同名`.blockly.json`の対。保存名は英字・数字・`_`（例: MyBlock）。`.blockly.json`は一覧を壊さない（`.py`のみ走査）。

ブロック： program（NAME＋DO）、press（ボタン14種＋長さ＋待ち）、wait（秒）。繰り返し・条件は標準ブロックを使う。画像認識ブロックはTODO-22で後付け。

資産： `SerialController/assets/blockly/`にvendored（Blockly 13.2.1、CDN禁止）。VERSIONに版を記録。入手元は https://registry.npmjs.org/blockly/-/blockly-13.2.1.tgz から該当5点のみ（手順はplan参照）。

検証： `uv run --frozen pytest tests/test_blockly_assets.py tests/test_blockly_validate.py tests/test_blockly_save.py -q`
```

- [ ] **Step 6: gateを確認する**

Run: `ruff check`＋`format --check`＋`mypy`を`SerialController/ui/blockly_editor.py SerialController/Menubar.py`に掛ける
Expected: すべてPASS。次に`uv run --frozen python tools/check_core.py`（ui→Window禁止に触れないこと）と`python tools/check_user_api.py`が緑であること。

- [ ] **Step 7: 実機で手動確認する（GUI＋ブラウザのため自動化しない）**

手順と期待結果（報告に転記すること）：
1. `python SerialController/Window.py`を起動し、メニュー→コマンドに「Blocklyエディタ...」がある
2. 開くとブラウザに編集画面が出る（PokeCon＋標準の2分類、日本語）
3. press（A）＋wait（0.5）＋controls_repeat（B）を組んで保存名MyBlockで保存→「保存しました」と出る
4. `Commands/PythonCommands/MyBlock.py`と`MyBlock.blockly.json`ができる。`.py`はダブルクォート・4スペース・末尾改行で`ruff format --check`が通る
5. エディタ小窓を閉じると一覧に載り、選んで実行できる（実行中は開けないこと）
6. 保存名に`my-pack`を入れると「保存名は英字・数字・`_`」で断られる
