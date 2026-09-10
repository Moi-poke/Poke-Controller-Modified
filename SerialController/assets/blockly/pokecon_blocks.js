// PokeCon用ブロック定義と生成器（ブラウザ用）。
// 対応：program（NAME＋DO）、press（ボタン＋長さ＋待ち）、wait（秒）。
// 繰り返し・条件は標準ブロック（controls_repeat等）を使う。
(function () {
  "use strict";

  // ブラウザの <script> 読みでは pythonGenerator 大域は存在しない。
  // 生成器は python_compressed.js が Blockly.Python に付ける（Node の
  // require().pythonGenerator とは形が違う）。無いまま進むと、定義は
  // 読めるのに保存だけ ReferenceError で死ぬため、ここで確定させる。
  var pythonGenerator =
    (typeof Blockly !== "undefined" && Blockly.Python) || null;
  if (!pythonGenerator) {
    throw new Error(
      "Blockly.Python がありません（python_compressed.js の読込を確認してください）"
    );
  }

  function pyStr(s) {
    return '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
  }

  Blockly.defineBlocksWithJsonArray([
    {
      type: "pokecon_program",
      message0: "プログラム %1 %2",
      args0: [
        { type: "field_input", name: "NAME", text: "ブロック作成" },
        { type: "input_statement", name: "DO" },
      ],
      colour: 230,
    },
    {
      type: "pokecon_press",
      message0: "%1 を押す 長さ %2 待ち %3",
      args0: [
        {
          type: "field_dropdown",
          name: "BUTTON",
          options: [
            ["Y", "Y"],
            ["B", "B"],
            ["A", "A"],
            ["X", "X"],
            ["L", "L"],
            ["R", "R"],
            ["ZL", "ZL"],
            ["ZR", "ZR"],
            ["MINUS", "MINUS"],
            ["PLUS", "PLUS"],
            ["LCLICK", "LCLICK"],
            ["RCLICK", "RCLICK"],
            ["HOME", "HOME"],
            ["CAPTURE", "CAPTURE"],
          ],
        },
        { type: "field_number", name: "DURATION", value: 0.1, min: 0, max: 10 },
        { type: "field_number", name: "WAIT", value: 0.1, min: 0, max: 60 },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 160,
    },
    {
      type: "pokecon_wait",
      message0: "%1 秒待つ",
      args0: [
        { type: "field_number", name: "SEC", value: 0.5, min: 0, max: 3600 },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 160,
    },
  ]);

  // ブロック上の📷ボタン。押すと範囲選択モーダルをそのブロック用に開く。
  // FieldLabel継承で文面描画だけ借り、showEditor_上書きでクリック可能にする
  //（クリック可否はshowEditor_の上書き有無で決まる）。
  // 値を持たない（EDITABLE=false・SERIALIZABLE=false）ため保存物・生成コードに影響しない。
  // 開く先は editor.html が Blockly.PokeconOpenBlockModal に登録する。
  class CapOpenField extends Blockly.FieldLabel {
    constructor(text) {
      super(text == null ? "📷" : text);
      this.EDITABLE = false;
      this.SERIALIZABLE = false;
    }
    showEditor_() {
      var b = this.getSourceBlock();
      if (b && typeof Blockly.PokeconOpenBlockModal === "function") {
        Blockly.PokeconOpenBlockModal(b.id);
      }
    }
    static fromJson(options) {
      return new CapOpenField(options ? options.text : undefined);
    }
  }
  Blockly.fieldRegistry.register("field_capopen", CapOpenField);

  Blockly.defineBlocksWithJsonArray([
    {
      type: "pokecon_vision_contains",
      message0: "画像 %1 がある 閾値 %2 範囲 %3 %4 %5",
      args0: [
        { type: "field_dropdown", name: "TEMPLATE", options: [["my-pack/a.png", "my-pack/a.png"]] },
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_input", name: "CROP", text: "" },
        {
          type: "field_image",
          name: "PREVIEW",
          src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
          width: 120,
          height: 90,
          alt: "*",
        },
        { type: "field_capopen", name: "CAPOPEN", text: "📷" },
      ],
      extensions: ["pokecon_template_preview", "pokecon_template_options"],
      output: "Boolean",
      colour: 210,
    },
    {
      type: "pokecon_vision_wait_appear",
      message0: "画像 %1 が出るまで待つ 上限 %2 閾値 %3 範囲 %4 %5 %6",
      args0: [
        { type: "field_dropdown", name: "TEMPLATE", options: [["my-pack/a.png", "my-pack/a.png"]] },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_input", name: "CROP", text: "" },
        {
          type: "field_image",
          name: "PREVIEW",
          src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
          width: 120,
          height: 90,
          alt: "*",
        },
        { type: "field_capopen", name: "CAPOPEN", text: "📷" },
      ],
      extensions: ["pokecon_template_preview", "pokecon_template_options"],
      previousStatement: null,
      nextStatement: null,
      colour: 210,
    },
    {
      type: "pokecon_vision_wait_gone",
      message0: "画像 %1 が消えるまで待つ 上限 %2 閾値 %3 範囲 %4 %5 %6",
      args0: [
        { type: "field_dropdown", name: "TEMPLATE", options: [["my-pack/a.png", "my-pack/a.png"]] },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_input", name: "CROP", text: "" },
        {
          type: "field_image",
          name: "PREVIEW",
          src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
          width: 120,
          height: 90,
          alt: "*",
        },
        { type: "field_capopen", name: "CAPOPEN", text: "📷" },
      ],
      extensions: ["pokecon_template_preview", "pokecon_template_options"],
      previousStatement: null,
      nextStatement: null,
      colour: 210,
    },
    {
      type: "pokecon_vision_position",
      message0: "画像 %1 の位置 閾値 %2 範囲 %3 %4 %5",
      args0: [
        { type: "field_dropdown", name: "TEMPLATE", options: [["my-pack/a.png", "my-pack/a.png"]] },
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_input", name: "CROP", text: "" },
        {
          type: "field_image",
          name: "PREVIEW",
          src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
          width: 120,
          height: 90,
          alt: "*",
        },
        { type: "field_capopen", name: "CAPOPEN", text: "📷" },
      ],
      extensions: ["pokecon_template_preview", "pokecon_template_options"],
      output: null,
      colour: 210,
    },
    {
      type: "pokecon_vision_wait_stable",
      message0: "画面が止まるまで待つ 静止 %1 上限 %2",
      args0: [
        { type: "field_number", name: "QUIET", value: 0.5, min: 0, max: 60 },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 210,
    },
    {
      type: "pokecon_vision_color",
      message0: "色 下限 %1 %2 %3 上限 %4 %5 %6 割合 %7",
      args0: [
        { type: "field_number", name: "H1", value: 0, min: 0, max: 179 },
        { type: "field_number", name: "S1", value: 0, min: 0, max: 255 },
        { type: "field_number", name: "V1", value: 0, min: 0, max: 255 },
        { type: "field_number", name: "H2", value: 179, min: 0, max: 179 },
        { type: "field_number", name: "S2", value: 255, min: 0, max: 255 },
        { type: "field_number", name: "V2", value: 255, min: 0, max: 255 },
        { type: "field_number", name: "RATIO", value: 0.6, min: 0, max: 1 },
      ],
      output: "Boolean",
      colour: 210,
    },
  ]);

  // TEMPLATE欄の候補一覧。editor.html が ./templates の取得結果で更新する。
  // 未登録名は先頭に足して保持する（打ち間違いの既存保存物を壊さない）。
  // Array.isArray で見る（instanceof はvm等の別レルム配列で偽になるため）。
  Blockly.Extensions.register("pokecon_template_options", function () {
    var f = this.getField("TEMPLATE");
    if (!f) {
      return;
    }
    f.menuGenerator_ = function () {
      var list =
        typeof Blockly.PokeconTemplates !== "undefined" &&
        Array.isArray(Blockly.PokeconTemplates)
          ? Blockly.PokeconTemplates
          : [];
      var opts = list.map(function (n) {
        return [n, n];
      });
      var cur = f.getValue();
      if (cur && !opts.some(function (o) { return o[1] === cur; })) {
        opts.unshift([cur, cur]);
      }
      // 空欄も常時選べる（従来の空テキストと同等。保存時は書式検査ではねられる）。
      opts.push(["(空欄)", ""]);
      return opts;
    };
  });
  // TEMPLATE欄の変更をダミーPREVIEW欄へ反映する。生成コードには触らない。
  // 欠損時は透明placeholderのままにする（保存は塞がない）。
  var PREVIEW_EMPTY =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
  function previewUrl(v) {
    return "./template_image?name=" + encodeURIComponent(v);
  }
  Blockly.Extensions.register("pokecon_template_preview", function () {
    var tpl = this.getField("TEMPLATE");
    var prev = this.getField("PREVIEW");
    if (!tpl || !prev) {
      return;
    }
    tpl.setValidator(function (v) {
      var b = this.getSourceBlock();
      if (b) {
        var p = b.getField("PREVIEW");
        if (p) {
          p.setValue(v ? previewUrl(v) : PREVIEW_EMPTY);
        }
      }
      return v;
    });
  });
  // リポジトリは4スペース字下げ（ruff format）。既定の2スペースのままでは通らない。
  pythonGenerator.INDENT = "    ";

  pythonGenerator.forBlock["pokecon_program"] = function (block, generator) {
    var name = block.getFieldValue("NAME");
    var body = generator.statementToCode(block, "DO");
    var inner = body ? generator.prefixLines(body, "    ") : "        pass\n";
    // statementToCodeだけ・prefixLinesだけの片方では字下げが壊れる（spike確定）。
    // 両方を使ってdo()の中に寄せる。
    var vision =
      /self\.(isContainTemplate|waitTemplate|waitTemplateGone|getTemplatePosition|waitStable|getColorRatio|isSimilarColor|isContainTemplateDump|findAllTemplates|countTemplate)\s*\(/.test(
        body
      );
    var head;
    if (vision) {
      // press系と混ぜても `Button` が未定義にならないよう、vision側にも付ける。
      head =
        "from Commands.Keys import Button\n" +
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n" +
        "\n\n" +
        "class BlocklyCmd(ImageProcPythonCommand):\n" +
        "    NAME = " +
        pyStr(name) +
        "\n\n" +
        "    def __init__(self, cam, gui=None):\n" +
        "        super().__init__(cam, gui)\n" +
        "\n" +
        "    def do(self) -> None:\n";
    } else {
      head =
        "from Commands.Keys import Button\n" +
        "from Commands.PythonCommandBase import PythonCommand\n" +
        "\n\n" +
        "class BlocklyCmd(PythonCommand):\n" +
        "    NAME = " +
        pyStr(name) +
        "\n\n" +
        "    def do(self) -> None:\n";
    }
    return head + inner;
  };

  pythonGenerator.forBlock["pokecon_press"] = function (block) {
    var btn = block.getFieldValue("BUTTON");
    var dur = block.getFieldValue("DURATION");
    var wait = block.getFieldValue("WAIT");
    return (
      "self.press(Button." +
      btn +
      ", duration=" +
      dur +
      ", wait=" +
      wait +
      ")\n"
    );
  };

  pythonGenerator.forBlock["pokecon_wait"] = function (block) {
    return "self.wait(" + block.getFieldValue("SEC") + ")\n";
  };

  function visionCrop(block) {
    var c = String(block.getFieldValue("CROP") || "").trim();
    var m = c.match(/^(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)$/);
    return m
      ? ", crop=[" + m[1] + "," + m[2] + "," + m[3] + "," + m[4] + "]"
      : "";
  }

  pythonGenerator.forBlock["pokecon_vision_contains"] = function (
    block,
    generator
  ) {
    var code =
      "self.isContainTemplate(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      ")";
    return [code, generator.ORDER_ATOMIC];
  };

  pythonGenerator.forBlock["pokecon_vision_wait_appear"] = function (block) {
    return (
      "self.waitTemplate(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", timeout=" +
      block.getFieldValue("TIMEOUT") +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      ")\n"
    );
  };

  pythonGenerator.forBlock["pokecon_vision_wait_gone"] = function (block) {
    return (
      "self.waitTemplateGone(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", timeout=" +
      block.getFieldValue("TIMEOUT") +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      ")\n"
    );
  };

  pythonGenerator.forBlock["pokecon_vision_position"] = function (
    block,
    generator
  ) {
    var code =
      "self.getTemplatePosition(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      ")";
    return [code, generator.ORDER_ATOMIC];
  };

  pythonGenerator.forBlock["pokecon_vision_wait_stable"] = function (block) {
    return (
      "self.waitStable(quiet=" +
      block.getFieldValue("QUIET") +
      ", timeout=" +
      block.getFieldValue("TIMEOUT") +
      ")\n"
    );
  };

  pythonGenerator.forBlock["pokecon_vision_color"] = function (
    block,
    generator
  ) {
    var code =
      "self.isSimilarColor([], [" +
      block.getFieldValue("H1") +
      "," +
      block.getFieldValue("S1") +
      "," +
      block.getFieldValue("V1") +
      "], [" +
      block.getFieldValue("H2") +
      "," +
      block.getFieldValue("S2") +
      "," +
      block.getFieldValue("V2") +
      "], ratio=" +
      block.getFieldValue("RATIO") +
      ")";
    return [code, generator.ORDER_ATOMIC];
  };
})();
