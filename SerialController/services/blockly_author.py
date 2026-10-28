#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""コンパクト仕様から Blockly の保存物を作る（GUI非依存）。

エージェント向けの小さな仕様（`docs/design-blockly-agent.md`）を、
決定的に Blockly の workspace JSON へ組み、本物の Blockly（node）で
Python を生成し、保存前検査を通してから `.blockly.json` / `.py` の対を
保存する。例外は外へ投げず、日本語のエラー一覧で返す。
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import keyword
import re
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path, PurePosixPath
from typing import Any

from core import blockly_headless, blockly_validate
from services import blockly_match, blockly_save, blockly_templates

_STEM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CROP_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$")
_DYNAMIC_IF_INPUT_RE = re.compile(r"^(IF|DO)(\d+)$")

#: 仕様のトップレベルで許す鍵。
_TOP_KEYS = frozenset({"stem", "name", "tags", "do", "subs"})

#: 短縮形（文）の一覧。
_SHORT_FORMS = frozenset(
    {
        "press",
        "hold",
        "release",
        "stick",
        "stick_off",
        "wait",
        "press_rep",
        "repeat",
        "while",
        "if",
        "break",
        "continue",
        "print",
        "comment",
        "finish",
        "set",
        "change",
        "call",
        "call_value",
        "screenshot",
        "discord",
        "dialog_choice",
        "dialog_number",
        "dialog_check",
    }
)

#: 短縮形ごとに許す追加項目。`press` の `wait` のように短縮形名と
#: パラメータ名が衝突するため、残りの項目を説明できる形で主キーを選ぶ。
_SHORT_PARAMS: dict[str, frozenset[str]] = {
    "press": frozenset({"duration", "wait"}),
    "hold": frozenset({"wait"}),
    "release": frozenset(),
    "stick": frozenset({"angle", "duration", "wait"}),
    "stick_off": frozenset(),
    "wait": frozenset(),
    "press_rep": frozenset({"count", "duration", "interval", "wait"}),
    "repeat": frozenset({"do"}),
    "while": frozenset({"do"}),
    "if": frozenset({"do", "else"}),
    "break": frozenset(),
    "continue": frozenset(),
    "print": frozenset({"kind", "result"}),
    "comment": frozenset(),
    "finish": frozenset(),
    "set": frozenset({"to"}),
    "change": frozenset({"by"}),
    "call": frozenset({"args"}),
    "call_value": frozenset({"args"}),
    "screenshot": frozenset(),
    "discord": frozenset({"kind"}),
    "dialog_choice": frozenset({"var", "title", "label", "options", "default"}),
    "dialog_number": frozenset({"var", "title", "label", "min", "max", "default"}),
    "dialog_check": frozenset({"var", "title", "label", "default"}),
}

#: 設定ダイアログのブロック種。VAR は Blockly 変数ではなく Python の文字列。
_DIALOG_BLOCKS = frozenset(
    {"pokecon_dialog_choice", "pokecon_dialog_number", "pokecon_dialog_check"}
)

#: 表示専用で、仕様に書かれても無視するフィールド。
_IGNORED_FIELDS = frozenset({"PREVIEW", "CAPOPEN"})

#: サブルーチン呼び出しブロックの最大引数（Blockly ブロックの制約）。
_MAX_SUB_ARGS = 3

#: サブルーチン名に使えない名前（生成コードの構造と衝突する）。
_RESERVED_SUB_NAMES = frozenset({"do", "__init__", "self", "NAME", "TAGS"})

#: 設定ダイアログの受け変数に使えない名前（core.blockly_validate と同方向）。
_DIALOG_RESERVED = frozenset({"self", "do", "NAME", "True", "False", "None"})


@dataclass
class BuildResult:
    """仕様→workspace JSON の結果。"""

    workspace: dict[str, Any] | None
    errors: list[str] = dataclass_field(default_factory=list)
    warnings: list[str] = dataclass_field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.workspace is not None and not self.errors


@dataclass
class CheckResult:
    """保存前検査の結果。"""

    ok: bool
    errors: list[str] = dataclass_field(default_factory=list)
    warnings: list[str] = dataclass_field(default_factory=list)
    workspace: dict[str, Any] | None = None
    code: str = ""
    stem: str | None = None


@dataclass
class RenderResult:
    """workspace JSON → Python の結果。"""

    ok: bool
    code: str = ""
    errors: list[str] = dataclass_field(default_factory=list)
    warnings: list[str] = dataclass_field(default_factory=list)


@dataclass
class PatchResult:
    """workspace の部分編集の結果。"""

    workspace: dict[str, Any] | None
    errors: list[str] = dataclass_field(default_factory=list)
    changes: list[dict[str, Any]] = dataclass_field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.workspace is not None and not self.errors


def build_workspace(spec: Any, catalog: Any) -> BuildResult:
    """コンパクト仕様を workspace JSON へ組み立てる。例外は投げない。"""
    try:
        builder = _Builder(catalog)
        workspace = builder.build(spec)
    except Exception as e:  # 仕様が想定外の型でも落とさない
        return BuildResult(None, errors=[f"仕様を組み立てられませんでした（{e}）"])
    if builder.errors:
        return BuildResult(None, builder.errors, builder.warnings)
    return BuildResult(workspace, [], builder.warnings)


def check_spec(spec: Any, app_dir: str | Path) -> CheckResult:
    """仕様を組み立て、保存前検査を順に通す。例外は投げない。"""
    try:
        return _check_spec(spec, app_dir)
    except Exception as e:
        return CheckResult(False, errors=[f"検査できませんでした（{e}）"])


def check_workspace(workspace_json: Any, app_dir: str | Path) -> CheckResult:
    """既存 workspace JSON を、保存前検査と同じ経路で検査する。

    ブロックID・配置・コメント・変数はそのまま保持し、生成コードの検査だけを
    共通化する。仕様（コンパクトJSON）からの再構築はしない。
    """
    try:
        return _check_workspace(workspace_json, app_dir)
    except Exception as e:
        return CheckResult(False, errors=[f"検査できませんでした（{e}）"])


def render_workspace(workspace_json: Any, app_dir: str | Path) -> RenderResult:
    """workspace JSON から Python を生成する。例外は投げない。"""
    try:
        return _render_workspace(workspace_json, app_dir)
    except Exception as e:
        return RenderResult(False, errors=[f"生成できませんでした（{e}）"])


def save_spec(
    spec: Any, app_dir: str | Path, stem: str | None = None
) -> blockly_save.SaveResult:
    """検査が通った仕様を `.py` / `.blockly.json` の対として保存する。"""
    try:
        resolved = stem if stem is not None else _spec_stem(spec)
        if not isinstance(resolved, str) or not resolved.strip():
            message = "保存名（stem）を書いてください"
            return blockly_save.SaveResult(
                status="failed", message=f"保存できません: {message}", errors=[message]
            )
        stem_errors = blockly_validate.validate_stem(resolved)
        if stem_errors:
            return blockly_save.SaveResult(
                status="failed",
                message="保存できません:\n- " + "\n- ".join(stem_errors),
                errors=stem_errors,
            )
        checked = check_spec(spec, app_dir)
        if not checked.ok or checked.workspace is None:
            errors = checked.errors or ["検査に通りませんでした"]
            return blockly_save.SaveResult(
                status="failed",
                message="保存できません:\n- " + "\n- ".join(errors),
                errors=errors,
                warnings=checked.warnings,
            )
        workspace_json = json.dumps(checked.workspace, ensure_ascii=False)
        return blockly_save.save_blockly(
            app_dir, resolved, workspace_json, checked.code
        )
    except Exception as e:
        return blockly_save.SaveResult(
            status="failed",
            message=f"保存できません: {e}",
            errors=[str(e)],
        )


def save_workspace(
    workspace_json: Any, app_dir: str | Path, stem: str
) -> blockly_save.SaveResult:
    """検査が通った workspace JSON を、そのままの構造で対として保存する。

    コンパクト仕様からの再構築はしない（ブロックID・配置・コメント・変数・
    extraState を保持する）。`pokecon.pySha256` は既存の保存層が更新する。
    """
    try:
        if not isinstance(stem, str) or not stem.strip():
            message = "保存名（stem）を書いてください"
            return blockly_save.SaveResult(
                status="failed", message=f"保存できません: {message}", errors=[message]
            )
        stem_errors = blockly_validate.validate_stem(stem)
        if stem_errors:
            return blockly_save.SaveResult(
                status="failed",
                message="保存できません:\n- " + "\n- ".join(stem_errors),
                errors=stem_errors,
            )
        checked = check_workspace(workspace_json, app_dir)
        if not checked.ok or checked.workspace is None:
            errors = checked.errors or ["検査に通りませんでした"]
            return blockly_save.SaveResult(
                status="failed",
                message="保存できません:\n- " + "\n- ".join(errors),
                errors=errors,
                warnings=checked.warnings,
            )
        text = json.dumps(checked.workspace, ensure_ascii=False)
        return blockly_save.save_blockly(app_dir, stem, text, checked.code)
    except Exception as e:
        return blockly_save.SaveResult(
            status="failed",
            message=f"保存できません: {e}",
            errors=[str(e)],
        )


def workspace_digest(workspace_json: Any) -> str:
    """workspace の正規化した要約値（`pokecon` 記録は除く）。

    同じ構造なら JSON の空白・キー順・改行に左右されない。保存物の版確認に使う。
    おかしければ ValueError。
    """
    workspace, errors = _workspace_object(workspace_json)
    if errors or workspace is None:
        raise ValueError(errors[0] if errors else "workspace を読めません")
    data = {key: value for key, value in workspace.items() if key != "pokecon"}
    try:
        canonical = json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError, RecursionError) as e:
        raise ValueError(f"workspace を正規化できません（{e}）") from None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: `patch_workspace` が受ける操作。
_PATCH_OPS = frozenset(
    {"set_field", "set_extra_state", "create_block", "connect", "delete_block"}
)


