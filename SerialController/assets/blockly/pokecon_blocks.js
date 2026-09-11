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

  // スティックパッド欄の値論理（描画なしの純粋部。node vmで検証する）。
  // 値の形は "角度,強さ"（例: "90,100"）。角度は8方向（45度刻み）へ
  // スナップし、強さは常に100%（全倒し）。Direction の流儀
  // （0=右・90=上）に合わせる。
  var PokeconStick = {
    normAngle: function (a) {
      a = a % 360;
      if (a < 0) {
        a += 360;
      }
      return Math.round(a) % 360;
    },
    snapAngle: function (a) {
      if (!isFinite(a)) {
        return 90;
      }
      return (Math.round(PokeconStick.normAngle(a) / 45) % 8) * 45;
    },
    clampMag: function (m) {
      return Math.min(100, Math.max(0, Math.round(m)));
    },
    parsePadValue: function (v) {
      if (v == null) {
        return null;
      }
      var parts = String(v).split(",");
      if (parts.length !== 2) {
        return null;
      }
      var a = Number(String(parts[0]).trim());
      var m = Number(String(parts[1]).trim());
      if (!isFinite(a) || !isFinite(m)) {
        return null;
      }
      return {
        angle: PokeconStick.normAngle(a),
        mag: PokeconStick.clampMag(m),
      };
    },
    formatPadValue: function (angle) {
      return PokeconStick.snapAngle(Number(angle)) + ",100";
    },
    angleMagToXY: function (angle, magPct, r) {
      var rad = (angle * Math.PI) / 180;
      return {
        x: (Math.cos(rad) * magPct * r) / 100,
        y: (-Math.sin(rad) * magPct * r) / 100,
      };
    },
    xyToAngleMag: function (dx, dy) {
      var angle = 90;
      if (isFinite(dx) && isFinite(dy)) {
        angle = PokeconStick.snapAngle((Math.atan2(-dy, dx) * 180) / Math.PI);
      }
      return { angle: angle, mag: 100 };
    },
  };
  Blockly.PokeconStick = PokeconStick;
  // ブロック内蔵のミニスティック欄。48pxの円パッドを直接ドラッグする。
  // 値は "角度,強さ" 文字列で持ち運び（SERIALIZABLE のため保存物に残る）。
  // 描画（initView/updateSize_）は実ブラウザでのみ走り、検証・生成の
  // 経路（値論理＋generator）は PokeconStick に寄せてnodeで確かめる。
  class StickPadField extends Blockly.Field {
    constructor(value) {
      super(value == null ? "90,100" : value);
      this.SERIALIZABLE = true;
    }
    static fromJson(options) {
      return new StickPadField(options ? options.value : undefined);
    }
    // 不正値は受け付けず（null＝拒否）、常に正規形で保つ。
    doClassValidation_(newValue) {
      var parsed = PokeconStick.parsePadValue(newValue);
      if (!parsed) {
        return null;
      }
      return PokeconStick.formatPadValue(parsed.angle);
    }
    getText_() {
      return "";
    }
    // クリックで開く文字編集は使わない（パッドを直接触る）。
    showEditor_() {
      return;
    }
    initView() {
      this.padSize_ = 48;
      this.padR_ = 20;
      var NS = "http://www.w3.org/2000/svg";
      var field = this;
      var circle = document.createElementNS(NS, "circle");
      circle.setAttribute("cx", this.padSize_ / 2);
      circle.setAttribute("cy", this.padSize_ / 2);
      circle.setAttribute("r", this.padR_);
      circle.setAttribute("fill", "#f4f4f4");
      circle.setAttribute("stroke", "#999");
      circle.setAttribute("stroke-width", "1");
      this.fieldGroup_.appendChild(circle);
      var dot = document.createElementNS(NS, "circle");
      dot.setAttribute("r", "5");
      dot.setAttribute("fill", "#06c");
      dot.setAttribute("style", "cursor:move");
      this.fieldGroup_.appendChild(dot);
      this.padDot_ = dot;
      // 要素捕捉でpad内完結にする（window共有の既存modalと干渉させない）。
      var dragging = false;
      var posOf = function (ev) {
        var rect = field.fieldGroup_.getBoundingClientRect();
        return {
          dx: ev.clientX - (rect.left + rect.width / 2),
          dy: ev.clientY - (rect.top + rect.height / 2),
          range:
            (Math.min(rect.width, rect.height) / 2 / field.padSize_) *
            field.padR_,
        };
      };
      circle.addEventListener("pointerdown", function (ev) {
        dragging = true;
        // ブロック自体のドラッグ開始に伝搬させない（パッド操作に専念する）。
        if (ev.stopPropagation) {
          ev.stopPropagation();
        }
        try {
          if (circle.setPointerCapture && ev.pointerId !== undefined) {
            circle.setPointerCapture(ev.pointerId);
          }
        } catch (e) {
          /* 掴めなくてもドラッグは続ける */
        }
        var p = posOf(ev);
        var v = PokeconStick.xyToAngleMag(p.dx, p.dy, p.range);
        field.setValue(v.angle + "," + v.mag);
        if (ev.preventDefault) {
          ev.preventDefault();
        }
      });
      circle.addEventListener("pointermove", function (ev) {
        if (!dragging) {
          return;
        }
        var p = posOf(ev);
        var v = PokeconStick.xyToAngleMag(p.dx, p.dy, p.range);
        field.setValue(v.angle + "," + v.mag);
      });
      var stop = function () {
        dragging = false;
      };
      circle.addEventListener("pointerup", stop);
      circle.addEventListener("pointercancel", stop);
      this.updateSize_();
      this.doValueUpdate_(this.getValue());
    }
    updateSize_() {
      var s = this.padSize_ || 48;
      this.size_ = new Blockly.utils.Size(s, s);
    }
    doValueUpdate_(newValue) {
      Blockly.Field.prototype.doValueUpdate_.call(this, newValue);
      if (!this.padDot_) {
        return;
      }
      var parsed = PokeconStick.parsePadValue(newValue);
      if (!parsed) {
        return;
      }
      var s = this.padSize_ || 48;
      var r = this.padR_ || 20;
      var p = PokeconStick.angleMagToXY(parsed.angle, parsed.mag, r);
      this.padDot_.setAttribute("cx", s / 2 + p.x);
      this.padDot_.setAttribute("cy", s / 2 + p.y);
    }
  }
  Blockly.fieldRegistry.register("field_stickpad", StickPadField);
  Blockly.StickPadField = StickPadField;

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
      message0: "スティック %1 %2 角度 %3 長さ %4 待ち %5",
      args0: [
        {
          type: "field_dropdown",
          name: "STICK",
          options: [
            ["L", "LEFT"],
            ["R", "RIGHT"],
          ],
        },
        { type: "field_stickpad", name: "PAD", value: "90,100" },
        { type: "field_number", name: "ANGLE", value: 90, min: 0, max: 360 },
        { type: "field_number", name: "DURATION", value: 0.1, min: 0, max: 10 },
        { type: "field_number", name: "WAIT", value: 0.1, min: 0, max: 60 },
      ],
      extensions: ["pokecon_stick_pad_sync"],
      previousStatement: null,
      nextStatement: null,
      colour: 160,
      tooltip: "L/Rスティックをパッドか角度で倒す（8方向・強さ100%固定）。",
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
      type: "pokecon_hold",
      message0: "%1 を押し続ける 待ち %2",
      args0: [
        {
          type: "field_dropdown",
          name: "TARGET",
          options: [
            ["Y", "Button.Y"],
            ["B", "Button.B"],
            ["A", "Button.A"],
            ["X", "Button.X"],
            ["L", "Button.L"],
            ["R", "Button.R"],
            ["ZL", "Button.ZL"],
            ["ZR", "Button.ZR"],
            ["MINUS", "Button.MINUS"],
            ["PLUS", "Button.PLUS"],
            ["LCLICK", "Button.LCLICK"],
            ["RCLICK", "Button.RCLICK"],
            ["HOME", "Button.HOME"],
            ["CAPTURE", "Button.CAPTURE"],
            ["↑", "Direction.UP"],
            ["→", "Direction.RIGHT"],
            ["↓", "Direction.DOWN"],
            ["←", "Direction.LEFT"],
            ["↗", "Direction.UP_RIGHT"],
            ["↘", "Direction.DOWN_RIGHT"],
            ["↙", "Direction.DOWN_LEFT"],
            ["↖", "Direction.UP_LEFT"],
            ["R↑", "Direction.R_UP"],
            ["R→", "Direction.R_RIGHT"],
            ["R↓", "Direction.R_DOWN"],
            ["R←", "Direction.R_LEFT"],
            ["R↗", "Direction.R_UP_RIGHT"],
            ["R↘", "Direction.R_DOWN_RIGHT"],
            ["R↙", "Direction.R_DOWN_LEFT"],
            ["R↖", "Direction.R_UP_LEFT"],
          ],
        },
        { type: "field_number", name: "WAIT", value: 0.1, min: 0, max: 60 },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 160,
      tooltip: "押しっぱなしにする（holdEndで離す）。",
    },
    {
      type: "pokecon_hold_end",
      message0: "%1 を離す",
      args0: [
        {
          type: "field_dropdown",
          name: "TARGET",
          options: [
            ["Y", "Button.Y"],
            ["B", "Button.B"],
            ["A", "Button.A"],
            ["X", "Button.X"],
            ["L", "Button.L"],
            ["R", "Button.R"],
            ["ZL", "Button.ZL"],
            ["ZR", "Button.ZR"],
            ["MINUS", "Button.MINUS"],
            ["PLUS", "Button.PLUS"],
            ["LCLICK", "Button.LCLICK"],
            ["RCLICK", "Button.RCLICK"],
            ["HOME", "Button.HOME"],
            ["CAPTURE", "Button.CAPTURE"],
            ["↑", "Direction.UP"],
            ["→", "Direction.RIGHT"],
            ["↓", "Direction.DOWN"],
            ["←", "Direction.LEFT"],
            ["↗", "Direction.UP_RIGHT"],
            ["↘", "Direction.DOWN_RIGHT"],
            ["↙", "Direction.DOWN_LEFT"],
            ["↖", "Direction.UP_LEFT"],
            ["R↑", "Direction.R_UP"],
            ["R→", "Direction.R_RIGHT"],
            ["R↓", "Direction.R_DOWN"],
            ["R←", "Direction.R_LEFT"],
            ["R↗", "Direction.R_UP_RIGHT"],
            ["R↘", "Direction.R_DOWN_RIGHT"],
            ["R↙", "Direction.R_DOWN_LEFT"],
            ["R↖", "Direction.R_UP_LEFT"],
          ],
        },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 160,
      tooltip: "押しっぱなしを離す。",
    },
    {
      type: "pokecon_finish",
      message0: "正常終了する",
      args0: [],
      previousStatement: null,
      nextStatement: null,
      colour: 160,
      tooltip: "コマンドを正常終了する（色違い検出時など）。",
    },
    {
      type: "pokecon_press_rep",
      message0: "%1 を %2 回押す 長さ %3 間隔 %4 待ち %5",
      args0: [
        {
          type: "field_dropdown",
          name: "TARGET",
          options: [
            ["Y", "Button.Y"],
            ["B", "Button.B"],
            ["A", "Button.A"],
            ["X", "Button.X"],
            ["L", "Button.L"],
            ["R", "Button.R"],
            ["ZL", "Button.ZL"],
            ["ZR", "Button.ZR"],
            ["MINUS", "Button.MINUS"],
            ["PLUS", "Button.PLUS"],
            ["LCLICK", "Button.LCLICK"],
            ["RCLICK", "Button.RCLICK"],
            ["HOME", "Button.HOME"],
            ["CAPTURE", "Button.CAPTURE"],
            ["↑", "Direction.UP"],
            ["→", "Direction.RIGHT"],
            ["↓", "Direction.DOWN"],
            ["←", "Direction.LEFT"],
            ["↗", "Direction.UP_RIGHT"],
            ["↘", "Direction.DOWN_RIGHT"],
            ["↙", "Direction.DOWN_LEFT"],
            ["↖", "Direction.UP_LEFT"],
            ["R↑", "Direction.R_UP"],
            ["R→", "Direction.R_RIGHT"],
            ["R↓", "Direction.R_DOWN"],
            ["R←", "Direction.R_LEFT"],
            ["R↗", "Direction.R_UP_RIGHT"],
            ["R↘", "Direction.R_DOWN_RIGHT"],
            ["R↙", "Direction.R_DOWN_LEFT"],
            ["R↖", "Direction.R_UP_LEFT"],
          ],
        },
        { type: "field_number", name: "COUNT", value: 3, min: 1, max: 1000 },
        { type: "field_number", name: "DURATION", value: 0.1, min: 0, max: 10 },
        { type: "field_number", name: "INTERVAL", value: 0.1, min: 0, max: 60 },
        { type: "field_number", name: "WAIT", value: 0.1, min: 0, max: 60 },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 160,
      tooltip: "指定回数だけ繰り返し押す。",
    },
    {
      type: "pokecon_print",
      message0: "表示 %1 %2",
      args0: [
        {
          type: "field_dropdown",
          name: "KIND",
          options: [
            ["表示", "print"],
            ["結果", "print2"],
          ],
        },
        { type: "input_value", name: "TEXT" },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 60,
      tooltip: "ログへ出す（表示＝進捗・結果＝後で見返す用）。",
    },
    {
      type: "pokecon_screenshot",
      message0: "スクショを撮る",
      args0: [],
      previousStatement: null,
      nextStatement: null,
      colour: 210,
      tooltip: "カメラ画像を保存する。使うと画像認識ありになる。",
    },
    {
      type: "pokecon_discord",
      message0: "Discord %1 %2",
      args0: [
        {
          type: "field_dropdown",
          name: "KIND",
          options: [
            ["テキスト", "text"],
            ["画像付き", "image"],
          ],
        },
        { type: "input_value", name: "CONTENT" },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 210,
      tooltip: "Discordへ通知する。画像付きは画像認識ありになる。",
    },
    {
      type: "pokecon_audio_tone_contains",
      message0: "音 %1〜%2Hz が閾値 %3 を超えた",
      args0: [
        { type: "field_number", name: "LO", value: 3000, min: 0, max: 22050 },
        { type: "field_number", name: "HI", value: 3200, min: 0, max: 22050 },
        { type: "field_number", name: "THRESH", value: 1000000 },
      ],
      output: "Boolean",
      colour: 250,
      tooltip: "指定帯域の音量が閾値を超えたら真（要調整）。",
    },
    {
      type: "pokecon_audio_wait_tone",
      message0: "音 %1〜%2Hz を待つ 閾値 %3 上限 %4",
      args0: [
        { type: "field_number", name: "LO", value: 3000, min: 0, max: 22050 },
        { type: "field_number", name: "HI", value: 3200, min: 0, max: 22050 },
        { type: "field_number", name: "THRESH", value: 1000000 },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 250,
      tooltip: "指定帯域の音が鳴るまで待つ（要調整）。",
    },
    {
      type: "pokecon_vision_press_until",
      message0: "画像 %1 が出るまで %2 を押す 上限 %3 閾値 %4 範囲 %5 グレー %6 %7 %8",
      args0: [
        { type: "field_dropdown", name: "TEMPLATE", options: [["my-pack/a.png", "my-pack/a.png"]] },
        {
          type: "field_dropdown",
          name: "TARGET",
          options: [
            ["Y", "Button.Y"],
            ["B", "Button.B"],
            ["A", "Button.A"],
            ["X", "Button.X"],
            ["L", "Button.L"],
            ["R", "Button.R"],
            ["ZL", "Button.ZL"],
            ["ZR", "Button.ZR"],
            ["MINUS", "Button.MINUS"],
            ["PLUS", "Button.PLUS"],
            ["LCLICK", "Button.LCLICK"],
            ["RCLICK", "Button.RCLICK"],
            ["HOME", "Button.HOME"],
            ["CAPTURE", "Button.CAPTURE"],
          ],
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
      type: "pokecon_vision_press_until_gone",
      message0: "画像 %1 が消えるまで %2 を押す 上限 %3 閾値 %4 範囲 %5 グレー %6 %7 %8",
      args0: [
        { type: "field_dropdown", name: "TEMPLATE", options: [["my-pack/a.png", "my-pack/a.png"]] },
        {
          type: "field_dropdown",
          name: "TARGET",
          options: [
            ["Y", "Button.Y"],
            ["B", "Button.B"],
            ["A", "Button.A"],
            ["X", "Button.X"],
            ["L", "Button.L"],
            ["R", "Button.R"],
            ["ZL", "Button.ZL"],
            ["ZR", "Button.ZR"],
            ["MINUS", "Button.MINUS"],
            ["PLUS", "Button.PLUS"],
            ["LCLICK", "Button.LCLICK"],
            ["RCLICK", "Button.RCLICK"],
            ["HOME", "Button.HOME"],
            ["CAPTURE", "Button.CAPTURE"],
          ],
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
      type: "pokecon_vision_wait_count",
      message0: "画像 %1 が %2 個出るまで待つ 上限 %3 閾値 %4 範囲 %5 グレー %6 %7 %8",
      args0: [
        { type: "field_dropdown", name: "TEMPLATE", options: [["my-pack/a.png", "my-pack/a.png"]] },
        { type: "field_number", name: "COUNT", value: 2, min: 1, max: 100 },
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
      type: "pokecon_vision_count",
      message0: "画像 %1 の個数 閾値 %2 範囲 %3 グレー %4 %5 %6",
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
        { type: "field_checkbox", name: "USE_GRAY", checked: false },
        { type: "field_capopen", name: "CAPOPEN", text: "📷" },
      ],
      extensions: ["pokecon_template_preview", "pokecon_template_options"],
      output: "Number",
      colour: 210,
    },
    {
      type: "pokecon_sub_def",
      message0: "サブルーチン %1 引数 %2 %3 戻り値 %4",
      args0: [
        { type: "field_input", name: "NAME", text: "my_sub" },
        { type: "field_input", name: "ARGS", text: "" },
        { type: "input_statement", name: "DO" },
        { type: "input_value", name: "RETURN" },
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
      type: "pokecon_sub_call_value",
      message0: "呼んだ値 %1 引数 %2 %3 %4",
      args0: [
        { type: "field_input", name: "NAME", text: "my_sub" },
        { type: "input_value", name: "ARG0" },
        { type: "input_value", name: "ARG1" },
        { type: "input_value", name: "ARG2" },
      ],
      output: null,
      colour: 290,
      tooltip: "戻り値つきサブルーチンを呼び出す（値として使う）。",
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
  // スティック欄の相互反映。PAD（パッド）とANGLE（数値）を同期する。
  // 角度は8方向へスナップし、強さは100%固定（PAD正規形 "角度,100"）。
  // 生成コードはPADを正とする（下のstickPadValueと対）。
  // 再入防止のguardつき。保存物の読込時（validator発火）もそのまま寄る。
  Blockly.Extensions.register("pokecon_stick_pad_sync", function () {
    var pad = this.getField("PAD");
    var ang = this.getField("ANGLE");
    if (!pad || !ang) {
      return;
    }
    // 旧保存物のMAG欄が残っていても触らない（100%固定のため）。
    var syncing = false;
    function padToNums(v) {
      var b = pad.getSourceBlock();
      if (!b || syncing) {
        return v;
      }
      var parsed = null;
      try {
        parsed = PokeconStick.parsePadValue(v);
      } catch (e) {
        parsed = null;
      }
      if (!parsed) {
        return v;
      }
      syncing = true;
      try {
        b.setFieldValue(String(parsed.angle), "ANGLE");
      } finally {
        syncing = false;
      }
      return v;
    }
    function angleToPad(field, v) {
      var b = field.getSourceBlock();
      if (!b || syncing) {
        return v;
      }
      var na = Number(v);
      if (!isFinite(na)) {
        return v;
      }
      syncing = true;
      try {
        b.setFieldValue(PokeconStick.formatPadValue(na), "PAD");
      } finally {
        syncing = false;
      }
      return v;
    }
    pad.setValidator(padToNums);
    ang.setValidator(function (v) {
      return angleToPad(this, v);
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
    var ret = "";
    try {
      ret = generator.valueToCode(defBlock, "RETURN", generator.ORDER_NONE);
    } catch (e) {
      ret = "";
    }
    var inner = body ? generator.prefixLines(body, "    ") : "";
    var sig = args.length ? "self, " + args.join(", ") : "self";
    if (ret != null && String(ret).trim() !== "") {
      // 戻り値つき。注釈なしにする（-> None のまま return 値を書くと
      // 型検査で落ちるため）。
      inner += "    return " + String(ret).trim() + "\n";
      return "    def " + name + "(" + sig + "):\n" + inner + "\n";
    }
    if (!inner) {
      inner = "        pass\n";
    }
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
      /self\.(isContainTemplate|waitTemplate|waitTemplateGone|getTemplatePosition|waitStable|getColorRatio|isSimilarColor|isContainTemplateDump|findAllTemplates|countTemplate|press_until|press_until_gone|wait_count)\s*\(/.test(
        combined,
      );
    // カメラ・画像付きDiscordも ImageProc（cam あり）が必要なため同扱いにする。
    var needImageProc =
      vision ||
      /self\.camera\s*\./.test(combined) ||
      /self\.discord_image\s*\(/.test(combined);
    // 音声ブロックは AudioMixin が要る。画像系と混ざれば併用基底にする。
    var useAudio =
      /self\.(waitTone|isTonePresent|waitSound|isSoundPresent|recordClip)\s*\(/.test(
        combined,
      );
    // スティックを使うときだけ Direction・Stick を足す（未使用のimportを出さない）。
    var useStick = /Direction\s*\(/.test(combined);
    var keysImport = useStick
      ? "from Commands.Keys import Button, Direction, Stick\n"
      : "from Commands.Keys import Button\n";
    var head;
    if (needImageProc && useAudio) {
      // 画像＋音声の併用。実行側の ImageProc 分岐に載り、音声源は属性で渡る。
      head =
        keysImport +
        "from Commands.PythonCommandBase import ImageProcAudioPythonCommand\n" +
        "\n\n" +
        "class BlocklyCmd(ImageProcAudioPythonCommand):\n" +
        "    NAME = " +
        pyStr(name) +
        "\n\n" +
        "    def __init__(self, cam, gui=None, audio=None):\n" +
        "        super().__init__(cam, gui, audio)\n" +
        "\n" +
        "    def do(self) -> None:\n";
    } else if (useAudio) {
      // 音声のみ。実行側の Audio 分岐が音声源を位置引数で渡す。
      head =
        keysImport +
        "from Commands.PythonCommandBase import AudioPythonCommand\n" +
        "\n\n" +
        "class BlocklyCmd(AudioPythonCommand):\n" +
        "    NAME = " +
        pyStr(name) +
        "\n\n" +
        "    def __init__(self, audio=None):\n" +
        "        super().__init__(audio)\n" +
        "\n" +
        "    def do(self) -> None:\n";
    } else if (needImageProc) {
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

  function subCallCode(block, generator) {
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
    return "self." + name + "(" + args.join(", ") + ")";
  }

  pythonGenerator.forBlock["pokecon_sub_call"] = function (block, generator) {
    return subCallCode(block, generator) + "\n";
  };

  pythonGenerator.forBlock["pokecon_sub_call_value"] = function (
    block,
    generator,
  ) {
    return [subCallCode(block, generator), generator.ORDER_ATOMIC];
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

  // PAD欄（"角度,強さ"）を読む。無い旧保存物はANGLE/MAG欄に fallback する。
  function stickPadValue(block) {
    var parsed = null;
    try {
      parsed = PokeconStick.parsePadValue(block.getFieldValue("PAD"));
    } catch (e) {
      parsed = null;
    }
    if (parsed) {
      return parsed;
    }
    var angle = 90;
    var magPct = 100;
    try {
      var a = Number(block.getFieldValue("ANGLE"));
      if (isFinite(a)) {
        angle = a;
      }
      var m = Number(block.getFieldValue("MAG"));
      if (isFinite(m)) {
        magPct = Math.min(100, Math.max(0, m));
      }
    } catch (e) {
      /* 欄が無ければ既定のまま */
    }
    return { angle: angle, mag: magPct };
  }

  pythonGenerator.forBlock["pokecon_stick"] = function (block) {
    var v = stickPadValue(block);
    var rawStick = "";
    try {
      rawStick = block.getFieldValue("STICK");
    } catch (e) {
      rawStick = "";
    }
    var stick = rawStick === "RIGHT" ? "RIGHT" : "LEFT";
    var dur = block.getFieldValue("DURATION");
    var wait = block.getFieldValue("WAIT");
    return (
      "self.press(Direction(Stick." +
      stick +
      ", " +
      v.angle +
      ", magnification=" +
      v.mag / 100 +
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

  pythonGenerator.forBlock["pokecon_hold"] = function (block) {
    return (
      "self.hold(" +
      block.getFieldValue("TARGET") +
      ", wait=" +
      block.getFieldValue("WAIT") +
      ")\n"
    );
  };

  pythonGenerator.forBlock["pokecon_hold_end"] = function (block) {
    return "self.holdEnd(" + block.getFieldValue("TARGET") + ")\n";
  };

  pythonGenerator.forBlock["pokecon_finish"] = function () {
    return "self.finish()\n";
  };

  pythonGenerator.forBlock["pokecon_press_rep"] = function (block) {
    return (
      "self.pressRep(" +
      block.getFieldValue("TARGET") +
      ", " +
      block.getFieldValue("COUNT") +
      ", duration=" +
      block.getFieldValue("DURATION") +
      ", interval=" +
      block.getFieldValue("INTERVAL") +
      ", wait=" +
      block.getFieldValue("WAIT") +
      ")\n"
    );
  };

  function valueOrEmpty(block, generator, inputName) {
    var code = "";
    try {
      code = generator.valueToCode(block, inputName, generator.ORDER_NONE);
    } catch (e) {
      code = "";
    }
    if (code == null || String(code).trim() === "") {
      return '""';
    }
    return String(code).trim();
  }

  pythonGenerator.forBlock["pokecon_print"] = function (block, generator) {
    var expr = valueOrEmpty(block, generator, "TEXT");
    if (block.getFieldValue("KIND") === "print2") {
      return "self.print2(" + expr + ")\n";
    }
    return "print(" + expr + ")\n";
  };

  pythonGenerator.forBlock["pokecon_screenshot"] = function () {
    return "self.camera.saveCapture()\n";
  };

  pythonGenerator.forBlock["pokecon_discord"] = function (block, generator) {
    var method =
      block.getFieldValue("KIND") === "image"
        ? "discord_image"
        : "discord_text";
    return (
      "self." +
      method +
      "(content=" +
      valueOrEmpty(block, generator, "CONTENT") +
      ")\n"
    );
  };

  function audioToneArgs(block) {
    return (
      "[(" +
      block.getFieldValue("LO") +
      ", " +
      block.getFieldValue("HI") +
      ")], [" +
      block.getFieldValue("THRESH") +
      "]"
    );
  }

  pythonGenerator.forBlock["pokecon_audio_tone_contains"] = function (
    block,
    generator,
  ) {
    var code = "self.isTonePresent(" + audioToneArgs(block) + ")";
    return [code, generator.ORDER_ATOMIC];
  };

  pythonGenerator.forBlock["pokecon_audio_wait_tone"] = function (block) {
    return (
      "self.waitTone(" +
      audioToneArgs(block) +
      ", timeout=" +
      block.getFieldValue("TIMEOUT") +
      ")\n"
    );
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

  pythonGenerator.forBlock["pokecon_vision_press_until"] = function (block) {
    return (
      "self.press_until(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", " +
      block.getFieldValue("TARGET") +
      ", timeout=" +
      block.getFieldValue("TIMEOUT") +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      visionGray(block) +
      ")\n"
    );
  };

  pythonGenerator.forBlock["pokecon_vision_press_until_gone"] = function (
    block,
  ) {
    return (
      "self.press_until_gone(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", " +
      block.getFieldValue("TARGET") +
      ", timeout=" +
      block.getFieldValue("TIMEOUT") +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      visionGray(block) +
      ")\n"
    );
  };

  pythonGenerator.forBlock["pokecon_vision_wait_count"] = function (block) {
    return (
      "self.wait_count(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", " +
      block.getFieldValue("COUNT") +
      ", timeout=" +
      block.getFieldValue("TIMEOUT") +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      visionGray(block) +
      ")\n"
    );
  };

  pythonGenerator.forBlock["pokecon_vision_count"] = function (
    block,
    generator,
  ) {
    var code =
      "self.countTemplate(" +
      pyStr(block.getFieldValue("TEMPLATE")) +
      ", threshold=" +
      block.getFieldValue("THRESHOLD") +
      visionCrop(block) +
      visionGray(block) +
      ")";
    return [code, generator.ORDER_ATOMIC];
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
