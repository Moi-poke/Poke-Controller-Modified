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


def test_modal_hint_is_static_markup() -> None:
    """操作案内は常設文言であり、操作フィードバックで消えないこと。"""
    html = (BLOCKLY / "editor.html").read_text(encoding="utf-8")
    assert 'id="capmodalhint"' in html
    assert "枠内ドラッグで移動" in html
    # 案内はcapModalSetStatusの対象ではないこと（上書きされない）。
    assert "capModalSetStatus('ドラッグで新規選択" not in html


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
for (const id of ['stem', 'save', 'status', 'files', 'open', 'del', 'tpllist', 'tplapply', 'ws',
  'capget', 'capwrap', 'capimg', 'caprect', 'capname', 'capsave', 'capstatus',
  'capmodal', 'capmodalclose', 'capmodalbox', 'capbigwrap', 'capbigimg', 'capbigrect',
  'caph-nw', 'caph-n', 'caph-ne', 'caph-e', 'caph-se', 'caph-s', 'caph-sw', 'caph-w',
  'capx', 'capy', 'capw', 'caph', 'caprx', 'capry', 'caprw', 'caprh', 'capretake', 'capok', 'capcancel', 'capmodalstatus',
  'capth', 'caprth', 'capmatch', 'capmatchrect', 'capmatchstatus', 'capmatchtpl', 'capmatchsize', 'capgray', 'caproionly', 'capmatchcrop', 'capmodelabel', 'capupload', 'capuseupload',   'capuploadname', 'capblockopen', 'capblockrow', 'capblocktpl', 'capblockapply', 'capdimxy', 'capdimwh', 'capmodalname', 'capmodalsave']) {
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
  window: { confirm: function () { return true; }, addEventListener: function () {} },
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
    # USE_GRAY無しの旧保存物は定義既定(FALSE=カラー)が補われ、明示で出る。
    assert (
        'self.waitTemplate("my-pack/a.png", timeout=10, threshold=0.7, use_gray=False)'
        in code
    )
    assert (
        'self.isContainTemplate("my-pack/a.png", threshold=0.7, use_gray=False)' in code
    )
    assert "self.press(Button.A" in code
    assert blockly_validate.validate_generated_code(code) == []
    assert blockly_templates.validate_template_refs(code) == []
    assert blockly_templates.warn_template_refs(code) == []


def test_vision_blocks_expose_use_gray() -> None:
    """テンプレ4ブロックはUSE_GRAY切替を持ち、生成コードにuse_grayを出すこと。"""
    src = (BLOCKLY / "pokecon_blocks.js").read_text(encoding="utf-8")
    assert "USE_GRAY" in src
    assert "use_gray=" in src
    assert "visionGray" in src


PROBE_GRAY_JS = """\
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
// 実ブロック経由：保存物を読み込ませ、定義既定の適用まで含めて生成する。
function genFor(type, fields) {
  const ws = new sandbox.Blockly.Workspace();
  sandbox.Blockly.serialization.workspaces.load(
    { blocks: { languageVersion: 0, blocks: [{ type: type, fields: fields }] } }, ws);
  const blk = ws.getAllBlocks(false)[0];
  const out = gen.forBlock[type](blk, gen);
  const code = Array.isArray(out) ? out[0] : out;
  ws.dispose();
  return code;
}
function expectContains(code, sub, label) {
  if (code.indexOf(sub) === -1) {
    fail(label + ': ' + JSON.stringify(sub) + ' が無い: ' + code);
  }
}
const cases = [
  ['pokecon_vision_contains', 'self.isContainTemplate('],
  ['pokecon_vision_wait_appear', 'self.waitTemplate('],
  ['pokecon_vision_wait_gone', 'self.waitTemplateGone('],
  ['pokecon_vision_position', 'self.getTemplatePosition('],
];
for (const [type, head] of cases) {
  const base = { TEMPLATE: 'my-pack/a.png', THRESHOLD: 0.7, CROP: '' };
  if (type === 'pokecon_vision_wait_appear' || type === 'pokecon_vision_wait_gone') {
    base.TIMEOUT = 10;
  }
  const off = genFor(type, Object.assign({}, base, { USE_GRAY: 'FALSE' }));
  expectContains(off, head, type + ' FALSE');
  expectContains(off, ', use_gray=False', type + ' FALSE');
  const on = genFor(type, Object.assign({}, base, { USE_GRAY: 'TRUE' }));
  expectContains(on, ', use_gray=True', type + ' TRUE');
}
// 旧保存物相当：USE_GRAYを知らない偽ブロックは引数を省略する。
const legacy = gen.forBlock['pokecon_vision_contains']({
  getFieldValue: function (n) {
    if (n === 'TEMPLATE') { return 'my-pack/a.png'; }
    if (n === 'THRESHOLD') { return 0.7; }
    return null;
  },
}, gen);
const legacyCode = Array.isArray(legacy) ? legacy[0] : legacy;
if (legacyCode.indexOf('use_gray') !== -1) {
  fail('旧field欠損で use_gray が出た: ' + legacyCode);
}
console.log('GRAY-PROBE-OK');
"""


@NEEDS_NODE
def test_browser_vision_use_gray_codegen() -> None:
    proc = subprocess.run(
        ["node", "-e", PROBE_GRAY_JS, str(BLOCKLY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"probe失敗:\n{proc.stderr}\n{proc.stdout}"
    assert "GRAY-PROBE-OK" in proc.stdout


CAPTURE_PROBE_JS = """\
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const root = process.argv[1];
const src = fs.readFileSync(path.join(root, 'pokecon_capture.js'), 'utf8');
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: 'pokecon_capture.js' });
const fail = (msg) => {
  console.error('CAPTURE-PROBE-FAIL: ' + msg);
  process.exit(1);
};
const c = sandbox.PokeconCapture;
if (!c) { fail('PokeconCapture が無い'); }
let r = c.normalizeDrag01(0.75, 0.75, 0.25, 0.25);
if (r.x !== 0.25 || r.y !== 0.25 || r.width !== 0.5 || r.height !== 0.5) {
  fail('逆転ドラッグの正規化: ' + JSON.stringify(r));
}
r = c.normalizeDrag01(-0.5, -0.5, 1.5, 1.5);
if (r.x !== 0 || r.y !== 0 || r.width !== 1 || r.height !== 1) {
  fail('はみ出し丸め: ' + JSON.stringify(r));
}
if (c.isValidPixels({ x: 0, y: 0, width: 0.5, height: 0.5 }, 200, 100) !== true) {
  fail('正常矩形を通さない');
}
if (c.isValidPixels({ x: 0, y: 0, width: 0.01, height: 0.5 }, 200, 100) !== false) {
  fail('8px未満を通した');
}
if (c.isValidPixels({ x: 0, y: 0, width: 0.04, height: 0.08 }, 200, 100) !== true) {
  fail('8px境界を通さない');
}
r = c.moveRect01({ x: 0.25, y: 0.25, width: 0.5, height: 0.5 }, 0.25, 0);
if (r.x !== 0.5 || r.y !== 0.25 || r.width !== 0.5 || r.height !== 0.5) {
  fail('移動: ' + JSON.stringify(r));
}
r = c.moveRect01({ x: 0.25, y: 0.25, width: 0.5, height: 0.5 }, 1, 1);
if (r.x !== 0.5 || r.y !== 0.5 || r.width !== 0.5 || r.height !== 0.5) {
  fail('移動のはみ出し丸め: ' + JSON.stringify(r));
}
r = c.resizeRect01({ x: 0.25, y: 0.25, width: 0.5, height: 0.5 }, 'se', 0.25, 0.25);
if (r.x !== 0.25 || r.y !== 0.25 || r.width !== 0.75 || r.height !== 0.75) {
  fail('右下リサイズ: ' + JSON.stringify(r));
}
r = c.resizeRect01({ x: 0.25, y: 0.25, width: 0.5, height: 0.5 }, 'nw', -0.25, -0.25);
if (r.x !== 0 || r.y !== 0 || r.width !== 0.75 || r.height !== 0.75) {
  fail('左上リサイズ: ' + JSON.stringify(r));
}
r = c.resizeRect01({ x: 0.25, y: 0.25, width: 0.5, height: 0.5 }, 'e', 5, 0);
if (r.width !== 0.75) {
  fail('リサイズのはみ出し丸め: ' + JSON.stringify(r));
}
var px = c.rect01ToPx({ x: 0.25, y: 0.25, width: 0.5, height: 0.5 }, 200, 100);
if (px.x !== 50 || px.y !== 25 || px.width !== 100 || px.height !== 50) {
  fail('画素換算: ' + JSON.stringify(px));
}
r = c.pxToRect01(50, 25, 100, 50, 200, 100);
if (r.x !== 0.25 || r.y !== 0.25 || r.width !== 0.5 || r.height !== 0.5) {
  fail('画素からの復元: ' + JSON.stringify(r));
}
var base = { x: 0.25, y: 0.25, width: 0.5, height: 0.5 };
if (c.hitHandle(base, 0.25, 0.25, 0.05) !== 'nw') { fail('角判定nw'); }
if (c.hitHandle(base, 0.5, 0.25, 0.05) !== 'n') { fail('辺判定n'); }
if (c.hitHandle(base, 0.75, 0.75, 0.05) !== 'se') { fail('角判定se'); }
if (c.hitHandle(base, 0.75, 0.5, 0.05) !== 'e') { fail('辺判定e'); }
if (c.hitHandle(base, 0.5, 0.5, 0.05) !== 'inside') { fail('内側判定'); }
if (c.hitHandle(base, 0.1, 0.1, 0.05) !== null) { fail('外側判定'); }
if (c.hitHandle(null, 0.5, 0.5, 0.05) !== null) { fail('null矩形判定'); }
var cp = c.parseCropText('10,20,110,120');
if (!cp || cp.x1 !== 10 || cp.y1 !== 20 || cp.x2 !== 110 || cp.y2 !== 120) {
  fail('CROP解釈: ' + JSON.stringify(cp));
}
if (c.parseCropText('') !== null) { fail('空CROPは無効'); }
if (c.parseCropText('a,b,c,d') !== null) { fail('非数値CROPは無効'); }
if (c.parseCropText('110,20,10,120') !== null) { fail('逆転CROPは無効'); }
if (c.formatCrop(10, 20, 110, 120) !== '10,20,110,120') { fail('CROP整形'); }
console.log('CAPTURE-PROBE-OK');
"""


@NEEDS_NODE
def test_capture_rect_helpers() -> None:
    proc = subprocess.run(
        ["node", "-e", CAPTURE_PROBE_JS, str(BLOCKLY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"probe失敗:\n{proc.stderr}\n{proc.stdout}"
    assert "CAPTURE-PROBE-OK" in proc.stdout


MODAL_PROBE_JS = """\
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const root = process.argv[1];
const html = fs.readFileSync(path.join(root, 'editor.html'), 'utf8');
const m = html.match(/<script>([\\s\\S]*?)<\\/script>\\s*<\\/body>/);
if (!m) { console.error('MODAL-FAIL: inline script not found'); process.exit(1); }
const capSrc = fs.readFileSync(path.join(root, 'pokecon_capture.js'), 'utf8');
function makeEl() {
  return { value: '', textContent: '', style: {}, handlers: {},
    appendChild: function () {}, addEventListener: function (ev, fn) { this.handlers[ev] = fn; } };
}
const els = {};
for (const id of ['stem', 'save', 'status', 'files', 'open', 'del', 'tpllist', 'tplapply', 'ws',
  'capget', 'capwrap', 'capimg', 'caprect', 'capname', 'capsave', 'capstatus',
  'capmodal', 'capmodalclose', 'capmodalbox', 'capbigwrap', 'capbigimg', 'capbigrect',
  'caph-nw', 'caph-n', 'caph-ne', 'caph-e', 'caph-se', 'caph-s', 'caph-sw', 'caph-w',
  'capx', 'capy', 'capw', 'caph', 'caprx', 'capry', 'caprw', 'caprh', 'capretake', 'capok', 'capcancel', 'capmodalstatus',
  'capth', 'caprth', 'capmatch', 'capmatchrect', 'capmatchstatus', 'capmatchtpl', 'capmatchsize', 'capgray', 'caproionly', 'capmatchcrop', 'capmodelabel', 'capupload', 'capuseupload',   'capuploadname', 'capblockopen', 'capblockrow', 'capblocktpl', 'capblockapply', 'capdimxy', 'capdimwh', 'capmodalname', 'capmodalsave']) {
  els[id] = makeEl();
}
// 拡大画像の寸法スタブ（表示800x600・実画像1280x960）
els.capbigimg.offsetWidth = 800;
els.capbigimg.offsetHeight = 600;
els.capbigimg.naturalWidth = 1280;
els.capbigimg.naturalHeight = 960;
els.capbigimg.getBoundingClientRect = function () {
  return { left: 0, top: 0, width: 800, height: 600 };
};
const winHandlers = {};
const wsStub = { addChangeListener: function () {},
  getBlockById: function (id) { return id === vblk.id ? vblk : null; } };
let liveSelected = null;
const vblk = { id: 'vblk1', applied: {}, fields: { TEMPLATE: 'my-pack/a.png', CROP: '', THRESHOLD: '0.7' },
  getField: function (n) {
    if (n !== 'TEMPLATE' && n !== 'CROP' && n !== 'THRESHOLD') { return null; }
    return { getValue: function () { return vblk.fields[n]; } };
  },
  setFieldValue: function (v, f) { vblk.applied[f] = v; vblk.fields[f] = v; } };
const sandbox = { console, Promise, JSON, setTimeout, clearTimeout,
  URL: { createObjectURL: () => 'blob:fake', revokeObjectURL: () => {} },
  document: {
    getElementById: function (id) { return els[id]; },
    createElement: function () { return makeEl(); },
  },
  window: {
    confirm: function () { return true; },
    addEventListener: function (ev, fn) { winHandlers[ev] = fn; },
  },
  fetch: function (url) {
    if (url.indexOf('frame') >= 0) {
      return Promise.resolve({ headers: { get: () => 'image/png' }, blob: () => Promise.resolve({}) });
    }
    if (url === './template') {
      return Promise.resolve({ json: function () {
        return Promise.resolve({ ok: true, message: '保存しました: Template/blockly/t1.png', path: 'blockly/t1.png' });
      } });
    }
    const body = url.indexOf('templates') >= 0 ? { templates: ['my-pack/a.png'] } : { stems: [] };
    return Promise.resolve({ json: function () { return Promise.resolve(body); } });
  },
  Blockly: {
    inject: function () { return wsStub; },
    Events: { SELECTED: 'selected' },
    getSelected: function () { return liveSelected; },
  },
};
vm.createContext(sandbox);
vm.runInContext(capSrc, sandbox, { filename: 'pokecon_capture.js' });
try {
  vm.runInContext(m[1], sandbox, { filename: 'editor-inline.js' });
} catch (e) {
  console.error('MODAL-FAIL: inline script threw: ' + e.constructor.name + ': ' + e.message);
  process.exit(1);
}
const fail = (msg) => { console.error('MODAL-FAIL: ' + msg); process.exit(1); };
function down(x, y) {
  els.capbigwrap.handlers.mousedown({ clientX: x, clientY: y, preventDefault: function () {} });
}
function move(x, y) { winHandlers.mousemove({ clientX: x, clientY: y }); }
// 回帰: mousedownは枠・グリップからのバブルも受ける位置（ラッパ）で受けること。
if (typeof els.capbigwrap.handlers.mousedown !== 'function') {
  fail('capbigwrapにmousedownが無い（枠上クリックが届かない）');
}
// 新規ドラッグ (80,60)→(240,180): 正規化(0.1,0.1,0.2,0.2) → 画素(128,96,256,192)
down(80, 60);
move(240, 180);
winHandlers.mouseup();
if (els.capx.value !== 128 || els.capy.value !== 96 ||
    els.capw.value !== 256 || els.caph.value !== 192) {
  fail('新規ドラッグが反映されない: ' + [els.capx.value, els.capy.value, els.capw.value, els.caph.value]);
}
// 移動: 枠内(160,120)→(240,200)でxが128→256にずれること
down(160, 120);
move(240, 200);
winHandlers.mouseup();
if (els.capx.value !== 256) { fail('枠内ドラッグで移動しない: capx=' + els.capx.value); }
// スライダー操作: xスライダーを384に動かすと数値欄と枠が連動すること
els.caprx.value = '384';
els.caprx.handlers.input();
if (els.capx.value !== 384) { fail('スライダーが反映されない: capx=' + els.capx.value); }
// ホイール相当: 数値欄のinputイベントで枠とスライダーが連動すること
els.capx.value = '400';
els.capx.handlers.input();
if (els.caprx.value !== 400) { fail('数値欄のinputが枠に届かない: caprx=' + els.caprx.value); }
// ホバー: 枠内(320,200)ではmoveカーソル、東辺(410,200)ではew-resizeになること
winHandlers.mousemove({ clientX: 320, clientY: 200 });
if (els.capbigimg.style.cursor !== 'move') { fail('枠内カーソル: ' + els.capbigimg.style.cursor); }
winHandlers.mousemove({ clientX: 410, clientY: 200 });
if (els.capbigimg.style.cursor !== 'ew-resize') { fail('辺カーソル: ' + els.capbigimg.style.cursor); }
// ブロック用起動: 選択中visionブロックで開き、テンプレ初期値・CROP初期表示されること
liveSelected = vblk;
vblk.fields.CROP = '128,96,384,288';
vblk.fields.THRESHOLD = '0.55';
els.capbigimg.src = 'blob:fake';
// 実markupは value="0.7" 既定。スタブ既定''のままだとmodal内自動実行が
// しきい値検証で止まり配線検証にならないため先に寄せる（test-only）。
els.capth.value = '0.7';
sandbox.Blockly.PokeconOpenBlockModal('nope');
if (els.capmodal.style.display === 'block') { fail('不明ブロックで開いた'); }
if (els.status.textContent.indexOf('選んでください') < 0) { fail('案内が出ない'); }
sandbox.Blockly.PokeconOpenBlockModal('vblk1');
if (els.capmodal.style.display !== 'block') { fail('ブロック用に開かない'); }
if (els.capx.value !== 128 || els.capy.value !== 96 ||
    els.capw.value !== 256 || els.caph.value !== 192) {
  fail('CROP初期表示されない: ' + [els.capx.value, els.capy.value, els.capw.value, els.caph.value]);
}
if (els.capdimxy.textContent !== '128,96') { fail('寸法表示xy: ' + els.capdimxy.textContent); }
if (els.capdimwh.textContent !== '256×192') { fail('寸法表示wh: ' + els.capdimwh.textContent); }
if (els.capth.value !== '0.55' || els.caprth.value !== '0.55') {
  fail('しきい値初期値: ' + els.capth.value + '/' + els.caprth.value);
}
setTimeout(function () {
  try {
    if (els.capblocktpl.value !== 'my-pack/a.png') { fail('テンプレ初期値: ' + els.capblocktpl.value); }
    // ライブ反映: 変更・ドラッグ確定でクリックなしに書き戻ること
    // （開始点は既存枠の角を避けた空き領域にする。角上は仕様どおりリサイズ掴みになる）
    vblk.applied = {};
    els.capblocktpl.handlers.change();
    down(400, 300); move(560, 420); winHandlers.mouseup();
    if (vblk.applied.TEMPLATE !== 'my-pack/a.png') { fail('TEMPLATE即時反映'); }
    if (vblk.applied.CROP !== '640,480,896,672') { fail('CROP即時反映: ' + vblk.applied.CROP); }
    // ハンドル掴みで対応数値が強調されること（東辺(560,360)→幅のみ）
    down(560, 360);
    if (els.capw.style.background !== '#fff3cd') { fail('幅強調なし'); }
    if (els.capx.style.background === '#fff3cd') { fail('xが誤強調'); }
    winHandlers.mouseup();
    // 照合fetchの解決を待ってから次へ（連番ガードの取捨て対象にしないため）。
    setTimeout(function () {
    try {
    if (els.capmodal.style.display !== 'block') { fail('途中で閉じた'); }
    els.capmatchtpl.value = 'pack/part.png';
    els.capth.value = '0.7';
    els.capth.handlers.input();
    setTimeout(function () {
      try {
        if (els.capmatchstatus.textContent !== '照合できません') { fail('自動実行されない: ' + els.capmatchstatus.textContent); }
        if (els.capmatchrect.style.display !== 'none') { fail('失敗時に重ね描きが残る'); }
        // 切出し用に戻す（実利用どおり閉じる操作でmodeをリセットする）。
        els.capcancel.handlers.click();
        if (els.capmodal.style.display !== 'none') { fail('閉じない'); }
        els.tpllist.value = 'my-pack/a.png';
        els.capmatchtpl.value = '';
        els.capmatchtpl.textContent = '';
        els.capmatchstatus.textContent = '';
        els.capget.handlers.click();
        setTimeout(function () {
          try {
            if (!els.capmatchstatus.textContent) { fail('初回自動実行されない: 空'); }
            if (els.capmatchstatus.textContent === '画像を選んでください') { fail('初回が空selectで止まったまま: ' + els.capmatchstatus.textContent); }
            // ブロック用自動取得: 画像なしで開くと取得後にブロック用で開くこと
            els.capmodal.style.display = 'none';
            els.capbigimg.src = '';
            sandbox.Blockly.PokeconOpenBlockModal('vblk1');
            setTimeout(function () {
              try {
                if (els.capmodal.style.display !== 'block') { fail('自動取得で開かない'); }
                if (els.capblockrow.style.display !== 'flex') { fail('ブロック行が出ない'); }
                // しきい値の書き戻し: 他タイマ枯渇後・ブロック用表示中に単独実行する
                els.capth.value = '0.8';
                els.capth.handlers.input();
                setTimeout(function () {
                  try {
                    if (vblk.applied.THRESHOLD !== '0.8') { fail('しきい値即時反映: ' + vblk.applied.THRESHOLD); }
                    // modal内保存: 名前＋枠で保存でき、開いたままになること
                    // （他タイマ枯渇後のため状態の上書き競合なし）
                    els.capmodalname.value = 't1';
                    els.capmodalsave.handlers.click();
                    setTimeout(function () {
                      try {
                        if (els.capmodalstatus.textContent.indexOf('保存しました') !== 0) { fail('modal保存されない: ' + els.capmodalstatus.textContent); }
                        if (els.capmodal.style.display !== 'block') { fail('保存で閉じた'); }
                        console.log('MODAL-PROBE-OK');
                      } catch (e) { fail('例外: ' + (e && e.message)); }
                    }, 300);
                  } catch (e) { fail('例外: ' + (e && e.message)); }
                }, 300);
              } catch (e) { fail('例外: ' + (e && e.message)); }
            }, 800);
          } catch (e) { fail('例外: ' + (e && e.message)); }
        }, 800);
      } catch (e) { fail('例外: ' + (e && e.message)); }
    }, 600);
    } catch (e) { fail('例外: ' + (e && e.message)); }
    }, 50);
  } catch (e) { fail('例外: ' + (e && e.message)); }
}, 50);
"""


@NEEDS_NODE
def test_modal_drag_routing() -> None:
    proc = subprocess.run(
        ["node", "-e", MODAL_PROBE_JS, str(BLOCKLY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"probe失敗:\n{proc.stderr}\n{proc.stdout}"
    assert "MODAL-PROBE-OK" in proc.stdout


def test_match_section_markup_exists() -> None:
    """照合区画の要素がeditor.htmlにあること。"""
    html = (BLOCKLY / "editor.html").read_text(encoding="utf-8")
    for token in [
        'id="capmatchtpl"',
        'id="capmatchsize"',
        'id="capgray"',
        'id="caproionly"',
        'id="capmatchrect"',
        'id="capmatchstatus"',
        'id="capmatchcrop"',
        'id="capmodelabel"',
        'id="capupload"',
        'id="capuploadname"',
        'id="capdimxy"',
        'id="capdimwh"',
        'id="capmodalname"',
        'id="capmodalsave"',
    ]:
        assert token in html
    assert 'id="capmatch"' not in html
    assert 'id="capuseupload"' not in html


PREVIEW_PROBE_JS = """\
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
vm.runInContext(read('pokecon_blocks.js'), sandbox, { filename: 'pokecon_blocks.js' });
const fail = (msg) => { console.error('PREVIEW-FAIL: ' + msg); process.exit(1); };
const B = sandbox.Blockly;
if (!B || typeof B.Python !== 'object') { fail('Blockly.Python が無い'); }
const ws = new B.Workspace();
B.PokeconTemplates = ['my-pack/a.png', 'x-icon.png', 'blockly/mark.png', 'a.png', 'b/c.png'];
const state = { blocks: { languageVersion: 0, blocks: [
  { type: 'pokecon_vision_contains',
    fields: { TEMPLATE: 'x-icon.png', THRESHOLD: 0.7, CROP: '' } },
] } };
B.serialization.workspaces.load(state, ws);
const blk = ws.getAllBlocks(false)[0];
if (blk.getFieldValue('TEMPLATE') !== 'x-icon.png') {
  fail('読込で保存値が保たれない: ' + blk.getFieldValue('TEMPLATE'));
}
if (typeof blk.getField !== 'function' || !blk.getField('PREVIEW')) {
  fail('PREVIEW欄が無い');
}
blk.setFieldValue('blockly/mark.png', 'TEMPLATE');
const shown = blk.getFieldValue('PREVIEW');
if (shown !== './template_image?name=' + encodeURIComponent('blockly/mark.png')) {
  fail('TEMPLATE変更でpreviewが更新されない: ' + shown);
}
blk.setFieldValue('', 'TEMPLATE');
if (blk.getFieldValue('PREVIEW').indexOf('data:image/png;base64,') !== 0) {
  fail('空欄でplaceholderに戻らない: ' + blk.getFieldValue('PREVIEW'));
}
const capField = blk.getField('CAPOPEN');
if (!capField) { fail('CAPOPEN欄が無い'); }
let opened = null;
B.PokeconOpenBlockModal = function (id) { opened = id; };
capField.showEditor_();
if (opened !== blk.id) { fail('ボタンクリックでmodalが開かない: ' + opened); }
blk.setFieldValue('blockly/mark.png', 'TEMPLATE');
B.PokeconTemplates = ['a.png', 'b/c.png'];
const opts = blk.getField('TEMPLATE').getOptions();
function hasOpt(v) { return opts.some(function (o) { return o[1] === v; }); }
if (!hasOpt('a.png') || !hasOpt('b/c.png')) { fail('候補に出ない: ' + JSON.stringify(opts)); }
if (!hasOpt('')) { fail('空欄選択肢が無い'); }
B.PokeconTemplates = ['only-one.png'];
const opts2 = blk.getField('TEMPLATE').getOptions();
if (opts2[0][0] !== 'blockly/mark.png' || opts2[0][1] !== 'blockly/mark.png') {
  fail('現値が先頭に無い: ' + JSON.stringify(opts2[0]));
}
const code = B.Python.workspaceToCode(ws);
ws.dispose();
if (code.indexOf('template_image') !== -1 || code.indexOf('PREVIEW') !== -1) {
  fail('生成コードにpreviewが混入した');
}
if (code.indexOf('self.isContainTemplate("blockly/mark.png"') === -1) {
  fail('生成コードのTEMPLATEが変わった');
}
console.log('PREVIEW-PROBE-OK');
"""


@NEEDS_NODE
def test_preview_follows_template_field() -> None:
    proc = subprocess.run(
        ["node", "-e", PREVIEW_PROBE_JS, str(BLOCKLY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"probe失敗:\n{proc.stderr}\n{proc.stdout}"
    assert "PREVIEW-PROBE-OK" in proc.stdout


PROBE_COLOR_JS = """\
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
  console.error('COLOR-PROBE-FAIL: ' + msg);
  process.exit(1);
};
try {
  vm.runInContext(read('pokecon_blocks.js'), sandbox, { filename: 'pokecon_blocks.js' });
} catch (e) {
  fail('pokecon_blocks.js が投げた: ' + e.constructor.name + ': ' + e.message);
}
const gen = sandbox.Blockly.Python;
if (!gen.forBlock || typeof gen.forBlock['pokecon_vision_color'] !== 'function') {
  fail('pokecon_vision_color が登録されていない');
}
function genFor(type, fields) {
  const ws = new sandbox.Blockly.Workspace();
  sandbox.Blockly.serialization.workspaces.load(
    { blocks: { languageVersion: 0, blocks: [{ type: type, fields: fields }] } }, ws);
  const blk = ws.getAllBlocks(false)[0];
  const out = gen.forBlock[type](blk, gen);
  const code = Array.isArray(out) ? out[0] : out;
  ws.dispose();
  return code;
}
function expectContains(code, sub, label) {
  if (code.indexOf(sub) === -1) {
    fail(label + ': ' + JSON.stringify(sub) + ' が無い: ' + code);
  }
}
const base = { H1: 0, S1: 0, V1: 0, H2: 179, S2: 255, V2: 255, RATIO: 0.6 };
const withCrop = genFor('pokecon_vision_color', Object.assign({}, base, { CROP: '10,20,110,120' }));
expectContains(withCrop, 'self.isSimilarColor([10,20,110,120], [0,0,0], [179,255,255], ratio=0.6)', 'CROPあり');
const empty = genFor('pokecon_vision_color', Object.assign({}, base, { CROP: '' }));
expectContains(empty, 'self.isSimilarColor([], [0,0,0], [179,255,255], ratio=0.6)', 'CROP空');
console.log('COLOR-PROBE-OK');
"""


@NEEDS_NODE
def test_browser_color_crop_codegen() -> None:
    proc = subprocess.run(
        ["node", "-e", PROBE_COLOR_JS, str(BLOCKLY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"probe失敗:\n{proc.stderr}\n{proc.stdout}"
    assert "COLOR-PROBE-OK" in proc.stdout


def test_color_block_def_has_crop() -> None:
    """color定義はCROP欄を持ち、generatorはisSimilarColorを出すこと（node無し）。"""
    src = (BLOCKLY / "pokecon_blocks.js").read_text(encoding="utf-8")
    start = src.index('type: "pokecon_vision_color"')
    color_def = src[start : src.index("]);", start)]
    assert '"CROP"' in color_def
    gen_start = src.index('forBlock["pokecon_vision_color"]')
    color_gen = src[gen_start:]
    assert "isSimilarColor" in color_gen
    assert '"CROP"' in color_gen


def test_subroutine_and_comment_blocks_registered() -> None:
    """サブルーチン定義・呼出・コメントのブロックと生成器があること（node無し）。"""
    src = (BLOCKLY / "pokecon_blocks.js").read_text(encoding="utf-8")
    for block_type in [
        "pokecon_sub_def",
        "pokecon_sub_call",
        "pokecon_comment",
    ]:
        assert f'type: "{block_type}"' in src, f"{block_type} 定義が無い"
        assert f'forBlock["{block_type}"]' in src, f"{block_type} 生成器が無い"
    # 呼出は self.xxx(…)、コメントは # …、定義は def … を出すこと。
    assert "self." in src
    assert '"# "' in src or "'# '" in src or '"#"' in src


def test_toolbox_lists_subroutine_and_comment() -> None:
    """toolboxに新ブロックが並び、標準に変数・論理・計算があること."""
    html = (BLOCKLY / "editor.html").read_text(encoding="utf-8")
    for block_type in [
        "pokecon_sub_def",
        "pokecon_sub_call",
        "pokecon_comment",
    ]:
        assert block_type in html, f"toolboxに {block_type} が無い"
    for std in ["logic_compare", "math_arithmetic", "text"]:
        assert std in html, f"toolboxに {std} が無い"


def test_stick_block_registered() -> None:
    """スティックブロックと生成器があること（node無し）。"""
    src = (BLOCKLY / "pokecon_blocks.js").read_text(encoding="utf-8")
    assert 'type: "pokecon_stick"' in src
    assert 'forBlock["pokecon_stick"]' in src
    assert "Direction(" in src
    assert "field_stickpad" in src
    assert "PokeconStick" in src
    assert "parsePadValue" in src


def test_toolbox_lists_stick_and_single_program() -> None:
    """toolboxにスティックがあり、保存時にプログラム1個制限があること."""
    html = (BLOCKLY / "editor.html").read_text(encoding="utf-8")
    assert "pokecon_stick" in html
    assert "stickpad" in html.lower() or "stick-pad" in html or "stickPad" in html
    assert "1個まで" in html
    assert "'PAD'" in html or '"PAD"' in html


PROBE_STICK_JS = """\
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
  console.error('STICK-PROBE-FAIL: ' + msg);
  process.exit(1);
};
try {
  vm.runInContext(read('pokecon_blocks.js'), sandbox, { filename: 'pokecon_blocks.js' });
} catch (e) {
  fail('pokecon_blocks.js が投げた: ' + e.constructor.name + ': ' + e.message);
}
const gen = sandbox.Blockly.Python;
if (!gen.forBlock || typeof gen.forBlock['pokecon_stick'] !== 'function') {
  fail('pokecon_stick が登録されていない');
}
const state = {
  blocks: {
    languageVersion: 0,
    blocks: [
      {
        type: 'pokecon_program',
        fields: { NAME: 'StickTest' },
        inputs: {
          DO: {
            block: {
              type: 'pokecon_stick',
              fields: { STICK: 'LEFT', PAD: '90,100', DURATION: 0.5, WAIT: 0.2 },
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
def test_browser_stick_codegen() -> None:
    from core import blockly_validate

    proc = subprocess.run(
        ["node", "-e", PROBE_STICK_JS, str(BLOCKLY)],
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
    assert "Direction(Stick.LEFT, 90, magnification=1)" in code
    assert "duration=0.5" in code
    assert "wait=0.2" in code
    assert "from Commands.Keys import Button, Direction, Stick" in code
    assert blockly_validate.validate_generated_code(code) == []


PROBE_STICKPAD_JS = """\
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
  console.error('STICKPAD-PROBE-FAIL: ' + msg);
  process.exit(1);
};
try {
  vm.runInContext(read('pokecon_blocks.js'), sandbox, { filename: 'pokecon_blocks.js' });
} catch (e) {
  fail('pokecon_blocks.js が投げた: ' + e.constructor.name + ': ' + e.message);
}
const B = sandbox.Blockly;
if (!B.PokeconStick) { fail('Blockly.PokeconStick が無い'); }
const S = B.PokeconStick;
function eq(a, b, label) {
  if (a !== b) { fail(label + ': ' + JSON.stringify(a) + ' !== ' + JSON.stringify(b)); }
}
let p = S.parsePadValue('90,100');
if (!p) { fail('基本形を読めない'); }
eq(p.angle, 90, 'angle');
eq(p.mag, 100, 'mag');
p = S.parsePadValue(' 0,0 ');
if (!p) { fail('0,0 を読めない'); }
eq(p.angle, 0, 'angle0');
eq(p.mag, 0, 'mag0');
if (S.parsePadValue('') !== null) { fail('空文字はnull'); }
if (S.parsePadValue(null) !== null) { fail('nullはnull'); }
if (S.parsePadValue('a,b') !== null) { fail('非数値はnull'); }
if (S.parsePadValue('90') !== null) { fail('要素不足はnull'); }
p = S.parsePadValue('370,150');
if (!p) { fail('範囲外を読めない'); }
eq(p.angle, 10, 'angle正規化');
eq(p.mag, 100, 'mag丸め');
eq(S.formatPadValue(90, 100), '90,100', 'format');
const xy = S.angleMagToXY(90, 100, 54);
if (Math.abs(xy.x) > 1e-9 || xy.y !== -54) { fail('XY換算: ' + JSON.stringify(xy)); }
const back = S.xyToAngleMag(0, -54, 54);
eq(back.angle, 90, '逆換算angle');
eq(back.mag, 100, '逆換算mag');
const center = S.xyToAngleMag(0, 0, 54);
eq(center.mag, 0, '中心mag');
if (typeof B.StickPadField !== 'function') { fail('StickPadField が無い'); }
if (typeof B.StickPadField.fromJson !== 'function') { fail('fromJson が無い'); }
// 旧保存物相当：PAD無し・ANGLE/MAGありの偽ブロックは従来値で出すこと。
const legacy = B.Python.forBlock['pokecon_stick']({
  getFieldValue: function (n) {
    if (n === 'STICK') { return 'RIGHT'; }
    if (n === 'ANGLE') { return 180; }
    if (n === 'MAG') { return 50; }
    if (n === 'DURATION') { return 0.5; }
    if (n === 'WAIT') { return 0.2; }
    return null;
  },
}, B.Python);
if (legacy.indexOf('Direction(Stick.RIGHT, 180, magnification=0.5)') === -1) {
  fail('旧欄フォールバック: ' + legacy);
}
console.log('STICKPAD-PROBE-OK');
"""


@NEEDS_NODE
def test_browser_stickpad_logic_and_legacy() -> None:
    proc = subprocess.run(
        ["node", "-e", PROBE_STICKPAD_JS, str(BLOCKLY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"probe失敗:\n{proc.stderr}\n{proc.stdout}"
    assert "STICKPAD-PROBE-OK" in proc.stdout


PROBE_SUB_JS = """\
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
  console.error('SUB-PROBE-FAIL: ' + msg);
  process.exit(1);
};
try {
  vm.runInContext(read('pokecon_blocks.js'), sandbox, { filename: 'pokecon_blocks.js' });
} catch (e) {
  fail('pokecon_blocks.js が投げた: ' + e.constructor.name + ': ' + e.message);
}
const gen = sandbox.Blockly.Python;
for (const t of ['pokecon_sub_def', 'pokecon_sub_call', 'pokecon_comment']) {
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
        fields: { NAME: 'SubTest' },
        inputs: {
          DO: {
            block: {
              type: 'pokecon_comment',
              fields: { TEXT: '開始メモ' },
              next: {
                block: {
                  type: 'pokecon_sub_call',
                  fields: { NAME: 'my_sub' },
                  inputs: {
                    ARG0: { block: { type: 'math_number', fields: { NUM: 3 } } },
                  },
                },
              },
            },
          },
        },
      },
      {
        type: 'pokecon_sub_def',
        fields: { NAME: 'my_sub', ARGS: 'n' },
        inputs: {
          DO: {
            block: {
              type: 'pokecon_press',
              fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 },
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
def test_browser_subroutine_codegen() -> None:
    from core import blockly_validate

    proc = subprocess.run(
        ["node", "-e", PROBE_SUB_JS, str(BLOCKLY)],
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
    assert "def my_sub(self, n)" in code
    assert "self.my_sub(3)" in code
    assert "# 開始メモ" in code
    assert "self.press(Button.A" in code
    assert blockly_validate.validate_generated_code(code) == []


PROBE_SUB_VISION_JS = """\
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
  console.error('SUB-VISION-PROBE-FAIL: ' + msg);
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
        fields: { NAME: 'SubVision' },
        inputs: {
          DO: {
            block: {
              type: 'pokecon_sub_call',
              fields: { NAME: 'check' },
            },
          },
        },
      },
      {
        type: 'pokecon_sub_def',
        fields: { NAME: 'check', ARGS: '' },
        inputs: {
          DO: {
            block: {
              type: 'pokecon_vision_wait_appear',
              fields: { TEMPLATE: 'my-pack/a.png', TIMEOUT: 10, THRESHOLD: 0.7, CROP: '' },
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
def test_browser_subroutine_vision_switches_base() -> None:
    from core import blockly_validate

    proc = subprocess.run(
        ["node", "-e", PROBE_SUB_VISION_JS, str(BLOCKLY)],
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
    assert "def check(self)" in code
    assert "self.check()" in code
    assert blockly_validate.validate_generated_code(code) == []
