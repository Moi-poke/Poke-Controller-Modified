"""Blockly Phase 3（ブロック表現力）の検証。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BLOCKLY = ROOT / "SerialController" / "assets" / "blockly"
SRC = BLOCKLY / "pokecon_blocks.js"

NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node が無いため飛ばす"
)


def test_press_block_offers_dpad() -> None:
    """pressブロックで十字キー（Direction）を選べること。"""
    text = SRC.read_text(encoding="utf-8")
    press_sec = text[text.index('type: "pokecon_press"') :]
    press_sec = press_sec[: press_sec.index('type: "pokecon_stick"')]
    assert "Direction.UP" in press_sec
    assert "Direction.R_UP" in press_sec


def test_press_generator_handles_direction() -> None:
    """Direction選択時はButton.を付けないこと。"""
    text = SRC.read_text(encoding="utf-8")
    gen_start = text.index('forBlock["pokecon_press"]')
    gen_sec = text[gen_start : gen_start + 600]
    assert "Direction." in gen_sec


def test_program_imports_direction_for_dpad_press() -> None:
    """十字キーpressだけのプログラムでもDirectionがimportされること。"""
    text = SRC.read_text(encoding="utf-8")
    assert (
        "Direction"
        in text[text.index("var useStick") : text.index("var useButton") + 200]
    )


PROBE_PRESS_DPAD_JS = """\
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const root = process.argv[1];
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const sandbox = { console, setTimeout, clearTimeout };
vm.createContext(sandbox);
for (const f of [
  'blockly_compressed.js',
  'blocks_compressed.js',
  'python_compressed.js',
  'msg/ja.js',
]) {
  vm.runInContext(read(f), sandbox, { filename: f });
}
const fail = (msg) => {
  console.error('PRESS-DPAD-FAIL: ' + msg);
  process.exit(1);
};
try {
  vm.runInContext(read('pokecon_blocks.js'), sandbox, { filename: 'pokecon_blocks.js' });
} catch (e) {
  fail('pokecon_blocks.js が投げた: ' + e.constructor.name + ': ' + e.message);
}
const gen = sandbox.Blockly.Python;
const state = {
  blocks: {
    languageVersion: 0,
    blocks: [
      {
        type: 'pokecon_program',
        fields: { NAME: 'DpadTest' },
        inputs: {
          DO: {
            block: {
              type: 'pokecon_press',
              fields: { BUTTON: 'Direction.UP', DURATION: 0.1, WAIT: 0.1 },
            },
          },
        },
      },
    ],
  },
};
const ws = new sandbox.Blockly.Workspace();
sandbox.Blockly.serialization.workspaces.load(state, ws);
const code = gen.workspaceToCode(ws);
ws.dispose();
console.log('=== GENERATED START ===');
console.log(code);
console.log('=== GENERATED END ===');
"""


@NEEDS_NODE
def test_browser_press_dpad_codegen() -> None:
    """十字キーpressの生成コードが実行可能形であること。"""
    from core import blockly_validate

    proc = subprocess.run(
        ["node", "-e", PROBE_PRESS_DPAD_JS, str(BLOCKLY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"probe失敗:\n{proc.stderr}\n{proc.stdout}"
    start = proc.stdout.index("=== GENERATED START ===\n") + len(
        "=== GENERATED START ===\n"
    )
    end = proc.stdout.index("=== GENERATED END ===")
    code = proc.stdout[start:end]
    assert "self.press(Direction.UP, duration=0.1, wait=0.1)" in code
    assert "Button.Direction" not in code
    assert "from Commands.Keys import Direction, Stick" in code
    assert blockly_validate.validate_generated_code(code) == []
