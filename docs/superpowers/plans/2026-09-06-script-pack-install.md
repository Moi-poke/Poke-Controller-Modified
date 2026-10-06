# スクリプト配布の導入・梱包（zip ops＋services＋GUI＋CLI）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 配布zipの検証・導入・削除・梱包を、GUI（メニュー）とCLIの両入口で使えるようにする。

**Architecture:** zipの安全操作は tkinter-free の `core/pack_zip.py`、配置・記録・退避の手順は tkinter-free の `services/script_pack.py`（Window は `print` を渡さない純粋返値方式）に置く。画面は `ui/script_pack_dialogs.py` の modal 対話に閉じ、実操作後に既存 `reloadCommands()` を呼ぶ。`core/CommandVision.py` の読み込み挙動は変えない。

**Tech Stack:** Python 3.12 / 標準ライブラリ（zipfile・ast・tempfile・shutil・argparse）/ tkinter（ui のみ）/ pytest, ruff, mypy, bounds, userapi

**Spec:** チャット合意 — zip配置は `pokecon.json`＋`PythonCommands/<entry>`＋`Template/<name>/...` をルートに持つ v0（前plan）。entry は単一 `.py` のみ。McuCommands 配布は v0 対象外。entry のフォルダ区切りは import 可能な名前のみ。

## Global Constraints

- `SerialController/` を `sys.path` 前提の絶対 import、相対 import 禁止。
- `core/` は tkinter・pygubu・アプリ層 import 禁止。新規は標準ライブラリ＋`core.*` のみ。
- `services/` は tkinter・画面部品 import 禁止。`core.*`・標準ライブラリ・loguru は可。
- `ui/` は `Window` 本体 import 禁止。tkinter・`services`・`WindowUtils`（既存 pattern）可。
- `Commands.*` の公開面を変えない。`settings*.ini` は触らない。
- Python floor 3.12（pin 3.12.10）。`X | None`・builtin generics 可。Ruff `I`＋`UP006/007/008/035`。
- 実行中の導入・削除・リロードは断る（既存 `reloadCommands` と同じ規則）。
- Gate: `ruff check`＋`ruff format --check`＋`mypy`＋`bounds`＋`userapi`＋`pytest` が全緑。
- コミットはユーザーの明示指示があるときのみ（stepにcommitを含めない）。

## File Structure

- `SerialController/core/pack_zip.py`（新規）— zip生成・安全展開・staged検証・entry import走査。`pack_manifest` の利用者。
- `SerialController/core/pack_manifest.py`（追記）— `validate_entry_segments(entry)` を追加のみ。既存関数の挙動は変えない。
- `SerialController/services/script_pack.py`（新規）— 導入・更新・削除・一覧・退避・記録。UIなし。
- `SerialController/ui/script_pack_dialogs.py`（新規）— modal対話3関数。実操作は services。
- `SerialController/Menubar.py`（追記）— 「スクリプトの導入...」「スクリプトの削除...」の2項目と対応メソッド。
- `SerialController/ScriptPackTool.py`（新規）— CLI（pack/check/install/uninstall/list）。
- `docs/PACK_FORMAT_v0.md`（追記）— zip配置・導入挙動・CLIの節。
- Tests: `tests/test_pack_zip.py`（新規）、`tests/test_script_pack.py`（新規）、`tests/test_script_pack_tool.py`（新規）、`tests/test_pack_manifest.py`（2テスト追記）。

---

### Task 1: `core/pack_zip.py`＋entry区切り検査＋テスト

**Files:**
- Create: `SerialController/core/pack_zip.py`
- Modify: `SerialController/core/pack_manifest.py`（`validate_entry_segments` を末尾へ追加のみ）
- Test: `tests/test_pack_zip.py`（新規）
- Modify: `tests/test_pack_manifest.py`（2テスト追記のみ）

**Interfaces:**
- Consumes: 前planの `pack_manifest.PackManifest/load_manifest/validate_package_layout`。
- Produces: `pack_zip.PackError`／`scan_entry_imports(path)->tuple[list[str],list[str]]`／`safe_extract(zip,dest)->list[str]`／`validate_staged(dir)->StagedReport(.manifest/.errors/.warnings/.ok)`／`create_pack(src,out)->list[str]`。Task 2 が使う。

定数（verbatim）: `MAX_ZIP_FILES = 1000`、`MAX_FILE_BYTES = 16 * 1024 * 1024`、`MAX_TOTAL_BYTES = 64 * 1024 * 1024`、`ZIP_MANIFEST = "pokecon.json"`。import走査の許可集合は `tools/check_user_api.py` と同じ値（`Keys/PythonCommandBase/McuCommandBase/WakeLink/CommandVision`＋`cv2/numpy/PIL/pandas/scipy/requests/yaml/loguru/icecream/deprecated/pynput/serial/pygubu/matplotlib/pyaudio`）を複写し、出典コメントを付ける。

- [ ] **Step 1: manifest 側の追加テストを書く（REDはまだ走らせない）**

`tests/test_pack_manifest.py` の末尾に以下を追加する：

```python
def test_entry_segments_reject_hyphen_dir() -> None:
    from core.pack_manifest import validate_entry_segments

    errors = validate_entry_segments("my-pack/MyPack.py")
    assert any("フォルダ名" in e for e in errors)


def test_entry_segments_accept_flat_and_ident_dir() -> None:
    from core.pack_manifest import validate_entry_segments

    assert validate_entry_segments("MyPack.py") == []
    assert validate_entry_segments("my_pack/MyPack.py") == []
```

- [ ] **Step 2: zip のテストを書く**

`tests/test_pack_zip.py` を新規作成する。内容は以下全文：

