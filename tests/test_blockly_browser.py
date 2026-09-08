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


PROBE_TPLAPPLY_JS = """\
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const root = process.argv[1];
const html = fs.readFileSync(path.join(root, 'editor.html'), 'utf8');
const m = html.match(/<script>([\\s\\S]*?)<\\/script>\\s*<\\/body>/);
if (!m) { console.error('TPLAPPLY-FAIL: inline script not found'); process.exit(1); }

// ---- 最小DOM/Blocklyスタブ（フォーカス喪失の再現が目的） ----
function makeEl() {
  return { value: '', textContent: '', style: {}, handlers: {},
    appendChild: function () {}, addEventListener: function (ev, fn) { this.handlers[ev] = fn; } };
}
const els = {};
for (const id of ['stem', 'save', 'status', 'files', 'open', 'del', 'tpllist', 'tplapply', 'ws']) {
  els[id] = makeEl();
}
els.tpllist.value = 'my-pack/a.png';
const blocks = {};
function fakeBlock(withTpl) {
  return { applied: null,
    getField: function (n) { return (n === 'TEMPLATE' && withTpl) ? {} : null; },
    setFieldValue: function (v, f) { this.applied = [v, f]; } };
}
blocks.blk1 = fakeBlock(true);
blocks.blk2 = fakeBlock(false);
let changeFn = null;
let liveSelected = null;
const wsStub = {
  addChangeListener: function (fn) { changeFn = fn; },
  getBlockById: function (id) { return blocks[id]; },
  getTopBlocks: function () { return []; },
  getAllBlocks: function () { return []; },
};
const sandbox = { console, setTimeout, clearTimeout, Promise,
  JSON,
  document: {
    getElementById: function (id) { return els[id]; },
    createElement: function () { return makeEl(); },
  },
  window: { confirm: function () { return true; } },
  fetch: function (url) {
    const body = url.indexOf('templates') >= 0 ? { templates: ['my-pack/a.png'] } : { stems: [] };
    return Promise.resolve({ json: function () { return Promise.resolve(body); } });
  },
  Blockly: {
    inject: function () { return wsStub; },
    Events: { SELECTED: 'selected' },
    getSelected: function () { return liveSelected; },
    serialization: { workspaces: { save: function () { return {}; }, load: function () {} } },
    Python: null,
  },
};
vm.createContext(sandbox);
try {
  vm.runInContext(m[1], sandbox, { filename: 'editor-inline.js' });
} catch (e) {
  console.error('TPLAPPLY-FAIL: inline script threw: ' + e.constructor.name + ': ' + e.message);
  process.exit(1);
}
function clickApply() { els.tplapply.handlers.click(); }
function out(tag, v) { console.log(tag + '=' + v); }
// 1. visionブロック選択後にフォーカス喪失（click時はnull）→ 記憶から反映されること。
changeFn({ type: 'selected', oldElementId: undefined, newElementId: 'blk1' });
liveSelected = null;
clickApply();
out('case1_applied', JSON.stringify(blocks.blk1.applied));
out('case1_status', els.status.textContent);
// 2. 非テンプレブロックを選択したら記憶は消え、案内が出ること。
blocks.blk1.applied = null;
changeFn({ type: 'selected', oldElementId: 'blk1', newElementId: 'blk2' });
liveSelected = null;
clickApply();
out('case2_applied', JSON.stringify(blocks.blk1.applied));
out('case2_status', els.status.textContent);
// 3. 生選択が有効な場合はそれを使うこと。
liveSelected = blocks.blk1;
clickApply();
out('case3_applied', JSON.stringify(blocks.blk1.applied));
"""


@NEEDS_NODE
def test_tplapply_falls_back_to_last_selected() -> None:
    proc = subprocess.run(
        ["node", "-e", PROBE_TPLAPPLY_JS, str(BLOCKLY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"probe失敗:\n{proc.stderr}\n{proc.stdout}"
    lines = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    assert lines["case1_applied"] == '["my-pack/a.png","TEMPLATE"]'
    assert lines["case1_status"].startswith("反映しました")
    assert lines["case2_applied"] == "null"
    assert "選んでください" in lines["case2_status"]
    assert lines["case3_applied"] == '["my-pack/a.png","TEMPLATE"]'


PROBE_VISION_JS = """\
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
  console.error('BROWSER-PROBE-FAIL: ' + msg);
  process.exit(1);
};
try {
  vm.runInContext(read('pokecon_blocks.js'), sandbox, { filename: 'pokecon_blocks.js' });
} catch (e) {
  fail('pokecon_blocks.js が投げた: ' + e.constructor.name + ': ' + e.message);
}
const gen = sandbox.Blockly.Python;
for (const t of [
  'pokecon_vision_contains',
  'pokecon_vision_wait_appear',
  'pokecon_vision_wait_gone',
  'pokecon_vision_position',
  'pokecon_vision_wait_stable',
  'pokecon_vision_color',
]) {
  if (!gen.forBlock || typeof gen.forBlock[t] !== 'function') {
    fail(t + ' が登録されていない');
  }
}
const state = {
  blocks: {
    languageVersion: 0,
    blocks: [
      {
        type: 'pokecon_program',
        fields: { NAME: '画像待機' },
        inputs: {
          DO: {
            block: {
              type: 'pokecon_vision_wait_appear',
              fields: { TEMPLATE: 'my-pack/a.png', TIMEOUT: 10, THRESHOLD: 0.7, CROP: '' },
              next: {
                block: {
                  type: 'controls_if',
                  inputs: {
                    IF0: {
                      block: {
                        type: 'pokecon_vision_contains',
                        fields: { TEMPLATE: 'my-pack/a.png', THRESHOLD: 0.7, CROP: '' },
                      },
                    },
                    DO0: {
                      block: {
                        type: 'pokecon_press',
                        fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 },
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
def test_browser_vision_codegen_switches_base() -> None:
    from core import blockly_validate
    from services import blockly_templates

    proc = subprocess.run(
        ["node", "-e", PROBE_VISION_JS, str(BLOCKLY)],
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
    assert "ImageProcPythonCommand" in code
    assert "def __init__(self, cam, gui=None):" in code
    assert 'self.waitTemplate("my-pack/a.png", timeout=10, threshold=0.7)' in code
    assert 'self.isContainTemplate("my-pack/a.png", threshold=0.7)' in code
    assert "self.press(Button.A" in code
    assert blockly_validate.validate_generated_code(code) == []
    assert blockly_templates.validate_template_refs(code) == []
    assert blockly_templates.warn_template_refs(code) == []
