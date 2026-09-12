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


#: `self.xxx()` で呼び出せる公開API（サブルーチン以外の正当な呼び先）。
#: `Commands.PythonCommandBase` 系の公開面に対応する。
_KNOWN_SELF_METHODS = frozenset(
    {
        "press",
        "pressRep",
        "pressEvery",
        "hold",
        "holdEnd",
        "wait",
        "finish",
        "print2",
        "discord_text",
        "discord_image",
        "dialogue",
        "dialogue6widget",
        "isContainTemplate",
        "isContainTemplate_max",
        "isContainTemplateGPU",
        "waitTemplate",
        "waitTemplateGone",
        "waitStable",
        "getTemplatePosition",
        "isContainTemplateDump",
        "findAllTemplates",
        "countTemplate",
        "press_until",
        "press_until_gone",
        "wait_count",
        "getColorRatio",
        "isSimilarColor",
        "waitTone",
        "waitSound",
        "isTonePresent",
    }
)


def _check_subroutines(tree: ast.AST) -> list[str]:
    """サブルーチン（`do` 以外のメソッド）の検査。異常の一覧を返す。"""
    import keyword

    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        methods: list[ast.FunctionDef] = [
            item for item in node.body if isinstance(item, ast.FunctionDef)
        ]
        if not methods:
            continue
        # 重複（`do` 同士も含む）は上書きで片方が消えるため異常にする。
        seen: set[str] = set()
        dupes: set[str] = set()
        for m in methods:
            if m.name in seen:
                dupes.add(m.name)
            seen.add(m.name)
        for name in sorted(dupes):
            errors.append(f"サブルーチン名が重複しています: {name}")
        subs: list[ast.FunctionDef] = [
            m for m in methods if m.name not in ("do", "__init__")
        ]
        sub_names = {m.name for m in subs}
        sigs: dict[str, tuple[list[str], int, bool]] = {}
        for m in subs:
            name = m.name
            if _STEM_RE.fullmatch(name) is None:
                errors.append(f"サブルーチン名は英字・数字・`_`にしてください: {name}")
                continue
            if name.startswith("__") and name.endswith("__"):
                errors.append(f"予約された名前は使えません: {name}")
                continue
            if name in _KNOWN_SELF_METHODS:
                errors.append(f"サブルーチン名が公開APIと重複しています: {name}")
                continue
            pos = list(m.args.posonlyargs) + list(m.args.args)
            if not pos or pos[0].arg != "self":
                errors.append(f"サブルーチンの第1引数は self にしてください: {name}")
                continue
            params = [a.arg for a in pos[1:]] + [a.arg for a in m.args.kwonlyargs]
            if m.args.vararg is not None:
                params.append(m.args.vararg.arg)
            if m.args.kwarg is not None:
                params.append(m.args.kwarg.arg)
            bad = [p for p in params if _STEM_RE.fullmatch(p) is None]
            if bad:
                errors.append(
                    f"引数名は英字・数字・`_`にしてください（{name}: {', '.join(bad)}）"
                )
                continue
            kw = {p for p in params if p != "self"}
            if len(kw) != len(params):
                errors.append(f"引数名が重複しています: {name}")
                continue
            if any(p in keyword.kwlist for p in params):
                errors.append(f"引数名に予約語は使えません: {name}")
                continue
            has_star = m.args.vararg is not None or m.args.kwarg is not None
            pos_params = len(pos) - 1
            kwonly = len(m.args.kwonlyargs)
            n_defaults = len(m.args.defaults)
            n_kw_defaults = sum(1 for d in m.args.kw_defaults if d is not None)
            required = (pos_params - n_defaults) + (kwonly - n_kw_defaults)
            # kwonly名も含めて保持する（呼び出し検査用）。
            sigs[name] = (
                [a.arg for a in pos[1:]] + [a.arg for a in m.args.kwonlyargs],
                required if required >= 0 else 0,
                has_star,
            )
        # 呼び出しの収集（caller -> [(attr, pos数, kw名)]）。
        calls: dict[str, list[tuple[str, int, list[str], bool]]] = {}
        for m in methods:
            found: list[tuple[str, int, list[str], bool]] = []
            for sub in ast.walk(m):
                if not isinstance(sub, ast.Call):
                    continue
                func = sub.func
                if not isinstance(func, ast.Attribute):
                    continue
                recv = func.value
                if not (isinstance(recv, ast.Name) and recv.id == "self"):
                    continue
                attr = func.attr
                has_unpack = any(isinstance(a, ast.Starred) for a in sub.args) or any(
                    k.arg is None for k in sub.keywords
                )
                kw_names = [k.arg for k in sub.keywords if k.arg is not None]
                found.append((attr, len(sub.args), kw_names, has_unpack))
            calls[m.name] = found
        for caller, lst in calls.items():
            for attr, n_pos, kw_names, has_unpack in lst:
                if attr in ("do", "__init__"):
                    errors.append(
                        f"`self.{attr}()` は呼ばないでください（{caller} から）"
                    )
                    continue
                if attr in _KNOWN_SELF_METHODS:
                    continue
                if attr not in sub_names:
                    errors.append(
                        f"未定義のサブルーチンです: self.{attr}()（{caller} から）"
                    )
                    continue
                if attr in sigs and not has_unpack:
                    param_names, required, has_star = sigs[attr]
                    total = len(param_names)
                    given = n_pos + len(kw_names)
                    unknown_kw = [k for k in kw_names if k not in param_names]
                    if unknown_kw:
                        errors.append(
                            f"引数名が違います: self.{attr}()"
                            f"（{caller} から: {', '.join(unknown_kw)}）"
                        )
                    elif not has_star and (given < required or given > total):
                        errors.append(
                            f"引数の数が違います: self.{attr}()"
                            f"（{caller} から: {given}個、定義は{total}個）"
                        )
        # 循環（直接の再帰を含む）は無限ループになるため異常にする。
        graph: dict[str, set[str]] = {}
        nodes = set(sub_names) | {"do"}
        for caller, lst in calls.items():
            if caller not in nodes:
                continue
            edges = {attr for attr, _, _, _ in lst if attr in sub_names or attr == "do"}
            # `do` への辺は循環検出に含める（sub -> do -> sub を捉える）。
            graph[caller] = edges
        visited: dict[str, int] = {}

        def _visit(cur: str, stack: list[str]) -> None:
            state = visited.get(cur, 0)
            if state == 2:
                return
            if state == 1:
                cycle = stack[stack.index(cur) :] + [cur] if cur in stack else [cur]
                errors.append(
                    "サブルーチンの循環呼び出しがあります: "
                    + " -> ".join(f"self.{c}()" for c in cycle)
                )
                return
            visited[cur] = 1
            for nxt in sorted(graph.get(cur, set())):
                _visit(nxt, [*stack, cur])
            visited[cur] = 2

        for start in sorted(graph):
            _visit(start, [])
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
    program_count = 0
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
                program_count += 1
    if program_count > 1:
        errors.append("プログラムは1個までにしてください")
    if not found_pair:
        if not any_name:
            errors.append('`NAME = "..."`（空でない文字）がありません')
        if not any_do:
            errors.append("`def do(self)` がありません")
        if any_name and any_do:
            errors.append('`NAME = "..."`（空でない文字）がありません')
            errors.append("`def do(self)` がありません")
    errors.extend(_check_subroutines(tree))
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


