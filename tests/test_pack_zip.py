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
    (base / "Commands" / "PythonCommands").mkdir(parents=True)
    (base / "Template" / "my-pack").mkdir(parents=True)
    (base / "pokecon.json").write_text(json.dumps(data), encoding="utf-8")
    (base / "Commands" / "PythonCommands" / "MyPack.py").write_text(
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
    assert "Commands/PythonCommands/MyPack.py" in files
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
    info.external_attr = 0o120777 << 16
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
    (src / "Commands" / "PythonCommands" / "my-pack").mkdir(parents=True, exist_ok=True)
    (src / "Commands" / "PythonCommands" / "my-pack" / "MyPack.py").write_bytes(
        "from Commands.Keys import Button\n".encode("utf-8")
    )
    report = pack_zip.validate_staged(src)
    assert not report.ok
