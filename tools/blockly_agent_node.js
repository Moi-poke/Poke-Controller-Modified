// PokeCon Blockly のヘッドレス入り口（node 用）。
// ブラウザと同じ vendored 資産を vm で読み、カタログ取得と workspace→Python
// 生成だけを行う。描画（inject）はしない。stdout へ JSON を1個だけ出し、
// 期待される検証エラーでも exit 0 にする（node 自体の異常だけ非ゼロ）。
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ALLOWED_STANDARD = [
  "controls_repeat",
  "controls_repeat_ext",
  "controls_whileUntil",
  "controls_for",
  "controls_if",
  "controls_flow_statements",
  "logic_compare",
  "logic_operation",
  "logic_negate",
  "logic_boolean",
  "math_number",
  "math_arithmetic",
  "math_modulo",
  "math_change",
  "math_random_int",
  "math_random_float",
  "text",
  "text_join",
  "variables_get",
  "variables_set",
];

//: ブラウザ（editor.html）と同じ読込順。全部必須。
const ASSETS = [
  "blockly_compressed.js",
  "blocks_compressed.js",
  "python_compressed.js",
  "msg/ja.js",
  "pokecon_blocks.js",
  "pokecon_capture.js",
];

// ---- 最小 DOM スタブ -------------------------------------------------------
// Blockly は shadow の直列化に document と XMLSerializer を使う。描画用の
// 完全な DOM は要らないが、要素の生成・属性・子の追加と直列化は要る。

class TextNode {
  constructor(text) {
    this.nodeType = 3;
    this.nodeName = "#text";
    this.textContent = String(text);
    this.childNodes = [];
    this.parentNode = null;
  }
}

class StubElement {
  constructor(namespaceURI, name) {
    this.nodeType = 1;
    this.namespaceURI = namespaceURI;
    this.nodeName = name;
    this.tagName = name;
    this.attributes = Object.create(null);
    this.childNodes = [];
    this.parentNode = null;
  }
  setAttribute(key, value) {
    this.attributes[key] = String(value);
  }
  getAttribute(key) {
    return key in this.attributes ? this.attributes[key] : null;
  }
  hasAttribute(key) {
    return key in this.attributes;
  }
  appendChild(child) {
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  removeChild(child) {
    const index = this.childNodes.indexOf(child);
    if (index >= 0) {
      this.childNodes.splice(index, 1);
    }
    return child;
  }
  get firstChild() {
    return this.childNodes[0] || null;
  }
  get textContent() {
    return this.childNodes.map((child) => child.textContent).join("");
  }
  set textContent(value) {
    this.childNodes = [];
    if (value !== "" && value != null) {
      this.appendChild(new TextNode(value));
    }
  }
  getElementsByTagName() {
    return [];
  }
}

function escapeXml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

class StubXmlSerializer {
  serializeToString(node) {
    if (node.nodeType === 3) {
      return escapeXml(node.textContent);
    }
    let out = "<" + node.nodeName;
    for (const [key, value] of Object.entries(node.attributes)) {
      out += " " + key + '="' + escapeXml(value) + '"';
    }
    if (!node.childNodes.length) {
      return out + "/>";
    }
    out += ">";
    for (const child of node.childNodes) {
      out += this.serializeToString(child);
    }
    return out + "</" + node.nodeName + ">";
  }
}

const stubDocument = {
  createElementNS: (namespaceURI, name) =>
    new StubElement(namespaceURI, name),
  createTextNode: (text) => new TextNode(text),
  createElement: (name) => new StubElement(null, name),
  getElementById: () => null,
  querySelector: () => null,
};

// ---- 出力 -----------------------------------------------------------------

function emit(payload) {
  process.stdout.write(JSON.stringify(payload) + "\n");
}

function failure(errors, warnings) {
  emit({ ok: false, errors: errors, warnings: warnings || [] });
}

function errorText(err) {
  if (err && typeof err.message === "string") {
    return err.message;
  }
  return String(err);
}

function readRequest(requestPath) {
  const raw = fs.readFileSync(requestPath, "utf8");
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("request は JSON オブジェクトで書いてください");
  }
  return parsed;
}

