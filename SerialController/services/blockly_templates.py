#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Template画像の一覧と生成コード内の参照検査（GUI非依存）。

一覧は配布zipの `Template/<名>/...` 規約と同方向の相対表示にする。
参照検査は書式のみを見て存在は見ない。存在の有無は実行時の
`FileNotFoundError` が置き場所付きで知らせるため。
"""

from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath

TEMPLATE_DIR_REL = "Template"

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}

#: テンプレート参照を持つVision API名（第1引数または一覧引数）。
VISION_TEMPLATE_APIS = frozenset(
    {
        "isContainTemplate",
        "isContainTemplateDump",
        "isContainTemplateGPU",
        "isContainTemplate_max",
        "waitTemplate",
        "waitTemplateGone",
        "getTemplatePosition",
        "findAllTemplates",
        "countTemplate",
        "preloadTemplates",
    }
)

#: 単一パスを受け取るキーワード名。
_TEMPLATE_PATH_KWS = frozenset({"template_path", "mask_path"})

#: 一覧を受け取るキーワード名。
_LIST_TEMPLATE_KWS = frozenset({"template_path_list", "template_paths"})

#: 素名への警告文。
BARE_TEMPLATE_HINT = (
    "共有画像のため配布zipに含まれません。"
    "配布する場合は `Template/<名>/...` に置いてください"
)

#: 動的指定への警告文（落とさず注意だけする）。
DYNAMIC_TEMPLATE_HINT = (
    "動的なテンプレート参照があります（配布に含まれるか確認してください）"
)


def list_image_templates(app_dir: str | Path) -> list[str]:
    """`Template/` 以下の画像を相対（posix）で名前順に返す。無ければ空。"""
    base = Path(app_dir) / TEMPLATE_DIR_REL
    if not base.is_dir():
        return []
    found: list[str] = []
    for path in base.rglob("*"):
        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES:
            rel = path.relative_to(base).as_posix()
            found.append(PurePosixPath(rel).as_posix())
    return sorted(found)


def _static_value(node: ast.expr) -> str | None:
    """文字定数なら中身、そうでなければ None を返す。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _static_list_values(node: ast.expr) -> list[str] | None:
    """文字定数の一覧なら中身、1つでも動的なら None を返す。"""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values: list[str] = []
    for elt in node.elts:
        value = _static_value(elt)
        if value is None:
            return None
        values.append(value)
    return values


def _collect_static(node: ast.expr, out: list[str]) -> None:
    """単一または一覧の静的指定を集める。動的は無視する。"""
    value = _static_value(node)
    if value is not None:
        out.append(value)
        return
    listed = _static_list_values(node)
    if listed is not None:
        out.extend(listed)


def _is_dynamic(node: ast.expr) -> bool:
    """静的に読めない指定なら True（一覧内の動的も含む）。"""
    if _static_value(node) is not None:
        return False
    if isinstance(node, (ast.List, ast.Tuple)):
        return _static_list_values(node) is None
    return True


def _template_args(python_code: str) -> list[str]:
    """vision系API呼び出しのテンプレ指定（文字定数のみ）を集める。

    位置の第1引数と `template_path=`・`mask_path=`・
    `template_path_list=`（`template_paths=`）の両方を見る。
    変数・f文字列などの動的指定は含めない（落とさず警告にする）。
    """
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return []
    args: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in VISION_TEMPLATE_APIS):
            continue
        if node.args:
            _collect_static(node.args[0], args)
        for kw in node.keywords:
            if kw.arg in _TEMPLATE_PATH_KWS:
                value = _static_value(kw.value)
                if value is not None:
                    args.append(value)
            elif kw.arg in _LIST_TEMPLATE_KWS:
                _collect_static(kw.value, args)
    return args


def _has_dynamic_template_arg(python_code: str) -> bool:
    """動的なテンプレ指定があれば True（保存は通し警告だけ出す）。"""
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in VISION_TEMPLATE_APIS):
            continue
        if node.args and _is_dynamic(node.args[0]):
            return True
        for kw in node.keywords:
            if kw.arg in _TEMPLATE_PATH_KWS and _static_value(kw.value) is None:
                return True
            if kw.arg in _LIST_TEMPLATE_KWS and _is_dynamic(kw.value):
                return True
    return False


def _bad_reason(value: str) -> str | None:
    """テンプレ参照として不正なら理由、正常なら None を返す。"""
    if not value.strip():
        return "テンプレート名を書いてください"
    probe = value.replace("\\", "/")
    if probe.startswith("/") or (len(probe) > 1 and probe[1] == ":"):
        return "絶対パスは使えません（`Template/` からの相対で書いてください）"
    if ".." in PurePosixPath(probe).parts:
        return "`..` は使えません"
    if ":" in value:
        return "`:` は使えません"
    return None


def validate_template_refs(python_code: str) -> list[str]:
    """生成コード内のテンプレ参照を検査する。異常の一覧を返す。"""
    errors: list[str] = []
    for value in _template_args(python_code):
        reason = _bad_reason(value)
        if reason is not None:
            errors.append(f"テンプレート名が不正です（{reason}）: {value!r}")
    return errors


def warn_template_refs(python_code: str) -> list[str]:
    """配布に含まれない素名参照への注意を返す。保存は通す。"""
    warnings: list[str] = []
    seen: set[str] = set()
    for value in _template_args(python_code):
        if value in seen:
            continue
        seen.add(value)
        if _bad_reason(value) is not None:
            continue
        if "/" not in value.replace("\\", "/"):
            warnings.append(f"{value!r} は{BARE_TEMPLATE_HINT}")
    if _has_dynamic_template_arg(python_code):
        warnings.append(DYNAMIC_TEMPLATE_HINT)
    return warnings