```python
"""pack_zip（生成・安全展開・staged検証）の検証。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from core import pack_zip


def make_staged(base: Path) -> dict:
    data = {
        "name": "my-pack",
        "version": "1.0.0",
        "author": "alice",
        "description": "sample",
        "entry": "MyPack.py",
        "minAppVersion": "4.0.0",
        "templates": ["my-pack/a.png"],
    }
    (base / "PythonCommands").mkdir(parents=True)
    (base / "Template" / "my-pack").mkdir(parents=True)
    (base / "pokecon.json").write_text(json.dumps(data), encoding="utf-8")
    (base / "PythonCommands" / "MyPack.py").write_text(
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n",
        encoding="utf-8",
    )
    (base / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return data


def test_roundtrip_create_extract_validate(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    make_staged(src)
    out = tmp_path / "my-pack.zip"
    names = pack_zip.create_pack(src, out)
    assert "pokecon.json" in names
    dest = tmp_path / "dest"
    dest.mkdir()
    files = pack_zip.safe_extract(out, dest)
    assert "PythonCommands/MyPack.py" in files
    report = pack_zip.validate_staged(dest)
    assert report.ok
    assert report.manifest is not None and report.manifest.name == "my-pack"


def test_dotdot_entry_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "evil.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.py", "# evil\n")
    with pytest.raises(pack_zip.PackError):
        pack_zip.safe_extract(bad, tmp_path / "out")


def test_absolute_entry_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "abs.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("/abs/a.png", "x")
    with pytest.raises(pack_zip.PackError):
        pack_zip.safe_extract(bad, tmp_path / "out")


def test_symlink_entry_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "link.zip"
    info = zipfile.ZipInfo("link.png")
    info.external_attr = (0o120777 << 16)
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr(info, "target")
    with pytest.raises(pack_zip.PackError):
        pack_zip.safe_extract(bad, tmp_path / "out")


def test_total_cap_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pack_zip, "MAX_TOTAL_BYTES", 10)
    src = tmp_path / "src"
    src.mkdir()
    make_staged(src)
    out = tmp_path / "my-pack.zip"
    pack_zip.create_pack(src, out)
    with pytest.raises(pack_zip.PackError):
        pack_zip.safe_extract(out, tmp_path / "out")


def test_scan_entry_imports(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    good.write_text("from Commands.Keys import Button\n", encoding="utf-8")
    errors, _ = pack_zip.scan_entry_imports(good)
    assert errors == []
    rel = tmp_path / "rel.py"
    rel.write_text("from . import foo\n", encoding="utf-8")
    errors, _ = pack_zip.scan_entry_imports(rel)
    assert errors != []
    unknown = tmp_path / "unknown.py"
    unknown.write_text("from Commands.Unknown import X\n", encoding="utf-8")
    errors, _ = pack_zip.scan_entry_imports(unknown)
    assert errors != []
    third = tmp_path / "third.py"
    third.write_text("import somelib_xyz\n", encoding="utf-8")
    _, warnings = pack_zip.scan_entry_imports(third)
    assert warnings != []


def test_staged_with_hyphen_dir_fails(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    data = make_staged(src)
    data["entry"] = "my-pack/MyPack.py"
    data["templates"] = ["my-pack/a.png"]
    (src / "pokecon.json").write_text(json.dumps(data), encoding="utf-8")
    report = pack_zip.validate_staged(src)
    assert not report.ok
```

- [ ] **Step 3: REDを確認する**

Run: `uv run --frozen pytest tests/test_pack_zip.py tests/test_pack_manifest.py -q`
Expected: FAIL（`core.pack_zip` が無いため collection error＋2追加テスト失敗）。

- [ ] **Step 4: manifest へ `validate_entry_segments` を追加する**

`SerialController/core/pack_manifest.py` の末尾（`validate_package_layout` の後）に以下を追加する：

```python
_SEGMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def validate_entry_segments(entry: str) -> list[str]:
    """entry の各区切りが import 可能な名前かを検査する。

    CommandLoader は受け取った相対パスをそのまま import 名にするため、
    `my-pack/MyPack.py` のような区切りは読み込み不能になる。配置前に
    ここで断り、使える書き方（例: my_pack/）を案内する。
    """
    if not isinstance(entry, str) or not entry.strip():
        return ["`entry` を書いてください"]
    parts = [p for p in entry.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        return ["`entry` が空です"]
    errors: list[str] = []
    for part in parts[:-1]:
        if _SEGMENT_RE.fullmatch(part) is None:
            errors.append(
                f"`entry` のフォルダ名は英字・数字・`_`にしてください: {part!r}"
                "（例: my_pack/）"
            )
    stem = parts[-1]
    if not stem.lower().endswith(".py"):
        errors.append("`entry` の末尾は `.py` にしてください")
    elif _SEGMENT_RE.fullmatch(stem[:-3]) is None:
        errors.append(f"`entry` のファイル名は英字・数字・`_`にしてください: {stem!r}")
    return errors
```

- [ ] **Step 5: `core/pack_zip.py` を作成する**

内容は以下全文（標準ライブラリ＋`core.pack_manifest` のみ）：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配布zipの生成と安全な展開（GUI非依存）。

zip配置 v0: ルート直下に `pokecon.json`＋`PythonCommands/<entry>`＋
`Template/<name>/...` を持つ。展開・検証・梱包の順に使う。

安全の考え方:
  zipは他人が作ったものとして扱う。絶対パス・`..`・ドライブ文字・
  シンボリックリンクは展開前に断り、件数と容量に上限を付ける。
  entry の import 走査の許可集合は `tools/check_user_api.py` と同じ値
  （あちらが正本。変えたらこちらも合わせる）。