// ---- 資産読込 -------------------------------------------------------------

function loadBlockly(assetsDir, warnings) {
  const missing = ASSETS.filter(
    (name) => !fs.existsSync(path.join(assetsDir, name)),
  );
  if (missing.length) {
    throw new Error("Blockly の資産がありません: " + missing.join(", "));
  }
  const sandboxConsole = {
    log: (...args) => warnings.push("log: " + args.map(String).join(" ")),
    info: (...args) => warnings.push("info: " + args.map(String).join(" ")),
    warn: (...args) => warnings.push("warn: " + args.map(String).join(" ")),
    error: (...args) => warnings.push("error: " + args.map(String).join(" ")),
  };
  const sandbox = {
    console: sandboxConsole,
    document: stubDocument,
    XMLSerializer: StubXmlSerializer,
    setTimeout,
    clearTimeout,
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
  };
  vm.createContext(sandbox);
  for (const name of ASSETS) {
    const source = fs.readFileSync(path.join(assetsDir, name), "utf8");
    vm.runInContext(source, sandbox, { filename: name });
  }
  if (!sandbox.Blockly) {
    throw new Error("Blockly を読み込めませんでした（assets を確認してください）");
  }
  return sandbox.Blockly;
}

// ---- カタログ -------------------------------------------------------------

function fieldKind(Blockly, field, name) {
  if (name === "VAR") {
    return "variable";
  }
  if (name === "TEMPLATE") {
    return "template";
  }
  if (name === "CROP") {
    return "crop";
  }
  if (name === "BUTTON" || name === "TARGET") {
    return "controller";
  }
  if (name === "PAD") {
    return "stickpad";
  }
  if (typeof Blockly.FieldCheckbox === "function" && field instanceof Blockly.FieldCheckbox) {
    return "checkbox";
  }
  if (typeof Blockly.FieldNumber === "function" && field instanceof Blockly.FieldNumber) {
    return "number";
  }
  return "text";
}

function fieldOptions(field) {
  if (typeof field.getOptions !== "function") {
    return null;
  }
  let raw;
  try {
    raw = field.getOptions();
  } catch (err) {
    return null;
  }
  if (!Array.isArray(raw) || !raw.length) {
    return null;
  }
  const options = [];
  for (const item of raw) {
    if (Array.isArray(item)) {
      const value = item.length > 1 ? item[1] : item[0];
      options.push([String(item[0]), String(value)]);
    } else if (item && typeof item === "object") {
      options.push([String(item.label), String(item.value)]);
    } else {
      options.push([String(item), String(item)]);
    }
  }
  return options;
}

function fieldInfo(Blockly, field) {
  const name = field.name;
  const kind = fieldKind(Blockly, field, name);
  let value = null;
  try {
    value = field.getValue();
  } catch (err) {
    value = null;
  }
  if (kind === "variable") {
    // 既定は生成済みの変数 id なので、仕様の参照名としては意味を持たない。
    value = "";
  }
  return {
    name: name,
    kind: kind,
    default: value,
    options: fieldOptions(field),
    serializable: field.SERIALIZABLE !== false,
  };
}

