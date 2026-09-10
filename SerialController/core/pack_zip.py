#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配布zipの生成と安全な展開（GUI非依存）。

zip配置 v0: ルート直下に `pokecon.json`＋`Commands/PythonCommands/<entry>`＋
`Template/<name>/...` を持つ（実ツリーと同じ形）。展開・検証・梱包の順に使う。

安全の考え方:
  zipは他人が作ったものとして扱う。絶対パス・`..`・ドライブ文字・
  シンボリックリンクは展開前に断り、件数と容量に上限を付ける。
  entry の import 走査の許可集合は `core/user_api_allowlist.py` が正本。
  深刻度（仕様）: 相対 import・公開API面外の Commands 経路は異常
  （導入不可）、見知らぬトップレベルは注意（導入は通す）。保存時
  （blockly_validate）の異常扱いとは違い、ここでは先方の環境差を
  注意で残す。
"""

from __future__ import annotations

import ast
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from core import pack_manifest
from core.pack_manifest import PackManifest
from core.user_api_allowlist import ALLOWED_COMMANDS_SUBS, THIRD_PARTY

ZIP_MANIFEST = "pokecon.json"
MAX_ZIP_FILES = 1000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024

# 正本は core/user_api_allowlist.py。後方互換のため旧名でも読める。
_ALLOWED_COMMANDS_SUBS = ALLOWED_COMMANDS_SUBS
_THIRD_PARTY = THIRD_PARTY


class PackError(ValueError):
    """zipの形・容量・配置の異常。利用者にそのまま見せられる文面にする。"""


@dataclass
class StagedReport:
    """展開済み配置の検証結果。ok のときだけ manifest が入る。"""

    manifest: PackManifest | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.manifest is not None


def scan_entry_imports(entry_file: Path) -> tuple[list[str], list[str]]:
    """entry の import を走査する。(異常, 注意) を返す。

    異常（導入不可）: 相対 import・公開API面外の Commands 経路。
    注意（導入は通す）: 見知らぬトップレベル（導入先で動かない可能性）。
    """
    try:
        tree = ast.parse(
            Path(entry_file).read_text(encoding="utf-8"),
            filename=Path(entry_file).name,
        )
    except (OSError, UnicodeDecodeError) as e:
        return ([f"entry を読めません: {e}"], [])
    except SyntaxError as e:
        return ([f"entry の文法が壊れています（{e}）"], [])
    allowed_top = set(sys.stdlib_module_names) | _THIRD_PARTY | {"Commands"}
    errors: list[str] = []
    warnings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top not in allowed_top:
                    warnings.append(
                        f"未知の import があります: import {a.name}"
                        "（導入先で動かない可能性があります）"
                    )
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
                    warnings.append(
                        f"未知の import があります: from {node.module} import ..."
                        "（導入先で動かない可能性があります）"
                    )
    return (errors, warnings)


def _checked_rel(info: zipfile.ZipInfo) -> str | None:
    """zip1件の配置先（posix相対）を返す。置けないものは PackError。

    ディレクトリ項目は None を返す（作るだけで検証は要らない）。
    """
    raw = info.filename.replace("\\", "/")
    if raw.endswith("/"):
        return None
    parts = PurePosixPath(raw).parts
    if (
        not raw
        or raw.startswith("/")
        or PurePosixPath(raw).is_absolute()
        or ".." in parts
        or ":" in raw
    ):
        raise PackError(f"置けないパスです: {info.filename!r}")
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        raise PackError(f"シンボリックリンクは置けません: {info.filename!r}")
    return PurePosixPath(raw).as_posix()


def safe_extract(zip_path: str | Path, dest_dir: str | Path) -> list[str]:
    """zipを dest_dir へ安全に展開し、置いた相対の一覧を返す。"""
    dest = Path(dest_dir)
    files: list[str] = []
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ZIP_FILES:
            raise PackError(f"件数が多すぎます（上限 {MAX_ZIP_FILES} 件）")
        rels: list[tuple[zipfile.ZipInfo, str]] = []
        for info in infos:
            rel = _checked_rel(info)
            if rel is None:
                continue
            if info.file_size > MAX_FILE_BYTES:
                raise PackError(f"大きすぎるファイルがあります: {rel}")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise PackError(
                    f"合計が大きすぎます（上限 {MAX_TOTAL_BYTES // 1024 // 1024} MiB）"
                )
            rels.append((info, rel))
        for info, rel in rels:
            target = dest / PurePosixPath(rel).as_posix()
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            files.append(rel)
    return files


def validate_staged(staged_dir: str | Path) -> StagedReport:
    """展開済み配置を検証する。manifest・実体・区切り・import を見る。"""
    base = Path(staged_dir)
    try:
        manifest = pack_manifest.load_manifest(base / pack_manifest.MANIFEST_FILENAME)
    except ValueError as e:
        return StagedReport(manifest=None, errors=[str(e)])
    errors = pack_manifest.validate_package_layout(base)
    errors.extend(pack_manifest.validate_entry_segments(manifest.entry))
    warnings: list[str] = []
    entry_file = (
        base / "Commands" / "PythonCommands" / PurePosixPath(manifest.entry).as_posix()
    )
    if entry_file.is_file():
        import_errors, import_warnings = scan_entry_imports(entry_file)
        errors.extend(import_errors)
        warnings.extend(import_warnings)
    return StagedReport(manifest=manifest, errors=errors, warnings=warnings)


def create_pack(src_dir: str | Path, out_zip: str | Path) -> list[str]:
    """配置済みフォルダから配布zipを作り、収録名の一覧を返す。"""
    report = validate_staged(src_dir)
    if not report.ok or report.manifest is None:
        raise PackError("梱包できません:\n- " + "\n- ".join(report.errors))
    manifest = report.manifest
    base = Path(src_dir)
    targets = [PurePosixPath("Commands/PythonCommands") / manifest.entry] + [
        PurePosixPath("Template") / t for t in manifest.templates
    ]
    entry_rel = PurePosixPath("Commands/PythonCommands") / manifest.entry
    json_rel = entry_rel.with_suffix(".blockly.json")
    if (base / json_rel.as_posix()).is_file():
        targets.append(json_rel)
    names: list[str] = [ZIP_MANIFEST]
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(base / ZIP_MANIFEST, ZIP_MANIFEST)
        for target in targets:
            arc = target.as_posix()
            zf.write(base / arc, arc)
            names.append(arc)
    return names
