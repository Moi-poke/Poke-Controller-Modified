"""Blockly資産をnodeのvmで読み込む検証補助（テスト本体ではない）。

ブラウザの `<script>` 読みと同じ順で資産を流し込み、本文の JS を
実行して最後に `RESULT=<JSON>` で返させる。描画（inject）はしない。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
BLOCKLY = ROOT / "SerialController" / "assets" / "blockly"

NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node が無いため飛ばす"
)

#: editor.html と同じ読込順（pokecon_editor.js は無くても先へ進める）。
_ASSETS = (
    "blockly_compressed.js",
    "blocks_compressed.js",
    "python_compressed.js",
    "msg/ja.js",
    "pokecon_blocks.js",
    "pokecon_capture.js",
    "pokecon_editor.js",
)

_PRELUDE = """\
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const root = process.argv[1];
const sandbox = { console, setTimeout, clearTimeout, Promise, JSON };
vm.createContext(sandbox);
for (const f of %s) {
  const p = path.join(root, f);
  if (!fs.existsSync(p)) { continue; }
  vm.runInContext(fs.readFileSync(p, 'utf8'), sandbox, { filename: f });
}
const Blockly = sandbox.Blockly;
const E = Blockly.PokeconEditor || null;
function done(v) { console.log('RESULT=' + JSON.stringify(v)); }
"""


#: editor.html の inline script を、本物の Blockly（描画なし）と最小DOMで
#: 動かす前置き。要素は id ごとに自動で作り、fetch・confirm・localStorage は
#: 記録つきの代役にする。本文からは `els`・`calls`・`confirms`・`store`・
#: `ws`・`flush()`・`routes`・`winHandlers` を使える。
_EDITOR_PRELUDE = """\
const html = fs.readFileSync(path.join(root, 'editor.html'), 'utf8');
const inline = html.match(/<script>([\\s\\S]*?)<\\/script>\\s*<\\/body>/)[1];
function makeEl(id) {
  return { id, value: '', textContent: '', className: '', style: {}, handlers: {},
    attrs: {}, open: false, disabled: false, options: [],
    appendChild(c) { this.options.push(c); }, focus() {},
    setAttribute(k, v) { this.attrs[k] = v; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 400, height: 300 }; },
    addEventListener(ev, fn) { this.handlers[ev] = fn; } };
}
const els = {};
const doc = {
  getElementById(id) { if (!els[id]) { els[id] = makeEl(id); } return els[id]; },
  createElement() { return makeEl(''); },
  title: '',
};
const store = {};
const confirms = [];
let confirmAnswer = true;
const calls = [];
const routes = { list: { stems: [] }, templates: { templates: [] },
  save: { ok: true, message: '保存しました' } };
const winHandlers = {};
const sandboxWin = {
  confirm(msg) { confirms.push(msg); return confirmAnswer; },
  addEventListener(ev, fn) { winHandlers[ev] = fn; }, innerWidth: 1280,
  localStorage: { getItem: (k) => (k in store ? store[k] : null),
                  setItem: (k, v) => { store[k] = String(v); } },
};
sandbox.document = doc;
sandbox.window = sandboxWin;
sandbox.fetch = function (url, opts) {
  calls.push({ url, body: opts && opts.body ? JSON.parse(opts.body) : null });
  const key = String(url).replace('./', '').split('?')[0];
  const body = routes[key] || { ok: false, message: 'no route' };
  return Promise.resolve({ json: () => Promise.resolve(body),
    headers: { get: () => 'application/json' } });
};
const ws = new Blockly.Workspace();
ws.scrollCenter = () => {}; ws.zoomToFit = () => {}; ws.cleanUp = () => {};
Blockly.inject = () => ws;
function flush(ms) { return new Promise((r) => setTimeout(r, ms || 1000)); }
function boot() { vm.runInContext(inline, sandbox, { filename: 'editor-inline.js' }); }
"""


def run_editor(body: str) -> dict[str, Any]:
    """editor.html の inline script を起動できる文脈で body を実行する。

    body では起動前の準備（routes・store の設定）をしてから `boot()` を呼ぶ。
    """
    return run_blockly(_EDITOR_PRELUDE + body)


def run_blockly(body: str) -> dict[str, Any]:
    """資産を読み込んだ文脈で body を実行し、done() に渡された値を返す。"""
    script = (
        (_PRELUDE % json.dumps(list(_ASSETS))) + "(async () => {\n" + body + "\n})()"
    )
    script += ".catch((e) => { console.error('PROBE-THREW: ' + e.stack); process.exit(1); });\n"
    proc = subprocess.run(
        ["node", "-e", script, str(BLOCKLY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"probe失敗:\n{proc.stderr}\n{proc.stdout}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT=")]
    assert lines, f"結果が出ていない:\n{proc.stdout}\n{proc.stderr}"
    value: dict[str, Any] = json.loads(lines[-1][len("RESULT=") :])
    return value