function blockInfo(Blockly, workspace, blockType) {
  const block = workspace.newBlock(blockType);
  const fields = [];
  const inputs = [];
  for (const input of block.inputList) {
    const connection = input.connection;
    if (connection) {
      const check =
        typeof connection.getCheck === "function" ? connection.getCheck() : null;
      inputs.push({
        name: input.name,
        kind: input.type === 3 ? "statement" : "value",
        check: Array.isArray(check) ? check : [],
      });
    }
    for (const field of input.fieldRow) {
      if (field.name) {
        fields.push(fieldInfo(Blockly, field));
      }
    }
  }
  let output = null;
  if (block.outputConnection) {
    const check =
      typeof block.outputConnection.getCheck === "function"
        ? block.outputConnection.getCheck()
        : null;
    output = Array.isArray(check) ? check : [];
  }
  return {
    type: blockType,
    tooltip: typeof block.tooltip === "string" ? block.tooltip : "",
    previous: !!block.previousConnection,
    next: !!block.nextConnection,
    output: output,
    fields: fields,
    inputs: inputs,
  };
}

function collectCatalog(Blockly, warnings) {
  const names = Object.keys(Blockly.Blocks)
    .filter(
      (name) =>
        name.startsWith("pokecon_") || ALLOWED_STANDARD.indexOf(name) >= 0,
    )
    .sort();
  const workspace = new Blockly.Workspace();
  const blocks = {};
  for (const name of names) {
    try {
      blocks[name] = blockInfo(Blockly, workspace, name);
    } catch (err) {
      warnings.push("カタログを作れません: " + name + " (" + errorText(err) + ")");
    }
  }
  return { ok: true, blocks: blocks, warnings: warnings };
}

// ---- 生成 -----------------------------------------------------------------

function checkTopBlocks(workspace) {
  const tops = workspace.getTopBlocks(false) || [];
  let programs = 0;
  const bad = [];
  for (const block of tops) {
    if (block.type === "pokecon_program") {
      programs += 1;
    } else if (block.type !== "pokecon_sub_def") {
      bad.push(block.type);
    }
  }
  const errors = [];
  if (bad.length) {
    errors.push(
      "プログラムの外にブロックがあります（pokecon_program / pokecon_sub_def の外へは置けません）: " +
        bad.join(", "),
    );
  }
  if (!programs) {
    errors.push("プログラム（pokecon_program）がありません");
  }
  return errors;
}

function collectCode(Blockly, request, warnings) {
  const workspaceJson = request.workspace;
  if (
    !workspaceJson ||
    typeof workspaceJson !== "object" ||
    Array.isArray(workspaceJson)
  ) {
    return {
      ok: false,
      errors: ["request.workspace がありません"],
      warnings: warnings,
    };
  }
  const workspace = new Blockly.Workspace();
  Blockly.serialization.workspaces.load(workspaceJson, workspace);
  const topErrors = checkTopBlocks(workspace);
  if (topErrors.length) {
    return { ok: false, errors: topErrors, warnings: warnings };
  }
  const code = Blockly.Python.workspaceToCode(workspace);
  if (typeof code !== "string") {
    return {
      ok: false,
      errors: ["Python コードを生成できませんでした"],
      warnings: warnings,
    };
  }
  return { ok: true, code: code, warnings: warnings };
}

// ---- 本体 -----------------------------------------------------------------

function main() {
  const mode = process.argv[2];
  const assetsDir = process.argv[3];
  const requestPath = process.argv[4];
  const warnings = [];
  if (!mode || !assetsDir || !requestPath) {
    failure([
      "使い方: node blockly_agent_node.js <catalog|generate> <assets-dir> <request.json>",
    ]);
    return;
  }
  let request;
  try {
    request = readRequest(requestPath);
  } catch (err) {
    failure(["request を読めません: " + errorText(err)]);
    return;
  }
  let Blockly;
  try {
    Blockly = loadBlockly(assetsDir, warnings);
  } catch (err) {
    failure([errorText(err)], warnings);
    return;
  }
  try {
    if (mode === "catalog") {
      emit(collectCatalog(Blockly, warnings));
    } else if (mode === "generate") {
      emit(collectCode(Blockly, request, warnings));
    } else {
      failure(["不明なモードです: " + String(mode)]);
    }
  } catch (err) {
    failure([errorText(err)], warnings);
  }
}

main();