"""

from __future__ import annotations

import ast
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from core import pack_manifest
from core.pack_manifest import PackManifest

ZIP_MANIFEST = "pokecon.json"
MAX_ZIP_FILES = 1000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024

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


class PackError(ValueError):
    """zipの形・容量・配置の異常。利用者にそのまま見せられる文面にする。"""


@dataclass
class StagedReport:
    """展開済み配置の検証結果。ok のときだけ manifest が入る。"""

    manifest: PackManifest | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.manifest is not None


def scan_entry_imports(entry_file: Path) -> tuple[list[str], list[str]]:
    """entry の import を走査する。(異常, 注意) を返す。

    異常（導入不可）: 相対 import・公開API面外の Commands 経路。
    注意（導入は通す）: 見知らぬトップレベル（導入先で動かない可能性）。
    """
    try:
        tree = ast.parse(
            Path(entry_file).read_text(encoding="utf-8"),
            filename=Path(entry_file).name,
        )
    except (OSError, UnicodeDecodeError) as e:
        return ([f"entry を読めません: {e}"], [])
    except SyntaxError as e:
        return ([f"entry の文法が壊れています（{e}）"], [])
    allowed_top = set(sys.stdlib_module_names) | _THIRD_PARTY | {"Commands"}
    errors: list[str] = []
    warnings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top not in allowed_top:
                    warnings.append(
                        f"未知の import があります: import {a.name}"
                        "（導入先で動かない可能性があります）"
                    )
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
                    warnings.append(
                        f"未知の import があります: from {node.module} import ..."
                        "（導入先で動かない可能性があります）"
                    )
    return (errors, warnings)


def _checked_rel(info: zipfile.ZipInfo) -> str | None:
    """zip1件の配置先（posix相対）を返す。置けないものは PackError。

    ディレクトリ項目は None を返す（作るだけで検証は要らない）。
    """
    raw = info.filename.replace("\\", "/")
    if raw.endswith("/"):
        return None
    parts = PurePosixPath(raw).parts
    if (
        not raw
        or raw.startswith("/")
        or PurePosixPath(raw).is_absolute()
        or ".." in parts
        or ":" in raw
    ):
        raise PackError(f"置けないパスです: {info.filename!r}")
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        raise PackError(f"シンボリックリンクは置けません: {info.filename!r}")
    return PurePosixPath(raw).as_posix()


def safe_extract(zip_path: str | Path, dest_dir: str | Path) -> list[str]:
    """zipを dest_dir へ安全に展開し、置いた相対の一覧を返す。"""
    dest = Path(dest_dir)
    files: list[str] = []
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ZIP_FILES:
            raise PackError(f"件数が多すぎます（上限 {MAX_ZIP_FILES} 件）")
        rels: list[tuple[zipfile.ZipInfo, str]] = []
        for info in infos:
            rel = _checked_rel(info)
            if rel is None:
                continue
            if info.file_size > MAX_FILE_BYTES:
                raise PackError(f"大きすぎるファイルがあります: {rel}")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise PackError(
                    f"合計が大きすぎます（上限 {MAX_TOTAL_BYTES // 1024 // 1024} MiB）"
                )
            rels.append((info, rel))
        for info, rel in rels:
            target = dest / PurePosixPath(rel).as_posix()
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            files.append(rel)
    return files


def validate_staged(staged_dir: str | Path) -> StagedReport:
    """展開済み配置を検証する。manifest・実体・区切り・import を見る。"""
    base = Path(staged_dir)
    try:
        manifest = pack_manifest.load_manifest(base / pack_manifest.MANIFEST_FILENAME)
    except ValueError as e:
        return StagedReport(manifest=None, errors=[str(e)])
    errors = pack_manifest.validate_package_layout(base)
    errors.extend(pack_manifest.validate_entry_segments(manifest.entry))
    warnings: list[str] = []
    entry_file = base / "PythonCommands" / PurePosixPath(manifest.entry).as_posix()
    if entry_file.is_file():
        import_errors, import_warnings = scan_entry_imports(entry_file)
        errors.extend(import_errors)
        warnings.extend(import_warnings)
    return StagedReport(manifest=manifest, errors=errors, warnings=warnings)


def create_pack(src_dir: str | Path, out_zip: str | Path) -> list[str]:
    """配置済みフォルダから配布zipを作り、収録名の一覧を返す。"""
    report = validate_staged(src_dir)
    if not report.ok or report.manifest is None:
        raise PackError("梱包できません:\n- " + "\n- ".join(report.errors))
    manifest = report.manifest
    base = Path(src_dir)
    targets = [PurePosixPath("PythonCommands") / manifest.entry] + [
        PurePosixPath("Template") / t for t in manifest.templates
    ]
    names: list[str] = [ZIP_MANIFEST]
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(base / ZIP_MANIFEST, ZIP_MANIFEST)
        for target in targets:
            arc = target.as_posix()
            zf.write(base / arc, arc)
            names.append(arc)
    return names
```

- [ ] **Step 6: GREENとgateを確認する**

Run: `uv run --frozen pytest tests/test_pack_zip.py tests/test_pack_manifest.py -q`
Expected: PASS（7＋11＝18 passed）。

Run: `uv run --frozen ruff check SerialController/core/pack_zip.py SerialController/core/pack_manifest.py tests/test_pack_zip.py tests/test_pack_manifest.py`、次に `ruff format --check`（同ファイル）、次に `mypy`（同ファイル）
Expected: すべてPASS。`ruff format` の直しは本Taskのファイルに限定する。

---

### Task 2: `services/script_pack.py`＋テスト

**Files:**
- Create: `SerialController/services/script_pack.py`
- Test: `tests/test_script_pack.py`

**Interfaces:**
- Consumes: Task 1 の `pack_zip.safe_extract/validate_staged/create_pack`、`pack_manifest`。
- Produces: `InstallResult/UninstallResult/InstalledRecord`／`install_zip(app,zip,*,allow_overwrite)->InstallResult`／`uninstall_package(app,name)->UninstallResult`／`list_installed(app)->list[InstalledRecord]`／`read_record(app,name)`。Task 3・4 が使う。

約束（verbatim）: 記録場所 `<app>/InstalledPacks/<name>.json`、退避場所 `<app>/InstalledPacks/.backup/<name>_<YYYYMMDD_HHMMSS>/...`（相対を保つ）。`status` は `"installed"`／`"confirm-overwrite"`／`"failed"` の3値。`files` は app からの `/` 区切り相対。既存版の同版再導入は `failed`、異版は `allow_overwrite` 無しで `confirm-overwrite`（`current_version` 付き）。`uninstall` は記録の無い名前に `failed`、消えた実体は警告文に含めて続行し、空になった配下フォルダだけ畳む（`PythonCommands/`・`Template/` 自体は残す）。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_script_pack.py` を新規作成する。内容は以下全文：

```python
"""script_pack（導入・更新・削除・一覧）の検証。実機・GUIなしで回す。"""

from __future__ import annotations

import json
from pathlib import Path

from core import pack_zip
from services import script_pack


def make_src(base: Path, version: str = "1.0.0") -> Path:
    src = base / "src"
    (src / "PythonCommands").mkdir(parents=True, exist_ok=True)
    (src / "Template" / "my-pack").mkdir(parents=True, exist_ok=True)
    data = {
        "name": "my-pack",
        "version": version,
        "author": "alice",
        "description": "sample",
        "entry": "MyPack.py",
        "minAppVersion": "4.0.0",
        "templates": ["my-pack/a.png"],
    }
    (src / "pokecon.json").write_text(json.dumps(data), encoding="utf-8")
    (src / "PythonCommands" / "MyPack.py").write_text(
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n",
        encoding="utf-8",
    )
    (src / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return src


def make_zip(base: Path, version: str = "1.0.0") -> Path:
    out = base / f"my-pack-{version}.zip"
    pack_zip.create_pack(make_src(base / f"v{version}", version), out)
    return out


def test_install_happy_path(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    res = script_pack.install_zip(app, make_zip(tmp_path, "1.0.0"))
    assert res.status == "installed"
    assert (app / "PythonCommands" / "MyPack.py").is_file()
    assert (app / "Template" / "my-pack" / "a.png").is_file()
    assert (app / "InstalledPacks" / "my-pack.json").is_file()
    assert [r.name for r in script_pack.list_installed(app)] == ["my-pack"]


def test_reinstall_same_version_fails(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    script_pack.install_zip(app, make_zip(tmp_path, "1.0.0"))
    res = script_pack.install_zip(app, make_zip(tmp_path, "1.0.0"))
    assert res.status == "failed"


def test_update_needs_confirm_then_backs_up(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    script_pack.install_zip(app, make_zip(tmp_path, "1.0.0"))
    (app / "Template" / "my-pack" / "a.png").write_bytes(b"old-bytes")
    res = script_pack.install_zip(app, make_zip(tmp_path, "2.0.0"))
    assert res.status == "confirm-overwrite"
    assert res.current_version == "1.0.0"
    res2 = script_pack.install_zip(app, make_zip(tmp_path, "2.0.0"), allow_overwrite=True)
    assert res2.status == "installed"
    assert res2.backup_rel is not None
    assert (app / res2.backup_rel / "Template/my-pack/a.png").read_bytes() == b"old-bytes"
    assert (app / "Template" / "my-pack" / "a.png").read_bytes() == b"\x89PNG\r\n\x1a\n"


def test_uninstall_removes_files_and_record(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    script_pack.install_zip(app, make_zip(tmp_path, "1.0.0"))
    res = script_pack.uninstall_package(app, "my-pack")
    assert res.status == "removed"
    assert not (app / "PythonCommands" / "MyPack.py").exists()
    assert not (app / "InstalledPacks" / "my-pack.json").exists()
    assert script_pack.list_installed(app) == []
    again = script_pack.uninstall_package(app, "no-such-pack")
    assert again.status == "failed"
```

`UninstallResult.status` は `"removed"`／`"failed"` の2値とすること（テストが `removed` を要求する）。

- [ ] **Step 2: REDを確認する**

Run: `uv run --frozen pytest tests/test_script_pack.py -q`
Expected: FAIL（`services.script_pack` が無いため collection error）。

- [ ] **Step 3: `services/script_pack.py` を作成する**

内容は以下全文（tkinterなし。画面への通知は戻り値の message に載せ、呼び出し側が print する）：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自作スクリプト配布の導入・更新・削除・一覧の手順（GUI非依存）。

実ファイル操作だけを持ち、確認ダイアログや一覧表示は持たない。
上書きの要否は戻り値の status で返し、聞くのは呼び出し側（GUI・CLI）
の仕事にする。ヘッドレスの検証でそのまま動く。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from core import pack_manifest, pack_zip
from loguru import logger

RECORD_DIRNAME = "InstalledPacks"
BACKUP_DIRNAME = ".backup"


@dataclass
class InstallResult:
    """導入の結果。status は installed / confirm-overwrite / failed。"""

    status: str
    message: str
    manifest: pack_manifest.PackManifest | None = None
    files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    backup_rel: str | None = None
    current_version: str | None = None


@dataclass
class UninstallResult:
    """削除の結果。status は removed / failed。"""

    status: str
    message: str
    removed: list[str] = field(default_factory=list)


@dataclass
class InstalledRecord:
    """導入記録。InstalledPacks/<name>.json の中身そのもの。"""

    name: str
    version: str
    author: str
    description: str
    entry: str
    minAppVersion: str
    templates: list[str]
    files: list[str]
    installed_at: str
    zip_name: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @staticmethod
    def from_json(text: str) -> InstalledRecord:
        data = json.loads(text)
        return InstalledRecord(
            name=str(data["name"]),
            version=str(data["version"]),
            author=str(data["author"]),
            description=str(data["description"]),
            entry=str(data["entry"]),
            minAppVersion=str(data["minAppVersion"]),
            templates=[str(v) for v in data["templates"]],
            files=[str(v) for v in data["files"]],
            installed_at=str(data["installed_at"]),
            zip_name=str(data["zip_name"]),
        )


def _record_path(app_dir: Path, name: str) -> Path:
    return app_dir / RECORD_DIRNAME / f"{name}.json"


def read_record(app_dir: str | Path, name: str) -> InstalledRecord | None:
    """導入記録を読む。無ければ None（壊れていれば警告して None）。"""
    path = _record_path(Path(app_dir), name)
    if not path.is_file():
        return None
    try:
        return InstalledRecord.from_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as e:
        logger.warning(f"導入記録を読めません: {path}（{e}）")
        return None


def list_installed(app_dir: str | Path) -> list[InstalledRecord]:
    """導入済みの一覧を名前順で返す。"""
    base = Path(app_dir) / RECORD_DIRNAME
    if not base.is_dir():
        return []
    records: list[InstalledRecord] = []
    for path in sorted(base.glob("*.json")):
        rec = read_record(base.parent, path.stem)
        if rec is not None:
            records.append(rec)
    return records


def install_zip(
    app_dir: str | Path, zip_path: str | Path, *, allow_overwrite: bool = False
) -> InstallResult:
    """配布zipを導入する。上書きが必要なら confirm-overwrite で返す。"""
    app = Path(app_dir)
    with tempfile.TemporaryDirectory(prefix="pokecon_pack_") as tmp:
        staged = Path(tmp) / "staged"
        staged.mkdir()
        try:
            pack_zip.safe_extract(zip_path, staged)
        except pack_zip.PackError as e:
            return InstallResult(status="failed", message=f"zip を開けません: {e}")
        report = pack_zip.validate_staged(staged)
        if not report.ok or report.manifest is None:
            return InstallResult(
                status="failed",
                message="配布物が不正です:\n- " + "\n- ".join(report.errors),
            )
        manifest = report.manifest
        old = read_record(app, manifest.name)
        if old is not None and not allow_overwrite:
            if old.version == manifest.version:
                return InstallResult(
                    status="failed",
                    manifest=manifest,
                    message=f"すでに同じ版（{old.version}）が導入済みです: {manifest.name}",
                )
            return InstallResult(
                status="confirm-overwrite",
                manifest=manifest,
                warnings=list(report.warnings),
                current_version=old.version,
                message=(
                    f"{manifest.name} は版 {old.version} が導入済みです。"
                    f"版 {manifest.version} に更新します。上書きしてよいですか。"
                ),
            )
        rels = ["PythonCommands/" + PurePosixPath(manifest.entry).as_posix()] + [
            "Template/" + PurePosixPath(t).as_posix() for t in manifest.templates
        ]
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup_root = app / RECORD_DIRNAME / BACKUP_DIRNAME / f"{manifest.name}_{stamp}"
        backed = False
        for rel in rels:
            existed = app / PurePosixPath(rel).as_posix()
            if existed.is_file():
                target = backup_root / PurePosixPath(rel).as_posix()
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(existed, target)
                backed = True
        for rel in rels:
            src = staged / PurePosixPath(rel).as_posix()
            dst = app / PurePosixPath(rel).as_posix()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        record = InstalledRecord(
            name=manifest.name,
            version=manifest.version,
            author=manifest.author,
            description=manifest.description,
            entry=manifest.entry,
            minAppVersion=manifest.minAppVersion,
            templates=list(manifest.templates),
            files=rels,
            installed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            zip_name=Path(zip_path).name,
        )
        rec_path = _record_path(app, manifest.name)
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        rec_path.write_text(record.to_json() + "\n", encoding="utf-8")
        logger.info(f"導入: {manifest.name} 版{manifest.version}（{len(rels)}件）")
        return InstallResult(
            status="installed",
            manifest=manifest,
            files=rels,
            warnings=list(report.warnings),
            backup_rel=backup_root.relative_to(app).as_posix() if backed else None,
            message=(
                f"導入しました: {manifest.name} 版{manifest.version}（{len(rels)}件）"
                + ("。上書き前の写しを残しました" if backed else "")
            ),
        )


def uninstall_package(app_dir: str | Path, name: str) -> UninstallResult:
    """導入記録に従って実体を消す。空になった配下フォルダだけ畳む。"""
    app = Path(app_dir)
    rec = read_record(app, name)
    if rec is None:
        return UninstallResult(status="failed", message=f"導入記録がありません: {name}")
    removed: list[str] = []
    missing: list[str] = []
    for rel in rec.files:
        path = app / PurePosixPath(rel).as_posix()
        if path.is_file():
            path.unlink()
            removed.append(rel)
        else:
            missing.append(rel)
    roots = {app / "PythonCommands", app / "Template"}
    for rel in rec.files:
        folder = (app / PurePosixPath(rel).as_posix()).parent
        while folder != app and folder not in roots and folder.is_dir():
            try:
                next(folder.iterdir())
                break
            except StopIteration:
                folder.rmdir()
                folder = folder.parent
    _record_path(app, name).unlink(missing_ok=True)
    logger.info(f"削除: {name}（{len(removed)}件）")
    message = f"削除しました: {name}（{len(removed)}件）"
    if missing:
        message += f"。見つからなかったもの: {', '.join(missing)}"
    return UninstallResult(status="removed", message=message, removed=removed)
```

- [ ] **Step 4: GREENとgateを確認する**

Run: `uv run --frozen pytest tests/test_script_pack.py -q`
Expected: PASS（4 passed）。

Run: `ruff check`＋`format --check`＋`mypy` を `SerialController/services/script_pack.py tests/test_script_pack.py` に掛ける
Expected: すべてPASS。直しは本Taskのファイルに限定する。

---

### Task 3: `ui/script_pack_dialogs.py`＋メニュー配線

**Files:**
- Create: `SerialController/ui/script_pack_dialogs.py`
- Modify: `SerialController/Menubar.py`（「スクリプトの導入...」「スクリプトの削除...」の2項目＋2メソッド。`closeAll` は触らない）

**Interfaces:**
- Consumes: Task 2 の `install_zip/uninstall_package/list_installed`、既存 `reloadCommands`（選択復元・実行中拒否を持つ）。
- Produces: modal対話3関数。実機確認は手動手順（Step 4）で行う。headlessテストは書かない。

約束: 実行中は入口で断る（`is_busy`）。成功・失敗の要点は `print`（LogPane行き）＋messagebox。確認文には manifest 要点＋注意（最大5件＋残り件数）を載せる。上書きは `allow_overwrite=True` で取り直す。

- [ ] **Step 1: `ui/script_pack_dialogs.py` を作成する**

内容は以下全文：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配布スクリプトの導入・削除の対話手順（tkinter）。

実ファイル操作は services.script_pack が持ち、ここでは選ぶ・確かめる・
知らせるだけにする。導入後は呼び出し側の reload_commands（既存の
reloadCommands を渡す）で一覧を作り直す。実行中の可否は呼び出し側の
is_busy で見る（reloadCommands 側も塞いでいるが、入口でも断つ）。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
from collections.abc import Callable
from tkinter import filedialog
from typing import Any

from services import script_pack


def _summary_text(manifest: Any, warnings: list[str]) -> str:
    """確認ダイアログに出す要点。注意は最大5件まで出す。"""
    lines = [
        f"名前: {manifest.name}",
        f"版: {manifest.version}",
        f"作者: {manifest.author}",
        f"説明: {manifest.description}",
        f"本体: PythonCommands/{manifest.entry}",
        f"画像: {len(manifest.templates)}件",
    ]
    for warning in warnings[:5]:
        lines.append(f"注意: {warning}")
    if len(warnings) > 5:
        lines.append(f"注意: ほか {len(warnings) - 5} 件")
    return "\n".join(lines)


def install_script_zip(
    root: tk.Misc,
    app_dir: str,
    *,
    is_busy: Callable[[], bool],
    reload_commands: Callable[[], None],
) -> None:
    """配布zipを選んで導入する。終わったら一覧を作り直す。"""
    if is_busy():
        print("実行中はスクリプトを導入できません")
        tkmsg.showwarning("導入", "実行中はスクリプトを導入できません", parent=root)
        return
    zippath = filedialog.askopenfilename(
        parent=root,
        title="配布zipを選ぶ",
        filetypes=[("PokeCon配布", "*.zip"), ("すべて", "*.*")],
    )
    if not zippath:
        return
    res = script_pack.install_zip(app_dir, zippath)
    if res.status == "failed" or res.manifest is None:
        print(f"導入できません: {res.message}")
        tkmsg.showerror("導入失敗", res.message, parent=root)
        return
    if res.status == "confirm-overwrite":
        body = (
            f"{res.manifest.name} は版 {res.current_version} が導入済みです。\n"
            f"版 {res.manifest.version} に更新します。上書きしてよいですか。\n\n"
            + _summary_text(res.manifest, res.warnings)
        )
        if not tkmsg.askyesno("上書き確認", body, parent=root):
            print("導入を取り消しました")
            return
        res = script_pack.install_zip(app_dir, zippath, allow_overwrite=True)
        if res.status != "installed" or res.manifest is None:
            print(f"導入できません: {res.message}")
            tkmsg.showerror("導入失敗", res.message, parent=root)
            return
    reload_commands()
    done = res.message
    if res.backup_rel is not None:
        done += f"\n上書き前の写し: {res.backup_rel}"
    print(done)
    tkmsg.showinfo("導入完了", done, parent=root)


def uninstall_script_dialog(
    root: tk.Misc,
    app_dir: str,
    *,
    is_busy: Callable[[], bool],
    reload_commands: Callable[[], None],
) -> None:
    """導入済みの一覧から選んで削除する小窓を開く。"""
    if is_busy():
        print("実行中はスクリプトを削除できません")
        tkmsg.showwarning("削除", "実行中はスクリプトを削除できません", parent=root)
        return
    if not script_pack.list_installed(app_dir):
        tkmsg.showinfo("削除", "導入済みパッケージはありません", parent=root)
        return
    win = tk.Toplevel(root)
    win.title("スクリプトの削除")
    win.transient(root)  # type: ignore[call-overload]  # CommandPalette.py と同じ理由（Misc 許容の stub 不整合）
    win.grab_set()
    win.resizable(False, False)
    records_box = tk.Listbox(win, width=48, height=10)
    records_box.pack(padx=10, pady=10)

    def refresh() -> None:
        records_box.delete(0, tk.END)
        for rec in script_pack.list_installed(app_dir):
            records_box.insert(tk.END, f"{rec.name} 版{rec.version}")

    def do_delete() -> None:
        selected = records_box.curselection()
        if not selected:
            return
        label = str(records_box.get(selected[0]))
        name = label.split(" ")[0]
        if not tkmsg.askyesno(
            "削除確認", f"{label} を削除します。よろしいですか。", parent=win
        ):
            return
        res = script_pack.uninstall_package(app_dir, name)
        if res.status != "removed":
            print(f"削除できません: {res.message}")
            tkmsg.showerror("削除失敗", res.message, parent=win)
            return
        print(res.message)
        reload_commands()
        refresh()

    buttons = ttk.Frame(win)
    buttons.pack(fill="x", padx=10, pady=(0, 10))
    ttk.Button(buttons, text="削除", command=do_delete).pack(side="left")
    ttk.Button(buttons, text="閉じる", command=win.destroy).pack(side="right")
    refresh()
    root.wait_window(win)
```

- [ ] **Step 2: Menubar へ2項目を足す**

`Menubar.py` 先頭の import 群へ `import WindowUtils` を1行足す（`camera_panel.py` と同じ書き方。`APP_DIR` を使うため）。次に `AssignMenuCommand` の末尾（Discord通知の設定の後）へ以下を足す：

```python
        self.menu_command.add(
            "command", command=self.OpenScriptInstall, label="スクリプトの導入..."
        )
        self.menu_command.add(
            "command", command=self.OpenScriptUninstall, label="スクリプトの削除..."
        )
```

次に `open_discord_notify_setting` の後へ以下を足す：

```python
    def OpenScriptInstall(self) -> None:
        """配布zipを選んで導入する。実手順は ui.script_pack_dialogs。"""
        from ui import script_pack_dialogs

        script_pack_dialogs.install_script_zip(
            self.root,
            WindowUtils.APP_DIR,
            is_busy=lambda: self.app.runner.is_busy(),
            reload_commands=self.app.reloadCommands,
        )

    def OpenScriptUninstall(self) -> None:
        """導入済みを選んで削除する。実手順は ui.script_pack_dialogs。"""
        from ui import script_pack_dialogs

        script_pack_dialogs.uninstall_script_dialog(
            self.root,
            WindowUtils.APP_DIR,
            is_busy=lambda: self.app.runner.is_busy(),
            reload_commands=self.app.reloadCommands,
        )
```

メソッド内の遅延 import にするのは、Menubar の import 時に ui 配下を引き込んで循環にするのを避けるため（`ui/` から `Window` は禁止だが、Menubar→ui の直接参照も起動順に敏感なため）。

- [ ] **Step 3: gate を確認する**

Run: `ruff check`＋`format --check`＋`mypy` を `SerialController/ui/script_pack_dialogs.py SerialController/Menubar.py` に掛ける
Expected: すべてPASS。次に `uv run --frozen python tools/check_core.py`（ui→Window 禁止に触れないこと）と `python tools/check_user_api.py` が緑であること。

- [ ] **Step 4: 実機で手動確認する（GUIのため自動化しない）**

手順と期待結果（報告に転記すること）：
1. `python SerialController/Window.py` を起動し、メニュー→コマンドに「スクリプトの導入...」「スクリプトの削除...」がある
2. `docs/pack_sample/` 相当のzip（Task 4 のCLIで作るか手作り）を導入→一覧に増え、導入完了ダイアログが出る
3. 同版の再導入→「同じ版が導入済み」で失敗ダイアログ
4. 異版の導入→上書き確認→更新され、退避フォルダができる
5. 削除ダイアログで削除→一覧から消える
6. コマンド実行中に導入・削除→警告で断られる

---

### Task 4: `ScriptPackTool.py` CLI＋文書＋テスト

**Files:**
- Create: `SerialController/ScriptPackTool.py`
- Test: `tests/test_script_pack_tool.py`
- Modify: `docs/PACK_FORMAT_v0.md`（末尾へ3節を追記のみ）

**Interfaces:**
- Consumes: Task 1〜2 の `create_pack/validate_staged/install_zip/uninstall_package/list_installed`。
- Produces: `main(argv)->int`。終了符号は 0＝成功、1＝検証・処理の失敗、2＝使い方誤り（argparse 既定）。

約束: APP_DIR の既定は本ファイルの位置（起動場所に依存しない）。`os.chdir` しない。上書き・削除の非対話時は `--yes` が要る（無ければ1で終了し促す）。`install`・`uninstall` の実行中ガードはできないため、注意文を出す（GUI側が担う）。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_script_pack_tool.py` を新規作成する。内容は以下全文：

```python
"""ScriptPackTool（CLI）の検証。実機・GUIなしで回す。"""

from __future__ import annotations

import json
from pathlib import Path

import ScriptPackTool


def make_src(base: Path) -> Path:
    src = base / "src"
    (src / "PythonCommands").mkdir(parents=True)
    (src / "Template" / "my-pack").mkdir(parents=True)
    data = {
        "name": "my-pack",
        "version": "1.0.0",
        "author": "alice",
        "description": "sample",
        "entry": "MyPack.py",
        "minAppVersion": "4.0.0",
        "templates": ["my-pack/a.png"],
    }
    (src / "pokecon.json").write_text(json.dumps(data), encoding="utf-8")
    (src / "PythonCommands" / "MyPack.py").write_text(
        "from Commands.PythonCommandBase import PythonCommand\n",
        encoding="utf-8",
    )
    (src / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return src


def test_check_good_zip_returns_zero(tmp_path: Path, capsys: object) -> None:
    out = tmp_path / "my-pack.zip"
    assert ScriptPackTool.main(["pack", str(make_src(tmp_path)), str(out)]) == 0
    assert ScriptPackTool.main(["check", str(out)]) == 0


def test_pack_bad_dir_returns_one(tmp_path: Path) -> None:
    src = tmp_path / "empty"
    src.mkdir()
    assert ScriptPackTool.main(["pack", str(src), str(tmp_path / "x.zip")]) == 1


def test_install_list_uninstall(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    out = tmp_path / "my-pack.zip"
    assert ScriptPackTool.main(["pack", str(make_src(tmp_path)), str(out)]) == 0
    assert ScriptPackTool.main(["install", str(out), "--app-dir", str(app)]) == 0
    assert ScriptPackTool.main(["list", "--app-dir", str(app)]) == 0
    assert ScriptPackTool.main(["uninstall", "my-pack", "--app-dir", str(app)]) == 1
    assert ScriptPackTool.main(["uninstall", "my-pack", "--app-dir", str(app), "--yes"]) == 0
    assert ScriptPackTool.main(["list", "--app-dir", str(app)]) == 0
```

`capsys: object` と書いているのは、`pytest` の `capsys` fixture の型を mypy に問わせないため（既存 tests の流儀に合わせ、出力のassertはしない）。

- [ ] **Step 2: REDを確認する**

Run: `uv run --frozen pytest tests/test_script_pack_tool.py -q`
Expected: FAIL（`ScriptPackTool` が無いため collection error）。

- [ ] **Step 3: `ScriptPackTool.py` を作成する**

内容は以下全文：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自作スクリプト配布のCLI（梱包・検証・導入・削除・一覧）。

使い方（リポジトリ直下で）:
  uv run --frozen python SerialController/ScriptPackTool.py pack <配置DIR> <出すZIP>
  uv run --frozen python SerialController/ScriptPackTool.py check <ZIP>
  uv run --frozen python SerialController/ScriptPackTool.py install <ZIP> [--app-dir DIR] [--yes]
  uv run --frozen python SerialController/ScriptPackTool.py uninstall <名前> [--app-dir DIR] [--yes]
  uv run --frozen python SerialController/ScriptPackTool.py list [--app-dir DIR]

--app-dir を省いたら本ファイルの場所を使う。os.chdir はしない。
導入・削除の実行中ガードは GUI 側が担うため、CLI では注意文を出す。
"""

