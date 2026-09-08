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

#: 第1引数がテンプレートパスのVision API名。
VISION_TEMPLATE_APIS = frozenset(
    {
        "isContainTemplate",
        "isContainTemplateDump",
        "waitTemplate",
        "waitTemplateGone",
        "getTemplatePosition",
        "findAllTemplates",
        "countTemplate",
    }
)

#: 素名への警告文。
BARE_TEMPLATE_HINT = (
    "共有画像のため配布zipに含まれません。"
    "配布する場合は `Template/<名>/...` に置いてください"
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


def _template_args(python_code: str) -> list[str]:
    """vision系API呼び出しの第1引数（文字定数のみ）を集める。"""
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
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                args.append(value)
    return args


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
    return warnings