def patch_workspace(workspace_json: Any, operations: Any, catalog: Any) -> PatchResult:
    """workspace を部分編集する（保存はしない）。

    操作は全件を検証してから適用し、1件でも不正なら何も変えない。フィールドは
    カタログの型・選択肢に合わせて検査する。ブロックIDは変更しない。
    対象指定は `blockId` か `selector`（`type` + 0始まりの `occurrence`）の
    どちらか一方だけ。
    """
    workspace, errors = _workspace_object(workspace_json)
    if errors or workspace is None:
        return PatchResult(None, errors)
    ops, op_errors = _patch_operations(operations)
    if op_errors or ops is None:
        return PatchResult(None, op_errors)
    catalog_blocks = _catalog_blocks(catalog)
    changes: list[dict[str, Any]] = []
    for index, op in enumerate(ops, 1):
        where = f"操作{index}"
        if not isinstance(op, dict):
            return PatchResult(None, [f"{where}: オブジェクトで書いてください"])
        name = op.get("op")
        if name not in _PATCH_OPS:
            return PatchResult(
                None,
                [
                    f"{where}: 知らない操作です: {name!r}"
                    "（set_field / set_extra_state / create_block / connect / delete_block）"
                ],
            )
        blocks = _iter_blocks(workspace)
        by_id, id_errors = _blocks_by_id(blocks)
        if id_errors:
            return PatchResult(None, id_errors)
        if name in ("set_field", "set_extra_state"):
            result = _apply_field_op(
                workspace, op, str(name), blocks, by_id, catalog_blocks, where
            )
        elif name == "create_block":
            result = _apply_create_block(
                workspace, op, blocks, by_id, catalog_blocks, where
            )
        elif name == "connect":
            result = _apply_connect(workspace, op, blocks, by_id, catalog_blocks, where)
        else:
            result = _apply_delete_block(workspace, op, blocks, by_id, where)
        if isinstance(result, str):
            return PatchResult(None, [result])
        changes.extend(result)
    return PatchResult(workspace, [], changes)


def _apply_field_op(
    workspace: dict[str, Any],
    op: dict[str, Any],
    name: str,
    blocks: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    catalog_blocks: dict[str, dict[str, Any]],
    where: str,
) -> list[dict[str, Any]] | str:
    target, target_error = _patch_target(op, blocks, by_id, where)
    if target_error is not None or target is None:
        return target_error or f"{where}: 対象がありません"
    block_type = str(target.get("type", ""))
    if name == "set_field":
        field = op.get("field")
        if not isinstance(field, str) or not field:
            return f"{where}: field を文字列で書いてください"
        info = catalog_blocks.get(block_type)
        if info is None:
            return f"{where}: ブロック種 {block_type!r} はカタログにありません"
        spec = _field_spec(info, field)
        if spec is None:
            return f"{where}: {block_type} にフィールド {field!r} はありません"
        value, value_error = _coerce_field_value(spec, op.get("value"), where, field)
        if value_error is not None:
            return value_error
        fields = target.get("fields")
        if not isinstance(fields, dict):
            fields = {}
            target["fields"] = fields
        old = fields.get(field)
        fields[field] = value
        kind = spec.get("kind")
        live_value: Any = value
        if kind == "checkbox":
            live_value = value == "TRUE"
        return [
            {
                "op": "set_field",
                "blockId": target.get("id"),
                "type": block_type,
                "field": field,
                "kind": kind,
                "old": old,
                "new": value,
                "liveValue": live_value,
            }
        ]
    value = op.get("extraState")
    if value is not None and not isinstance(value, dict):
        return f"{where}: extraState はオブジェクトか null で書いてください"
    old_state = target.get("extraState")
    if value is None:
        target.pop("extraState", None)
    else:
        target["extraState"] = copy.deepcopy(value)
    return [
        {
            "op": "set_extra_state",
            "blockId": target.get("id"),
            "type": block_type,
            "old": old_state,
            "new": copy.deepcopy(value),
        }
    ]


def _apply_create_block(
    workspace: dict[str, Any],
    op: dict[str, Any],
    blocks: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    catalog_blocks: dict[str, dict[str, Any]],
    where: str,
) -> list[dict[str, Any]] | str:
    block_type = op.get("type")
    if not isinstance(block_type, str) or not block_type:
        return f"{where}: type を文字列で書いてください"
    info = catalog_blocks.get(block_type)
    if info is None:
        return f"{where}: ブロック種 {block_type!r} はカタログにありません"
    block_id = op.get("id")
    if block_id is None:
        block_id = _new_block_id(workspace)
    if not isinstance(block_id, str) or not block_id:
        return f"{where}: id は空でない文字列で書いてください"
    if block_id in by_id:
        return f"{where}: ブロックIDが重複しています: {block_id!r}"
    fields = op.get("fields")
    if fields is not None and not isinstance(fields, dict):
        return f"{where}: fields はオブジェクトで書いてください"
    block: dict[str, Any] = {"type": block_type, "id": block_id}
    if isinstance(fields, dict) and fields:
        applied: dict[str, Any] = {}
        for field_name, raw in fields.items():
            if not isinstance(field_name, str) or not field_name:
                return f"{where}: fields のキーは文字列で書いてください"
            spec = _field_spec(info, field_name)
            if spec is None:
                return f"{where}: {block_type} にフィールド {field_name!r} はありません"
            value, value_error = _coerce_field_value(spec, raw, where, field_name)
            if value_error is not None:
                return value_error
            applied[field_name] = value
        block["fields"] = applied
    parent_ref = op.get("parent")
    after_ref = op.get("after")
    if parent_ref is not None and after_ref is not None:
        return f"{where}: parent と after は同時に使えません"
    if parent_ref is not None:
        input_name = op.get("input")
        if not isinstance(input_name, str) or not input_name:
            return f"{where}: parent を使うときは input も書いてください"
        parent, ref_error = _resolve_ref(parent_ref, blocks, by_id, where, "parent")
        if ref_error is not None or parent is None:
            return ref_error or f"{where}: parent がありません"
        insert = op.get("insert") is True
        problem = _connection_problem(
            catalog_blocks,
            parent,
            input_name,
            block,
            where,
            allow_occupied=insert,
        )
        if problem is not None:
            return problem
        inputs = parent.setdefault("inputs", {})
        slot = inputs.get(input_name)
        if isinstance(slot, dict) and isinstance(slot.get("block"), dict):
            # insert: 既存の並びを新しいブロックの後ろへつなぐ。
            block["next"] = {"block": slot["block"]}
        inputs[input_name] = {"block": block}
        target = f"{parent.get('id')}:{input_name}"
    elif after_ref is not None:
        after, ref_error = _resolve_ref(after_ref, blocks, by_id, where, "after")
        if ref_error is not None or after is None:
            return ref_error or f"{where}: after がありません"
        existing = None
        nxt = after.get("next")
        if isinstance(nxt, dict) and isinstance(nxt.get("block"), dict):
            if op.get("insert") is not True:
                return (
                    f"{where}: after の後ろは既に埋まっています（insert: true で挿入）"
                )
            existing = nxt["block"]
        if not info.get("previous"):
            return f"{where}: {block_type} は後ろへ接続できません"
        if existing is not None:
            block["next"] = {"block": existing}
        after["next"] = {"block": block}
        target = f"{after.get('id')}:next"
    else:
        x = op.get("x", 40)
        y = op.get("y", 40)
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return f"{where}: x は数値で書いてください"
        if isinstance(y, bool) or not isinstance(y, (int, float)):
            return f"{where}: y は数値で書いてください"
        block["x"] = x
        block["y"] = y
        workspace.setdefault("blocks", {}).setdefault("blocks", []).append(block)
        target = "top"
    return [
        {
            "op": "create_block",
            "blockId": block_id,
            "type": block_type,
            "target": target,
        }
    ]


def _apply_connect(
    workspace: dict[str, Any],
    op: dict[str, Any],
    blocks: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    catalog_blocks: dict[str, dict[str, Any]],
    where: str,
) -> list[dict[str, Any]] | str:
    child, child_error = _resolve_ref(op.get("child"), blocks, by_id, where, "child")
    if child_error is not None or child is None:
        return child_error or f"{where}: child がありません"
    child_id = child.get("id")
    if not isinstance(child_id, str) or not child_id:
        return f"{where}: child にブロックIDがありません"
    parent_ref = op.get("parent")
    after_ref = op.get("after")
    if (parent_ref is None) == (after_ref is None):
        return f"{where}: parent か after のどちらか一方を書いてください"
    descendants = _subtree_ids(child)
    if parent_ref is not None:
        input_name = op.get("input")
        if not isinstance(input_name, str) or not input_name:
            return f"{where}: parent を使うときは input も書いてください"
        parent, ref_error = _resolve_ref(parent_ref, blocks, by_id, where, "parent")
        if ref_error is not None or parent is None:
            return ref_error or f"{where}: parent がありません"
        if parent.get("id") in descendants:
            return f"{where}: 自分の子孫へは接続できません"
        insert = op.get("insert") is True
        problem = _connection_problem(
            catalog_blocks,
            parent,
            input_name,
            child,
            where,
            allow_occupied=insert,
        )
        if problem is not None:
            return problem
        existing = None
        slot = parent.get("inputs", {}).get(input_name)
        if isinstance(slot, dict) and isinstance(slot.get("block"), dict):
            existing = slot["block"]
            if existing.get("id") in descendants:
                return f"{where}: 自分自身の並びへは挿入できません"
        detach_error = _detach_block(workspace, child)
        if detach_error is not None:
            return detach_error
        if existing is not None:
            _last_in_chain(child)["next"] = {"block": existing}
        parent.setdefault("inputs", {})[input_name] = {"block": child}
        target = f"{parent.get('id')}:{input_name}"
    else:
        after, ref_error = _resolve_ref(after_ref, blocks, by_id, where, "after")
        if ref_error is not None or after is None:
            return ref_error or f"{where}: after がありません"
        if after.get("id") in descendants:
            return f"{where}: 自分の子孫へは接続できません"
        existing = None
        nxt = after.get("next")
        if isinstance(nxt, dict) and isinstance(nxt.get("block"), dict):
            if op.get("insert") is not True:
                return (
                    f"{where}: after の後ろは既に埋まっています（insert: true で挿入）"
                )
            existing = nxt["block"]
            if existing.get("id") in descendants:
                return f"{where}: 自分自身の並びへは挿入できません"
        child_info = catalog_blocks.get(str(child.get("type")))
        if child_info is None or not child_info.get("previous"):
            return f"{where}: このブロックは後ろへ接続できません"
        detach_error = _detach_block(workspace, child)
        if detach_error is not None:
            return detach_error
        if existing is not None:
            _last_in_chain(child)["next"] = {"block": existing}
        after["next"] = {"block": child}
        target = f"{after.get('id')}:next"
    return [
        {
            "op": "connect",
            "blockId": child_id,
            "type": child.get("type"),
            "target": target,
        }
    ]


