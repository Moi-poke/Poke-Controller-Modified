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

  // リポジトリは4スペース字下げ（ruff format）。既定の2スペースのままでは通らない。
  pythonGenerator.INDENT = "    ";

  pythonGenerator.forBlock["pokecon_program"] = function (block, generator) {
    var name = block.getFieldValue("NAME");
    var body = generator.statementToCode(block, "DO");
    var inner = body ? generator.prefixLines(body, "    ") : "        pass\n";
    // statementToCodeだけ・prefixLinesだけの片方では字下げが壊れる（spike確定）。
    // 両方を使ってdo()の中に寄せる。
    return (
      "from Commands.Keys import Button\n" +
      "from Commands.PythonCommandBase import PythonCommand\n" +
      "\n\n" +
      "class BlocklyCmd(PythonCommand):\n" +
      "    NAME = " +
      pyStr(name) +
      "\n\n" +
      "    def do(self) -> None:\n" +
      inner
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

  pythonGenerator.forBlock["pokecon_wait"] = function (block) {
    return "self.wait(" + block.getFieldValue("SEC") + ")\n";
  };
})();
