"""Blocklyブラウザ資産の結合検証（node必須、無ければskip）。

ブラウザの `<script>` 読みでは `pythonGenerator` 大域は存在せず、
生成器は `Blockly.Python` に付く（Node の `require().pythonGenerator`
とは形が違う）。カスタム生成器の登録漏れはヘッドレス gate では
検出できないため、node の vm でブラウザ相当の文脈を作り、
読込・登録・生成までを通して確かめる。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BLOCKLY = ROOT / "SerialController" / "assets" / "blockly"

NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node が無いため飛ばす"
)

PROBE_JS = """\
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const root = process.argv[1];
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
// ブラウザ相当：module/define/exports の無い文脈で UMD の Script 分岐に入る。
// Blockly のイベント系が setTimeout を要求するため与える（描画はしない）。
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
  console.error('BROWSER-PROBE-FAIL: ' + msg);
  process.exit(1);
};
if (!sandbox.Blockly || typeof sandbox.Blockly.Python !== 'object') {
  fail('Blockly.Python が無い');
}
try {
  vm.runInContext(read('pokecon_blocks.js'), sandbox, { filename: 'pokecon_blocks.js' });
} catch (e) {
  fail('pokecon_blocks.js が投げた: ' + e.constructor.name + ': ' + e.message);
}
const gen = sandbox.Blockly.Python;
for (const t of ['pokecon_program', 'pokecon_press', 'pokecon_wait']) {
  if (!gen.forBlock || typeof gen.forBlock[t] !== 'function') {
    fail(t + ' が登録されていない');
  }
}
if (gen.INDENT !== '    ') {
  fail('INDENT が4スペースでない: ' + JSON.stringify(gen.INDENT));
}
// 利用者の操作相当：program の中に press＋wait＋repeat(B) を並べる。
const state = {
  blocks: {
    languageVersion: 0,
    blocks: [
      {
        type: 'pokecon_program',
        fields: { NAME: '結合検証' },
        inputs: {
          DO: {
            block: {
              type: 'pokecon_press',
              fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 },
              next: {
                block: {
                  type: 'pokecon_wait',
                  fields: { SEC: 0.5 },
                  next: {
                    block: {
                      type: 'controls_repeat',
                      fields: { TIMES: 3 },
                      inputs: {
                        DO: {
                          block: {
                            type: 'pokecon_press',
                            fields: { BUTTON: 'B', DURATION: 0.1, WAIT: 0.1 },
                          },
                        },
                      },
                    },
                  },
                },
              },
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
def test_browser_globals_and_codegen() -> None:
    from core import blockly_validate

    proc = subprocess.run(
        ["node", "-e", PROBE_JS, str(BLOCKLY)],
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
    assert "self.press(Button.A" in code
    assert "self.wait(" in code
    # JS生成の do() 本体は8スペース（INDENT 4＋prefixLines 4）。12では無い。
    assert "\n        self.press(Button.A" in code
    assert "\n            self.press(Button.B" in code
    assert blockly_validate.validate_stem("BrowserTest") == []
    assert blockly_validate.validate_generated_code(code) == []