def _apply_delete_block(
    workspace: dict[str, Any],
    op: dict[str, Any],
    blocks: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    where: str,
) -> list[dict[str, Any]] | str:
    target, target_error = _patch_target(op, blocks, by_id, where)
    if target_error is not None or target is None:
        return target_error or f"{where}: 対象がありません"
    block_type = str(target.get("type", ""))
    if block_type in ("pokecon_program", "pokecon_sub_def"):
        return f"{where}: プログラム / サブルーチン定義は削除できません"
    recursive = op.get("recursive") is True
    nested = False
    inputs = target.get("inputs")
    if isinstance(inputs, dict):
        nested = any(
            isinstance(slot, dict) and slot.get("block") for slot in inputs.values()
        )
    if nested and not recursive:
        return f"{where}: 子ブロックがあります（消すなら recursive: true を書いてください）"
    if recursive:
        detach_error = _detach_block(workspace, target)
    else:
        # 並びの途中なら、前後をつないで抜く（Blocklyの削除と同じ）。
        detach_error = _delete_splice(workspace, target)
    if detach_error is not None:
        return detach_error
    return [
        {
            "op": "delete_block",
            "blockId": target.get("id"),
            "type": block_type,
            "recursive": recursive,
        }
    ]


def _resolve_ref(
    ref: Any,
    blocks: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    where: str,
    label: str,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(ref, dict):
        return (
            None,
            f"{where}: {label} は blockId / selector のオブジェクトで書いてください",
        )
    fake = {"blockId": ref.get("blockId"), "selector": ref.get("selector")}
    return _patch_target(fake, blocks, by_id, f"{where} の {label}")


def _input_spec(info: dict[str, Any], name: str) -> dict[str, Any] | None:
    for spec in info.get("inputs", []):
        if isinstance(spec, dict) and str(spec.get("name")) == name:
            return spec
    return None


def _connection_problem(
    catalog_blocks: dict[str, dict[str, Any]],
    parent: dict[str, Any],
    input_name: str,
    child: dict[str, Any],
    where: str,
    *,
    allow_occupied: bool = False,
) -> str | None:
    parent_info = catalog_blocks.get(str(parent.get("type")))
    if parent_info is None:
        return f"{where}: parent のブロック種がカタログにありません"
    spec = _input_spec(parent_info, input_name)
    if spec is None:
        return f"{where}: {parent.get('type')} に接続口 {input_name!r} はありません"
    inputs = parent.get("inputs")
    if isinstance(inputs, dict):
        slot = inputs.get(input_name)
        if isinstance(slot, dict) and slot.get("block"):
            if not allow_occupied:
                return f"{where}: 接続口 {input_name!r} は既に埋まっています"
            if spec.get("kind") != "statement":
                return f"{where}: 値の接続口には挿入できません"
    child_info = catalog_blocks.get(str(child.get("type")))
    if child_info is None:
        return f"{where}: child のブロック種がカタログにありません"
    if spec.get("kind") == "statement":
        if not child_info.get("previous"):
            return f"{where}: このブロックは文として接続できません"
        return None
    if not child_info.get("output"):
        return f"{where}: このブロックは値として接続できません"
    check = spec.get("check") or []
    output = child_info.get("output") or []
    if check and output and not (set(check) & set(output)):
        return f"{where}: 型が合いません（入力 {check} / ブロック {output}）"
    return None


def _locate_block(
    workspace: dict[str, Any], block_id: str
) -> tuple[dict[str, Any] | None, tuple[str, Any, Any] | None]:
    top = workspace.get("blocks", {}).get("blocks", [])
    stack: list[tuple[Any, tuple[str, Any, Any]]] = [
        (block, ("top", None, None)) for block in top
    ]
    while stack:
        block, where = stack.pop()
        if not isinstance(block, dict):
            continue
        if block.get("id") == block_id:
            return block, where
        inputs = block.get("inputs")
        if isinstance(inputs, dict):
            for name, slot in inputs.items():
                if isinstance(slot, dict) and isinstance(slot.get("block"), dict):
                    stack.append((slot["block"], ("input", block, name)))
        nxt = block.get("next")
        if isinstance(nxt, dict) and isinstance(nxt.get("block"), dict):
            stack.append((nxt["block"], ("next", block, None)))
    return None, None


def _detach_block(workspace: dict[str, Any], block: dict[str, Any]) -> str | None:
    block_id = block.get("id")
    if not isinstance(block_id, str) or not block_id:
        return "ブロックIDがありません"
    found, where = _locate_block(workspace, block_id)
    if found is None or where is None:
        return f"ブロックが見つかりません: {block_id!r}"
    kind = where[0]
    if kind == "top":
        workspace["blocks"]["blocks"].remove(found)
        return None
    parent = where[1]
    if kind == "input":
        name = where[2]
        slot = parent.get("inputs", {}).get(name)
        if isinstance(slot, dict):
            slot.pop("block", None)
            if not slot:
                parent["inputs"].pop(name, None)
        return None
    parent.pop("next", None)
    return None


def _last_in_chain(block: dict[str, Any]) -> dict[str, Any]:
    current = block
    while True:
        nxt = current.get("next")
        if not (isinstance(nxt, dict) and isinstance(nxt.get("block"), dict)):
            return current
        current = nxt["block"]


def _delete_splice(workspace: dict[str, Any], block: dict[str, Any]) -> str | None:
    block_id = block.get("id")
    if not isinstance(block_id, str) or not block_id:
        return "ブロックIDがありません"
    found, where = _locate_block(workspace, block_id)
    if found is None or where is None:
        return f"ブロックが見つかりません: {block_id!r}"
    following = None
    nxt = found.get("next")
    if isinstance(nxt, dict) and isinstance(nxt.get("block"), dict):
        following = nxt["block"]
    kind = where[0]
    if kind == "top":
        top = workspace["blocks"]["blocks"]
        index = next((i for i, item in enumerate(top) if item is found), None)
        if index is None:
            return f"ブロックが見つかりません: {block_id!r}"
        if following is None:
            top.pop(index)
        else:
            top[index] = following
        return None
    parent = where[1]
    if kind == "input":
        name = where[2]
        slot = parent.get("inputs", {}).get(name)
        if not isinstance(slot, dict):
            return f"接続口 {name!r} が見つかりません"
        if following is None:
            slot.pop("block", None)
            if not slot:
                parent["inputs"].pop(name, None)
        else:
            slot["block"] = following
        return None
    if following is None:
        parent.pop("next", None)
    else:
        parent["next"] = {"block": following}
    return None


def _subtree_ids(block: Any) -> set[str]:
    found: set[str] = set()
    stack = [block]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        block_id = current.get("id")
        if isinstance(block_id, str) and block_id:
            found.add(block_id)
        inputs = current.get("inputs")
        if isinstance(inputs, dict):
            for slot in inputs.values():
                if isinstance(slot, dict) and isinstance(slot.get("block"), dict):
                    stack.append(slot["block"])
        nxt = current.get("next")
        if isinstance(nxt, dict) and isinstance(nxt.get("block"), dict):
            stack.append(nxt["block"])
    return found


def _new_block_id(workspace: dict[str, Any]) -> str:
    used = {block.get("id") for block in _iter_blocks(workspace)}
    for index in range(1, 10000):
        candidate = f"ai{index}"
        if candidate not in used:
            return candidate
    return "ai9999"


def _patch_operations(operations: Any) -> tuple[list[Any] | None, list[str]]:
    raw = operations
    if isinstance(raw, dict):
        raw = raw.get("operations")
    if not isinstance(raw, list):
        return None, ["operations は配列で書いてください"]
    if not raw:
        return None, ["operations が空です"]
    return raw, []


def _blocks_by_id(
    blocks: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    by_id: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for block in blocks:
        block_id = block.get("id")
        if not isinstance(block_id, str) or not block_id:
            continue
        if block_id in by_id:
            errors.append(f"ブロックIDが重複しています: {block_id!r}")
            continue
        by_id[block_id] = block
    return by_id, errors


def _patch_target(
    op: dict[str, Any],
    blocks: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    where: str,
) -> tuple[dict[str, Any] | None, str | None]:
    block_id = op.get("blockId")
    selector = op.get("selector")
    if (block_id is None) == (selector is None):
        return None, f"{where}: blockId か selector のどちらか一方を書いてください"
    if block_id is not None:
        if not isinstance(block_id, str) or not block_id:
            return None, f"{where}: blockId は空でない文字列で書いてください"
        target = by_id.get(block_id)
        if target is None:
            return None, f"{where}: ブロックが見つかりません: {block_id!r}"
        return target, None
    if not isinstance(selector, dict):
        return None, f"{where}: selector はオブジェクトで書いてください"
    if "id" in selector:
        value = selector.get("id")
        if not isinstance(value, str) or not value:
            return None, f"{where}: selector.id は空でない文字列で書いてください"
        target = by_id.get(value)
        if target is None:
            return None, f"{where}: ブロックが見つかりません: {value!r}"
        return target, None
    block_type = selector.get("type")
    if not isinstance(block_type, str) or not block_type:
        return None, f"{where}: selector.type を文字列で書いてください"
    occurrence = selector.get("occurrence", 0)
    if (
        not isinstance(occurrence, int)
        or isinstance(occurrence, bool)
        or occurrence < 0
    ):
        return None, f"{where}: selector.occurrence は0以上の整数で書いてください"
    matches = [block for block in blocks if block.get("type") == block_type]
    if occurrence >= len(matches):
        return None, (
            f"{where}: {block_type} の {occurrence} 番目がありません"
            f"（{len(matches)} 個あります）"
        )
    return matches[occurrence], None


def _field_spec(info: dict[str, Any], name: str) -> dict[str, Any] | None:
    for spec in info.get("fields", []):
        if isinstance(spec, dict) and str(spec.get("name")) == name:
            return spec
    return None


def _coerce_field_value(
    spec: dict[str, Any], value: Any, where: str, field: str
) -> tuple[Any, str | None]:
    if spec.get("serializable") is False:
        return None, f"{where}: {field} は保存されない表示専用フィールドです"
    kind = spec.get("kind")
    if kind == "variable":
        return None, (
            f"{where}: {field} は変数フィールドです。patch では変更できません"
            "（workspace を直接編集するか、仕様から作り直してください）"
        )
    if kind == "checkbox":
        if isinstance(value, bool):
            return ("TRUE" if value else "FALSE"), None
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "false"):
                return ("TRUE" if lowered == "true" else "FALSE"), None
        return None, f"{where}: {field} は true / false で書いてください"
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            if isinstance(value, str):
                try:
                    value = float(value)
                except ValueError:
                    return None, f"{where}: {field} は数値で書いてください"
            else:
                return None, f"{where}: {field} は数値で書いてください"
        number = float(value)
        if not (number == number and number not in (float("inf"), float("-inf"))):
            return None, f"{where}: {field} は有限の数値で書いてください"
        if number.is_integer():
            return int(number), None
        return number, None
    if kind == "crop":
        if not isinstance(value, str):
            return None, f"{where}: {field} は文字列（x1,y1,x2,y2）で書いてください"
        text = value.strip()
        if not text:
            return "", None
        match = _CROP_RE.match(text)
        if match is None:
            return None, f"{where}: {field} は x1,y1,x2,y2 の形で書いてください"
        x1, y1, x2, y2 = (int(part) for part in match.groups())
        if x1 >= x2 or y1 >= y2:
            return None, f"{where}: {field} は x2>x1 / y2>y1 にしてください"
        return text, None
    if kind in ("controller", "stickpad"):
        options = spec.get("options")
        if isinstance(options, list) and options:
            allowed = {
                str(item[1])
                for item in options
                if isinstance(item, (list, tuple)) and len(item) > 1
            }
            if not isinstance(value, str) or value not in allowed:
                return (
                    None,
                    f"{where}: {field} は次のどれかで書いてください: {sorted(allowed)}",
                )
        elif not isinstance(value, str):
            return None, f"{where}: {field} は文字列で書いてください"
        return value, None
    if kind == "template":
        if not isinstance(value, str) or not value.strip():
            return None, f"{where}: {field} は空でない文字列で書いてください"
        return value, None
    if not isinstance(value, str):
        return None, f"{where}: {field} は文字列で書いてください"
    return value, None


def catalog_markdown(catalog: Any, templates: Any) -> str:
    """カタログをエージェント向けの Markdown にする（生成物）。"""
    lines: list[str] = [
        "# Blockly ブロック一覧（生成物）",
        "",
        "このファイルは自動生成です。手で編集しないでください。",
        "",
        "生成コマンド:",
        "",
        "```sh",
        "uv run --frozen python tools/blockly_agent.py catalog --format markdown "
        "--output docs/blockly-agent/references/blocks.md",
        "```",
        "",
        "仕様の書き方は [spec.md](spec.md)、使い方は [手順](../README.md) を参照してください。",
        "",
    ]
    template_names = (
        [str(name) for name in templates]
        if isinstance(templates, (list, tuple))
        else []
    )
    lines.extend(["## 画像テンプレート（`TEMPLATE` に書ける名前）", ""])
    if template_names:
        for name in sorted(template_names):
            lines.append(f"- `{name}`")
    else:
        lines.append("- （`Template/` に画像がありません）")
    lines.append("")

    blocks = _catalog_blocks(catalog)
    if not blocks:
        lines.extend(
            [
                "## ブロック",
                "",
                "- カタログを取得できませんでした（assets/blockly を確認してください）",
                "",
            ]
        )
        return "\n".join(lines)

    pokecon = sorted(name for name in blocks if name.startswith("pokecon_"))
    standard = sorted(name for name in blocks if not name.startswith("pokecon_"))
    lines.extend(["## ブロック", ""])
    lines.extend(
        [
            "### 短縮形",
            "",
            "仕様では次の短縮形を使えます（`type`/`fields`/`inputs` の汎用形も可）。",
            "",
            "- `press` / `hold` / `release` / `stick` / `stick_off` / `wait` / "
            "`press_rep`",
            "- `repeat` / `while` / `if`(+`else`) / `break` / `continue`",
            "- `print` / `comment` / `finish` / `set` / `change` / `call` / "
            "`call_value`",
            "- `screenshot` / `discord` / `dialog_choice` / `dialog_number` / "
            "`dialog_check`",
            "",
        ]
    )
    lines.extend(["## PokeCon ブロック", ""])
    for name in pokecon:
        lines.extend(_block_markdown(name, blocks[name]))
    lines.extend(["## 標準ブロック（許可分）", ""])
    for name in standard:
        lines.extend(_block_markdown(name, blocks[name]))
    lines.extend(
        [
            "## 補足",
            "",
            '- `controls_if` の `else` は `extraState: {"hasElse": true}` と '
            "`inputs.ELSE` で表す（仕様の `if` 短縮形が自動で行う）。",
            "- `variables_set` / `variables_get` / `math_change` / `controls_for` の "
            '`VAR` は変数名の文字列で書き、builder が `{"id": 名前}` と '
            "`variables` を補う。",
            "- 設定ダイアログの `VAR` は Python の変数名（文字列）のままで、"
            "`variables` には足さない。",
            "- 画像認識ブロックの `TEMPLATE` は空にできない。"
            "`Template/<名前>` が存在すること。`CROP` は空か `x1,y1,x2,y2`、"
            "`THRESHOLD` は 0〜1。",
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 検査の流れ
# ---------------------------------------------------------------------------


def _check_spec(spec: Any, app_dir: str | Path) -> CheckResult:
    catalog_result = blockly_headless.catalog(app_dir)
    if not catalog_result.ok or catalog_result.data is None:
        return CheckResult(
            False,
            errors=catalog_result.errors or ["Blockly のカタログを取得できません"],
            warnings=catalog_result.warnings,
            stem=_spec_stem(spec),
        )
    built = build_workspace(spec, catalog_result.data)
    warnings = [*catalog_result.warnings, *built.warnings]
    stem = _spec_stem(spec)
    if not built.ok or built.workspace is None:
        return CheckResult(False, built.errors, warnings, built.workspace, "", stem)
    return _inspect_workspace(
        built.workspace, app_dir, catalog_result.data, warnings, stem
    )


def _check_workspace(workspace_json: Any, app_dir: str | Path) -> CheckResult:
    workspace, errors = _workspace_object(workspace_json)
    if errors or workspace is None:
        return CheckResult(False, errors)
    errors = _validate_json_text(workspace)
    if errors:
        return CheckResult(False, _dedupe(errors))
    catalog_result = blockly_headless.catalog(app_dir)
    if not catalog_result.ok or catalog_result.data is None:
        return CheckResult(
            False,
            errors=catalog_result.errors or ["Blockly のカタログを取得できません"],
            warnings=catalog_result.warnings,
            workspace=workspace,
        )
    return _inspect_workspace(
        workspace, app_dir, catalog_result.data, list(catalog_result.warnings), None
    )


def _inspect_workspace(
    workspace: dict[str, Any],
    app_dir: str | Path,
    catalog: Any,
    warnings: list[str],
    stem: str | None,
) -> CheckResult:
    """workspace を共通の検査へ通す（仕様由来と既存workspaceの両方）。"""
    errors: list[str] = []
    if stem is not None:
        errors.extend(f"stem: {item}" for item in blockly_validate.validate_stem(stem))
    errors.extend(_check_template_files(workspace, app_dir, catalog))
    if errors:
        return CheckResult(False, _dedupe(errors), warnings, workspace, "", stem)

    generated = blockly_headless.generate(workspace, app_dir)
    warnings = [*warnings, *generated.warnings]
    if not generated.ok or generated.data is None:
        errors.extend(generated.errors or ["Blockly でコードを生成できませんでした"])
        return CheckResult(False, _dedupe(errors), warnings, workspace, "", stem)
    code = generated.data.get("code")
    if not isinstance(code, str) or not code.strip():
        errors.append("Blockly の生成コードが空です")
        return CheckResult(False, _dedupe(errors), warnings, workspace, "", stem)
    errors.extend(blockly_validate.validate_generated_code(code))
    errors.extend(blockly_templates.validate_template_refs(code))
    return CheckResult(not errors, _dedupe(errors), warnings, workspace, code, stem)


def _workspace_object(
    workspace_json: Any,
) -> tuple[dict[str, Any] | None, list[str]]:
    """workspace JSON（dict か JSON 文字列）を dict にする。"""
    if isinstance(workspace_json, str):
        try:
            workspace = json.loads(workspace_json)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return None, [f"ワークスペースJSONが壊れています（{e}）"]
    elif isinstance(workspace_json, dict):
        workspace = copy.deepcopy(workspace_json)
    else:
        return None, ["ワークスペースJSONはオブジェクトで書いてください"]
    if not isinstance(workspace, dict):
        return None, ["ワークスペースJSONはオブジェクトで書いてください"]
    return workspace, []


def _render_workspace(workspace_json: Any, app_dir: str | Path) -> RenderResult:
    checked = _check_workspace(workspace_json, app_dir)
    return RenderResult(
        checked.ok,
        code=checked.code,
        errors=checked.errors,
        warnings=checked.warnings,
    )


def _validate_json_text(workspace: Any) -> list[str]:
    try:
        text = json.dumps(workspace, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError) as e:
        return [f"ワークスペースをJSONにできません（{e}）"]
    errors = blockly_validate.validate_workspace_json(text)
    errors.extend(blockly_validate.validate_dialog_vars(text))
    return errors


def _check_template_files(
    workspace: dict[str, Any], app_dir: str | Path, catalog: Any
) -> list[str]:
    """画像認識ブロックの TEMPLATE の実体を確かめる。

    名前の存在だけでなく、`Template/` 配下に実在すること（外へのリンクを
    辿らない）と、画像として復号できることまで見る。重複参照は一度だけ
    読む。実フレームが無い検査では、検索ROIの画面内整合や認識精度までは
    確かめない（それは実画像の照合で確認する）。
    """
    base = (Path(app_dir) / "Template").resolve()
    names = blockly_templates.list_image_templates(app_dir)
    base_names = {PurePosixPath(name).name for name in names}
    blocks = _catalog_blocks(catalog)
    errors: list[str] = []
    reported: set[str] = set()
    decoded: dict[Path, list[str]] = {}
    for block in _iter_blocks(workspace):
        info = blocks.get(str(block.get("type")))
        if info is None:
            continue
        fields = block.get("fields")
        if not isinstance(fields, dict):
            fields = {}
        for spec in info.get("fields", []):
            if not isinstance(spec, dict):
                continue
            if spec.get("kind") != "template" or spec.get("serializable") is False:
                continue
            name = str(spec.get("name", "TEMPLATE"))
            value = fields.get(name, spec.get("default"))
            if not isinstance(value, str) or not value.strip():
                if "empty" not in reported:
                    reported.add("empty")
                    errors.append(
                        "TEMPLATE が空です（Template/ の画像名を指定してください）"
                    )
                continue
            raw_rel = value.replace("\\", "/").lstrip("/")
            if ":" in raw_rel or ".." in raw_rel.split("/"):
                if raw_rel not in reported:
                    reported.add(raw_rel)
                    errors.append(f"TEMPLATE の場所が不正です: {value!r}")
                continue
            rel = PurePosixPath(raw_rel).as_posix()
            if rel in reported:
                continue
            reported.add(rel)
            target = base / rel
            try:
                resolved = target.resolve()
                resolved.relative_to(base)
            except (OSError, ValueError):
                errors.append(f"TEMPLATE の場所が不正です: {value!r}")
                continue
            if resolved not in decoded:
                decoded[resolved] = _decode_template_file(resolved, value)
            errors.extend(decoded[resolved])
            if not resolved.is_file():
                hint = _suggest_hint(PurePosixPath(rel).name, base_names)
                errors.append(
                    f"TEMPLATE の画像が見つかりません: {value!r}"
                    f"（Template/ に置くか --app-dir を確認してください）{hint}"
                )
    return errors


def _decode_template_file(path: Path, shown: str) -> list[str]:
    """テンプレ画像1枚を復号して、読めるか・寸法が正しいかを見る。"""
    if not path.is_file():
        return []
    try:
        data = path.read_bytes()
    except OSError as e:
        return [f"TEMPLATE の画像を読めません: {shown!r}（{e}）"]
    try:
        image = blockly_match.decode_frame_png(data)
    except ValueError:
        return [f"TEMPLATE の画像が壊れています（画像として読めません）: {shown!r}"]
    if image.size == 0 or image.shape[0] == 0 or image.shape[1] == 0:
        return [f"TEMPLATE の画像が空です: {shown!r}"]
    return []


def _iter_blocks(workspace: dict[str, Any]) -> list[dict[str, Any]]:
    """workspace 内の実ブロックを文書順に返す（影は含めない）。"""
    tops = workspace.get("blocks")
    if not isinstance(tops, dict) or not isinstance(tops.get("blocks"), list):
        return []
    found: list[dict[str, Any]] = []
    stack: list[Any] = list(reversed(tops["blocks"]))
    while stack:
        block = stack.pop()
        if not isinstance(block, dict):
            continue
        found.append(block)
        children: list[dict[str, Any]] = []
        inputs = block.get("inputs")
        if isinstance(inputs, dict):
            for slot in inputs.values():
                if isinstance(slot, dict):
                    inner = slot.get("block")
                    if isinstance(inner, dict):
                        children.append(inner)
        nxt = block.get("next")
        if isinstance(nxt, dict) and isinstance(nxt.get("block"), dict):
            children.append(nxt["block"])
        stack.extend(reversed(children))
    return found


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _spec_stem(spec: Any) -> str | None:
    if isinstance(spec, dict):
        value = spec.get("stem")
        if isinstance(value, str):
            return value
    return None


def _catalog_blocks(catalog: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(catalog, dict):
        return {}
    blocks = catalog.get("blocks")
    if not isinstance(blocks, dict):
        return {}
    return {str(name): info for name, info in blocks.items() if isinstance(info, dict)}


def _suggest_hint(word: str, candidates: set[str]) -> str:
    matches = difflib.get_close_matches(word, sorted(candidates), n=3, cutoff=0.5)
    if not matches:
        return ""
    return "（似た名前: " + ", ".join(f"`{m}`" for m in matches) + "）"


# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------


class _Builder:
    """仕様を workspace JSON へ組み立てる。エラーは self.errors に溜める。"""

    def __init__(self, catalog: Any) -> None:
        self.blocks = _catalog_blocks(catalog)
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.variables: list[dict[str, str]] = []
        self._var_ids: set[str] = set()
        self.sub_defs: dict[str, list[str]] = {}
        self._bad_sub_args: set[str] = set()
        self.calls: list[tuple[str, str, int]] = []
        self.caller = "do"

    # -- 入口 ---------------------------------------------------------------

    def build(self, spec: Any) -> dict[str, Any] | None:
        if not isinstance(spec, dict):
            self.error("仕様は JSON オブジェクトで書いてください")
            return None
        for key in spec:
            if key not in _TOP_KEYS:
                self.error(
                    f"仕様の項目 `{key}` は使えません"
                    + _suggest_hint(str(key), set(_TOP_KEYS))
                )
        stem = spec.get("stem")
        if stem is not None:
            if not isinstance(stem, str):
                self.error("stem は文字列で書いてください")
            else:
                for item in blockly_validate.validate_stem(stem):
                    self.error(f"stem: {item}")
        name = spec.get("name")
        if not isinstance(name, str) or not name.strip():
            self.error("name（一覧に出る表示名）を書いてください")
            name = "無題"
        tags = self._tags(spec.get("tags"))
        raw_do = spec.get("do", [])
        raw_subs = spec.get("subs", [])
        if not isinstance(raw_subs, list):
            self.error("subs は配列で書いてください")
            raw_subs = []

        # 呼び出しの解決に備え、先に定義名と引数だけ読む。
        sub_items: list[dict[str, Any]] = []
        for index, item in enumerate(raw_subs):
            if not isinstance(item, dict):
                self.error(f"subs[{index}] は JSON オブジェクトで書いてください")
                continue
            if not self._sub_metadata(item, f"subs[{index}]"):
                continue
            sub_items.append(item)

        self.caller = "do"
        body = self._statements(raw_do, "do")
        sub_blocks: list[dict[str, Any]] = []
        for item in sub_items:
            sub_block = self._sub_block(item)
            if sub_block is not None:
                sub_blocks.append(sub_block)
        self._validate_calls()

        program: dict[str, Any] = {
            "type": "pokecon_program",
            "fields": {"NAME": name, "TAGS": ",".join(tags)},
        }
        if body:
            program["inputs"] = {"DO": {"block": body[0]}}
        top = [program, *sub_blocks]
        workspace: dict[str, Any] = {
            "blocks": {"languageVersion": 0, "blocks": top},
        }
        if self.variables:
            workspace["variables"] = self.variables
        if self.errors:
            return None
        return workspace

    # -- メタ ---------------------------------------------------------------

    def _sub_metadata(self, item: dict[str, Any], where: str) -> bool:
        allowed = {"name", "args", "do", "return"}
        self._only(item, allowed, where, "サブプログラム")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            self.error(f"{where}: name を書いてください")
            return False
        name = name.strip()
        if not _is_identifier(name):
            self.error(f"サブプログラム名は識別子にしてください: {name}")
            return False
        if name in _RESERVED_SUB_NAMES or (
            name.startswith("__") and name.endswith("__")
        ):
            self.error(f"サブプログラム名に予約された名前は使えません: {name}")
            return False
        if name in self.sub_defs:
            self.error(f"サブプログラム名が重複しています: {name}")
            return False
        args = self._parse_sub_args(item.get("args", ""), where, name)
        self.sub_defs[name] = args
        return True

    def _parse_sub_args(self, raw: Any, where: str, name: str) -> list[str]:
        if raw is None:
            parts: list[str] = []
        elif isinstance(raw, str):
            parts = [part.strip() for part in raw.split(",")]
            parts = [part for part in parts if part]
        elif isinstance(raw, list):
            parts = []
            for part in raw:
                if not isinstance(part, str):
                    self.error(f"{where}: 引数名は文字列で書いてください")
                    continue
                if part.strip():
                    parts.append(part.strip())
        else:
            self.error(f"{where}: args は文字列か配列で書いてください")
            return []
        if len(parts) > _MAX_SUB_ARGS:
            self.error(f"{where}: サブルーチンの引数は最大 {_MAX_SUB_ARGS} 個までです")
            self._bad_sub_args.add(name)
            parts = parts[:_MAX_SUB_ARGS]
        seen: set[str] = set()
        valid = True
        for part in parts:
            if not _is_identifier(part):
                self.error(f"{where}: 引数名は識別子にしてください: {part}")
                valid = False
            elif part == "self":
                self.error(f"{where}: 引数名に self は使えません")
                valid = False
            elif part in seen:
                self.error(f"{where}: 引数名が重複しています: {part}")
                valid = False
            seen.add(part)
        if not valid:
            self._bad_sub_args.add(name)
        return parts

    def _sub_block(self, item: dict[str, Any]) -> dict[str, Any] | None:
        name = item["name"].strip()
        args = self.sub_defs.get(name, [])
        self.caller = name
        block: dict[str, Any] = {
            "type": "pokecon_sub_def",
            "fields": {"NAME": name, "ARGS": ",".join(args)},
        }
        raw_do = item.get("do", [])
        body = self._statements(raw_do, f"sub {name}")
        if body:
            block["inputs"] = {"DO": {"block": body[0]}}
        if "return" in item:
            value = self._value(item["return"], None, f"sub {name} return")
            if value is not None:
                block.setdefault("inputs", {})["RETURN"] = {"block": value}
        self.caller = "do"
        return self._finish(block, f"sub {name}")

    # -- 文の組み立て -------------------------------------------------------

    def _statement_param(self, raw: Any, where: str) -> list[dict[str, Any]]:
        """短縮形の `do` / `else` を文の並びにする（単一 dict も許容）。"""
        if isinstance(raw, dict):
            return self._statements([raw], where)
        return self._statements(raw, where)

    def _statements(self, raw: Any, where: str) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            self.error(f"{where}: 文の並びは配列で書いてください")
            return []
        built: list[dict[str, Any]] = []
        for index, item in enumerate(raw):
            block = self._statement(item, f"{where}[{index}]")
            if block is not None:
                built.append(block)
        for index in range(len(built) - 1):
            info = self._block_info(built[index]["type"])
            if info is not None and not info.get("next"):
                self.error(f"{where}: `{built[index]['type']}` の後ろに文を置けません")
                break
            built[index]["next"] = {"block": built[index + 1]}
        return built

    def _statement(self, item: Any, where: str) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            self.error(f"{where}: 文は JSON オブジェクトで書いてください")
            return None
        if "type" in item:
            return self._generic(item, where, as_value=False)
        found = [key for key in item if key in _SHORT_FORMS]
        if not found:
            self.error(
                f"{where}: ブロックの種類が分かりません（`type` か短縮形を書いてください）"
            )
            return None
        candidates = [
            key
            for key in found
            if all(
                other == key
                or other not in _SHORT_FORMS
                or other in _SHORT_PARAMS.get(key, frozenset())
                for other in item
            )
        ]
        if len(candidates) != 1:
            self.error(f"{where}: 短縮形を1つに決められません（{', '.join(found)}）")
            return None
        short = candidates[0]
        params = {key: value for key, value in item.items() if key != short}
        handler = getattr(self, f"_short_{short}", None)
        if handler is None:
            self.error(f"{where}: `{short}` は使えません")
            return None
        block = handler(item[short], params, where)
        if block is None:
            return None
        if not self._require_statement(block, where):
            return None
        return block

    def _generic(
        self, item: dict[str, Any], where: str, *, as_value: bool
    ) -> dict[str, Any] | None:
        allowed = {"type", "fields", "inputs", "extraState"}
        self._only(item, allowed, where, "ブロック")
        block_type = item.get("type")
        if not isinstance(block_type, str) or not block_type:
            self.error(f"{where}: type を書いてください")
            return None
        if self._block_info(block_type) is None:
            self.error(
                f"{where}: ブロック `{block_type}` は使えません"
                + _suggest_hint(block_type, set(self.blocks))
            )
            return None
        block: dict[str, Any] = {"type": block_type}
        fields = item.get("fields", {})
        if fields is not None:
            if not isinstance(fields, dict):
                self.error(f"{where}: fields はオブジェクトで書いてください")
            else:
                for name, value in fields.items():
                    self._set_field(block, str(name), value, where)
        inputs = item.get("inputs", {})
        if inputs is not None:
            if not isinstance(inputs, dict):
                self.error(f"{where}: inputs はオブジェクトで書いてください")
            else:
                for name, value in inputs.items():
                    self._set_input(block, str(name), value, where)
        extra = item.get("extraState")
        if extra is not None:
            if not isinstance(extra, dict):
                self.error(f"{where}: extraState はオブジェクトで書いてください")
            else:
                block["extraState"] = extra
        if as_value:
            if not self._require_value(block, where):
                return None
        elif not self._require_statement(block, where):
            return None
        return self._finish(block, where)

    # -- 入力 ---------------------------------------------------------------

    def _set_input(
        self, block: dict[str, Any], name: str, value: Any, where: str
    ) -> None:
        info = self._input_info(block["type"], name)
        if info is None:
            self.error(
                f"{where}: ブロック `{block['type']}` に input `{name}` はありません"
                + _suggest_hint(name, self._input_names(block["type"]))
            )
            return
        if info.get("kind") == "statement":
            raw = value if isinstance(value, list) else [value]
            built = self._statements(raw, f"{where}.{name}")
            if built:
                block.setdefault("inputs", {})[name] = {"block": built[0]}
            return
        built_value = self._value(value, info.get("check"), f"{where}.{name}")
        if built_value is not None:
            block.setdefault("inputs", {})[name] = {"block": built_value}

    def _input_info(self, block_type: str, name: str) -> dict[str, Any] | None:
        info = self._block_info(block_type)
        if info is not None:
            for entry in info.get("inputs", []):
                if isinstance(entry, dict) and entry.get("name") == name:
                    return entry
        if block_type == "controls_if":
            if name == "ELSE":
                return {"name": "ELSE", "kind": "statement", "check": []}
            match = _DYNAMIC_IF_INPUT_RE.fullmatch(name)
            if match is not None:
                prefix = match.group(1)
                return {
                    "name": name,
                    "kind": "value" if prefix == "IF" else "statement",
                    "check": ["Boolean"] if prefix == "IF" else [],
                }
        return None

    def _input_names(self, block_type: str) -> set[str]:
        info = self._block_info(block_type)
        if info is None:
            return set()
        names = {
            str(entry.get("name"))
            for entry in info.get("inputs", [])
            if isinstance(entry, dict) and entry.get("name")
        }
        if block_type == "controls_if":
            names.update({"ELSE"})
        return names

    # -- 値 -----------------------------------------------------------------

    def _value(self, value: Any, check: Any, where: str) -> dict[str, Any] | None:
        check_list = [str(item) for item in check] if isinstance(check, list) else []
        built: dict[str, Any] | None
        if isinstance(value, dict):
            keys = set(value)
            if "type" in keys:
                built = self._generic(value, where, as_value=True)
            elif keys == {"var"}:
                name = self._var_name(value.get("var"), where)
                if name is None:
                    return None
                self._register_var(name)
                built = {"type": "variables_get", "fields": {"VAR": {"id": name}}}
            elif keys == {"elapsed"}:
                if value.get("elapsed") is not True:
                    self.error(f"{where}: elapsed は true で書いてください")
                    return None
                built = {"type": "pokecon_elapsed"}
            elif keys == {"call_value", "args"} or keys == {"call_value"}:
                built = self._short_call_value(
                    value.get("call_value"),
                    {"args": value.get("args", [])} if "args" in keys else {},
                    where,
                )
            else:
                self.error(
                    f"{where}: 値の書き方が分かりません（`type` / `var` / `elapsed` / "
                    "`call_value` のどれかで書いてください）"
                )
                return None
        else:
            if isinstance(value, bool):
                built = {
                    "type": "logic_boolean",
                    "fields": {"BOOL": "TRUE" if value else "FALSE"},
                }
            elif isinstance(value, int | float):
                built = {"type": "math_number", "fields": {"NUM": value}}
            elif isinstance(value, str):
                built = {"type": "text", "fields": {"TEXT": value}}
            else:
                self.error(
                    f"{where}: 値は数値・文字列・真偽値・ブロックで書いてください"
                )
                return None
        if built is None:
            return None
        if check_list and not self._value_fits(built["type"], check_list):
            self.error(
                f"{where}: この入力は {', '.join(check_list)} 型です"
                f"（{built['type']} は使えません）"
            )
            return None
        return built

    def _value_fits(self, block_type: str, check: list[str]) -> bool:
        info = self._block_info(block_type)
        if info is None:
            return False
        output = info.get("output")
        if output is None:
            return False
        if not isinstance(output, list) or not output:
            return True
        return bool(set(str(item) for item in output) & set(check))

    # -- フィールド ---------------------------------------------------------

    def _set_field(
        self, block: dict[str, Any], name: str, value: Any, where: str
    ) -> None:
        block_type = block["type"]
        if name in _IGNORED_FIELDS:
            return
        info = self._block_info(block_type)
        if info is None:
            self.error(f"{where}: ブロック `{block_type}` がカタログにありません")
            return
        spec = self._field_info(block_type, name)
        if spec is None:
            self.error(
                f"{where}: ブロック `{block_type}` に field `{name}` はありません"
                + _suggest_hint(name, self._field_names(block_type))
            )
            return
        if spec.get("serializable") is False:
            return
        kind = str(spec.get("kind", "text"))
        converted = self._convert_field(block_type, name, kind, spec, value, where)
        if converted is None:
            return
        block.setdefault("fields", {})[name] = converted
        if kind == "crop" and isinstance(converted, str) and converted:
            self._validate_crop(converted, where)
        if name == "THRESHOLD" and isinstance(converted, int | float):
            if isinstance(converted, bool) or not 0 <= converted <= 1:
                self.error(f"{where}: THRESHOLD は 0〜1 で書いてください: {converted}")

    def _convert_field(
        self,
        block_type: str,
        name: str,
        kind: str,
        spec: dict[str, Any],
        value: Any,
        where: str,
    ) -> Any:
        if kind == "variable":
            if block_type in _DIALOG_BLOCKS:
                text = self._require_str(value, where, f"{name}（受け変数）")
                if text is None:
                    return None
                if (
                    _STEM_RE.fullmatch(text) is None
                    or text in keyword.kwlist
                    or text in _DIALOG_RESERVED
                ):
                    self.error(
                        f"{where}: 受け変数名は英字・数字・`_`にしてください: {text}"
                    )
                    return None
                return text
            variable = self._var_name(value, where)
            if variable is None:
                return None
            self._register_var(variable)
            return {"id": variable}
        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, int | float):
                self.error(f"{where}: `{name}` は数値で書いてください: {value!r}")
                return None
            return value
        if kind == "checkbox":
            if isinstance(value, bool):
                return "TRUE" if value else "FALSE"
            if isinstance(value, str) and value.upper() in ("TRUE", "FALSE"):
                return value.upper()
            self.error(f"{where}: `{name}` は true / false で書いてください")
            return None
        if kind == "controller":
            return self._controller_value(name, spec, value, where)
        if kind == "stickpad":
            text = self._require_str(value, where, name)
            if text is None:
                return None
            if (
                re.fullmatch(r"\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*", text)
                is None
            ):
                self.error(
                    f"{where}: `{name}` は `角度,強さ` で書いてください: {text!r}"
                )
                return None
            return text
        if kind in ("display", "image"):
            return None
        text = self._require_str(value, where, name)
        if text is None:
            return None
        if name == "OPTIONS" and isinstance(value, list):
            # `_require_str` が先に落とすため、ここへは来ない（保険）。
            text = ",".join(str(item) for item in value)
        options = spec.get("options")
        if name not in ("NAME", "TEMPLATE") and isinstance(options, list) and options:
            values = [
                str(option[1])
                for option in options
                if isinstance(option, list | tuple) and len(option) > 1
            ]
            if values and text not in values:
                self.error(
                    f"{where}: `{name}` の値が不正です: {text!r}"
                    + _suggest_hint(text, set(values))
                )
                return None
        return text

    def _controller_value(
        self,
        name: str,
        spec: dict[str, Any],
        value: Any,
        where: str,
    ) -> str | None:
        text = self._require_str(value, where, name)
        if text is None:
            return None
        options = spec.get("options")
        values = (
            [
                str(option[1])
                for option in options
                if isinstance(option, list | tuple) and len(option) > 1
            ]
            if isinstance(options, list)
            else []
        )
        if not values:
            return text
        if text in values:
            return text
        if not text.startswith(("Button.", "Hat.", "Direction.")):
            candidate = "Button." + text
            if candidate in values:
                return candidate
        else:
            bare = text.split(".", 1)[1]
            if bare in values:
                return bare
        self.error(
            f"{where}: `{name}` のボタン名が不正です: {text!r}"
            + _suggest_hint(text, set(values))
        )
        return None

    def _validate_crop(self, text: str, where: str) -> None:
        match = _CROP_RE.fullmatch(text)
        if match is None:
            self.error(f"{where}: CROP は空か `x1,y1,x2,y2` で書いてください: {text!r}")
            return
        x1, y1, x2, y2 = (int(match.group(i)) for i in range(1, 5))
        if x2 <= x1 or y2 <= y1:
            self.error(f"{where}: CROP は x2>x1 かつ y2>y1 にしてください: {text!r}")

    # -- 短縮形 -------------------------------------------------------------

    def _short_press(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, {"duration", "wait"}, where, "press")
        block = self._new("pokecon_press", where)
        if block is None:
            return None
        self._set_field(block, "BUTTON", value, where)
        if "duration" in params:
            self._set_field(block, "DURATION", params["duration"], where)
        if "wait" in params:
            self._set_field(block, "WAIT", params["wait"], where)
        return self._finish(block, where)

    def _short_hold(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, {"wait"}, where, "hold")
        block = self._new("pokecon_hold", where)
        if block is None:
            return None
        self._set_field(block, "TARGET", value, where)
        if "wait" in params:
            self._set_field(block, "WAIT", params["wait"], where)
        return self._finish(block, where)

    def _short_release(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, set(), where, "release")
        block = self._new("pokecon_hold_end", where)
        if block is None:
            return None
        self._set_field(block, "TARGET", value, where)
        return self._finish(block, where)

    def _short_stick(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, {"angle", "duration", "wait"}, where, "stick")
        block = self._new("pokecon_stick", where)
        if block is None:
            return None
        stick = self._stick_side(value, where)
        if stick is not None:
            self._set_field(block, "STICK", stick, where)
        angle = params.get("angle", 90)
        if isinstance(angle, bool) or not isinstance(angle, int | float):
            self.error(f"{where}: angle は数値で書いてください: {angle!r}")
        else:
            angle_text = str(int(angle)) if float(angle).is_integer() else str(angle)
            self._set_field(block, "PAD", f"{angle_text},100", where)
            self._set_field(block, "ANGLE", angle, where)
        if "duration" in params:
            self._set_field(block, "DURATION", params["duration"], where)
        if "wait" in params:
            self._set_field(block, "WAIT", params["wait"], where)
        return self._finish(block, where)

    def _short_stick_off(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, set(), where, "stick_off")
        block = self._new("pokecon_stick_release", where)
        if block is None:
            return None
        stick = self._stick_side(value, where)
        if stick is not None:
            self._set_field(block, "STICK", stick, where)
        return self._finish(block, where)

    def _short_wait(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, set(), where, "wait")
        block = self._new("pokecon_wait", where)
        if block is None:
            return None
        self._set_field(block, "SEC", value, where)
        return self._finish(block, where)

    def _short_press_rep(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(
            params, {"count", "duration", "interval", "wait"}, where, "press_rep"
        )
        block = self._new("pokecon_press_rep", where)
        if block is None:
            return None
        self._set_field(block, "TARGET", value, where)
        for name, key in (
            ("COUNT", "count"),
            ("DURATION", "duration"),
            ("INTERVAL", "interval"),
            ("WAIT", "wait"),
        ):
            if key in params:
                self._set_field(block, name, params[key], where)
        return self._finish(block, where)

    def _short_repeat(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, {"do"}, where, "repeat")
        if "do" not in params:
            self.error(f"{where}: repeat には do を書いてください")
            return None
        block = self._new("controls_repeat_ext", where)
        if block is None:
            return None
        times = self._value(value, ["Number"], f"{where}.repeat")
        if times is not None:
            block.setdefault("inputs", {})["TIMES"] = {"block": times}
        body = self._statement_param(params["do"], f"{where}.do")
        if body:
            block.setdefault("inputs", {})["DO"] = {"block": body[0]}
        return self._finish(block, where)

    def _short_while(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, {"do"}, where, "while")
        if "do" not in params:
            self.error(f"{where}: while には do を書いてください")
            return None
        block = self._new("controls_whileUntil", where)
        if block is None:
            return None
        self._set_field(block, "MODE", "WHILE", where)
        condition = self._value(value, ["Boolean"], f"{where}.while")
        if condition is not None:
            block.setdefault("inputs", {})["BOOL"] = {"block": condition}
        body = self._statement_param(params["do"], f"{where}.do")
        if body:
            block.setdefault("inputs", {})["DO"] = {"block": body[0]}
        return self._finish(block, where)

    def _short_if(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, {"do", "else"}, where, "if")
        if "do" not in params:
            self.error(f"{where}: if には do を書いてください")
            return None
        block = self._new("controls_if", where)
        if block is None:
            return None
        condition = self._value(value, ["Boolean"], f"{where}.if")
        if condition is not None:
            block.setdefault("inputs", {})["IF0"] = {"block": condition}
        body = self._statement_param(params["do"], f"{where}.do")
        if body:
            block.setdefault("inputs", {})["DO0"] = {"block": body[0]}
        if "else" in params:
            other = self._statement_param(params["else"], f"{where}.else")
            if other:
                block["extraState"] = {"hasElse": True}
                block.setdefault("inputs", {})["ELSE"] = {"block": other[0]}
        return self._finish(block, where)

    def _short_break(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        return self._flow("BREAK", value, params, where, "break")

    def _short_continue(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        return self._flow("CONTINUE", value, params, where, "continue")

    def _flow(
        self,
        flow: str,
        value: Any,
        params: dict[str, Any],
        where: str,
        short: str,
    ) -> dict[str, Any] | None:
        self._only(params, set(), where, short)
        if value is not True:
            self.error(f"{where}: {short} は true で書いてください")
            return None
        block = self._new("controls_flow_statements", where)
        if block is None:
            return None
        self._set_field(block, "FLOW", flow, where)
        return self._finish(block, where)

    def _short_print(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, {"kind", "result"}, where, "print")
        if "kind" in params and "result" in params:
            self.error(f"{where}: print の kind と result は同時に使えません")
            return None
        kind = params.get("kind", "print")
        if "result" in params:
            if not isinstance(params["result"], bool):
                self.error(f"{where}: result は true / false で書いてください")
                return None
            kind = "print2" if params["result"] else "print"
        block = self._new("pokecon_print", where)
        if block is None:
            return None
        self._set_field(block, "KIND", kind, where)
        self._set_input(block, "TEXT", value, where)
        return self._finish(block, where)

    def _short_comment(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, set(), where, "comment")
        text = self._require_str(value, where, "comment")
        if text is None:
            return None
        block = self._new("pokecon_comment", where)
        if block is None:
            return None
        self._set_field(block, "TEXT", text, where)
        return self._finish(block, where)

    def _short_finish(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, set(), where, "finish")
        if value is not True:
            self.error(f"{where}: finish は true で書いてください")
            return None
        block = self._new("pokecon_finish", where)
        return self._finish(block, where) if block is not None else None

    def _short_screenshot(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, set(), where, "screenshot")
        if value is not True:
            self.error(f"{where}: screenshot は true で書いてください")
            return None
        block = self._new("pokecon_screenshot", where)
        return self._finish(block, where) if block is not None else None

    def _short_set(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, {"to"}, where, "set")
        if "to" not in params:
            self.error(f"{where}: set には to を書いてください")
            return None
        block = self._new("variables_set", where)
        if block is None:
            return None
        self._set_field(block, "VAR", value, where)
        self._set_input(block, "VALUE", params["to"], where)
        return self._finish(block, where)

    def _short_change(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, {"by"}, where, "change")
        if "by" not in params:
            self.error(f"{where}: change には by を書いてください")
            return None
        block = self._new("math_change", where)
        if block is None:
            return None
        self._set_field(block, "VAR", value, where)
        self._set_input(block, "DELTA", params["by"], where)
        return self._finish(block, where)

    def _short_call(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, {"args"}, where, "call")
        block = self._new("pokecon_sub_call", where)
        if block is None:
            return None
        name = self._call_name(block, value, where)
        if name is not None:
            args = self._call_args(params.get("args", []), f"{where}.args")
            for index, arg in enumerate(args):
                self._set_input(block, f"ARG{index}", arg, where)
            self.calls.append((self.caller, name, len(args)))
        return self._finish(block, where)

    def _short_call_value(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, {"args"}, where, "call_value")
        block = self._new("pokecon_sub_call_value", where)
        if block is None:
            return None
        name = self._call_name(block, value, where)
        if name is not None:
            args = self._call_args(params.get("args", []), f"{where}.args")
            for index, arg in enumerate(args):
                self._set_input(block, f"ARG{index}", arg, where)
            self.calls.append((self.caller, name, len(args)))
        return self._finish(block, where)

    def _call_name(self, block: dict[str, Any], value: Any, where: str) -> str | None:
        if not isinstance(value, str) or not value.strip():
            self.error(f"{where}: 呼ぶサブルーチン名を書いてください")
            return None
        name = value.strip()
        if not _is_identifier(name):
            self.error(f"{where}: サブルーチン名は識別子にしてください: {name}")
            return None
        block.setdefault("fields", {})["NAME"] = name
        return name

    def _call_args(self, raw: Any, where: str) -> list[Any]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            self.error(f"{where}: args は配列で書いてください")
            return []
        if len(raw) > _MAX_SUB_ARGS:
            self.error(f"{where}: サブルーチンの引数は最大 {_MAX_SUB_ARGS} 個までです")
            return raw[:_MAX_SUB_ARGS]
        return raw

    def _short_discord(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        self._only(params, {"kind"}, where, "discord")
        block = self._new("pokecon_discord", where)
        if block is None:
            return None
        self._set_field(block, "KIND", params.get("kind", "text"), where)
        self._set_input(block, "CONTENT", value, where)
        return self._finish(block, where)

    def _short_dialog_choice(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        return self._dialog(
            "pokecon_dialog_choice", value, params, where, "dialog_choice"
        )

    def _short_dialog_number(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        return self._dialog(
            "pokecon_dialog_number", value, params, where, "dialog_number"
        )

    def _short_dialog_check(
        self, value: Any, params: dict[str, Any], where: str
    ) -> dict[str, Any] | None:
        return self._dialog(
            "pokecon_dialog_check", value, params, where, "dialog_check"
        )

    def _dialog(
        self,
        block_type: str,
        value: Any,
        params: dict[str, Any],
        where: str,
        short: str,
    ) -> dict[str, Any] | None:
        mapping = {
            "pokecon_dialog_choice": {
                "var": "VAR",
                "title": "TITLE",
                "label": "LABEL",
                "options": "OPTIONS",
                "default": "DEFAULT",
            },
            "pokecon_dialog_number": {
                "var": "VAR",
                "title": "TITLE",
                "label": "LABEL",
                "min": "MIN",
                "max": "MAX",
                "default": "DEFAULT",
            },
            "pokecon_dialog_check": {
                "var": "VAR",
                "title": "TITLE",
                "label": "LABEL",
                "default": "DEFAULT",
            },
        }[block_type]
        if isinstance(value, str):
            merged: dict[str, Any] = {"var": value}
        elif isinstance(value, dict):
            merged = dict(value)
        else:
            self.error(f"{where}: {short} は受け変数名かオブジェクトで書いてください")
            return None
        for key, item in params.items():
            if key in merged:
                self.error(f"{where}: {short} の `{key}` が重複しています")
                continue
            merged[key] = item
        self._only(merged, set(mapping), where, short)
        if "options" in merged and isinstance(merged["options"], list):
            raw_options = merged["options"]
            if not raw_options:
                self.error(f"{where}: options を1つ以上書いてください")
            elif not all(isinstance(item, str) for item in raw_options):
                self.error(f"{where}: options は文字列の配列で書いてください")
            else:
                merged["options"] = ",".join(str(item) for item in raw_options)
        block = self._new(block_type, where)
        if block is None:
            return None
        for key, value_item in merged.items():
            field_name = mapping[key]
            self._set_field(block, field_name, value_item, where)
        return self._finish(block, where)

    # -- 小物 ---------------------------------------------------------------

    def _new(self, block_type: str, where: str) -> dict[str, Any] | None:
        if self._block_info(block_type) is None:
            self.error(
                f"{where}: ブロック `{block_type}` がカタログにありません"
                "（assets/blockly を確認してください）"
            )
            return None
        return {"type": block_type}

    def _finish(self, block: dict[str, Any], where: str) -> dict[str, Any]:
        self._check_constraints(block, where)
        if not block.get("fields"):
            block.pop("fields", None)
        if not block.get("inputs"):
            block.pop("inputs", None)
        return block

    def _check_constraints(self, block: dict[str, Any], where: str) -> None:
        info = self._block_info(block["type"])
        if info is None:
            return
        fields = block.get("fields")
        for spec in info.get("fields", []):
            if not isinstance(spec, dict):
                continue
            if spec.get("kind") != "template" or spec.get("serializable") is False:
                continue
            name = str(spec.get("name", "TEMPLATE"))
            value = fields.get(name) if isinstance(fields, dict) else None
            if value is None:
                value = spec.get("default")
            if not isinstance(value, str) or not value.strip():
                self.error(
                    f"{where}: TEMPLATE が空です"
                    f"（ブロック `{block['type']}` に Template/ の画像名を指定してください）"
                )

    def _require_statement(self, block: dict[str, Any], where: str) -> bool:
        info = self._block_info(block["type"])
        if info is None:
            return True
        if not info.get("previous"):
            self.error(f"{where}: ブロック `{block['type']}` は文として置けません")
            return False
        return True

    def _require_value(self, block: dict[str, Any], where: str) -> bool:
        info = self._block_info(block["type"])
        if info is None:
            return True
        if info.get("output") is None:
            self.error(f"{where}: ブロック `{block['type']}` は値として使えません")
            return False
        return True

    def _stick_side(self, value: Any, where: str) -> str | None:
        if not isinstance(value, str):
            self.error(f"{where}: スティックは L / R / L+R で書いてください")
            return None
        mapping = {
            "L": "LEFT",
            "LEFT": "LEFT",
            "R": "RIGHT",
            "RIGHT": "RIGHT",
            "L+R": "BOTH",
            "BOTH": "BOTH",
            "LR": "BOTH",
        }
        result = mapping.get(value.strip().upper())
        if result is None:
            self.error(f"{where}: スティックは L / R / L+R で書いてください: {value!r}")
        return result

    def _var_name(self, value: Any, where: str) -> str | None:
        if not isinstance(value, str) or not value.strip():
            self.error(f"{where}: 変数名を文字列で書いてください")
            return None
        name = value.strip()
        if not _is_identifier(name):
            self.error(f"{where}: 変数名は識別子にしてください: {name}")
            return None
        if name == "self":
            self.error(f"{where}: 変数名に self は使えません")
            return None
        return name

    def _register_var(self, name: str) -> None:
        if name in self._var_ids:
            return
        self._var_ids.add(name)
        self.variables.append({"name": name, "id": name})

    def _tags(self, value: Any) -> list[str]:
        if value is None:
            return ["blockly"]
        if isinstance(value, str):
            raw = re.split(r"[,、]", value)
        elif isinstance(value, list):
            raw = []
            for item in value:
                if not isinstance(item, str):
                    self.error("tags は文字列か文字列の配列で書いてください")
                    continue
                raw.append(item)
        else:
            self.error("tags は文字列か文字列の配列で書いてください")
            return ["blockly"]
        tags: list[str] = []
        for item in raw:
            text = item.strip()
            if text and text not in tags:
                tags.append(text)
        return tags or ["blockly"]

    def _validate_calls(self) -> None:
        for caller, name, argc in self.calls:
            if name not in self.sub_defs:
                self.error(
                    f"未定義のサブルーチンです: self.{name}()（{caller} から）"
                    + _suggest_hint(name, set(self.sub_defs))
                )
                continue
            if name in self._bad_sub_args:
                continue
            expected = len(self.sub_defs[name])
            if argc != expected:
                self.error(
                    f"引数の数が違います: self.{name}()"
                    f"（{caller} から: {argc}個、定義は{expected}個）"
                )
        graph: dict[str, set[str]] = {name: set() for name in self.sub_defs}
        for caller, name, _argc in self.calls:
            if caller in graph and name in graph:
                graph[caller].add(name)
        state: dict[str, int] = {}
        stack: list[str] = []
        reported: set[tuple[str, ...]] = set()

        def visit(node: str) -> None:
            current = state.get(node, 0)
            if current == 2:
                return
            if current == 1:
                if node in stack:
                    cycle = tuple(stack[stack.index(node) :] + [node])
                else:
                    cycle = (node,)
                if cycle not in reported:
                    reported.add(cycle)
                    self.error(
                        "サブルーチンの循環呼び出しがあります: "
                        + " -> ".join(f"self.{item}()" for item in cycle)
                    )
                return
            state[node] = 1
            stack.append(node)
            for nxt in sorted(graph.get(node, set())):
                visit(nxt)
            stack.pop()
            state[node] = 2

        for name in sorted(graph):
            visit(name)

    def _block_info(self, block_type: str) -> dict[str, Any] | None:
        info = self.blocks.get(block_type)
        return info if isinstance(info, dict) else None

    def _field_info(self, block_type: str, name: str) -> dict[str, Any] | None:
        info = self._block_info(block_type)
        if info is None:
            return None
        for spec in info.get("fields", []):
            if isinstance(spec, dict) and spec.get("name") == name:
                return spec
        return None

    def _field_names(self, block_type: str) -> set[str]:
        info = self._block_info(block_type)
        if info is None:
            return set()
        return {
            str(spec.get("name"))
            for spec in info.get("fields", [])
            if isinstance(spec, dict) and spec.get("name")
        }

    def _only(
        self, item: dict[str, Any], allowed: set[str], where: str, label: str
    ) -> None:
        for key in item:
            if key not in allowed:
                self.error(
                    f"{where}: {label} の項目 `{key}` は使えません"
                    + _suggest_hint(str(key), allowed)
                )

    def _require_str(self, value: Any, where: str, label: str) -> str | None:
        if not isinstance(value, str):
            self.error(f"{where}: `{label}` は文字列で書いてください")
            return None
        return value

    def error(self, message: str) -> None:
        self.errors.append(message)


def _is_identifier(name: str) -> bool:
    """Python の識別子として使えるか（予約語は除く）。"""
    return bool(name) and name.isidentifier() and not keyword.iskeyword(name)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _block_markdown(name: str, info: dict[str, Any]) -> list[str]:
    lines = [f"### `{name}`", ""]
    tooltip = info.get("tooltip")
    if isinstance(tooltip, str) and tooltip.strip():
        lines.extend([tooltip.strip(), ""])
    shape = _shape_label(info)
    lines.append(f"- 形: {shape}")
    fields = info.get("fields")
    if isinstance(fields, list) and fields:
        lines.append("- fields:")
        for spec in fields:
            if not isinstance(spec, dict) or not spec.get("name"):
                continue
            kind = str(spec.get("kind", "text"))
            if spec.get("serializable", True) is False:
                lines.append(f"  - `{spec['name']}` ({kind}、表示専用)")
                continue
            default = spec.get("default")
            suffix = ""
            if kind not in ("variable",) and default not in (None, ""):
                suffix = f"、既定 `{default}`"
            options = spec.get("options")
            option_text = ""
            if kind != "variable" and isinstance(options, list) and options:
                values = [
                    f"`{option[1]}`"
                    for option in options
                    if isinstance(option, list | tuple) and len(option) > 1
                ]
                if values:
                    shown = ", ".join(values[:24])
                    if len(values) > 24:
                        shown += f", …（他 {len(values) - 24} 個）"
                    option_text = f"、選択肢: {shown}"
            lines.append(f"  - `{spec['name']}` ({kind}{suffix}){option_text}")
    inputs = info.get("inputs")
    if isinstance(inputs, list) and inputs:
        lines.append("- inputs:")
        for spec in inputs:
            if not isinstance(spec, dict) or not spec.get("name"):
                continue
            check = spec.get("check")
            check_text = ""
            if isinstance(check, list) and check:
                check_text = " " + "|".join(str(item) for item in check)
            lines.append(
                f"  - `{spec['name']}` ({spec.get('kind', 'value')}{check_text})"
            )
    lines.append("")
    return lines


def _shape_label(info: dict[str, Any]) -> str:
    output = info.get("output")
    previous = bool(info.get("previous"))
    nxt = bool(info.get("next"))
    if output is not None:
        if isinstance(output, list) and output:
            return "値（" + "|".join(str(item) for item in output) + "）"
        return "値（型自由）"
    if previous and nxt:
        return "文（前後に接続）"
    if previous:
        return "文（末尾のみ）"
    return "トップレベル専用"