#: 設定ダイアログをもつブロック種。
_DIALOG_BLOCKS = frozenset(
    {
        "pokecon_dialog_choice",
        "pokecon_dialog_number",
        "pokecon_dialog_check",
    }
)

#: 設定の受け変数に使えない名前。
_DIALOG_RESERVED = frozenset({"self", "do", "NAME", "True", "False", "None"})


def _walk_blocks(block: object, found: list[dict[str, object]]) -> None:
    """ワークスペースJSON内を再帰でたどり、ブロックを集める。"""
    if not isinstance(block, dict):
        return
    found.append(block)
    inputs = block.get("inputs")
    if isinstance(inputs, dict):
        for slot in inputs.values():
            if isinstance(slot, dict) and isinstance(slot.get("block"), dict):
                _walk_blocks(slot["block"], found)
    nxt = block.get("next")
    if isinstance(nxt, dict) and isinstance(nxt.get("block"), dict):
        _walk_blocks(nxt["block"], found)


def validate_dialog_vars(text: str) -> list[str]:
    """設定ダイアログの受け変数・数値範囲を検査する。異常の一覧を返す。

    壊れたJSONはここでは黙る（validate_workspace_json 側が落とす）。
    """
    import keyword

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    blocks = data.get("blocks")
    if not isinstance(blocks, dict) or not isinstance(blocks.get("blocks"), list):
        return []
    found: list[dict[str, object]] = []
    for top in blocks["blocks"]:
        _walk_blocks(top, found)
    errors: list[str] = []
    for block in found:
        if block.get("type") not in _DIALOG_BLOCKS:
            continue
        fields = block.get("fields")
        if not isinstance(fields, dict):
            continue
        var = fields.get("VAR")
        if not isinstance(var, str) or not var.strip():
            errors.append("設定の受け変数名を書いてください")
            continue
        if (
            _STEM_RE.fullmatch(var) is None
            or var in keyword.kwlist
            or var in _DIALOG_RESERVED
        ):
            errors.append(f"受け変数名は英字・数字・`_`にしてください: {var}")
            continue
        if block.get("type") == "pokecon_dialog_number":
            try:
                lo = float(str(fields.get("MIN", "")))
                hi = float(str(fields.get("MAX", "")))
            except (TypeError, ValueError):
                errors.append(f"数値の範囲が読めません: {var}")
                continue
            if lo > hi:
                errors.append(f"数値の最小が最大を超えています: {var}")
    return errors
