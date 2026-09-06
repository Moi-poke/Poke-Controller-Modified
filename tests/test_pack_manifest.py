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
    (tmp_path / "Commands" / "PythonCommands").mkdir(parents=True)
    (tmp_path / "Template" / "my-pack").mkdir(parents=True)
    (tmp_path / "pokecon.json").write_text(json.dumps(good_data()), encoding="utf-8")
    (tmp_path / "Commands" / "PythonCommands" / "MyPack.py").write_text(
        "# ok\n", encoding="utf-8"
    )
    (tmp_path / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert validate_package_layout(tmp_path) == []


def test_legacy_root_template_gets_namespace_hint() -> None:
    data = good_data()
    data["templates"] = ["a.png"]
    errors = validate_manifest_data(data)
    assert any("<name>" in e or "Template" in e for e in errors)


def test_entry_segments_reject_hyphen_dir() -> None:
    from core.pack_manifest import validate_entry_segments

    errors = validate_entry_segments("my-pack/MyPack.py")
    assert any("フォルダ名" in e for e in errors)


def test_entry_segments_accept_flat_and_ident_dir() -> None:
    from core.pack_manifest import validate_entry_segments

    assert validate_entry_segments("MyPack.py") == []
    assert validate_entry_segments("my_pack/MyPack.py") == []
