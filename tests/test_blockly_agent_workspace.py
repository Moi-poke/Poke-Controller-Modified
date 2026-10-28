"""既存workspaceの検査・保存と、限定試行CLIの入口を確かめる。

CLI はサブプロセスで呼ぶ（`tools/blockly_agent.py`）。実機・実カメラは使わず、
一時アプリに Blockly 資産と有効なテンプレ画像だけを置く。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from blockly_node import NEEDS_NODE
from core import blockly_headless
from services import blockly_author

pytestmark = NEEDS_NODE

ROOT = Path(__file__).resolve().parent.parent
BLOCKLY = ROOT / "SerialController" / "assets" / "blockly"


def _app(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    shutil.copytree(BLOCKLY, app / "assets" / "blockly")
    template = app / "Template" / "controller_check"
    template.mkdir(parents=True)
    image = np.full((20, 20, 3), 100, dtype=np.uint8)
    ok, buf = cv2.imencode(".png", image)
    assert ok
    (template / "btn_a.png").write_bytes(bytes(buf))
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    return app


def _spec() -> dict[str, Any]:
    return {
        "stem": "WsBase",
        "name": "workspace の検査",
        "tags": ["blockly"],
        "do": [
            {"set": "count", "to": 0},
            {"wait": 1},
            {"press": "A", "duration": 0.02, "wait": 0},
        ],
    }


def _iter_blocks(workspace: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    stack: list[Any] = list(reversed(workspace["blocks"]["blocks"]))
    while stack:
        block = stack.pop()
        if not isinstance(block, dict):
            continue
        found.append(block)
        inputs = block.get("inputs")
        if isinstance(inputs, dict):
            for slot in inputs.values():
                if isinstance(slot, dict) and isinstance(slot.get("block"), dict):
                    stack.append(slot["block"])
        nxt = block.get("next")
        if isinstance(nxt, dict) and isinstance(nxt.get("block"), dict):
            stack.append(nxt["block"])
    return found


def _workspace(app: Path) -> dict[str, Any]:
    catalog = blockly_headless.catalog(app)
    assert catalog.ok, catalog.errors
    built = blockly_author.build_workspace(_spec(), catalog.data)
    assert built.ok, built.errors
    assert built.workspace is not None
    workspace = built.workspace
    for index, block in enumerate(_iter_blocks(workspace)):
        block["id"] = f"keep{index}"
        block["x"] = 10 * index
        block["y"] = 20 * index
    return workspace


def _struct_workspace(app: Path) -> dict[str, Any]:
    catalog = blockly_headless.catalog(app)
    assert catalog.ok, catalog.errors
    built = blockly_author.build_workspace(
        {
            "name": "struct",
            "do": [
                {
                    "if": True,
                    "do": [{"press": "A", "duration": 0.02, "wait": 0}],
                }
            ],
        },
        catalog.data,
    )
    assert built.ok, built.errors
    assert built.workspace is not None
    workspace = built.workspace
    for index, block in enumerate(_iter_blocks(workspace)):
        block["id"] = f"struct{index}"
    return workspace


def _empty_program(app: Path) -> dict[str, Any]:
    catalog = blockly_headless.catalog(app)
    assert catalog.ok, catalog.errors
    built = blockly_author.build_workspace({"name": "empty", "do": []}, catalog.data)
    assert built.ok, built.errors
    assert built.workspace is not None
    workspace = built.workspace
    for index, block in enumerate(_iter_blocks(workspace)):
        block["id"] = f"empty{index}"
    return workspace


def _first(workspace: dict[str, Any], block_type: str) -> dict[str, Any]:
    return next(b for b in _iter_blocks(workspace) if b.get("type") == block_type)


def _run(app: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "blockly_agent.py"), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _json(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert proc.stdout.strip(), proc.stderr
    value: dict[str, Any] = json.loads(proc.stdout)
    return value


def test_workspace_check_and_save_preserve_structure(tmp_path: Path) -> None:
    app = _app(tmp_path)
    workspace = _workspace(app)
    source = tmp_path / "Saved.blockly.json"
    source.write_text(json.dumps(workspace, ensure_ascii=False), encoding="utf-8")

    checked = _run(
        app, "check", str(source), "--workspace", "--app-dir", str(app), "--json"
    )
    assert checked.returncode == 0, checked.stderr
    payload = _json(checked)
    assert payload["ok"] is True
    assert "self.wait(1)" in payload["code"]

    saved = _run(
        app,
        "build",
        str(source),
        "--workspace",
        "--write",
        "--stem",
        "WsSaved",
        "--app-dir",
        str(app),
        "--json",
    )
    assert saved.returncode == 0, saved.stderr
    assert _json(saved)["status"] == "saved"
    stored = json.loads(
        (app / "Commands" / "PythonCommands" / "WsSaved.blockly.json").read_text(
            encoding="utf-8"
        )
    )
    kept = _iter_blocks(stored)
    original = _iter_blocks(workspace)
    assert [b.get("id") for b in kept] == [b.get("id") for b in original]
    assert kept[0]["x"] == 0 and kept[1]["x"] == 10
    assert stored["variables"] == workspace["variables"]


def test_workspace_save_rejects_external_edit_until_allowed(tmp_path: Path) -> None:
    app = _app(tmp_path)
    workspace = _workspace(app)
    source = tmp_path / "Saved.blockly.json"
    source.write_text(json.dumps(workspace, ensure_ascii=False), encoding="utf-8")
    first = _run(
        app,
        "build",
        str(source),
        "--workspace",
        "--write",
        "--stem",
        "WsEdit",
        "--app-dir",
        str(app),
        "--json",
    )
    assert first.returncode == 0, first.stderr
    py_path = app / "Commands" / "PythonCommands" / "WsEdit.py"
    py_path.write_text(
        py_path.read_text(encoding="utf-8") + "\n# hand edit\n", encoding="utf-8"
    )

    blocked = _run(
        app,
        "build",
        str(source),
        "--workspace",
        "--write",
        "--stem",
        "WsEdit",
        "--app-dir",
        str(app),
        "--json",
    )
    assert blocked.returncode == 1
    assert _json(blocked)["ok"] is False
    assert "# hand edit" in py_path.read_text(encoding="utf-8")

    allowed = _run(
        app,
        "build",
        str(source),
        "--workspace",
        "--write",
        "--stem",
        "WsEdit",
        "--app-dir",
        str(app),
        "--overwrite-external",
        "--json",
    )
    assert allowed.returncode == 0, allowed.stderr
    assert "# hand edit" not in py_path.read_text(encoding="utf-8")


def test_trial_preview_sends_nothing_and_requires_digest(tmp_path: Path) -> None:
    app = _app(tmp_path)
    spec = tmp_path / "TrialSpec.json"
    spec.write_text(json.dumps(_spec(), ensure_ascii=False), encoding="utf-8")

    # 到達できないポートでも、preview は通信しないので成功する。
    preview = _run(
        app,
        "trial",
        str(spec),
        "--server",
        "http://127.0.0.1:1",
        "--max-seconds",
        "3",
        "--app-dir",
        str(app),
        "--json",
    )
    assert preview.returncode == 0, preview.stderr
    payload = _json(preview)
    assert payload["status"] == "preview"
    assert len(payload["codeSha256"]) == 64
    assert "_pokecon_step(self, " in payload["code"]

    missing = _run(
        app,
        "trial",
        str(spec),
        "--server",
        "http://127.0.0.1:1",
        "--max-seconds",
        "3",
        "--allow-input",
        "--app-dir",
        str(app),
        "--json",
    )
    assert missing.returncode == 2
    assert _json(missing)["ok"] is False


def test_trial_rejects_wrong_digest_before_connecting(tmp_path: Path) -> None:
    app = _app(tmp_path)
    spec = tmp_path / "TrialSpec.json"
    spec.write_text(json.dumps(_spec(), ensure_ascii=False), encoding="utf-8")
    wrong = _run(
        app,
        "trial",
        str(spec),
        "--server",
        "http://127.0.0.1:1",
        "--max-seconds",
        "3",
        "--allow-input",
        "--expected-sha256",
        "0" * 64,
        "--app-dir",
        str(app),
        "--json",
    )
    assert wrong.returncode == 1
    assert "codeSha256" in _json(wrong)["message"]


def _ops(*items: dict[str, Any]) -> dict[str, Any]:
    return {"operations": list(items)}


def _patch(
    app: Path, workspace: Path, ops: dict[str, Any], *args: str
) -> subprocess.CompletedProcess[str]:
    target = workspace.parent / "ops.json"
    target.write_text(json.dumps(ops, ensure_ascii=False), encoding="utf-8")
    return _run(
        app,
        "patch",
        str(workspace),
        "--ops",
        str(target),
        "--app-dir",
        str(app),
        *args,
        "--json",
    )


def _wait_block(workspace: dict[str, Any]) -> dict[str, Any]:
    return next(b for b in _iter_blocks(workspace) if b.get("type") == "pokecon_wait")


def test_patch_sets_field_and_preserves_structure(tmp_path: Path) -> None:
    app = _app(tmp_path)
    workspace = _workspace(app)
    source = tmp_path / "Saved.blockly.json"
    source.write_text(json.dumps(workspace, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "patched.blockly.json"
    wait_id = _wait_block(workspace)["id"]

    result = _patch(
        app,
        source,
        _ops(
            {
                "op": "set_field",
                "blockId": wait_id,
                "field": "SEC",
                "value": 2.5,
            }
        ),
        "--output",
        str(output),
    )
    assert result.returncode == 0, result.stderr
    payload = _json(result)
    assert payload["status"] == "written"
    assert payload["changes"][0]["new"] == 2.5
    patched = json.loads(output.read_text(encoding="utf-8"))
    assert _wait_block(patched)["fields"]["SEC"] == 2.5
    assert [b.get("id") for b in _iter_blocks(patched)] == [
        b.get("id") for b in _iter_blocks(workspace)
    ]
    assert "self.wait(2.5)" in payload["code"]


def test_patch_dry_run_and_invalid_ops_write_nothing(tmp_path: Path) -> None:
    app = _app(tmp_path)
    workspace = _workspace(app)
    source = tmp_path / "Saved.blockly.json"
    source.write_text(json.dumps(workspace, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "never.blockly.json"

    dry = _patch(
        app,
        source,
        _ops(
            {
                "op": "set_field",
                "selector": {"type": "pokecon_wait", "occurrence": 0},
                "field": "SEC",
                "value": 3,
            }
        ),
        "--dry-run",
    )
    assert dry.returncode == 0, dry.stderr
    assert _json(dry)["status"] == "dry_run"
    assert not output.exists()

    bad_target = _patch(
        app,
        source,
        _ops({"op": "set_field", "blockId": "nope", "field": "SEC", "value": 3}),
        "--output",
        str(output),
    )
    assert bad_target.returncode == 1
    assert _json(bad_target)["ok"] is False
    assert not output.exists()

    bad_field = _patch(
        app,
        source,
        _ops(
            {
                "op": "set_field",
                "selector": {"type": "pokecon_wait", "occurrence": 0},
                "field": "NOPE",
                "value": 3,
            }
        ),
        "--output",
        str(output),
    )
    assert bad_field.returncode == 1
    assert "NOPE" in _json(bad_field)["message"]


def test_patch_checkbox_crop_and_variable_validation(tmp_path: Path) -> None:
    app = _app(tmp_path)
    catalog = blockly_headless.catalog(app)
    assert catalog.ok, catalog.errors
    built = blockly_author.build_workspace(
        {
            "name": "vision patch",
            "do": [
                {"set": "count", "to": 0},
                {
                    "if": {
                        "type": "pokecon_vision_contains",
                        "fields": {"TEMPLATE": "controller_check/btn_a.png"},
                    },
                    "do": [{"press": "A"}],
                },
            ],
        },
        catalog.data,
    )
    assert built.ok, built.errors
    assert built.workspace is not None
    source = tmp_path / "Vision.blockly.json"
    source.write_text(json.dumps(built.workspace, ensure_ascii=False), encoding="utf-8")

    checkbox = _patch(
        app,
        source,
        _ops(
            {
                "op": "set_field",
                "selector": {"type": "pokecon_vision_contains"},
                "field": "USE_GRAY",
                "value": False,
            }
        ),
        "--dry-run",
    )
    assert checkbox.returncode == 0, checkbox.stderr
    vision = next(
        b
        for b in _iter_blocks(_json(checkbox)["workspace"])
        if b.get("type") == "pokecon_vision_contains"
    )
    assert vision["fields"]["USE_GRAY"] == "FALSE"

    bad_crop = _patch(
        app,
        source,
        _ops(
            {
                "op": "set_field",
                "selector": {"type": "pokecon_vision_contains"},
                "field": "CROP",
                "value": "1,2,3",
            }
        ),
        "--dry-run",
    )
    assert bad_crop.returncode == 1
    assert "x1,y1,x2,y2" in _json(bad_crop)["message"]

    variable = _patch(
        app,
        source,
        _ops(
            {
                "op": "set_field",
                "selector": {"type": "variables_set"},
                "field": "VAR",
                "value": "other",
            }
        ),
        "--dry-run",
    )
    assert variable.returncode == 1
    assert "変数フィールド" in _json(variable)["message"]


def test_revision_guard_blocks_stale_write(tmp_path: Path) -> None:
    app = _app(tmp_path)
    workspace = _workspace(app)
    source = tmp_path / "Saved.blockly.json"
    source.write_text(json.dumps(workspace, ensure_ascii=False), encoding="utf-8")
    first = _run(
        app,
        "build",
        str(source),
        "--workspace",
        "--write",
        "--stem",
        "RevisionGuard",
        "--app-dir",
        str(app),
        "--json",
    )
    assert first.returncode == 0, first.stderr
    digest = _json(first)["workspaceSha256"]
    py_path = app / "Commands" / "PythonCommands" / "RevisionGuard.py"
    before = py_path.read_text(encoding="utf-8")

    stale = _patch(
        app,
        source,
        _ops(
            {
                "op": "set_field",
                "selector": {"type": "pokecon_wait"},
                "field": "SEC",
                "value": 2,
            }
        ),
        "--write",
        "--stem",
        "RevisionGuard",
        "--expect-current-sha256",
        "0" * 64,
    )
    assert stale.returncode == 1
    assert "変わっています" in _json(stale)["message"]
    assert py_path.read_text(encoding="utf-8") == before

    fresh = _patch(
        app,
        source,
        _ops(
            {
                "op": "set_field",
                "selector": {"type": "pokecon_wait"},
                "field": "SEC",
                "value": 2,
            }
        ),
        "--write",
        "--stem",
        "RevisionGuard",
        "--expect-current-sha256",
        digest,
    )
    assert fresh.returncode == 0, fresh.stderr
    assert "self.wait(2)" in py_path.read_text(encoding="utf-8")


def test_workspace_digest_ignores_record_and_format(tmp_path: Path) -> None:
    workspace = {
        "blocks": {"languageVersion": 0, "blocks": []},
        "variables": [{"name": "x", "id": "x"}],
    }
    with_record = {
        "variables": [{"name": "x", "id": "x"}],
        "blocks": {"blocks": [], "languageVersion": 0},
        "pokecon": {"pySha256": "abc"},
    }
    assert blockly_author.workspace_digest(
        workspace
    ) == blockly_author.workspace_digest(with_record)


def test_patch_structural_create_connect_delete(tmp_path: Path) -> None:
    app = _app(tmp_path)
    workspace = _struct_workspace(app)
    source = tmp_path / "Struct.blockly.json"
    source.write_text(json.dumps(workspace, ensure_ascii=False), encoding="utf-8")
    if_block = _first(workspace, "controls_if")
    program = _first(workspace, "pokecon_program")
    ops = _ops(
        {
            "op": "create_block",
            "type": "pokecon_wait",
            "id": "wait0",
            "fields": {"SEC": 1},
            "parent": {"blockId": program["id"]},
            "input": "DO",
            "insert": True,
        },
        {
            "op": "create_block",
            "type": "controls_if",
            "id": "if2",
            "after": {"blockId": if_block["id"]},
        },
        {
            "op": "create_block",
            "type": "logic_boolean",
            "id": "bool1",
            "fields": {"BOOL": "TRUE"},
            "x": 10,
            "y": 10,
        },
        {
            "op": "connect",
            "child": {"blockId": "bool1"},
            "parent": {"blockId": "if2"},
            "input": "IF0",
        },
        {
            "op": "create_block",
            "type": "pokecon_wait",
            "id": "wait2",
            "fields": {"SEC": 2},
            "after": {"blockId": "if2"},
        },
    )

    created = _patch(app, source, ops, "--dry-run")
    assert created.returncode == 0, created.stderr
    payload = _json(created)
    assert [change["op"] for change in payload["changes"]] == [
        "create_block",
        "create_block",
        "create_block",
        "connect",
        "create_block",
    ]
    assert "if True:" in payload["code"]
    assert "self.wait(2)" in payload["code"]
    patched = payload["workspace"]
    joined = " ".join(str(block.get("id")) for block in _iter_blocks(patched))
    assert "if2" in joined and "bool1" in joined and "wait2" in joined

    output = tmp_path / "StructPatched.blockly.json"
    written = _patch(app, source, ops, "--output", str(output))
    assert written.returncode == 0, written.stderr

    deleted = _patch(
        app,
        output,
        _ops(
            {"op": "delete_block", "blockId": "wait2"},
            {"op": "delete_block", "blockId": "wait0"},
            {"op": "delete_block", "blockId": "if2", "recursive": True},
        ),
        "--dry-run",
    )
    assert deleted.returncode == 0, deleted.stderr
    remaining = {
        str(block.get("id")) for block in _iter_blocks(_json(deleted)["workspace"])
    }
    assert "if2" not in remaining
    assert "bool1" not in remaining
    assert "wait0" not in remaining
    assert if_block["id"] in remaining


def test_patch_structural_rejects_bad_connections(tmp_path: Path) -> None:
    app = _app(tmp_path)
    workspace = _struct_workspace(app)
    source = tmp_path / "Struct.blockly.json"
    source.write_text(json.dumps(workspace, ensure_ascii=False), encoding="utf-8")
    if_block = _first(workspace, "controls_if")
    press = _first(workspace, "pokecon_press")

    empty = _empty_program(app)
    empty_source = tmp_path / "Empty.blockly.json"
    empty_source.write_text(json.dumps(empty, ensure_ascii=False), encoding="utf-8")
    empty_program = _first(empty, "pokecon_program")
    statement = _patch(
        app,
        empty_source,
        _ops(
            {
                "op": "create_block",
                "type": "math_number",
                "fields": {"NUM": 1},
                "parent": {"blockId": empty_program["id"]},
                "input": "DO",
            }
        ),
        "--dry-run",
    )
    assert statement.returncode == 1
    assert "文として" in _json(statement)["message"]

    bad_input = _patch(
        app,
        source,
        _ops(
            {
                "op": "create_block",
                "type": "logic_boolean",
                "fields": {"BOOL": "TRUE"},
                "parent": {"blockId": if_block["id"]},
                "input": "NOPE",
            }
        ),
        "--dry-run",
    )
    assert bad_input.returncode == 1
    assert "NOPE" in _json(bad_input)["message"]

    cycle = _patch(
        app,
        source,
        _ops(
            {
                "op": "connect",
                "child": {"blockId": if_block["id"]},
                "after": {"blockId": press["id"]},
            }
        ),
        "--dry-run",
    )
    assert cycle.returncode == 1
    assert "子孫" in _json(cycle)["message"]

    program = _first(workspace, "pokecon_program")
    children = _patch(
        app,
        source,
        _ops({"op": "delete_block", "blockId": if_block["id"]}),
        "--dry-run",
    )
    assert children.returncode == 1
    assert "子ブロック" in _json(children)["message"]

    root = _patch(
        app,
        source,
        _ops({"op": "delete_block", "blockId": program["id"]}),
        "--dry-run",
    )
    assert root.returncode == 1
    assert "削除できません" in _json(root)["message"]


def test_trial_rejects_non_localhost_and_bad_seconds(tmp_path: Path) -> None:
    app = _app(tmp_path)
    spec = tmp_path / "TrialSpec.json"
    spec.write_text(json.dumps(_spec(), ensure_ascii=False), encoding="utf-8")
    preview = _run(
        app,
        "trial",
        str(spec),
        "--server",
        "http://127.0.0.1:1",
        "--max-seconds",
        "3",
        "--app-dir",
        str(app),
        "--json",
    )
    assert preview.returncode == 0, preview.stderr
    digest = _json(preview)["codeSha256"]
    remote = _run(
        app,
        "trial",
        str(spec),
        "--server",
        "http://192.0.2.10:8000",
        "--max-seconds",
        "3",
        "--allow-input",
        "--expected-sha256",
        digest,
        "--app-dir",
        str(app),
        "--json",
    )
    assert remote.returncode == 2
    assert "localhost" in _json(remote)["message"]
    bad = _run(
        app,
        "trial",
        str(spec),
        "--server",
        "http://127.0.0.1:1",
        "--max-seconds",
        "0",
        "--app-dir",
        str(app),
        "--json",
    )
    assert bad.returncode == 2
