"""承認済みのみ依存導入を実行する検証（RED: 先に振る舞いを固める）。

実ネットワークには触らない。subprocess は必ず mock する。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import pack_zip
from services import script_pack


def _make_zip_with_dep(base: Path, dep: str = "somelib_xyz_unknown_aaa") -> Path:
    """未知依存1件を持つ配布 zip を作る（tmp 配下のみ）。"""
    src = base / "src"
    (src / "Commands" / "PythonCommands").mkdir(parents=True)
    (src / "Template" / "my-pack").mkdir(parents=True)
    data = {
        "name": "my-pack",
        "version": "1.0.0",
        "author": "tester",
        "description": "依存導入の検証",
        "entry": "MyPack.py",
        "minAppVersion": "4.0.0",
        "templates": ["my-pack/a.png"],
    }
    (src / "pokecon.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    (src / "Commands" / "PythonCommands" / "MyPack.py").write_text(
        f"from Commands.Keys import Button\nimport {dep}\n",
        encoding="utf-8",
    )
    (src / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    out = base / "my-pack.zip"
    pack_zip.create_pack(src, out)
    return out


def test_unapproved_install_deps_never_runs_subprocess(tmp_path: Path) -> None:
    """未承認の依存導入は subprocess を一切実行しない（承認ゲート最優先）。"""
    with patch.object(script_pack.subprocess, "run", autospec=True) as mock_run:
        res = script_pack.install_dependencies(
            ["somelib_xyz_unknown_aaa"], allow_install_deps=False
        )
        assert res.status == "confirm-install-deps"
        mock_run.assert_not_called()


def test_unapproved_install_zip_never_runs_subprocess(tmp_path: Path) -> None:
    """不足依存つき導入は未承認なら置かず・実行せず confirm で止める。"""
    zip_path = _make_zip_with_dep(tmp_path / "pack")
    app = tmp_path / "app"
    app.mkdir()
    with patch.object(script_pack.subprocess, "run", autospec=True) as mock_run:
        res = script_pack.install_zip(app, zip_path)
        assert res.status == "confirm-install-deps"
        mock_run.assert_not_called()
    assert not (app / "Commands" / "PythonCommands" / "MyPack.py").is_file()


def test_approved_install_deps_runs_pip_with_captured_output() -> None:
    """承認済みは pip を実行し stdout/stderr を捕捉する（sys.executable 起点）。"""
    import sys

    done = MagicMock()
    done.returncode = 0
    done.stdout = "ok-stdout"
    done.stderr = "ok-stderr"
    with patch.object(
        script_pack.subprocess, "run", autospec=True, return_value=done
    ) as mock_run:
        res = script_pack.install_dependencies(
            ["somelib_xyz_unknown_aaa"], allow_install_deps=True
        )
        assert res.status == "installed"
        assert res.deps_stdout == "ok-stdout"
        assert res.deps_stderr == "ok-stderr"
    assert mock_run.call_count == 1
    args, kwargs = mock_run.call_args
    cmd = list(args[0])
    assert cmd[:4] == [sys.executable, "-m", "pip", "install"]
    assert "somelib_xyz_unknown_aaa" in cmd
    assert kwargs.get("capture_output") is True
    assert kwargs.get("text") is True
    assert isinstance(kwargs.get("timeout"), (int, float))


def test_install_deps_failure_returns_failed_with_output() -> None:
    """pip 失敗は failed で終わり、出力を残す（黙って通さない）。"""
    done = MagicMock()
    done.returncode = 1
    done.stdout = "fail-out"
    done.stderr = "fail-err"
    with patch.object(script_pack.subprocess, "run", autospec=True, return_value=done):
        res = script_pack.install_dependencies(
            ["somelib_xyz_unknown_aaa"], allow_install_deps=True
        )
        assert res.status == "failed"
        assert "fail-out" in res.message or "fail-err" in res.message


def test_install_deps_timeout_returns_failed() -> None:
    """pip タイムアウトは failed で終わる（ぶら下がらない）。"""
    with patch.object(
        script_pack.subprocess,
        "run",
        autospec=True,
        side_effect=subprocess.TimeoutExpired(cmd="pip", timeout=1),
    ):
        res = script_pack.install_dependencies(
            ["somelib_xyz_unknown_aaa"], allow_install_deps=True
        )
        assert res.status == "failed"


def test_approved_install_zip_runs_pip_then_installs(tmp_path: Path) -> None:
    """承認つき導入は pip 実行後に実体を置き、記録へ依存を残す。"""
    zip_path = _make_zip_with_dep(tmp_path / "pack")
    app = tmp_path / "app"
    app.mkdir()
    done = MagicMock()
    done.returncode = 0
    done.stdout = "ok"
    done.stderr = ""
    with patch.object(
        script_pack.subprocess, "run", autospec=True, return_value=done
    ) as mock_run:
        res = script_pack.install_zip(app, zip_path, allow_install_deps=True)
        assert res.status == "installed"
        mock_run.assert_called_once()
    assert (app / "Commands" / "PythonCommands" / "MyPack.py").is_file()
    rec = script_pack.read_record(app, "my-pack")
    assert rec is not None
    assert "somelib_xyz_unknown_aaa" in rec.dependencies
