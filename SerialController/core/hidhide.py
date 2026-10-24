#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hidhide.py - HidHide の導入・設定状態を調べる（Tkなし）。

Switch Pro コントローラの HID ジャイロとパッド入力を同じアプリで
安定して使うには、Steam / SDL に同じ HID を触らせない必要がある。
HidHide はそのためのカーネルフィルタだが、導入・cloak・アプリ許可・
対象デバイスの hide が揃っていないと意味がない。ここでは CLI の
読み取りコマンドだけを短時間で実行し、状態を返す。

自動インストールはしない。カーネルドライバの導入は管理者権限と
再起動を伴うため、利用者が公式インストーラで行う前提にする。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

_HIDHIDE_CLI_CANDIDATES = (
    r"%ProgramFiles%\Nefarius Software Solutions\HidHide\HidHideCLI.exe",
    r"%ProgramFiles%\Nefarius Software Solutions\HidHide\x64\HidHideCLI.exe",
)
_CACHE_TTL_S = 5.0
_cached: tuple[float, "HidHideStatus"] | None = None


@dataclass(frozen=True)
class HidHideStatus:
    """HidHide の現在の設定。ready が True のときだけ HID を使ってよい。"""

    installed: bool = False
    cli_path: str = ""
    cloak_on: bool = False
    app_whitelisted: bool = False
    hidden_nintendo: bool = False
    error: str = ""

    @property
    def ready(self) -> bool:
        return (
            self.installed
            and self.cloak_on
            and self.app_whitelisted
            and self.hidden_nintendo
        )

    def summary(self) -> str:
        """利用者向けの短い状態説明。"""
        if not self.installed:
            return "HidHideがインストールされていません"
        if not self.cloak_on:
            return "HidHideのcloakがOFFです"
        if not self.hidden_nintendo:
            return "SwitchコントローラがHidHideのhide listにありません"
        if not self.app_whitelisted:
            return "Poke-ControllerがHidHideの許可アプリにありません"
        return "HidHide準備完了"


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def find_cli() -> str:
    """HidHideCLI.exe の場所。見つからなければ空文字。"""
    for candidate in _HIDHIDE_CLI_CANDIDATES:
        path = _expand(candidate)
        if os.path.isfile(path):
            return path
    return ""


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(_expand(str(path))))


def executable_candidates() -> tuple[str, ...]:
    """HidHide の許可リストと照合する実行ファイル候補。

    venv の python.exe は uv の base python と同一実体（ハードリンク）
    のことがあり、HidHide は実体で照合する。sys.executable だけでなく
    sys._base_executable も見る。
    """
    values: list[str] = []
    for value in (
        getattr(sys, "executable", ""),
        getattr(sys, "_base_executable", ""),
    ):
        if value:
            values.append(_norm(value))
    return tuple(dict.fromkeys(values))


def parse_cli_output(
    output: str,
    executables: tuple[str, ...] | None = None,
    *,
    installed: bool = True,
    cli_path: str = "",
    error: str = "",
) -> HidHideStatus:
    """CLI の読み取り出力（--cloak-state --dev-list --app-list）を解析する。"""
    cloak_on = False
    hidden_nintendo = False
    app_paths: set[str] = set()
    for raw in output.splitlines():
        line = raw.strip()
        low = line.lower()
        if low == "--cloak-on":
            cloak_on = True
        elif low.startswith("--app-reg "):
            value = line[len("--app-reg ") :].strip().strip('"')
            if value:
                app_paths.add(_norm(value))
        elif low.startswith("--dev-hide "):
            value = line[len("--dev-hide ") :].strip().strip('"')
            # Switch Pro / 互換機の HID、または USB 親。
            if "vid_057e" in value.lower() or "vid_2009" in value.lower():
                hidden_nintendo = True
    wanted = executable_candidates() if executables is None else executables
    app_whitelisted = any(_norm(p) in app_paths for p in wanted)
    return HidHideStatus(
        installed=installed,
        cli_path=cli_path,
        cloak_on=cloak_on,
        app_whitelisted=app_whitelisted,
        hidden_nintendo=hidden_nintendo,
        error=error,
    )


def detect_hidhide(
    cli_path: str | None = None,
    *,
    runner: Callable[[str], tuple[int, str, str]] | None = None,
    force: bool = False,
) -> HidHideStatus:
    """HidHide の状態を読む。結果は短時間キャッシュする。

    runner はテスト用。`(returncode, stdout, stderr)` を返す callable。
    CLI が無い・タイムアウト・異常終了でも例外を投げない。
    """
    global _cached
    now = time.monotonic()
    if not force and _cached is not None and now - _cached[0] <= _CACHE_TTL_S:
        return _cached[1]
    path = cli_path if cli_path is not None else find_cli()
    if not path or not os.path.isfile(path):
        status = HidHideStatus(installed=False)
        _cached = (now, status)
        return status
    try:
        if callable(runner):
            code, out, err = runner(path)
        else:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.run(
                [path, "--cloak-state", "--dev-list", "--app-list"],
                capture_output=True,
                text=True,
                timeout=2.0,
                creationflags=flags,
                check=False,
            )
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        error = "" if int(code) == 0 else str(err or f"exit {code}").strip()
        status = parse_cli_output(
            str(out or ""),
            installed=True,
            cli_path=path,
            error=error[:300],
        )
    except Exception as e:
        status = HidHideStatus(
            installed=True,
            cli_path=path,
            error=f"{type(e).__name__}: {e}"[:300],
        )
    _cached = (now, status)
    return status


def hidhide_ready(*, force: bool = False) -> bool:
    """HID 直結を使ってよい状態か。"""
    return detect_hidhide(force=force).ready


def clear_cache() -> None:
    """テスト・手動再検出用。"""
    global _cached
    _cached = None