from __future__ import annotations

import argparse
import os
import sys

APP_DIR_DEFAULT = os.path.dirname(os.path.abspath(__file__))

if __name__ != "__main__":
    _cli_dir = os.path.dirname(os.path.abspath(__file__))
    if _cli_dir not in sys.path:
        sys.path.insert(0, _cli_dir)


def _cmd_pack(args: argparse.Namespace) -> int:
    from core import pack_zip

    try:
        names = pack_zip.create_pack(args.src_dir, args.out_zip)
    except pack_zip.PackError as e:
        print(f"梱包できません: {e}")
        return 1
    print(f"梱包しました: {args.out_zip}（{len(names)}件）")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    import tempfile

    from core import pack_zip
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="pokecon_check_") as tmp:
        staged = Path(tmp) / "staged"
        staged.mkdir()
        try:
            pack_zip.safe_extract(args.zip, staged)
        except pack_zip.PackError as e:
            print(f"zip を開けません: {e}")
            return 1
        report = pack_zip.validate_staged(staged)
    if not report.ok or report.manifest is None:
        print("配布物が不正です:\n- " + "\n- ".join(report.errors))
        return 1
    print(f"OK: {report.manifest.name} 版{report.manifest.version}")
    for warning in report.warnings:
        print(f"注意: {warning}")
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    from services import script_pack

    print("注意: アプリが起動中・実行中なら先に止めてください（CLI では見張れません）")
    res = script_pack.install_zip(args.app_dir, args.zip, allow_overwrite=args.yes)
    if res.status == "confirm-overwrite":
        print(res.message)
        print("上書きするには --yes を付けてください")
        return 1
    print(res.message)
    for warning in res.warnings:
        print(f"注意: {warning}")
    return 0 if res.status == "installed" else 1


