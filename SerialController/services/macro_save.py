#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""録画マクロの保存手順（GUI非依存）。

`.py`のみ書く（`.blockly.json`側車は作らない）。上書きは許す
（blockly_save と同じ方針：作者本人の下書きのため退避は作らない）。
"""

from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from core import blockly_validate
from loguru import logger
from services import blockly_templates

PY_DIR_REL = "Commands/PythonCommands"

#: stemごとの保存錠（二重クリック保存の直列化用）。単一`.py`でも
#: tmp+os.replace の置き換え同士が競合しないよう同一stemで直列化する。
_save_locks: dict[str, list[Any]] = {}
_locks_guard = threading.Lock()
_MAX_SAVE_LOCKS = 512


def _lock_for(stem: str) -> threading.Lock:
    """stemの錠を返す（参照数を増やす）。表の操作は錠で守る。"""
    with _locks_guard:
        entry = _save_locks.get(stem)
        if entry is not None:
            entry[1] += 1
            return entry[0]
        if len(_save_locks) >= _MAX_SAVE_LOCKS:
            for old, (old_lock, refs) in list(_save_locks.items()):
                if refs == 0 and not old_lock.locked():
                    del _save_locks[old]
                    break
        lock = threading.Lock()
        _save_locks[stem] = [lock, 1]
        return lock


def _release_lock(stem: str, *, drop: bool = False) -> None:
    """参照を1つ返す。drop時は誰も掴んでいなければ表から捨てる。"""
    with _locks_guard:
        entry = _save_locks.get(stem)
        if entry is None:
            return
        entry[1] -= 1
        if entry[1] < 0:
            entry[1] = 0
        if drop and entry[1] == 0:
            _save_locks.pop(stem, None)


@dataclass
class SaveResult:
    """保存の結果。statusは saved / failed。"""

    status: str
    message: str
    py_rel: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _atomic_write(target: Path, text: str) -> None:
    """一時ファイル経由で書く。書けたらのみ置き換える。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp_macro_")
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


def save_macro(
    app_dir: str | Path,
    stem: str,
    code: str,
) -> SaveResult:
    """録画マクロを保存する。検証に落ちたら何も書かずfailedで返す。"""
    errors = blockly_validate.validate_stem(stem)
    errors.extend(blockly_validate.validate_generated_code(code))
    if errors:
        return SaveResult(
            status="failed",
            message="保存できません:\n- " + "\n- ".join(errors),
            errors=errors,
        )
    ref_errors = blockly_templates.validate_template_refs(code)
    if ref_errors:
        return SaveResult(
            status="failed",
            message="保存できません:\n- " + "\n- ".join(ref_errors),
            errors=ref_errors,
        )
    app = Path(app_dir)
    py_rel = (PurePosixPath(PY_DIR_REL) / f"{stem}.py").as_posix()
    text = code if code.endswith("\n") else code + "\n"
    py_path = app / PurePosixPath(py_rel).as_posix()
    lock = _lock_for(stem)
    try:
        with lock:
            try:
                _atomic_write(py_path, text)
            except Exception as e:
                logger.warning(f"マクロ保存に失敗: {e}")
                return SaveResult(
                    status="failed", message=f"保存できません: {e}", errors=[str(e)]
                )
    finally:
        _release_lock(stem, drop=False)
    warnings = blockly_templates.warn_template_refs(code)
    message = f"保存しました: {py_rel}"
    if warnings:
        message += "\n注意:\n- " + "\n- ".join(warnings)
    logger.info(f"マクロ保存: {py_rel}")
    return SaveResult(
        status="saved",
        message=message,
        py_rel=py_rel,
        warnings=warnings,
    )
