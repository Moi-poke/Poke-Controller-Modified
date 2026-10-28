#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blockly のヘッドレス生成（GUI非依存）。

ブラウザと同じ vendored 資産を node の vm で読み、ワークスペース JSON から
Python コードを生成する。描画（inject）はしないため、画面の無い環境でも
保存前検査に使える。node が無い環境では理由付きで失敗する（アプリ本体の
動作には影響しない）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: node が無いときの案内。呼び出し側はこの文で判定してよい。
NODE_MISSING_MESSAGE = "node が見つかりません（Blockly のヘッドレス生成に必要）"

#: アプリディレクトリからの Blockly 資産の相対位置。
ASSETS_BLOCKLY_REL = ("assets", "blockly")

#: node 側の入り口（リポジトリ直下の tools/）。
NODE_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "blockly_agent_node.js"

#: 1回の生成にかける上限（秒）。
DEFAULT_TIMEOUT = 60.0


@dataclass
class HeadlessResult:
    """node 実行の結果。ok=false でも errors に日本語の理由が入る。"""

    ok: bool
    data: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def node_path() -> str | None:
    """PATH 上の node を返す。無ければ None。"""
    found = shutil.which("node")
    return found if found else None


def resolve_assets(app_dir: str | Path) -> Path:
    """アプリディレクトリ（または資産ディレクトリ）から blockly 資産を返す。"""
    base = Path(app_dir)
    if (base / "blockly_compressed.js").is_file():
        return base
    return base.joinpath(*ASSETS_BLOCKLY_REL)


def catalog(app_dir: str | Path, *, timeout: float = DEFAULT_TIMEOUT) -> HeadlessResult:
    """ブロックカタログを node から取る。"""
    return _run("catalog", resolve_assets(app_dir), {}, timeout=timeout)


def generate(
    workspace: dict[str, Any],
    app_dir: str | Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> HeadlessResult:
    """ワークスペース JSON から Python コードを生成する。"""
    return _run(
        "generate", resolve_assets(app_dir), {"workspace": workspace}, timeout=timeout
    )


def generate_trial(
    workspace: dict[str, Any],
    app_dir: str | Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> HeadlessResult:
    """ワークスペース JSON から、試し実行用（目印つき）コードを生成する。

    目印の付け方はブラウザの `Blockly.PokeconEditor.trialCode` が正本。
    Node 側で文字列置換をせず、同じ関数を通す。
    """
    return _run(
        "trial", resolve_assets(app_dir), {"workspace": workspace}, timeout=timeout
    )


def _run(
    mode: str,
    assets: Path,
    request: dict[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> HeadlessResult:
    """node を起動して JSON を1個読み取る。例外は外へ出さない。"""
    node = node_path()
    if node is None:
        return HeadlessResult(False, errors=[NODE_MISSING_MESSAGE])
    if not (assets / "blockly_compressed.js").is_file():
        return HeadlessResult(False, errors=[f"Blockly の資産がありません: {assets}"])
    if not NODE_SCRIPT.is_file():
        return HeadlessResult(
            False, errors=[f"node 用スクリプトがありません: {NODE_SCRIPT}"]
        )
    request_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="pokecon_blockly_",
            delete=False,
        ) as fp:
            request_path = fp.name
            json.dump(request, fp, ensure_ascii=False)
        proc = subprocess.run(
            [node, str(NODE_SCRIPT), mode, str(assets.resolve()), request_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return HeadlessResult(
            False,
            errors=[f"Blockly の生成がタイムアウトしました（{timeout:g}秒）"],
        )
    except (TypeError, ValueError) as e:
        return HeadlessResult(False, errors=[f"node への要求を作れません: {e}"])
    except OSError as e:
        return HeadlessResult(False, errors=[f"node を起動できません: {e}"])
    finally:
        if request_path is not None:
            try:
                os.unlink(request_path)
            except OSError:
                pass
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        message = f"node が異常終了しました（コード {proc.returncode}）"
        return HeadlessResult(
            False, errors=[message] + ([detail[:1000]] if detail else [])
        )
    raw = proc.stdout.strip()
    if not raw:
        detail = (proc.stderr or "").strip()
        return HeadlessResult(
            False,
            errors=["node の出力が空です"] + ([detail[:1000]] if detail else []),
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        return HeadlessResult(
            False,
            errors=[f"node の出力を JSON として読めません（{e}）", raw[:1000]],
        )
    if not isinstance(payload, dict):
        return HeadlessResult(False, errors=["node の出力がオブジェクトではありません"])
    errors = _string_list(payload.get("errors"))
    warnings = _string_list(payload.get("warnings"))
    ok = payload.get("ok") is True
    data = payload if ok else None
    if not ok and not errors:
        errors = ["Blockly の処理に失敗しました"]
    if ok and not warnings and proc.stderr.strip():
        warnings = [proc.stderr.strip()[:1000]]
    return HeadlessResult(ok, data=data, errors=errors, warnings=warnings)


def _string_list(value: object) -> list[str]:
    """JSON の配列を文字列一覧にする（型違いは文字列化）。"""
    if not isinstance(value, list):
        return []
    return [item if isinstance(item, str) else str(item) for item in value]
