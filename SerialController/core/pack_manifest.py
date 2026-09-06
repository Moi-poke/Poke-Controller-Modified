#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pokecon.json v0 の読み込みと検証（GUI非依存）。

zip梱包・導入（次plan）の土台。ここでは検証だけを持ち、zip操作・
GUI・CommandLoader への組み込みは行わない。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MANIFEST_FILENAME = "pokecon.json"

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_VERSION_RE = re.compile(
    r"(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})"
)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
_REQUIRED_FIELDS = (
    "name",
    "version",
    "author",
    "description",
    "entry",
    "minAppVersion",
    "templates",
)


@dataclass(frozen=True)
class PackManifest:
    """検証済み manifest。未検証の dict はこの型に入れない。"""

    name: str
    version: str
    author: str
    description: str
    entry: str
    minAppVersion: str
    templates: list[str]


def _is_bad_rel(value: str, *, need_py: bool = False) -> str | None:
    """zip内相対パスとして不正なら理由、正常なら None を返す。"""
    if not value or not value.strip():
        return "空です"
    if value.startswith("/") or value.startswith("\\"):
        return "絶対パスは使えません（相対で書いてください）"
    if ".." in PurePosixPath(value).parts:
        return "`..` は使えません"
    if ":" in value:
        return "`:` は使えません（ドライブ文字・代替 stream のため）"
    if need_py and not value.lower().endswith(".py"):
        return "末尾は `.py` にしてください"
    return None


def validate_manifest_data(data: object) -> list[str]:
    """未検証 dict を検査し、異常の一覧（日本語）を返す。空なら正常。"""
    if not isinstance(data, dict):
        return ["manifest は JSON オブジェクトで書いてください"]
    errors: list[str] = []
    for field in _REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"`{field}` がありません")
    if errors:
        return errors

    name = data["name"]
    if not isinstance(name, str) or _NAME_RE.fullmatch(name) is None:
        errors.append(
            "`name` は英数始まりの `[A-Za-z0-9_-]`（最大64文字）にしてください"
        )
    for field in ("version", "minAppVersion"):
        value = data[field]
        if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
            errors.append(
                f"`{field}` は `X.Y.Z`（各0〜999の数字）にしてください（例: 1.0.0）"
            )
    for field in ("author", "description"):
        value = data[field]
        limit = 128 if field == "author" else 1024
        if not isinstance(value, str) or not value.strip():
            errors.append(f"`{field}` を1文字以上書いてください")
        elif len(value) > limit:
            errors.append(f"`{field}` は{limit}文字以内にしてください")
    entry = data["entry"]
    if not isinstance(entry, str):
        errors.append(
            "`entry` は `PythonCommands/` からの相対 `.py` パスで書いてください"
        )
    else:
        reason = _is_bad_rel(entry, need_py=True)
        if reason is not None:
            errors.append(f"`entry` が不正です（{reason}）: {entry!r}")

    templates = data["templates"]
    if not isinstance(templates, list):
        errors.append("`templates` は一覧で書いてください（0件なら `[]`）")
    else:
        for item in templates:
            if not isinstance(item, str):
                errors.append(f"`templates` の要素は文字にしてください: {item!r}")
                continue
            reason = _is_bad_rel(item)
            if reason is not None:
                errors.append(f"`templates` が不正です（{reason}）: {item!r}")
                continue
            if PurePosixPath(item).suffix.lower() not in _IMAGE_SUFFIXES:
                errors.append(
                    "`templates` は `.png`/`.jpg`/`.jpeg`/`.bmp` にしてください: "
                    f"{item!r}"
                )
            if "/" not in item.replace("\\", "/"):
                pkg = data.get("name")
                example = (
                    f"Template/{pkg}/{item}"
                    if isinstance(pkg, str) and pkg
                    else "Template/<name>/..."
                )
                errors.append(
                    "`templates` は `Template/<name>/...` の形にしてください "
                    f"（例: {example}）。"
                    "素の名前は既存配布との衝突のため新規では使えません"
                )
    return errors


def load_manifest(path: str | Path) -> PackManifest:
    """`pokecon.json` を読み、検証済みなら返す。異常なら ValueError。"""
    filespec = Path(path)
    try:
        data: object = json.loads(filespec.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"manifest がありません: {filespec}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"manifest を読めません: {filespec}（{e}）") from None
    except json.JSONDecodeError as e:
        raise ValueError(
            f"manifest の JSON が壊れています: {filespec}（{e}）"
        ) from None
    errors = validate_manifest_data(data)
    if errors:
        raise ValueError("manifest が不正です:\n- " + "\n- ".join(errors))
    assert isinstance(data, dict)
    templates = data["templates"]
    assert isinstance(templates, list)
    return PackManifest(
        name=data["name"],
        version=data["version"],
        author=data["author"],
        description=data["description"],
        entry=data["entry"],
        minAppVersion=data["minAppVersion"],
        templates=[str(v) for v in templates],
    )


def validate_package_layout(pkg_dir: str | Path) -> list[str]:
    """展開済み配置（pokecon.json＋Commands/PythonCommands＋Template）を検査する。"""
    base = Path(pkg_dir)
    try:
        manifest = load_manifest(base / MANIFEST_FILENAME)
    except ValueError as e:
        return [str(e)]
    errors: list[str] = []
    entry = (
        base / "Commands" / "PythonCommands" / PurePosixPath(manifest.entry).as_posix()
    )
    if not entry.is_file():
        errors.append(
            f"`entry` の実体がありません: Commands/PythonCommands/{manifest.entry}"
        )
    for item in manifest.templates:
        if not (base / "Template" / PurePosixPath(item).as_posix()).is_file():
            errors.append(f"`templates` の実体がありません: Template/{item}")
    return errors


_SEGMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def validate_entry_segments(entry: str) -> list[str]:
    """entry の各区切りが import 可能な名前かを検査する。

    CommandLoader は受け取った相対パスをそのまま import 名にするため、
    `my-pack/MyPack.py` のような区切りは読み込み不能になる。配置前に
    ここで断り、使える書き方（例: my_pack/）を案内する。
    """
    if not isinstance(entry, str) or not entry.strip():
        return ["`entry` を書いてください"]
    parts = [p for p in entry.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        return ["`entry` が空です"]
    errors: list[str] = []
    for part in parts[:-1]:
        if _SEGMENT_RE.fullmatch(part) is None:
            errors.append(
                f"`entry` のフォルダ名は英字・数字・`_`にしてください: {part!r}"
                "（例: my_pack/）"
            )
    stem = parts[-1]
    if not stem.lower().endswith(".py"):
        errors.append("`entry` の末尾は `.py` にしてください")
    elif _SEGMENT_RE.fullmatch(stem[:-3]) is None:
        errors.append(f"`entry` のファイル名は英字・数字・`_`にしてください: {stem!r}")
    return errors
