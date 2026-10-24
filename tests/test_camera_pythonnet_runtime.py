"""pythonnet の .NET ローダー選択の検証（環境変数を触らない）。

既定の .NET Framework ローダーでは DirectShowLib の ``import clr`` が
「Failed to resolve Python.Runtime.Loader.Initialize」で失敗する環境があり、
``PYTHONNET_RUNTIME=coreclr`` で通ることが実機で確認されている。
ただし dotnet が無い環境では coreclr を選べないため、Windows かつ
dotnet があるときだけ設定する。明示設定は尊重する。
"""

from __future__ import annotations

import types

import pytest
import ui.camera_panel as camera_panel


def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    os_name: str,
    dotnet: str | None,
    environ: dict[str, str],
) -> dict[str, str]:
    fake_os = types.SimpleNamespace(name=os_name, environ=environ)
    monkeypatch.setattr(camera_panel, "os", fake_os)
    monkeypatch.setattr(
        camera_panel,
        "shutil",
        types.SimpleNamespace(which=lambda _name: dotnet),
    )
    camera_panel._use_coreclr_for_pythonnet()
    return environ


def test_windows_with_dotnet_and_no_explicit_setting_uses_coreclr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environ = _run(monkeypatch, os_name="nt", dotnet="C:/dotnet/dotnet.exe", environ={})
    assert environ == {"PYTHONNET_RUNTIME": "coreclr"}


def test_windows_without_dotnet_leaves_the_environment_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environ = _run(monkeypatch, os_name="nt", dotnet=None, environ={})
    assert environ == {}


def test_windows_respects_an_explicit_runtime_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environ = _run(
        monkeypatch,
        os_name="nt",
        dotnet="C:/dotnet/dotnet.exe",
        environ={"PYTHONNET_RUNTIME": "mono"},
    )
    assert environ == {"PYTHONNET_RUNTIME": "mono"}


def test_non_windows_is_never_touched(monkeypatch: pytest.MonkeyPatch) -> None:
    environ = _run(monkeypatch, os_name="posix", dotnet="dotnet", environ={})
    assert environ == {}