def _cmd_uninstall(args: argparse.Namespace) -> int:
    from services import script_pack

    if not args.yes:
        print(f"{args.name} を削除するには --yes を付けてください")
        return 1
    print("注意: アプリが起動中・実行中なら先に止めてください（CLI では見張れません）")
    res = script_pack.uninstall_package(args.app_dir, args.name)
    print(res.message)
    return 0 if res.status == "removed" else 1


def _cmd_list(args: argparse.Namespace) -> int:
    from services import script_pack

    records = script_pack.list_installed(args.app_dir)
    if not records:
        print("導入済みパッケージはありません")
        return 0
    for rec in records:
        print(f"{rec.name} 版{rec.version}（{rec.installed_at}）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="自作スクリプト配布の梱包・導入")
    sub = parser.add_subparsers(dest="command", required=True)
    pack = sub.add_parser("pack", help="配置フォルダからzipを作る")
    pack.add_argument("src_dir")
    pack.add_argument("out_zip")
    pack.set_defaults(func=_cmd_pack)
    check = sub.add_parser("check", help="zipを検証する")
    check.add_argument("zip")
    check.set_defaults(func=_cmd_check)
    install = sub.add_parser("install", help="zipを導入する")
    install.add_argument("zip")
    install.add_argument("--app-dir", default=APP_DIR_DEFAULT)
    install.add_argument("--yes", action="store_true")
    install.set_defaults(func=_cmd_install)
    uninstall = sub.add_parser("uninstall", help="導入済みを削除する")
    uninstall.add_argument("name")
    uninstall.add_argument("--app-dir", default=APP_DIR_DEFAULT)
    uninstall.add_argument("--yes", action="store_true")
    uninstall.set_defaults(func=_cmd_uninstall)
    listing = sub.add_parser("list", help="導入済みを列挙する")
    listing.add_argument("--app-dir", default=APP_DIR_DEFAULT)
    listing.set_defaults(func=_cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    _main_dir = os.path.dirname(os.path.abspath(__file__))
    if _main_dir not in sys.path:
        sys.path.insert(0, _main_dir)
    raise SystemExit(main())
```

補足: 関数内の遅延 import（`from core import ...`・`from services import ...` を関数内で行う）なのは、pytest が `SerialController` を sys.path へ載せる流儀（`tests/conftest.py`）と、直起動（`__main__` 時の追い足し）の両方で動かすため。

- [ ] **Step 4: GREENと文書更新、gateを確認する**

Run: `uv run --frozen pytest tests/test_script_pack_tool.py -q`
Expected: PASS（3 passed）。

`docs/PACK_FORMAT_v0.md` の末尾へ以下を追記する：

```markdown

## zipの配置

- `pokecon.json`
- `PythonCommands/<entry>`（単一 `.py`。区切りは英字・数字・`_`のみ）
- `Template/<name>/...`

- 記録は `<APP_DIR>/InstalledPacks/<name>.json`、上書き前の写しは `<APP_DIR>/InstalledPacks/.backup/<name>_<日時>/`。
- 同版の再導入は失敗、異版は確認のうえ上書き。実行中はGUIが断る。
- CLI: `uv run --frozen python SerialController/ScriptPackTool.py <pack|check|install|uninstall|list> ...`（`--help` で詳細）。
```

Run: `ruff check`＋`format --check`＋`mypy` を `SerialController/ScriptPackTool.py tests/test_script_pack_tool.py` に掛けてPASSを確認する。最後にフルゲート（`ruff check`＋`format --check`＋`mypy`＋`bounds`＋`userapi`＋`pytest`）を通す。

---

## 追補（2026-09-06 実証で発覚・修正済み）

実ツリーへの導入検証で、entry がローダーに認識されない欠陥が見つかった。原因は配置対応の誤り：zip/staging 配置が `PythonCommands/<entry>` をルートに持っていたが、実ツリーは `Commands/PythonCommands/` 配下のため、`install_zip` が走査外（`SerialController/PythonCommands/`）へ置いていた。Template 側は正しかった。

修正方針：zip/staging 配置を実ツリーに合わせる（写像を挟まない）。manifest の entry 意味（`PythonCommands/` からの相対・単一 `.py`）は不変。

- `core/pack_manifest.py validate_package_layout`：`base/"Commands"/"PythonCommands"/entry` を見る。不足文も `Commands/PythonCommands/...` 形。
- `core/pack_zip.py`：docstring・`validate_staged` の entry 解決・`create_pack` の収録名に `Commands/` 頭辞。
- `services/script_pack.py`：`rels` の entry 側に `Commands/` 頭辞。削除時の prune 根も `{app/"Commands"/"PythonCommands", app/"Template"}`。
- tests 4件の staging/app  helper を `Commands/PythonCommands` 形へ（assert の rel 文字列含む）。
- `docs/pack_sample/PythonCommands/MyPack.py` → `docs/pack_sample/Commands/PythonCommands/MyPack.py` へ移動。`docs/PACK_FORMAT_v0.md` の zip 配置節も同形へ。
- v0 未公開のため旧配置の移行措置は不要。

検証：実ツリーへ導入→`CommandLoader` が「配布サンプル」を検出（`has-sample: True`）→削除→検出せず・残骸なし。フルゲート緑（87 passed）。

## 追補2（件数表示の明確化）

`pack` の「3件」と導入時の「2件」の差は正常（前者は manifest を含む収録数、後者は配置物のみ）だが紛らわしいため、文面に内訳を添えた。`pack`→「（N件： manifestを含む）」、`install`→「（N件： manifestを除く）」。終了符号・テストの期待値は不変。
