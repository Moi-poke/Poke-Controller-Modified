#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自作スクリプト配布の導入・更新・削除・一覧の手順（GUI非依存）。

実ファイル操作だけを持ち、確認ダイアログや一覧表示は持たない。
上書きの要否は戻り値の status で返し、聞くのは呼び出し側（GUI・CLI）
の仕事にする。ヘッドレスの検証でそのまま動く。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from core import pack_manifest, pack_zip
from loguru import logger

RECORD_DIRNAME = "InstalledPacks"
BACKUP_DIRNAME = ".backup"


@dataclass
class InstallResult:
    """導入の結果。status は installed / confirm-overwrite / failed。"""

    status: str
    message: str
    manifest: pack_manifest.PackManifest | None = None
    files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    backup_rel: str | None = None
    current_version: str | None = None


@dataclass
class UninstallResult:
    """削除の結果。status は removed / failed。"""

    status: str
    message: str
    removed: list[str] = field(default_factory=list)


@dataclass
class InstalledRecord:
    """導入記録。InstalledPacks/<name>.json の中身そのもの。"""

    name: str
    version: str
    author: str
    description: str
    entry: str
    minAppVersion: str
    templates: list[str]
    files: list[str]
    installed_at: str
    zip_name: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @staticmethod
    def from_json(text: str) -> InstalledRecord:
        data = json.loads(text)
        return InstalledRecord(
            name=str(data["name"]),
            version=str(data["version"]),
            author=str(data["author"]),
            description=str(data["description"]),
            entry=str(data["entry"]),
            minAppVersion=str(data["minAppVersion"]),
            templates=[str(v) for v in data["templates"]],
            files=[str(v) for v in data["files"]],
            installed_at=str(data["installed_at"]),
            zip_name=str(data["zip_name"]),
        )


def _record_path(app_dir: Path, name: str) -> Path:
    return app_dir / RECORD_DIRNAME / f"{name}.json"


def read_record(app_dir: str | Path, name: str) -> InstalledRecord | None:
    """導入記録を読む。無ければ None（壊れていれば警告して None）。"""
    path = _record_path(Path(app_dir), name)
    if not path.is_file():
        return None
    try:
        return InstalledRecord.from_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as e:
        logger.warning(f"導入記録を読めません: {path}（{e}）")
        return None


def list_installed(app_dir: str | Path) -> list[InstalledRecord]:
    """導入済みの一覧を名前順で返す。"""
    base = Path(app_dir) / RECORD_DIRNAME
    if not base.is_dir():
        return []
    records: list[InstalledRecord] = []
    for path in sorted(base.glob("*.json")):
        rec = read_record(base.parent, path.stem)
        if rec is not None:
            records.append(rec)
    return records


def install_zip(
    app_dir: str | Path, zip_path: str | Path, *, allow_overwrite: bool = False
) -> InstallResult:
    """配布zipを導入する。上書きが必要なら confirm-overwrite で返す。"""
    app = Path(app_dir)
    with tempfile.TemporaryDirectory(prefix="pokecon_pack_") as tmp:
        staged = Path(tmp) / "staged"
        staged.mkdir()
        try:
            pack_zip.safe_extract(zip_path, staged)
        except (pack_zip.PackError, OSError, zipfile.BadZipFile) as e:
            return InstallResult(status="failed", message=f"zip を開けません: {e}")
        report = pack_zip.validate_staged(staged)
        if not report.ok or report.manifest is None:
            return InstallResult(
                status="failed",
                message="配布物が不正です:\n- " + "\n- ".join(report.errors),
            )
        manifest = report.manifest
        old = read_record(app, manifest.name)
        if old is not None and not allow_overwrite:
            if old.version == manifest.version:
                return InstallResult(
                    status="failed",
                    manifest=manifest,
                    message=f"すでに同じ版（{old.version}）が導入済みです: {manifest.name}",
                )
            return InstallResult(
                status="confirm-overwrite",
                manifest=manifest,
                warnings=list(report.warnings),
                current_version=old.version,
                message=(
                    f"{manifest.name} は版 {old.version} が導入済みです。"
                    f"版 {manifest.version} に更新します。上書きしてよいですか。"
                ),
            )
        entry_rel = (
            "Commands/PythonCommands/" + PurePosixPath(manifest.entry).as_posix()
        )
        rels = [entry_rel] + [
            "Template/" + PurePosixPath(t).as_posix() for t in manifest.templates
        ]
        json_rel = PurePosixPath(entry_rel).with_suffix(".blockly.json").as_posix()
        if (staged / json_rel).is_file():
            rels.append(json_rel)
        stamp = (
            time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000_000:09d}"
        )
        backup_root = app / RECORD_DIRNAME / BACKUP_DIRNAME / f"{manifest.name}_{stamp}"
        backed = False
        for rel in rels:
            existed = app / PurePosixPath(rel).as_posix()
            if existed.is_file():
                target = backup_root / PurePosixPath(rel).as_posix()
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(existed, target)
                backed = True
        for rel in rels:
            src = staged / PurePosixPath(rel).as_posix()
            dst = app / PurePosixPath(rel).as_posix()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        record = InstalledRecord(
            name=manifest.name,
            version=manifest.version,
            author=manifest.author,
            description=manifest.description,
            entry=manifest.entry,
            minAppVersion=manifest.minAppVersion,
            templates=list(manifest.templates),
            files=rels,
            installed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            zip_name=Path(zip_path).name,
        )
        rec_path = _record_path(app, manifest.name)
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_rec = rec_path.with_name(rec_path.name + ".tmp")
        tmp_rec.write_text(record.to_json() + "\n", encoding="utf-8")
        os.replace(tmp_rec, rec_path)
        logger.info(f"導入: {manifest.name} 版{manifest.version}（{len(rels)}件）")
        return InstallResult(
            status="installed",
            manifest=manifest,
            files=rels,
            warnings=list(report.warnings),
            backup_rel=backup_root.relative_to(app).as_posix() if backed else None,
            message=(
                f"導入しました: {manifest.name} 版{manifest.version}"
                f"（{len(rels)}件： manifestを除く）"
                + ("。上書き前の写しを残しました" if backed else "")
            ),
        )


def uninstall_package(app_dir: str | Path, name: str) -> UninstallResult:
    """導入記録に従って実体を消す。空になった配下フォルダだけ畳む。"""
    app = Path(app_dir)
    if not name or "/" in name or "\\" in name or ".." in name or ":" in name:
        return UninstallResult(status="failed", message=f"不正な名前です: {name}")
    rec = read_record(app, name)
    if rec is None:
        return UninstallResult(status="failed", message=f"導入記録がありません: {name}")
    removed: list[str] = []
    missing: list[str] = []
    for rel in rec.files:
        path = app / PurePosixPath(rel).as_posix()
        if path.is_file():
            path.unlink()
            removed.append(rel)
        else:
            missing.append(rel)
    roots = {app / "Commands" / "PythonCommands", app / "Template"}
    for rel in rec.files:
        folder = (app / PurePosixPath(rel).as_posix()).parent
        while folder != app and folder not in roots and folder.is_dir():
            try:
                next(folder.iterdir())
                break
            except StopIteration:
                folder.rmdir()
                folder = folder.parent
    _record_path(app, name).unlink(missing_ok=True)
    logger.info(f"削除: {name}（{len(removed)}件）")
    message = f"削除しました: {name}（{len(removed)}件）"
    if missing:
        message += f"。見つからなかったもの: {', '.join(missing)}"
    return UninstallResult(status="removed", message=message, removed=removed)
