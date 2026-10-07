#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blockly編集結果の保存手順（GUI非依存）。

`.py`と`.blockly.json`を対で書く。`.blockly.json`は
`Utility.getModuleNames`（`.py`のみ走査）のため一覧を壊さない。
上書きは許す（作者本人の下書きのため退避は作らない）。
"""

from __future__ import annotations

import hashlib
import json
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

#: `.blockly.json` 内の PokeCon 用記録欄。Blockly の読込は未知の最上位欄を
#: 無視するが、開くときは取り除いて返す（ブロック以外を編集画面へ渡さない）。
RECORD_KEY = "pokecon"

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


def _py_digest(text: str) -> str:
    """`.py` 中身の要約値。改行はLFへ寄せる（git の autocrlf で揺れないように）。"""
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _with_record(workspace_json: str, code: str) -> str:
    """書いた `.py` の要約値を記録欄へ入れたJSON文字列を返す。

    手編集の検出は更新時刻では当てにならない（git取り出し・複写・整形で
    前後が入れ替わる）ため、中身の要約値で比べる。
    """
    data = json.loads(workspace_json)
    data[RECORD_KEY] = {"pySha256": _py_digest(code)}
    return json.dumps(data, ensure_ascii=False)


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
    stored_json = _with_record(workspace_json, code)
    py_path = app / PurePosixPath(py_rel).as_posix()
    json_path = app / PurePosixPath(json_rel).as_posix()
    # 同一stemの対書きは錠の下で直列化する（二重クリック保存のtorn防止）。
    lock = _lock_for(stem)
    try:
        with lock:
            try:
                _atomic_write(py_path, code)
                try:
                    _atomic_write(json_path, stored_json)
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


#: 作例（ギャラリー）の接頭辞。`Commands/PythonCommands/` 内の
#: `BlocklySample*.blockly.json`（同名 `.py` と対のもの）を作例として扱う。
SAMPLE_PREFIX = "BlocklySample"

#: 作例の説明の上限（文字数）。超えた分は `…` で丸める。
SAMPLE_SUMMARY_MAX = 40


def _sample_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    """ワークスペースJSON内の実ブロックを文書順に集める（影は除く）。"""
    tops = data.get("blocks")
    if not isinstance(tops, dict) or not isinstance(tops.get("blocks"), list):
        return []
    found: list[dict[str, Any]] = []

    def _walk(block: object) -> None:
        if not isinstance(block, dict):
            return
        found.append(block)
        inputs = block.get("inputs")
        if isinstance(inputs, dict):
            for slot in inputs.values():
                if isinstance(slot, dict) and isinstance(slot.get("block"), dict):
                    _walk(slot["block"])
        nxt = block.get("next")
        if isinstance(nxt, dict) and isinstance(nxt.get("block"), dict):
            _walk(nxt["block"])

    for top in tops["blocks"]:
        _walk(top)
    return found


def _sample_word(block_type: str) -> str | None:
    """ブロック種から説明用の短い語を返す。説明に要らない種は None。"""
    if block_type in ("pokecon_program", "pokecon_comment"):
        return None
    if block_type.startswith(("pokecon_press", "pokecon_hold", "pokecon_stick")):
        return "押す"
    if block_type.startswith("pokecon_vision"):
        return "画像認識"
    if block_type.startswith("pokecon_audio"):
        return "音検知"
    if block_type in {
        "controls_repeat",
        "controls_repeat_ext",
        "controls_whileUntil",
        "controls_for",
        "controls_forEach",
        "controls_flow_statements",
    }:
        return "繰り返し"
    if block_type == "controls_if" or block_type.startswith("logic_"):
        return "条件分岐"
    if block_type.startswith("variables_"):
        return "変数"
    if block_type.startswith(("math_", "text")):
        return "計算"
    if block_type == "pokecon_wait":
        return "待ち"
    if block_type in {"pokecon_print", "pokecon_screenshot", "pokecon_discord"}:
        return "出力"
    if block_type.startswith("pokecon_dialog"):
        return "設定入力"
    if block_type.startswith("pokecon_sub"):
        return "サブルーチン"
    if block_type == "pokecon_elapsed":
        return "時間制限"
    if block_type == "pokecon_finish":
        return "終了"
    return None


def _shorten_summary(text: str) -> str:
    """説明文を上限に丸める（超えたら末尾を `…` にする）。"""
    s = text.strip()
    if len(s) > SAMPLE_SUMMARY_MAX:
        return s[: SAMPLE_SUMMARY_MAX - 1] + "…"
    return s


def _sample_summary(blocks: list[dict[str, Any]]) -> str:
    """作例の説明を作る。先頭のコメントがあればその1行目、無ければ
    使っているブロックの種類から短く作る（最大40文字）。"""
    program = next((b for b in blocks if b.get("type") == "pokecon_program"), None)
    if program is not None:
        # 先頭＝プログラムの DO の最初のブロック。注釈だけが目的のため、
        # コメント以外の先頭では種類からの合成に落とす。
        node = program.get("inputs")
        if isinstance(node, dict):
            slot = node.get("DO")
            first = slot.get("block") if isinstance(slot, dict) else None
            if isinstance(first, dict) and first.get("type") == "pokecon_comment":
                fields = first.get("fields")
                text = fields.get("TEXT") if isinstance(fields, dict) else None
                if isinstance(text, str) and text.strip():
                    return _shorten_summary(text.strip().splitlines()[0])
    words: list[str] = []
    for block in blocks:
        word = _sample_word(str(block.get("type", "")))
        if word is not None and word not in words:
            words.append(word)
    if not words:
        return "ブロックの組み合わせ"
    return _shorten_summary("・".join(words))


def _sample_tags(program: dict[str, Any] | None) -> list[str]:
    """プログラム欄の TAGS 欄（カンマ区切り）を配列にする。"""
    if program is None:
        return []
    fields = program.get("fields")
    raw = fields.get("TAGS") if isinstance(fields, dict) else None
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    if not isinstance(raw, str):
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def list_samples(app_dir: str | Path) -> list[dict[str, Any]]:
    """作例の一覧を保存名順で返す。壊れた作例は飛ばす（例外を外へ出さない）。"""
    base = Path(app_dir) / PurePosixPath(PY_DIR_REL).as_posix()
    if not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for json_path in sorted(base.glob(f"{SAMPLE_PREFIX}*.blockly.json")):
        stem = json_path.name[: -len(".blockly.json")]
        try:
            # `.py` と対になっていない作例は一覧に出さない（実行できないため）。
            if not json_path.with_name(f"{stem}.py").is_file():
                continue
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            blocks = [b for b in _sample_blocks(data) if isinstance(b.get("type"), str)]
            if not blocks:
                continue
            program = next(
                (b for b in blocks if b.get("type") == "pokecon_program"), None
            )
            name = stem
            if program is not None:
                fields = program.get("fields")
                raw_name = fields.get("NAME") if isinstance(fields, dict) else None
                if isinstance(raw_name, str) and raw_name.strip():
                    name = raw_name.strip()
            out.append(
                {
                    "stem": stem,
                    "name": name,
                    "tags": _sample_tags(program),
                    "blocks": len(blocks),
                    "summary": _sample_summary(blocks),
                }
            )
        except Exception as e:
            # 壊れた作例は飛ばす（一覧全体を壊さない）。
            logger.warning(f"作例を飛ばします: {stem}: {e}")
            continue
    out.sort(key=lambda d: str(d["stem"]))
    return out


@dataclass
class LoadResult:
    """読み込みの結果。statusは ok / failed。"""

    status: str
    message: str
    workspace_json: str = ""
    #: `.py` が保存時の中身と違う（手で書き換えられた可能性）。記録の無い
    #: 旧保存物・`.py` 欠損は判定できないため偽にする。
    external_edit: bool = False


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
    data = json.loads(text)
    record = data.pop(RECORD_KEY, None)
    if record is None:
        return LoadResult(
            status="ok", message=f"開きました: {stem}", workspace_json=text
        )
    external = False
    digest = record.get("pySha256") if isinstance(record, dict) else None
    if isinstance(digest, str):
        try:
            current = path.with_name(f"{stem}.py").read_bytes().decode("utf-8")
            external = _py_digest(current) != digest
        except (OSError, UnicodeDecodeError):
            external = False
    return LoadResult(
        status="ok",
        message=f"開きました: {stem}",
        workspace_json=json.dumps(data, ensure_ascii=False),
        external_edit=external,
    )


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
