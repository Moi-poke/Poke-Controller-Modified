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
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from core import blockly_validate
from loguru import logger
from services import blockly_templates

PY_DIR_REL = "Commands/PythonCommands"

#: stemごとの保存錠（save/delete対の直列化用）。二重クリック保存で
#: `.py`と`.blockly.json`がちぐはぐ（torn）にならないよう、対の書換えは
#: 同一stemの錠の下で行う。表自体は _locks_guard で守る。
#: 値は [Lock, 参照数]。待ちも含め参照がある間は捨てない（別物錠の並走を防ぐ）。
#: delete後は参照ゼロなら捨てて有界に保つ。満杯時も未使用の古い錠から片付ける。
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
    json_rel: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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
    errors.extend(blockly_validate.validate_dialog_vars(workspace_json))
    if errors:
        return SaveResult(
            status="failed",
            message="保存できません:\n- " + "\n- ".join(errors),
            errors=errors,
        )
    ref_errors = blockly_templates.validate_template_refs(python_code)
    if ref_errors:
        return SaveResult(
            status="failed",
            message="保存できません:\n- " + "\n- ".join(ref_errors),
            errors=ref_errors,
        )
    app = Path(app_dir)
    py_rel = (PurePosixPath(PY_DIR_REL) / f"{stem}.py").as_posix()
    json_rel = (PurePosixPath(PY_DIR_REL) / f"{stem}.blockly.json").as_posix()
    code = python_code if python_code.endswith("\n") else python_code + "\n"
    py_path = app / PurePosixPath(py_rel).as_posix()
    json_path = app / PurePosixPath(json_rel).as_posix()
    # 同一stemの対書きは錠の下で直列化する（二重クリック保存のtorn防止）。
    lock = _lock_for(stem)
    try:
        with lock:
            try:
                _atomic_write(py_path, code)
                try:
                    _atomic_write(json_path, workspace_json)
                except Exception:
                    # json側の想定外の例外でも.pyだけ残さない。HTTP受け口は
                    # 例外を握れず無応答になるため、ここで必ず結果に変える。
                    try:
                        py_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise
            except Exception as e:
                logger.warning(f"Blockly保存に失敗: {e}")
                return SaveResult(
                    status="failed", message=f"保存できません: {e}", errors=[str(e)]
                )
    finally:
        _release_lock(stem, drop=False)
    warnings = blockly_templates.warn_template_refs(python_code)
    message = f"保存しました: {py_rel}"
    if warnings:
        message += "\n注意:\n- " + "\n- ".join(warnings)
    logger.info(f"Blockly保存: {py_rel}")
    return SaveResult(
        status="saved",
        message=message,
        py_rel=py_rel,
        json_rel=json_rel,
        warnings=warnings,
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
    # 保存と同じ錠で直列化する（保存と削除の競合で片方だけ残さない）。
    lock = _lock_for(stem)
    try:
        with lock:
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
                return DeleteResult(
                    status="failed", message=f"消すものがありません: {stem}"
                )
            logger.info(f"Blockly削除: {stem}（{len(removed)}件）")
            return DeleteResult(
                status="deleted", message=f"削除しました: {stem}", removed=removed
            )
    finally:
        # 参照ゼロなら表から捨てて有界に保つ（待ち無しのときのみ安全）。
        _release_lock(stem, drop=True)
