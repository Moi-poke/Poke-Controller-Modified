"""不足依存の検出と導入確認の検証（RED: 未実装の振る舞いを先に固める）。"""

from __future__ import annotations

import json
from pathlib import Path

from core import pack_zip
from core.user_api_allowlist import THIRD_PARTY
from services import script_pack


def _write_entry(base: Path, source: str) -> Path:
    """検証用の entry を書く（tmp 配下のみ）。"""
    entry = base / "entry.py"
    entry.write_text(source, encoding="utf-8")
    return entry


def _make_zip_with_entry(base: Path, entry_source: str) -> Path:
    """指定の entry 本文を持つ配布 zip を作る（tmp 配下のみ）。"""
    src = base / "src"
    (src / "Commands" / "PythonCommands").mkdir(parents=True)
    (src / "Template" / "my-pack").mkdir(parents=True)
    data = {
        "name": "my-pack",
        "version": "1.0.0",
        "author": "tester",
        "description": "不足依存の検証",
        "entry": "MyPack.py",
        "minAppVersion": "4.0.0",
        "templates": ["my-pack/a.png"],
    }
    (src / "pokecon.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    (src / "Commands" / "PythonCommands" / "MyPack.py").write_text(
        entry_source, encoding="utf-8"
    )
    (src / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    out = base / "my-pack.zip"
    pack_zip.create_pack(src, out)
    return out


def test_missing_deps_unknown_top_level(tmp_path: Path) -> None:
    """未知のトップレベルだけが不足依存として返る（標準・許可済みを除く）。"""
    # 標準ライブラリは除外し、THIRD_PARTY 正本の代表も除外する。
    entry = _write_entry(
        tmp_path,
        "import json\nimport cv2\nimport somelib_xyz_unknown_aaa\n",
    )
    missing = pack_zip.missing_third_party(entry)
    assert "somelib_xyz_unknown_aaa" in missing
    assert "json" not in missing
    assert "cv2" not in missing
    # 正本（THIRD_PARTY）にあるものは不足にしない。
    assert not (set(missing) & set(THIRD_PARTY))


def test_missing_deps_from_import_form(tmp_path: Path) -> None:
    """from 形式の未知トップレベルも不足依存として返る。"""
    entry = _write_entry(
        tmp_path,
        "from somelib_xyz_unknown_bbb import foo\n",
    )
    missing = pack_zip.missing_third_party(entry)
    assert "somelib_xyz_unknown_bbb" in missing


def test_confirm_install_deps_blocks_without_approval(tmp_path: Path) -> None:
    """不足依存がある導入は未承認なら confirm-install-deps で止める。"""
    # confirm-overwrite と同じく、聞くのは呼び出し側でここは止めるだけ。
    zip_path = _make_zip_with_entry(
        tmp_path / "pack",
        "from Commands.Keys import Button\nimport somelib_xyz_unknown_aaa\n",
    )
    app = tmp_path / "app"
    app.mkdir()
    res = script_pack.install_zip(app, zip_path)
    assert res.status == "confirm-install-deps"
    # 未承認なので実体は置かない。
    assert not (app / "Commands" / "PythonCommands" / "MyPack.py").is_file()
