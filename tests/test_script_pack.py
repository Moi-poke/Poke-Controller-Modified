"""script_pack（導入・更新・削除・一覧）の検証。実機・GUIなしで回す。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from core import pack_zip
from services import script_pack


def make_src(base: Path, version: str = "1.0.0") -> Path:
    src = base / "src"
    (src / "Commands" / "PythonCommands").mkdir(parents=True, exist_ok=True)
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
    (src / "Commands" / "PythonCommands" / "MyPack.py").write_text(
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
    assert (app / "Commands" / "PythonCommands" / "MyPack.py").is_file()
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
    res2 = script_pack.install_zip(
        app, make_zip(tmp_path, "2.0.0"), allow_overwrite=True
    )
    assert res2.status == "installed"
    assert res2.backup_rel is not None
    assert (
        app / res2.backup_rel / "Template/my-pack/a.png"
    ).read_bytes() == b"old-bytes"
    assert (app / "Template" / "my-pack" / "a.png").read_bytes() == b"\x89PNG\r\n\x1a\n"


def test_uninstall_removes_files_and_record(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    script_pack.install_zip(app, make_zip(tmp_path, "1.0.0"))
    res = script_pack.uninstall_package(app, "my-pack")
    assert res.status == "removed"
    assert not (app / "Commands" / "PythonCommands" / "MyPack.py").exists()
    assert not (app / "InstalledPacks" / "my-pack.json").exists()
    assert script_pack.list_installed(app) == []
    again = script_pack.uninstall_package(app, "no-such-pack")
    assert again.status == "failed"


def test_install_missing_zip_fails_gracefully(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    res = script_pack.install_zip(app, tmp_path / "no-such.zip")
    assert res.status == "failed"


def test_overwrite_backups_do_not_collide(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    base = tmp_path / "chain"
    base.mkdir()
    script_pack.install_zip(app, make_zip(base / "a", "1.0.0"))
    (app / "Template" / "my-pack" / "a.png").write_bytes(b"old-1")
    first = script_pack.install_zip(
        app, make_zip(base / "b", "2.0.0"), allow_overwrite=True
    )
    (app / "Template" / "my-pack" / "a.png").write_bytes(b"old-2")
    second = script_pack.install_zip(
        app, make_zip(base / "c", "3.0.0"), allow_overwrite=True
    )
    assert first.status == "installed" and second.status == "installed"
    assert first.backup_rel is not None and second.backup_rel is not None
    assert first.backup_rel != second.backup_rel


def test_uninstall_path_escape_rejected(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    assert script_pack.uninstall_package(app, "../../x").status == "failed"


def test_install_refuses_unowned_file_collision(tmp_path: Path) -> None:
    app = tmp_path / "app"
    entry = app / "Commands" / "PythonCommands" / "MyPack.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("# 手書きのコマンド\n", encoding="utf-8")

    src = tmp_path / "other" / "src"
    (src / "Commands" / "PythonCommands").mkdir(parents=True)
    data = {
        "name": "other-pack",
        "version": "1.0.0",
        "author": "bob",
        "description": "other",
        "entry": "MyPack.py",
        "minAppVersion": "4.0.0",
        "templates": [],
    }
    (src / "pokecon.json").write_text(json.dumps(data), encoding="utf-8")
    (src / "Commands" / "PythonCommands" / "MyPack.py").write_text(
        "from Commands.Keys import Button\n", encoding="utf-8"
    )
    zip_path = tmp_path / "other-pack.zip"
    pack_zip.create_pack(src, zip_path)

    res = script_pack.install_zip(app, zip_path)

    assert res.status == "failed"
    assert "衝突" in res.message
    assert entry.read_text(encoding="utf-8") == "# 手書きのコマンド\n"
    assert not (app / "InstalledPacks" / ".backup").exists()


def test_install_unsupported_compression_fails_gracefully(
    tmp_path: Path, monkeypatch
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    zip_path = make_zip(tmp_path, "1.0.0")

    def raise_unsupported(*_args, **_kwargs):
        raise NotImplementedError("compression method 9")

    monkeypatch.setattr(zipfile.ZipFile, "open", raise_unsupported)
    res = script_pack.install_zip(app, zip_path)

    assert res.status == "failed"
    assert "zip を開けません" in res.message


def test_uninstall_continues_after_unlink_error(tmp_path: Path, monkeypatch) -> None:
    app = tmp_path / "app"
    app.mkdir()
    script_pack.install_zip(app, make_zip(tmp_path, "1.0.0"))
    entry = app / "Commands" / "PythonCommands" / "MyPack.py"
    template = app / "Template" / "my-pack" / "a.png"
    real_unlink = Path.unlink

    def fail_entry_unlink(self: Path, *args, **kwargs) -> None:
        if self.name == "MyPack.py":
            raise PermissionError(13, "アクセスが拒否されました", str(self))
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_entry_unlink)
    res = script_pack.uninstall_package(app, "my-pack")

    assert res.status == "failed"
    assert entry.is_file()
    assert not template.exists()
    assert "MyPack.py" in res.message
    # 実体が残っている間は導入記録も残し、原因を直して再試行できるようにする。
    assert script_pack.read_record(app, "my-pack") is not None
