#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blockly生成物の検証（GUI非依存）。

編集画面（JS）が作ったPythonコードとワークスペースJSONを、保存前に
検査する。許可するimportの集合は `core/user_api_allowlist.py` が正本。
深刻度（仕様）: 許可外・未知の第三者は異常（保存不可）。配布時
（pack_zip）の注意扱いとは違い、ここでは厳しく落とす。
"""

from __future__ import annotations

import ast
import json
import re
import sys

from core.user_api_allowlist import ALLOWED_COMMANDS_SUBS, THIRD_PARTY

_STEM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# 正本は core/user_api_allowlist.py。後方互換のため旧名でも読める。
_ALLOWED_COMMANDS_SUBS = ALLOWED_COMMANDS_SUBS
_THIRD_PARTY = THIRD_PARTY


def validate_stem(stem: str) -> list[str]:
    """保存名（拡張子なし）を検査する。異常の一覧を返す。空なら正常。"""
    if not isinstance(stem, str) or not stem.strip():
        return ["保存名を書いてください"]
    if "/" in stem or "\\" in stem or "." in stem:
        return ["保存名に区切り文字は使えません（例: MyBlock）"]
    if _STEM_RE.fullmatch(stem) is None:
        return ["保存名は英字・数字・`_`にしてください（例: MyBlock）"]
    return []


def _check_imports(tree: ast.AST) -> list[str]:
    """import経路の検査。公開面外は異常、未知トップレベルは注意も異常扱い。"""
    allowed_top = set(sys.stdlib_module_names) | _THIRD_PARTY | {"Commands"}
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top not in allowed_top:
                    errors.append(f"許可外の import です: import {a.name}")
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
                    errors.append(
                        f"許可外の import です: from {node.module} import ..."
                    )
    return errors


def validate_generated_code(code: str) -> list[str]:
    """生成Pythonコードを検査する。異常の一覧を返す。空なら正常。"""
    if not isinstance(code, str) or not code.strip():
        return ["生成コードが空です"]
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"生成コードの文法が壊れています（{e}）"]
    errors = _check_imports(tree)
    any_name = False
    any_do = False
    found_pair = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            has_name = False
            has_do = False
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for t in item.targets:
                        if isinstance(t, ast.Name) and t.id == "NAME":
                            if isinstance(item.value, ast.Constant):
                                value = item.value.value
                                if isinstance(value, str) and value.strip():
                                    has_name = True
                if isinstance(item, ast.FunctionDef) and item.name == "do":
                    has_do = True
            any_name = any_name or has_name
            any_do = any_do or has_do
            if has_name and has_do:
                found_pair = True
    if not found_pair:
        if not any_name:
            errors.append('`NAME = "..."`（空でない文字）がありません')
        if not any_do:
            errors.append("`def do(self)` がありません")
        if any_name and any_do:
            errors.append('`NAME = "..."`（空でない文字）がありません')
            errors.append("`def do(self)` がありません")
    return errors


def validate_workspace_json(text: str) -> list[str]:
    """ワークスペースJSONを検査する。異常の一覧を返す。空なら正常。"""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return [f"ワークスペースJSONが壊れています（{e}）"]
    if not isinstance(data, dict):
        return ["ワークスペースJSONはオブジェクトで書いてください"]
    blocks = data.get("blocks")
    if not isinstance(blocks, dict) or not isinstance(blocks.get("blocks"), list):
        return ["ワークスペースJSONに `blocks.blocks` がありません"]
    return []
