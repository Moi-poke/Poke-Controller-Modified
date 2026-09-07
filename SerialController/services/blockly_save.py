#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blockly編集結果の保存手順（GUI非依存）。

`.py`と`.blockly.json`を対で書く。`.blockly.json`は
`Utility.getModuleNames`（`.py`のみ走査）のため一覧を壊さない。
上書きは許す（作者本人の下書きのため退避は作らない）。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from core import blockly_validate
from loguru import logger

PY_DIR_REL = "Commands/PythonCommands"


@dataclass
class SaveResult:
    """保存の結果。statusは saved / failed。"""

    status: str
    message: str
    py_rel: str = ""
    json_rel: str = ""
    errors: list[str] = field(default_factory=list)


def _atomic_write(target: Path, text: str) -> None:
    """一時ファイル経由で書く。書けたらのみ置き換える。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp_blockly_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fp:
            fp.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_blockly(
    app_dir: str | Path,
    stem: str,
    workspace_json: str,
    python_code: str,
) -> SaveResult:
    """編集結果を保存する。検証に落ちたら何も書かずfailedで返す。"""
    errors = blockly_validate.validate_stem(stem)
    errors.extend(blockly_validate.validate_workspace_json(workspace_json))
    errors.extend(blockly_validate.validate_generated_code(python_code))
    if errors:
        return SaveResult(
            status="failed",
            message="保存できません:\n- " + "\n- ".join(errors),
            errors=errors,
        )
    app = Path(app_dir)
    py_rel = (PurePosixPath(PY_DIR_REL) / f"{stem}.py").as_posix()
    json_rel = (PurePosixPath(PY_DIR_REL) / f"{stem}.blockly.json").as_posix()
    code = python_code if python_code.endswith("\n") else python_code + "\n"
    py_path = app / PurePosixPath(py_rel).as_posix()
    json_path = app / PurePosixPath(json_rel).as_posix()
    try:
        _atomic_write(py_path, code)
        try:
            _atomic_write(json_path, workspace_json)
        except OSError:
            try:
                py_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.warning(f"Blockly保存に失敗: {e}")
        return SaveResult(
            status="failed", message=f"保存できません: {e}", errors=[str(e)]
        )
    logger.info(f"Blockly保存: {py_rel}")
    return SaveResult(
        status="saved",
        message=f"保存しました: {py_rel}",
        py_rel=py_rel,
        json_rel=json_rel,
    )


def list_blockly(app_dir: str | Path) -> list[str]:
    """`.blockly.json`を持つ保存名の一覧を名前順で返す。"""
    base = Path(app_dir) / PurePosixPath(PY_DIR_REL).as_posix()
    if not base.is_dir():
        return []
    return sorted(p.name[: -len(".blockly.json")] for p in base.glob("*.blockly.json"))


@dataclass
class LoadResult:
    """読み込みの結果。statusは ok / failed。"""

    status: str
    message: str
    workspace_json: str = ""


def load_blockly(app_dir: str | Path, stem: str) -> LoadResult:
    """`.blockly.json`を読み、再編集用のJSONを返す。無ければfailed。"""
    errors = blockly_validate.validate_stem(stem)
    if errors:
        return LoadResult(
            status="failed", message="開けません:\n- " + "\n- ".join(errors)
        )
    path = Path(app_dir) / PurePosixPath(PY_DIR_REL).as_posix() / f"{stem}.blockly.json"
    if not path.is_file():
        return LoadResult(status="failed", message=f"編集データがありません: {stem}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"Blockly読込に失敗: {e}")
        return LoadResult(status="failed", message=f"開けません: {e}")
    errors = blockly_validate.validate_workspace_json(text)
    if errors:
        return LoadResult(
            status="failed", message="開けません:\n- " + "\n- ".join(errors)
        )
    return LoadResult(status="ok", message=f"開きました: {stem}", workspace_json=text)


@dataclass
class DeleteResult:
    """削除の結果。statusは deleted / failed。"""

    status: str
    message: str
    removed: list[str] = field(default_factory=list)


def delete_blockly(app_dir: str | Path, stem: str) -> DeleteResult:
    """`.py`と`.blockly.json`の対を消す。両方無ければfailed。"""
    errors = blockly_validate.validate_stem(stem)
    if errors:
        return DeleteResult(
            status="failed", message="削除できません:\n- " + "\n- ".join(errors)
        )
    app = Path(app_dir)
    rels = [
        (PurePosixPath(PY_DIR_REL) / f"{stem}.py").as_posix(),
        (PurePosixPath(PY_DIR_REL) / f"{stem}.blockly.json").as_posix(),
    ]
    removed: list[str] = []
    for rel in rels:
        path = app / PurePosixPath(rel).as_posix()
        try:
            if path.is_file():
                path.unlink()
                removed.append(rel)
        except OSError as e:
            logger.warning(f"Blockly削除に失敗: {e}")
            return DeleteResult(status="failed", message=f"削除できません: {e}")
    if not removed:
        return DeleteResult(status="failed", message=f"消すものがありません: {stem}")
    logger.info(f"Blockly削除: {stem}（{len(removed)}件）")
    return DeleteResult(
        status="deleted", message=f"削除しました: {stem}", removed=removed
    )
