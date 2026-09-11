// PokeCon用ブロック定義と生成器（ブラウザ用）。
// 対応：program（NAME＋DO）、press（ボタン＋長さ＋待ち）、
// stick（L/R＋角度＋強さ＋長さ＋待ち）、wait（秒）、
// サブルーチン（sub_def 定義＋sub_call 呼出、引数あり）、comment（# 注釈）。
// 繰り返し・条件・数値は標準ブロック（controls_repeat等）を使う。
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
      "Blockly.Python がありません（python_compressed.js の読込を確認してください）",
    );
  }

  function pyStr(s) {
    return (
      '"' +
      String(s)
        .replace(/\\/g, "\\\\")
        .replace(/"/g, '\\"')
        .replace(/\r/g, "\\r")
        .replace(/\n/g, "\\n") +
      '"'
    );
  }

  function parseSubArgs(text) {
    var t = String(text == null ? "" : text).trim();
    if (!t) {
      return [];
    }
    return t
      .split(",")
      .map(function (p) {
        return String(p).trim();
      })
      .filter(function (p) {
        return p.length > 0;
      });
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
      type: "pokecon_stick",
      message0: "スティック %1 角度 %2 強さ %3 長さ %4 待ち %5",
      args0: [
        {
          type: "field_dropdown",
          name: "STICK",
          options: [
            ["L", "LEFT"],
            ["R", "RIGHT"],
          ],
        },
        { type: "field_number", name: "ANGLE", value: 90, min: 0, max: 360 },
        { type: "field_number", name: "MAG", value: 100, min: 0, max: 100 },
        { type: "field_number", name: "DURATION", value: 0.1, min: 0, max: 10 },
        { type: "field_number", name: "WAIT", value: 0.1, min: 0, max: 60 },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 160,
      tooltip: "L/Rスティックを角度（度）＋強さ（%）で倒す。",
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
    {
      type: "pokecon_sub_def",
      message0: "サブルーチン %1 引数 %2 %3",
      args0: [
        { type: "field_input", name: "NAME", text: "my_sub" },
        { type: "field_input", name: "ARGS", text: "" },
        { type: "input_statement", name: "DO" },
      ],
      colour: 290,
      tooltip: "トップレベルに置く。呼ぶ側から self.名前() で呼べる。",
    },
    {
      type: "pokecon_sub_call",
      message0: "呼ぶ %1 引数 %2 %3 %4",
      args0: [
        { type: "field_input", name: "NAME", text: "my_sub" },
        { type: "input_value", name: "ARG0" },
        { type: "input_value", name: "ARG1" },
        { type: "input_value", name: "ARG2" },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 290,
      tooltip: "サブルーチン定義を呼び出す。引数は左から順に渡る。",
    },
    {
      type: "pokecon_comment",
      message0: "# %1",
      args0: [{ type: "field_input", name: "TEXT", text: "メモ" }],
      previousStatement: null,
      nextStatement: null,
      colour: 60,
      tooltip: "生成コードに # コメントとして残る。実行には影響しない。",
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
      message0: "画像 %1 がある 閾値 %2 範囲 %3 グレー %4 %5 %6",
      args0: [
        {
          type: "field_dropdown",
          name: "TEMPLATE",
          options: [["my-pack/a.png", "my-pack/a.png"]],
        },
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
        { type: "field_checkbox", name: "USE_GRAY", checked: false },
        { type: "field_capopen", name: "CAPOPEN", text: "📷" },
      ],
      extensions: ["pokecon_template_preview", "pokecon_template_options"],
      output: "Boolean",
      colour: 210,
    },
    {
      type: "pokecon_vision_wait_appear",
      message0:
        "画像 %1 が出るまで待つ 上限 %2 閾値 %3 範囲 %4 グレー %5 %6 %7",
      args0: [
        {
          type: "field_dropdown",
          name: "TEMPLATE",
          options: [["my-pack/a.png", "my-pack/a.png"]],
        },
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
        { type: "field_checkbox", name: "USE_GRAY", checked: false },
        { type: "field_capopen", name: "CAPOPEN", text: "📷" },
      ],
      extensions: ["pokecon_template_preview", "pokecon_template_options"],
      previousStatement: null,
      nextStatement: null,
      colour: 210,
    },
    {
      type: "pokecon_vision_wait_gone",
      message0:
        "画像 %1 が消えるまで待つ 上限 %2 閾値 %3 範囲 %4 グレー %5 %6 %7",
      args0: [
        {
          type: "field_dropdown",
          name: "TEMPLATE",
          options: [["my-pack/a.png", "my-pack/a.png"]],
        },
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
        { type: "field_checkbox", name: "USE_GRAY", checked: false },
        { type: "field_capopen", name: "CAPOPEN", text: "📷" },
      ],
      extensions: ["pokecon_template_preview", "pokecon_template_options"],
      previousStatement: null,
      nextStatement: null,
      colour: 210,
    },
    {
      type: "pokecon_vision_position",
      message0: "画像 %1 の位置 閾値 %2 範囲 %3 グレー %4 %5 %6",
      args0: [
        {
          type: "field_dropdown",
          name: "TEMPLATE",
          options: [["my-pack/a.png", "my-pack/a.png"]],
        },
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
        { type: "field_checkbox", name: "USE_GRAY", checked: false },
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
      message0: "色 下限 %1 %2 %3 上限 %4 %5 %6 割合 %7 範囲 %8 %9",
      args0: [
        { type: "field_number", name: "H1", value: 0, min: 0, max: 179 },
        { type: "field_number", name: "S1", value: 0, min: 0, max: 255 },
        { type: "field_number", name: "V1", value: 0, min: 0, max: 255 },
        { type: "field_number", name: "H2", value: 179, min: 0, max: 179 },
        { type: "field_number", name: "S2", value: 255, min: 0, max: 255 },
        { type: "field_number", name: "V2", value: 255, min: 0, max: 255 },
        { type: "field_number", name: "RATIO", value: 0.6, min: 0, max: 1 },
        { type: "field_input", name: "CROP", text: "" },
        { type: "field_capopen", name: "CAPOPEN", text: "📷" },
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
      if (
        cur &&
        !opts.some(function (o) {
          return o[1] === cur;
        })
      ) {
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

  function collectSubDefs(block) {
    var ws = block ? block.workspace : null;
    if (!ws || typeof ws.getAllBlocks !== "function") {
      return [];
    }
    var defs = ws.getAllBlocks(false).filter(function (b) {
      return b.type === "pokecon_sub_def";
    });
    defs.sort(function (a, b) {
      var na = "",
        nb = "";
      try {
        na = String(a.getFieldValue("NAME") || "");
        nb = String(b.getFieldValue("NAME") || "");
      } catch (e) {
        na = "";
        nb = "";
      }
      if (na < nb) {
        return -1;
      }
      if (na > nb) {
        return 1;
      }
      return 0;
    });
    return defs;
  }

  function buildSubMethod(defBlock, generator) {
    var rawName = "";
    try {
      rawName = defBlock.getFieldValue("NAME");
    } catch (e) {
      rawName = "";
    }
    var name = String(rawName || "").trim() || "__empty_sub__";
    var args = [];
    try {
      args = parseSubArgs(defBlock.getFieldValue("ARGS"));
    } catch (e) {
      args = [];
    }
    var body = "";
    try {
      body = generator.statementToCode(defBlock, "DO");
    } catch (e) {
      body = "";
    }
    var inner = body ? generator.prefixLines(body, "    ") : "        pass\n";
    var sig = args.length ? "self, " + args.join(", ") : "self";
    return "    def " + name + "(" + sig + ") -> None:\n" + inner + "\n";
  }

  pythonGenerator.forBlock["pokecon_program"] = function (block, generator) {
    var name = block.getFieldValue("NAME");
    var body = generator.statementToCode(block, "DO");
    var inner = body ? generator.prefixLines(body, "    ") : "        pass\n";
    // statementToCodeだけ・prefixLinesだけの片方では字下げが壊れる（spike確定）。
    // 両方を使ってdo()の中に寄せる。
    var defs = collectSubDefs(block);
    var methodsCode = defs
      .map(function (d) {
        return buildSubMethod(d, generator);
      })
      .join("");
    var combined = body + methodsCode;
    var vision =
      /self\.(isContainTemplate|waitTemplate|waitTemplateGone|getTemplatePosition|waitStable|getColorRatio|isSimilarColor|isContainTemplateDump|findAllTemplates|countTemplate)\s*\(/.test(
        combined,
      );
    // スティックを使うときだけ Direction・Stick を足す（未使用のimportを出さない）。
    var useStick = /Direction\s*\(/.test(combined);
    var keysImport = useStick
      ? "from Commands.Keys import Button, Direction, Stick\n"
      : "from Commands.Keys import Button\n";
    var head;
    if (vision) {
      // press系と混ぜても `Button` が未定義にならないよう、vision側にも付ける。
      head =
        keysImport +
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
        keysImport +
        "from Commands.PythonCommandBase import PythonCommand\n" +
        "\n\n" +
        "class BlocklyCmd(PythonCommand):\n" +
        "    NAME = " +
        pyStr(name) +
        "\n\n" +
        "    def do(self) -> None:\n";
    }
    if (!methodsCode) {
      return head + inner;
    }
    return head + inner + "\n" + methodsCode;
  };

  // 定義自体は program 側で集めてメソッド化するため、単体では何も出さない。
  // （workspaceToCode の重複を避ける。入子でも外でも集める。）
  pythonGenerator.forBlock["pokecon_sub_def"] = function () {
    return "";
  };

  pythonGenerator.forBlock["pokecon_sub_call"] = function (block, generator) {
    var rawName = "";
    try {
      rawName = block.getFieldValue("NAME");
    } catch (e) {
      rawName = "";
    }
    var name = String(rawName || "").trim() || "__empty_sub__";
    var args = [];
    ["ARG0", "ARG1", "ARG2"].forEach(function (inputName) {
      var code = "";
      try {
        code = generator.valueToCode(block, inputName, generator.ORDER_NONE);
      } catch (e) {
        code = "";
      }
      if (code != null && String(code).trim() !== "") {
        args.push(String(code).trim());
      }
    });
    return "self." + name + "(" + args.join(", ") + ")\n";
  };

  pythonGenerator.forBlock["pokecon_comment"] = function (block) {
    var text = "";
    try {
      text = block.getFieldValue("TEXT");
    } catch (e) {
      text = "";
    }
    var lines = String(text == null ? "" : text).split(/\r?\n/);
    if (!lines.length) {
      return "#\n";
    }
    return (
      lines
        .map(function (line) {
          return "# " + line;
        })
        .join("\n") + "\n"
    );
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

  pythonGenerator.forBlock["pokecon_stick"] = function (block) {
    var rawStick = "";
    try {
      rawStick = block.getFieldValue("STICK");
    } catch (e) {
      rawStick = "";
    }
    var stick = rawStick === "RIGHT" ? "RIGHT" : "LEFT";
    var angle = Number(block.getFieldValue("ANGLE"));
    if (!isFinite(angle)) {
      angle = 90;
    }
    var magPct = Number(block.getFieldValue("MAG"));
    if (!isFinite(magPct)) {
      magPct = 100;
    }
    magPct = Math.min(100, Math.max(0, magPct));
    var mag = magPct / 100;
    var dur = block.getFieldValue("DURATION");
    var wait = block.getFieldValue("WAIT");
    return (
      "self.press(Direction(Stick." +
      stick +
      ", " +
      angle +
      ", magnification=" +
      mag +
      ")" +
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

  function visionGray(block) {
    var g =
      typeof block.getFieldValue === "function"
        ? block.getFieldValue("USE_GRAY")
        : null;
    if (g === "TRUE") {
      return ", use_gray=True";
    }
    if (g === "FALSE") {
      return ", use_gray=False";
    }
    return "";
  }

  pythonGenerator.forBlock["pokecon_vision_contains"] = function (
    block,
    generator,
  ) {
    var code =
      "self.isContainTemplate(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      visionGray(block) +
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
      visionGray(block) +
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
      visionGray(block) +
      ")\n"
    );
  };

  pythonGenerator.forBlock["pokecon_vision_position"] = function (
    block,
    generator,
  ) {
    var code =
      "self.getTemplatePosition(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      visionGray(block) +
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
    generator,
  ) {
    var cropPart = (function () {
      var c = String(block.getFieldValue("CROP") || "").trim();
      var m = c.match(/^(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)$/);
      return m ? "[" + m[1] + "," + m[2] + "," + m[3] + "," + m[4] + "]" : "[]";
    })();
    var code =
      "self.isSimilarColor(" +
      cropPart +
      ", [" +
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
