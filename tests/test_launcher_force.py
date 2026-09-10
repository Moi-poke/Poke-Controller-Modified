"""launcher --force / write_lock 原子性 / sanitize_profile の回帰検証."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import launcher


def test_launch_force_steals_lock(monkeypatch, tmp_path: Path) -> None:
    """--force 相当で既存ロックを奪って起動できること (RED: force 引数なし)."""
    monkeypatch.setattr(launcher, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(launcher, "WINDOW_SCRIPT", str(tmp_path / "Window.py"))
    (tmp_path / "Window.py").write_text("# dummy", encoding="utf-8")
    # 生きている別プロセスが握っている想定
    monkeypatch.setattr(launcher, "running_pid", lambda profile: 999999)
    monkeypatch.setattr(launcher, "STARTUP_GRACE_SEC", 0.0)

    class FakeProc:
        pid = 12345

        def poll(self) -> None:
            return None

    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: FakeProc())

    import tempfile

    real_tmp = tempfile.NamedTemporaryFile

    def fake_tmp(*a, **k):
        return real_tmp(*a, **k)

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", fake_tmp)
    written: dict = {}

    def fake_write_lock(profile: str, pid: int) -> None:
        written["profile"] = profile
        written["pid"] = pid

    monkeypatch.setattr(launcher, "write_lock", fake_write_lock)
    # force=True で奪えること。現行は引数なしのため TypeError (RED).
    pid = launcher.launch("prof", force=True)  # type: ignore[call-arg]
    assert pid == 12345
    assert written["pid"] == 12345


def test_write_lock_is_atomic(monkeypatch, tmp_path: Path) -> None:
    """write_lock が temp+os.replace で原子的に書くこと (RED: 直接 open)."""
    monkeypatch.setattr(launcher, "APP_DIR", str(tmp_path))
    calls: list = []
    real_replace = os.replace

    def spy_replace(src: str, dst: str) -> None:
        calls.append((src, dst))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    launcher.write_lock("", 4242)
    assert calls, "os.replace が呼ばれていない (非原子)"
    import json

    data = json.loads((tmp_path / "pokecon.lock").read_text(encoding="utf-8"))
    assert data["pid"] == 4242


def test_sanitize_profile_no_syspath_pollution() -> None:
    """sanitize_profile 連打で sys.path が膨らまないこと (RED: 毎回 insert)."""
    before = list(sys.path)
    try:
        launcher.sanitize_profile("abc")
        launcher.sanitize_profile("abc")
        launcher.sanitize_profile("def")
        assert sys.path.count(launcher.APP_DIR) <= 1
    finally:
        # 汚染分を戻す (テスト隔離用)
        while sys.path.count(launcher.APP_DIR) > before.count(launcher.APP_DIR):
            sys.path.remove(launcher.APP_DIR)


def test_launch_force_restores_lock_on_popen_failure(
    monkeypatch, tmp_path: Path
) -> None:
    """--forceで奪った後に起動できなくても被害者の目印を残す。"""
    import json

    monkeypatch.setattr(launcher, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(launcher, "WINDOW_SCRIPT", str(tmp_path / "Window.py"))
    (tmp_path / "Window.py").write_text("# dummy", encoding="utf-8")
    victim = 777777
    launcher.write_lock("prof", victim)
    before = (tmp_path / "pokecon.prof.lock").read_bytes()

    def boom(*args: object, **kwargs: object) -> object:
        raise OSError("起動できません")

    monkeypatch.setattr(launcher.subprocess, "Popen", boom)
    try:
        launcher.launch("prof", force=True)
    except launcher.LaunchError:
        pass
    else:
        raise AssertionError("LaunchError が出るはず")
    after = tmp_path / "pokecon.prof.lock"
    assert after.is_file(), "被害者の目印が消えている"
    data = json.loads(after.read_bytes().decode("utf-8"))
    assert int(data.get("pid", 0)) == victim
    assert after.read_bytes() == before
