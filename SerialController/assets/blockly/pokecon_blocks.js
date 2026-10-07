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

  // ---- コントローラ入力欄（ボタン・十字キー・スティック） ----
  // 旧欄は「↑」が左スティック（Direction.UP）なのに十字キーと書かれ、
  // 十字キー（Hat）は選べなかった。候補を実物どおりの名前にし、十字キーを
  // 足す。保存値は従来の書式のまま（ボタンは press だけ素の名、ほかは
  // Button. 付き）なので、旧保存物はそのまま読める。
  // key は書式によらない識別子（ボタンは素の名、方向は Hat./Direction. 付き）。
  var PAD_BUTTONS = [
    ["A", "A"],
    ["B", "B"],
    ["X", "X"],
    ["Y", "Y"],
    ["L", "L"],
    ["R", "R"],
    ["ZL", "ZL"],
    ["ZR", "ZR"],
    ["MINUS", "−（マイナス）"],
    ["PLUS", "＋（プラス）"],
    ["HOME", "HOME"],
    ["CAPTURE", "キャプチャ"],
    ["LCLICK", "左スティック押し込み"],
    ["RCLICK", "右スティック押し込み"],
  ];
  // 8方向。[Hatの名, Directionの名, 矢印, 角度(度・右が0で反時計回り)]
  var PAD_DIRS = [
    ["TOP", "UP", "↑", 90],
    ["TOP_RIGHT", "UP_RIGHT", "↗", 45],
    ["RIGHT", "RIGHT", "→", 0],
    ["BTM_RIGHT", "DOWN_RIGHT", "↘", 315],
    ["BTM", "DOWN", "↓", 270],
    ["BTM_LEFT", "DOWN_LEFT", "↙", 225],
    ["LEFT", "LEFT", "←", 180],
    ["TOP_LEFT", "UP_LEFT", "↖", 135],
  ];
  var PAD_LABELS = {};
  var PAD_KEYS = [];
  PAD_BUTTONS.forEach(function (b) {
    PAD_LABELS[b[0]] = b[1];
    PAD_KEYS.push(b[0]);
  });
  PAD_DIRS.forEach(function (d) {
    PAD_LABELS["Hat." + d[0]] = "十字キー" + d[2];
    PAD_LABELS["Direction." + d[1]] = "左スティック" + d[2];
    PAD_LABELS["Direction.R_" + d[1]] = "右スティック" + d[2];
  });
  PAD_DIRS.forEach(function (d) {
    PAD_KEYS.push("Hat." + d[0]);
  });
  PAD_DIRS.forEach(function (d) {
    PAD_KEYS.push("Direction." + d[1]);
  });
  PAD_DIRS.forEach(function (d) {
    PAD_KEYS.push("Direction.R_" + d[1]);
  });

  // 絵の配置（選択画面の座標・px）。全候補を1回ずつ持つ（検証が見る）。
  var PAD_W = 420;
  var PAD_H = 280;
  var PAD_LAYOUT = [];
  function padAdd(key, x, y, w, h, text) {
    PAD_LAYOUT.push({ key: key, x: x, y: y, w: w, h: h, text: text });
  }
  padAdd("ZL", 14, 8, 60, 26, "ZL");
  padAdd("L", 80, 8, 60, 26, "L");
  padAdd("R", 280, 8, 60, 26, "R");
  padAdd("ZR", 346, 8, 60, 26, "ZR");
  padAdd("MINUS", 158, 48, 34, 26, "−");
  padAdd("CAPTURE", 158, 82, 34, 26, "◉");
  padAdd("PLUS", 228, 48, 34, 26, "＋");
  padAdd("HOME", 228, 82, 34, 26, "⌂");
  // 方向の輪（中心・半径）。中心は押し込み（スティック）か飾り（十字キー）。
  function padRing(prefix, cx, cy, r, center) {
    PAD_DIRS.forEach(function (d) {
      var rad = (d[3] * Math.PI) / 180;
      var x = Math.round(cx + Math.cos(rad) * r - 13);
      var y = Math.round(cy - Math.sin(rad) * r - 11);
      var key = prefix === "Hat." ? "Hat." + d[0] : prefix + d[1];
      padAdd(key, x, y, 26, 22, d[2]);
    });
    if (center) {
      padAdd(center, cx - 17, cy - 11, 34, 22, "押");
    }
  }
  padRing("Direction.", 84, 96, 40, "LCLICK");
  padRing("Hat.", 150, 196, 36, null);
  padRing("Direction.R_", 270, 196, 40, "RCLICK");
  padAdd("X", 336, 54, 30, 28, "X");
  padAdd("Y", 302, 84, 30, 28, "Y");
  padAdd("A", 370, 84, 30, 28, "A");
  padAdd("B", 336, 114, 30, 28, "B");

  // 値（書式つき）→ 識別子。press の素の名もボタン扱いに揃える。
  function padKeyOf(value) {
    var v = String(value == null ? "" : value);
    if (v.indexOf("Button.") === 0) {
      return v.slice(7);
    }
    return v;
  }
  // 識別子 → 値。bare は press 欄の書式（ボタンは素の名）。
  function padValueOf(key, bare) {
    if (key.indexOf("Hat.") === 0 || key.indexOf("Direction.") === 0) {
      return key;
    }
    return bare ? key : "Button." + key;
  }
  function padLabelOf(value) {
    var label = PAD_LABELS[padKeyOf(value)];
    return label == null ? String(value) : label;
  }

  Blockly.PokeconInput = {
    LAYOUT: PAD_LAYOUT,
    KEYS: PAD_KEYS,
    keyOf: padKeyOf,
    labelOf: padLabelOf,
  };

  class ControllerField extends Blockly.FieldDropdown {
    constructor(value, bare) {
      var isBare = !!bare;
      super(
        PAD_KEYS.map(function (k) {
          return [PAD_LABELS[k], padValueOf(k, isBare)];
        }),
      );
      this.bare_ = isBare;
      this.setValue(value != null ? String(value) : padValueOf("A", isBare));
    }
    static fromJson(options) {
      return new ControllerField(
        options ? options.value : undefined,
        options && options.style === "bare",
      );
    }
    // コントローラの絵から選ぶ。描画（DropDownDiv）が無い環境では標準の一覧に落とす。
    showEditor_(e) {
      var DDD = Blockly.DropDownDiv;
      if (!DDD || typeof document === "undefined" || !document.createElement) {
        return super.showEditor_(e);
      }
      var field = this;
      var bare = this.bare_;
      var current = padKeyOf(this.getValue());
      DDD.clearContent();
      var content = DDD.getContentDiv();
      var box = document.createElement("div");
      box.className = "pokeconPad";
      box.setAttribute("role", "group");
      box.setAttribute("aria-label", "コントローラから入力を選ぶ");
      box.style.cssText =
        "position:relative;width:" + PAD_W + "px;height:" + PAD_H + "px;" +
        "background:#2f3237;border-radius:40px 40px 70px 70px;font:13px/1 'Segoe UI','Yu Gothic UI',sans-serif;";
      var groups = [
        ["左スティック", 84, 166],
        ["十字キー", 150, 266],
        ["右スティック", 270, 266],
      ];
      groups.forEach(function (g) {
        var t = document.createElement("span");
        t.textContent = g[0];
        t.style.cssText =
          "position:absolute;left:" + (g[1] - 40) + "px;top:" + (g[2] - 12) +
          "px;width:80px;text-align:center;color:#c9ccd1;font-size:11px;pointer-events:none;";
        box.appendChild(t);
      });
      var currentBtn = null;
      PAD_LAYOUT.forEach(function (it) {
        var b = document.createElement("button");
        b.type = "button";
        b.textContent = it.text;
        b.title = PAD_LABELS[it.key];
        b.setAttribute("aria-label", PAD_LABELS[it.key]);
        var on = it.key === current;
        b.setAttribute("aria-pressed", on ? "true" : "false");
        b.style.cssText =
          "position:absolute;left:" + it.x + "px;top:" + it.y + "px;width:" + it.w +
          "px;height:" + it.h + "px;min-height:0;min-width:0;padding:0;border-radius:7px;cursor:pointer;" +
          "border:1px solid " + (on ? "#ffd166" : "#5b6068") + ";" +
          "background:" + (on ? "#ffd166" : "#43474e") + ";color:" + (on ? "#1f2328" : "#f1f3f5") +
          ";font-weight:600;font-size:" + (it.text.length > 2 ? 12 : 14) + "px;";
        b.addEventListener("click", function () {
          field.setValue(padValueOf(it.key, bare));
          DDD.hideIfOwner(field, true);
        });
        if (on) {
          currentBtn = b;
        }
        box.appendChild(b);
      });
      // 打鍵でも選べる（A/B/X/Y/L/R と矢印＝十字キー）。
      box.addEventListener("keydown", function (ev) {
        var k = String(ev.key || "");
        var map = {
          a: "A", b: "B", x: "X", y: "Y", l: "L", r: "R",
          ArrowUp: "Hat.TOP", ArrowDown: "Hat.BTM", ArrowLeft: "Hat.LEFT", ArrowRight: "Hat.RIGHT",
        };
        var key = map[k] || map[k.toLowerCase()];
        if (!key) {
          return;
        }
        ev.preventDefault();
        field.setValue(padValueOf(key, bare));
        DDD.hideIfOwner(field, true);
      });
      var hint = document.createElement("div");
      hint.textContent = "クリックで選ぶ（A/B/X/Y/L/R キー・矢印＝十字キーでも可）";
      hint.style.cssText = "padding:6px 4px 0;color:#5c636b;font-size:11px;";
      content.appendChild(box);
      content.appendChild(hint);
      DDD.setColour("#ffffff", "#d6d9de");
      DDD.showPositionedByField(this, function () {});
      setTimeout(function () {
        try {
          (currentBtn || box.querySelector("button")).focus();
        } catch (err) {
          /* 焦点が移せなくても選べる */
        }
      }, 0);
    }
  }
  Blockly.fieldRegistry.register("field_controller", ControllerField);

  Blockly.defineBlocksWithJsonArray([
    {
      type: "pokecon_program",
      message0: "プログラム %1 タグ %2 %3",
      args0: [
        { type: "field_input", name: "NAME", text: "ブロック作成" },
        { type: "field_input", name: "TAGS", text: "blockly" },
        { type: "input_statement", name: "DO" },
      ],
      colour: 120,
      tooltip:
        "コマンド本体（1個まで）。名前がPokeConの一覧に出る。中に操作を上から順に並べる。タグはカンマ区切り。",
    },
    {
      type: "pokecon_press",
      message0: "%1 を押す 長さ %2 秒 待ち %3 秒",
      args0: [
        { type: "field_controller", name: "BUTTON", style: "bare", value: "A" },
        { type: "field_number", name: "DURATION", value: 0.1, min: 0, max: 10 },
        { type: "field_number", name: "WAIT", value: 0.1, min: 0, max: 60 },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 160,
      tooltip: "ボタン・十字キーを押す。スティックの方向を選ぶと、その向きへ倒して戻す。",
    },
    {
      type: "pokecon_stick",
      message0: "スティック %1 %2 角度 %3 長さ %4 秒 待ち %5 秒",
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
      tooltip: "指定した秒数だけ何もせずに待つ。",
    },
    {
      type: "pokecon_elapsed",
      message0: "開始からの秒数",
      args0: [],
      output: "Number",
      colour: 120,
      tooltip: "開始からの経過秒。ifとfinishで時間制限に使う。",
    },
    {
      type: "pokecon_hold",
      message0: "%1 を押し続ける 待ち %2 秒",
      args0: [
        { type: "field_controller", name: "TARGET", value: "Button.A" },
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
        { type: "field_controller", name: "TARGET", value: "Button.A" },
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
      colour: 120,
      tooltip: "コマンドを正常終了する（色違い検出時など）。",
    },
    {
      type: "pokecon_press_rep",
      message0: "%1 を %2 回押す 長さ %3 秒 間隔 %4 秒 待ち %5 秒",
      args0: [
        { type: "field_controller", name: "TARGET", value: "Button.A" },
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
      colour: 40,
      tooltip: "ログへ出す（表示＝進捗・結果＝後で見返す用）。",
    },
    {
      type: "pokecon_screenshot",
      message0: "スクショを撮る",
      args0: [],
      previousStatement: null,
      nextStatement: null,
      colour: 40,
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
      colour: 40,
      tooltip: "Discordへ通知する。画像付きは画像認識ありになる。",
    },
    {
      type: "pokecon_dialog_choice",
      message0: "設定 %1 題 %2 項目 %3 選択肢 %4 既定 %5",
      args0: [
        { type: "field_input", name: "VAR", text: "setting" },
        { type: "field_input", name: "TITLE", text: "設定" },
        { type: "field_input", name: "LABEL", text: "項目" },
        { type: "field_input", name: "OPTIONS", text: "A,B" },
        { type: "field_input", name: "DEFAULT", text: "A" },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 40,
      tooltip: "実行前に選択肢から選ばせる。取消は終了する。",
    },
    {
      type: "pokecon_dialog_number",
      message0: "設定数値 %1 題 %2 項目 %3 最小 %4 最大 %5 既定 %6",
      args0: [
        { type: "field_input", name: "VAR", text: "count" },
        { type: "field_input", name: "TITLE", text: "設定" },
        { type: "field_input", name: "LABEL", text: "個数" },
        { type: "field_number", name: "MIN", value: 1 },
        { type: "field_number", name: "MAX", value: 10 },
        { type: "field_number", name: "DEFAULT", value: 3 },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 40,
      tooltip: "実行前に数値を選ばせる。取消は終了する。",
    },
    {
      type: "pokecon_dialog_check",
      message0: "設定確認 %1 題 %2 項目 %3 既定 %4",
      args0: [
        { type: "field_input", name: "VAR", text: "confirm" },
        { type: "field_input", name: "TITLE", text: "確認" },
        { type: "field_input", name: "LABEL", text: "送る" },
        { type: "field_checkbox", name: "DEFAULT", checked: true },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 40,
      tooltip: "実行前に確認を取る。取消は終了する。",
    },
    {
      type: "pokecon_audio_tone_contains",
      message0: "音 %1〜%2Hz が閾値 %3 を超えた",
      args0: [
        { type: "field_number", name: "LO", value: 3000, min: 0, max: 22050 },
        { type: "field_number", name: "HI", value: 3200, min: 0, max: 22050 },
        { type: "field_number", name: "THRESH", value: 1000000 },
      ],
      message1: "第2帯域 %1〜%2Hz 閾値 %3（0〜0で使わない）",
      args1: [
        { type: "field_number", name: "LO2", value: 0, min: 0, max: 22050 },
        { type: "field_number", name: "HI2", value: 0, min: 0, max: 22050 },
        { type: "field_number", name: "THRESH2", value: 0 },
      ],
      inputsInline: false,
      output: "Boolean",
      colour: 20,
      tooltip: "指定帯域の音量が閾値を超えたら真（要調整）。第2は0,0で使わない。",
    },
    {
      type: "pokecon_audio_wait_tone",
      message0: "音 %1〜%2Hz を待つ 閾値 %3 上限 %4 秒",
      args0: [
        { type: "field_number", name: "LO", value: 3000, min: 0, max: 22050 },
        { type: "field_number", name: "HI", value: 3200, min: 0, max: 22050 },
        { type: "field_number", name: "THRESH", value: 1000000 },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
      ],
      message1: "第2帯域 %1〜%2Hz 閾値 %3（0〜0で使わない）",
      args1: [
        { type: "field_number", name: "LO2", value: 0, min: 0, max: 22050 },
        { type: "field_number", name: "HI2", value: 0, min: 0, max: 22050 },
        { type: "field_number", name: "THRESH2", value: 0 },
      ],
      inputsInline: false,
      previousStatement: null,
      nextStatement: null,
      colour: 20,
      tooltip: "指定帯域の音が鳴るまで待つ（要調整）。第2は0,0で使わない。",
    },
    {
      type: "pokecon_vision_press_until",
      message0: "画像 %1 %2 が出るまで %3 を押す 上限 %4 秒",
      args0: [
        { type: "field_template", name: "TEMPLATE", value: "" },
        {
          type: "field_image",
          name: "PREVIEW",
          src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVQIHWMAAQAABQABim28IAAAAABJRU5ErkJggg==",
          width: 64,
          height: 48,
          alt: "*",
        },
        { type: "field_controller", name: "TARGET", value: "Button.A" },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
      ],
      message1: "閾値 %1 範囲 %2 グレー %3 %4",
      args1: [
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_crop", name: "CROP", text: "" },
        { type: "field_checkbox", name: "USE_GRAY", checked: false },
        { type: "field_capopen", name: "CAPOPEN", text: "📷 調整" },
      ],
      inputsInline: false,
      extensions: ["pokecon_template_preview"],
      previousStatement: null,
      nextStatement: null,
      colour: 195,
      tooltip:
        "テンプレ画像が出るまで、ボタンを押し続ける（A連打で戦闘開始を待つ等）。上限秒で打ち切る。",
    },
    {
      type: "pokecon_vision_press_until_gone",
      message0: "画像 %1 %2 が消えるまで %3 を押す 上限 %4 秒",
      args0: [
        { type: "field_template", name: "TEMPLATE", value: "" },
        {
          type: "field_image",
          name: "PREVIEW",
          src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVQIHWMAAQAABQABim28IAAAAABJRU5ErkJggg==",
          width: 64,
          height: 48,
          alt: "*",
        },
        { type: "field_controller", name: "TARGET", value: "Button.A" },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
      ],
      message1: "閾値 %1 範囲 %2 グレー %3 %4",
      args1: [
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_crop", name: "CROP", text: "" },
        { type: "field_checkbox", name: "USE_GRAY", checked: false },
        { type: "field_capopen", name: "CAPOPEN", text: "📷 調整" },
      ],
      inputsInline: false,
      extensions: ["pokecon_template_preview"],
      previousStatement: null,
      nextStatement: null,
      colour: 195,
      tooltip:
        "テンプレ画像が消えるまで、ボタンを押し続ける。上限秒で打ち切る。",
    },
    {
      type: "pokecon_vision_wait_count",
      message0: "画像 %1 %2 が %3 個出るまで待つ 上限 %4 秒",
      args0: [
        { type: "field_template", name: "TEMPLATE", value: "" },
        {
          type: "field_image",
          name: "PREVIEW",
          src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVQIHWMAAQAABQABim28IAAAAABJRU5ErkJggg==",
          width: 64,
          height: 48,
          alt: "*",
        },
        { type: "field_number", name: "COUNT", value: 2, min: 1, max: 100 },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
      ],
      message1: "閾値 %1 範囲 %2 グレー %3 %4",
      args1: [
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_crop", name: "CROP", text: "" },
        { type: "field_checkbox", name: "USE_GRAY", checked: false },
        { type: "field_capopen", name: "CAPOPEN", text: "📷 調整" },
      ],
      inputsInline: false,
      extensions: ["pokecon_template_preview"],
      previousStatement: null,
      nextStatement: null,
      colour: 195,
      tooltip:
        "テンプレ画像が指定の個数見つかるまで待つ（卵の数え上げ等）。上限秒で打ち切る。",
    },
    {
      type: "pokecon_vision_count",
      message0: "画像 %1 %2 の個数",
      args0: [
        { type: "field_template", name: "TEMPLATE", value: "" },
        {
          type: "field_image",
          name: "PREVIEW",
          src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVQIHWMAAQAABQABim28IAAAAABJRU5ErkJggg==",
          width: 64,
          height: 48,
          alt: "*",
        },
      ],
      message1: "閾値 %1 範囲 %2 グレー %3 %4",
      args1: [
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_crop", name: "CROP", text: "" },
        { type: "field_checkbox", name: "USE_GRAY", checked: false },
        { type: "field_capopen", name: "CAPOPEN", text: "📷 調整" },
      ],
      inputsInline: false,
      extensions: ["pokecon_template_preview"],
      output: "Number",
      colour: 195,
      tooltip:
        "画面内でテンプレ画像が見つかった個数（数値）。",
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
      extensions: ["pokecon_sub_rename"],
      colour: 290,
      tooltip: "トップレベルに置く。呼ぶ側から self.名前() で呼べる。",
    },
    {
      type: "pokecon_sub_call",
      message0: "呼ぶ %1 引数 %2 %3 %4",
      args0: [
        { type: "field_subname", name: "NAME", value: "my_sub" },
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
        { type: "field_subname", name: "NAME", value: "my_sub" },
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
      colour: "#8a8f98",
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

  // 範囲欄。空は画面全体の意味のため「全体」と見せる。書式は "x1,y1,x2,y2"
  // （実画素・x2>x1・y2>y1）。生成側は不正な書式を黙って落とすため、
  // 入口で受け付けない（打ち込み中は赤枠、確定時に直前の値へ戻る）。
  var CROP_TEXT_RE = /^(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)$/;
  class CropField extends Blockly.FieldTextInput {
    static fromJson(options) {
      return new CropField(options && options.text != null ? options.text : "");
    }
    doClassValidation_(newValue) {
      var t = String(newValue == null ? "" : newValue).trim();
      if (!t) {
        return "";
      }
      var m = t.match(CROP_TEXT_RE);
      if (!m || !(+m[3] > +m[1] && +m[4] > +m[2])) {
        return null;
      }
      return [+m[1], +m[2], +m[3], +m[4]].join(",");
    }
    getText_() {
      // 編集中は入力中の文字をそのまま出す（親の振る舞い）。
      var editing = super.getText_();
      if (editing != null) {
        return editing;
      }
      var v = this.getValue();
      return v ? String(v) : "全体";
    }
  }
  Blockly.fieldRegistry.register("field_crop", CropField);

  // 画像（テンプレ）欄。候補は editor.html が ./templates で配る一覧。
  // 候補外の値も受け付ける：一覧の到着前に開いた・画像を改名した等で
  // 保存値が候補に無くても、読込で黙って別の値へ書き換えない
  // （存在しない画像は保存時の参照検査が止める）。空は未選択の意味。
  var TEMPLATE_UNSET_LABEL = "（画像を選ぶ）";
  class TemplateField extends Blockly.FieldDropdown {
    constructor(value) {
      super(function () {
        var list =
          typeof Blockly.PokeconTemplates !== "undefined" &&
          Array.isArray(Blockly.PokeconTemplates)
            ? Blockly.PokeconTemplates
            : [];
        var opts = list.map(function (n) {
          return [n, n];
        });
        var cur = this.getValue();
        if (
          cur &&
          !opts.some(function (o) {
            return o[1] === cur;
          })
        ) {
          opts.unshift([cur, cur]);
        }
        opts.push([TEMPLATE_UNSET_LABEL, ""]);
        return opts;
      });
      this.setValue(value == null ? "" : String(value));
    }
    static fromJson(options) {
      return new TemplateField(options ? options.value : undefined);
    }
    doClassValidation_(newValue) {
      return newValue == null ? null : String(newValue);
    }
    // 候補の作り直し前でも、今の値をそのまま見せる。
    getText_() {
      var v = this.getValue();
      return v ? String(v) : TEMPLATE_UNSET_LABEL;
    }
  }
  Blockly.fieldRegistry.register("field_template", TemplateField);

  // サブルーチン呼出の名前欄。ワークスペースの定義名から選ぶ（打ち間違い防止）。
  // 候補外の値も受け付ける：読込で定義より先に呼出が来ても名前を失わない
  // （未定義の呼出は保存時の検査が止める）。
  function subDefNames(ws) {
    var names = [];
    if (!ws || typeof ws.getAllBlocks !== "function") {
      return names;
    }
    ws.getAllBlocks(false).forEach(function (d) {
      if (d.type !== "pokecon_sub_def") {
        return;
      }
      var n = String(d.getFieldValue("NAME") || "").trim();
      if (n && names.indexOf(n) === -1) {
        names.push(n);
      }
    });
    names.sort();
    return names;
  }
  class SubNameField extends Blockly.FieldDropdown {
    constructor(value) {
      super(function () {
        var b = this.getSourceBlock();
        var names = subDefNames(b ? b.workspace : null);
        var cur = this.getValue();
        if (cur && names.indexOf(cur) === -1) {
          names.unshift(cur);
        }
        if (!names.length) {
          names.push("my_sub");
        }
        return names.map(function (n) {
          return [n, n];
        });
      });
      if (value != null && String(value).trim()) {
        this.setValue(String(value).trim());
      }
    }
    static fromJson(options) {
      return new SubNameField(options ? options.value : undefined);
    }
    doClassValidation_(newValue) {
      var t = String(newValue == null ? "" : newValue).trim();
      return t ? t : null;
    }
    // 候補の作り直し前でも、今の値をそのまま見せる。
    getText_() {
      var v = this.getValue();
      return v == null ? "" : String(v);
    }
  }
  Blockly.fieldRegistry.register("field_subname", SubNameField);

  // 定義の名前を利用者が変えたら、同じ名前を呼んでいる呼出も追従させる。
  // 読込（作成事象）では動かさない：読込途中の既定名で無関係な呼出を
  // 書き換えないよう、名前欄の変更事象だけを見る。
  Blockly.Extensions.register("pokecon_sub_rename", function () {
    this.setOnChange(function (e) {
      if (
        !e ||
        e.type !== Blockly.Events.BLOCK_CHANGE ||
        e.blockId !== this.id ||
        e.element !== "field" ||
        e.name !== "NAME" ||
        this.isInFlyout
      ) {
        return;
      }
      var oldName = String(e.oldValue == null ? "" : e.oldValue).trim();
      var newName = String(e.newValue == null ? "" : e.newValue).trim();
      if (!oldName || !newName || oldName === newName || !this.workspace) {
        return;
      }
      // 同名の定義がまだ残っているなら、呼出はそちらを指しているとみなす。
      if (subDefNames(this.workspace).indexOf(oldName) !== -1) {
        return;
      }
      // 追従は利用者の改名と同じ事象グループに入れ、「元に戻す」1回で両方戻す。
      var prevGroup = Blockly.Events.getGroup();
      Blockly.Events.setGroup(e.group || prevGroup || true);
      try {
        this.workspace.getAllBlocks(false).forEach(function (c) {
          if (
            (c.type === "pokecon_sub_call" || c.type === "pokecon_sub_call_value") &&
            c.getFieldValue("NAME") === oldName
          ) {
            c.setFieldValue(newName, "NAME");
          }
        });
      } finally {
        Blockly.Events.setGroup(prevGroup);
      }
    });
  });

  Blockly.defineBlocksWithJsonArray([
    {
      type: "pokecon_vision_contains",
      message0: "画像 %1 %2 がある",
      args0: [
        { type: "field_template", name: "TEMPLATE", value: "" },
        {
          type: "field_image",
          name: "PREVIEW",
          src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVQIHWMAAQAABQABim28IAAAAABJRU5ErkJggg==",
          width: 64,
          height: 48,
          alt: "*",
        },
      ],
      message1: "閾値 %1 範囲 %2 グレー %3 %4",
      args1: [
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_crop", name: "CROP", text: "" },
        { type: "field_checkbox", name: "USE_GRAY", checked: false },
        { type: "field_capopen", name: "CAPOPEN", text: "📷 調整" },
      ],
      inputsInline: false,
      extensions: ["pokecon_template_preview"],
      output: "Boolean",
      colour: 195,
      tooltip:
        "画面にテンプレ画像があれば真。「もし」の条件に使う。📷 調整で実画面を見ながら範囲・閾値を決められる。",
    },
    {
      type: "pokecon_vision_wait_appear",
      message0: "画像 %1 %2 が出るまで待つ 上限 %3 秒",
      args0: [
        { type: "field_template", name: "TEMPLATE", value: "" },
        {
          type: "field_image",
          name: "PREVIEW",
          src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVQIHWMAAQAABQABim28IAAAAABJRU5ErkJggg==",
          width: 64,
          height: 48,
          alt: "*",
        },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
      ],
      message1: "閾値 %1 範囲 %2 グレー %3 %4",
      args1: [
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_crop", name: "CROP", text: "" },
        { type: "field_checkbox", name: "USE_GRAY", checked: false },
        { type: "field_capopen", name: "CAPOPEN", text: "📷 調整" },
      ],
      inputsInline: false,
      extensions: ["pokecon_template_preview"],
      previousStatement: null,
      nextStatement: null,
      colour: 195,
      tooltip:
        "テンプレ画像が出るまで待つ。上限秒で打ち切る。",
    },
    {
      type: "pokecon_vision_wait_gone",
      message0: "画像 %1 %2 が消えるまで待つ 上限 %3 秒",
      args0: [
        { type: "field_template", name: "TEMPLATE", value: "" },
        {
          type: "field_image",
          name: "PREVIEW",
          src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVQIHWMAAQAABQABim28IAAAAABJRU5ErkJggg==",
          width: 64,
          height: 48,
          alt: "*",
        },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
      ],
      message1: "閾値 %1 範囲 %2 グレー %3 %4",
      args1: [
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_crop", name: "CROP", text: "" },
        { type: "field_checkbox", name: "USE_GRAY", checked: false },
        { type: "field_capopen", name: "CAPOPEN", text: "📷 調整" },
      ],
      inputsInline: false,
      extensions: ["pokecon_template_preview"],
      previousStatement: null,
      nextStatement: null,
      colour: 195,
      tooltip:
        "テンプレ画像が消えるまで待つ。上限秒で打ち切る。",
    },
    {
      type: "pokecon_vision_position",
      message0: "画像 %1 %2 の位置",
      args0: [
        { type: "field_template", name: "TEMPLATE", value: "" },
        {
          type: "field_image",
          name: "PREVIEW",
          src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVQIHWMAAQAABQABim28IAAAAABJRU5ErkJggg==",
          width: 64,
          height: 48,
          alt: "*",
        },
      ],
      message1: "閾値 %1 範囲 %2 グレー %3 %4",
      args1: [
        { type: "field_number", name: "THRESHOLD", value: 0.7, min: 0, max: 1 },
        { type: "field_crop", name: "CROP", text: "" },
        { type: "field_checkbox", name: "USE_GRAY", checked: false },
        { type: "field_capopen", name: "CAPOPEN", text: "📷 調整" },
      ],
      inputsInline: false,
      extensions: ["pokecon_template_preview"],
      output: null,
      colour: 195,
      tooltip:
        "テンプレ画像が見つかった位置。",
    },
    {
      type: "pokecon_vision_wait_stable",
      message0: "画面が止まるまで待つ 静止 %1 秒 上限 %2 秒",
      args0: [
        { type: "field_number", name: "QUIET", value: 0.5, min: 0, max: 60 },
        { type: "field_number", name: "TIMEOUT", value: 10, min: 0, max: 3600 },
      ],
      previousStatement: null,
      nextStatement: null,
      colour: 195,
      tooltip: "画面の変化が「静止」秒だけ続けて止まるまで待つ（暗転・演出の終わり待ち等）。",
    },
    {
      type: "pokecon_vision_color",
      message0: "色 下限 H %1 S %2 V %3 〜 上限 H %4 S %5 V %6",
      args0: [
        { type: "field_number", name: "H1", value: 0, min: 0, max: 179 },
        { type: "field_number", name: "S1", value: 0, min: 0, max: 255 },
        { type: "field_number", name: "V1", value: 0, min: 0, max: 255 },
        { type: "field_number", name: "H2", value: 179, min: 0, max: 179 },
        { type: "field_number", name: "S2", value: 255, min: 0, max: 255 },
        { type: "field_number", name: "V2", value: 255, min: 0, max: 255 },
      ],
      message1: "が割合 %1 以上 範囲 %2 %3",
      args1: [
        { type: "field_number", name: "RATIO", value: 0.6, min: 0, max: 1 },
        { type: "field_crop", name: "CROP", text: "" },
        { type: "field_capopen", name: "CAPOPEN", text: "📷 調整" },
      ],
      inputsInline: false,
      output: "Boolean",
      colour: 195,
      tooltip:
        "範囲内でHSVの色範囲に入る画素の割合が指定以上なら真。📷 調整で実画面を見ながら決められる。",
    },
  ]);

  // TEMPLATE欄の変更をダミーPREVIEW欄へ反映する。生成コードには触らない。
  // 欠損時は透明placeholderのままにする（保存は塞がない）。
  var PREVIEW_EMPTY =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVQIHWMAAQAABQABim28IAAAAABJRU5ErkJggg==";
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

  // 変数名を読めるまま出す。Blockly既定は非ASCIIを `_E5_9B_9E` のように潰すが、
  // Python 3 は日本語の識別子を受け付けるため、識別子に使えない文字だけを _ にする
  // （文字・10進数字・結合文字・_ を残す。先頭が数字なら my_ を付ける）。
  if (Blockly.Names && Blockly.Names.prototype) {
    Blockly.Names.prototype.safeName = function (name) {
      if (!name) {
        return (Blockly.Msg && Blockly.Msg.UNNAMED_KEY) || "unnamed";
      }
      // Python は識別子を NFKC で正規化して比べる（全角「ａ」と「a」は同じ名前）。
      // 先に正規化しておけば、重複名・予約語の判定もPythonと同じ形で効く。
      var s = String(name)
        .normalize("NFKC")
        .replace(/[^\p{L}\p{Nd}\p{Mn}\p{Mc}_]/gu, "_");
      if (/^[\p{Nd}\p{Mn}\p{Mc}]/u.test(s)) {
        s = "my_" + s;
      }
      return s;
    };
  }

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
      // 型検査で落ちるため）。字下げは do() 本体と同じ8桁にする。
      inner += "        return " + String(ret).trim() + "\n";
      return "    def " + name + "(" + sig + "):\n" + inner + "\n";
    }
    if (!inner) {
      inner = "        pass\n";
    }
    return "    def " + name + "(" + sig + ") -> None:\n" + inner + "\n";
  }

  // プログラム欄のタグ文面を読む。区切りは TagEditor と同じく , と 、。
  // 空・重複は落とし、1つも無ければ blockly 既定にする（旧保存物互換）。
  function programTags(block) {
    var raw = "";
    try {
      raw = block.getFieldValue("TAGS");
    } catch (e) {
      raw = "";
    }
    var tags = String(raw == null ? "" : raw)
      .split(/[,、]/)
      .map(function (p) {
        return String(p).trim();
      })
      .filter(function (p, i, arr) {
        return p.length > 0 && arr.indexOf(p) === i;
      });
    if (!tags.length) {
      tags = ["blockly"];
    }
    return tags;
  }

  pythonGenerator.forBlock["pokecon_program"] = function (block, generator) {
    var name = block.getFieldValue("NAME");
    var tagsLine =
      "    TAGS = [" +
      programTags(block)
        .map(function (t) {
          return pyStr(t);
        })
        .join(", ") +
      "]\n";
    // 試し実行（一部だけ）のときは、編集画面が do() の中身を差し替える。
    var body =
      typeof generator.pokeconBodyOverride === "function"
        ? generator.pokeconBodyOverride(generator)
        : generator.statementToCode(block, "DO");
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
    // キーの名前はコードの部分だけで探す（コメント・文字列に書いた「Hat.」等で
    // 使っていない import を足さない）。文字列とコメントは左から1回で見分ける
    // （先にコメントを消すと、文字列中の「#」以降のキー名まで消えて import が欠ける）。
    var codeOnly = combined.replace(
      /'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|#[^\n]*/g,
      function (m) {
        return m.charAt(0) === "#" ? "" : "''";
      },
    );
    // スティックを使うときだけ Direction・Stick を足す（未使用のimportを出さない）。
    // スティックの方向（Direction.UP 等）は呼び括弧を持たないため . も見る。
    var useStick = /\bDirection\s*[\(.]/.test(codeOnly);
    var useButton = /\bButton\s*\./.test(codeOnly);
    // 十字キー（Hat.TOP 等）を使うときだけ Hat を足す。
    var useHat = /\bHat\s*\./.test(codeOnly);
    // 経過時間を使うときだけ time を足し、do() 先頭で起点を取る。
    var useTime = /_blockly_t0/.test(combined);
    // 乱数を使うときだけ random を足す（未使用のimportを出さない）。
    // 本体側も同名で足すため、こちらへ一本化する（二重化防止）。
    var useRandom = /random\./.test(combined);
    if (useRandom && generator.definitions_) {
      delete generator.definitions_["import_random"];
    }
    if (useTime) {
      inner = "        self._blockly_t0 = time.time()\n" + inner;
    }
    // 自前のimport群はdefinitions_へ寄せる。finish()がimport正規表現で
    // 判別して変数初期化より先に出すため、順序が保たれる。
    // （従来どおり先頭へ出る。変数を使うと初期化がimport群の後へ回る。）
    var baseImport = "from Commands.PythonCommandBase import PythonCommand\n";
    if (needImageProc && useAudio) {
      // 画像＋音声の併用。実行側の ImageProc 分岐に載り、音声源は属性で渡る。
      baseImport =
        "from Commands.PythonCommandBase import ImageProcAudioPythonCommand\n";
    } else if (useAudio) {
      // 音声のみ。実行側の Audio 分岐が音声源を位置引数で渡す。
      baseImport = "from Commands.PythonCommandBase import AudioPythonCommand\n";
    } else if (needImageProc) {
      // press系と混ぜても `Button` が未定義にならないよう、vision側にも付ける。
      baseImport =
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n";
    }
    var stdImports =
      (useRandom ? "import random\n" : "") + (useTime ? "import time\n" : "");
    var keyNames = [];
    if (useButton) {
      keyNames.push("Button");
    }
    if (useStick) {
      keyNames.push("Direction", "Stick");
    }
    if (useHat) {
      keyNames.push("Hat");
    }
    keyNames.sort();
    var keysLine =
      keyNames.length > 0
        ? "from Commands.Keys import " + keyNames.join(", ") + "\n"
        : "";
    generator.definitions_["pokecon_imports"] =
      (stdImports ? stdImports + "\n" : "") + keysLine + baseImport;
    var head;
    if (needImageProc && useAudio) {
      // 画像＋音声の併用。実行側の ImageProc 分岐に載り、音声源は属性で渡る。
      head =
        "class BlocklyCmd(ImageProcAudioPythonCommand):\n" +
        "    NAME = " +
        pyStr(name) +
        "\n" +
        tagsLine +
        "\n" +
        "    def __init__(self, cam, gui=None, audio=None):\n" +
        "        super().__init__(cam, gui, audio)\n" +
        "\n" +
        "    def do(self) -> None:\n";
    } else if (useAudio) {
      // 音声のみ。実行側の Audio 分岐が音声源を位置引数で渡す。
      head =
        "class BlocklyCmd(AudioPythonCommand):\n" +
        "    NAME = " +
        pyStr(name) +
        "\n" +
        tagsLine +
        "\n" +
        "    def __init__(self, audio=None):\n" +
        "        super().__init__(audio)\n" +
        "\n" +
        "    def do(self) -> None:\n";
    } else if (needImageProc) {
      // press系と混ぜても `Button` が未定義にならないよう、vision側にも付ける。
      head =
        "class BlocklyCmd(ImageProcPythonCommand):\n" +
        "    NAME = " +
        pyStr(name) +
        "\n" +
        tagsLine +
        "\n" +
        "    def __init__(self, cam, gui=None):\n" +
        "        super().__init__(cam, gui)\n" +
        "\n" +
        "    def do(self) -> None:\n";
    } else {
      head =
        "class BlocklyCmd(PythonCommand):\n" +
        "    NAME = " +
        pyStr(name) +
        "\n" +
        tagsLine +
        "\n" +
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
    // 方向（スティックの Direction.・十字キーの Hat.）はそのまま、ボタンは Button. を付ける。
    var target =
      String(btn).indexOf("Direction.") === 0 || String(btn).indexOf("Hat.") === 0
        ? btn
        : "Button." + btn;
    var dur = block.getFieldValue("DURATION");
    var wait = block.getFieldValue("WAIT");
    return (
      "self.press(" +
      target +
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

  pythonGenerator.forBlock["pokecon_elapsed"] = function (block, generator) {
    return ["(time.time() - self._blockly_t0)", generator.ORDER_ATOMIC];
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

  // 設定ダイアログ。受け変数へ入れ、取消は終了する。
  // 戻りはlistのため [0] で取り出す。数値・確認は型を寄せる。
  function dialogAssign(block, varName, spec, takeFirst) {
    return (
      varName +
      " = self.dialogue6widget(" +
      pyStr(block.getFieldValue("TITLE")) +
      ", [" +
      spec +
      "])\n" +
      "if " +
      varName +
      " is None:\n" +
      '    self.print2("取り消しました。")\n' +
      "    self.finish()\n" +
      "    " +
      varName +
      " = []\n" +
      "else:\n" +
      "    " +
      varName +
      " = " +
      takeFirst +
      "\n"
    );
  }

  function dialogVar(block) {
    var v = "";
    try {
      v = block.getFieldValue("VAR");
    } catch (e) {
      v = "";
    }
    return String(v || "").trim() || "setting";
  }

  pythonGenerator.forBlock["pokecon_dialog_choice"] = function (block) {
    var varName = dialogVar(block);
    var options = String(block.getFieldValue("OPTIONS") || "")
      .split(",")
      .map(function (p) {
        return pyStr(String(p).trim());
      })
      .join(", ");
    var spec =
      '["combo", ' +
      pyStr(block.getFieldValue("LABEL")) +
      ", [" +
      options +
      "], " +
      pyStr(block.getFieldValue("DEFAULT")) +
      "]";
    return dialogAssign(block, varName, spec, varName + "[0]");
  };

  pythonGenerator.forBlock["pokecon_dialog_number"] = function (block) {
    var varName = dialogVar(block);
    var spec =
      '["spin", ' +
      pyStr(block.getFieldValue("LABEL")) +
      ", list(map(str, range(" +
      block.getFieldValue("MIN") +
      ", " +
      block.getFieldValue("MAX") +
      " + 1))), " +
      pyStr(String(block.getFieldValue("DEFAULT"))) +
      "]";
    return dialogAssign(block, varName, spec, "int(" + varName + "[0])");
  };

  pythonGenerator.forBlock["pokecon_dialog_check"] = function (block) {
    var varName = dialogVar(block);
    var def = block.getFieldValue("DEFAULT") === "TRUE" ? "True" : "False";
    var spec =
      '["check", ' + pyStr(block.getFieldValue("LABEL")) + ", " + def + "]";
    return dialogAssign(block, varName, spec, "bool(" + varName + "[0])");
  };

  // 第2帯域は 0 < LO2 < HI2 のときだけ付ける（0,0で単帯域・旧保存物互換）。
  function audioToneArgs(block) {
    var bands =
      "[(" +
      block.getFieldValue("LO") +
      ", " +
      block.getFieldValue("HI") +
      ")]";
    var threshs = "[" + block.getFieldValue("THRESH") + "]";
    var lo2 = Number(block.getFieldValue("LO2"));
    var hi2 = Number(block.getFieldValue("HI2"));
    if (isFinite(lo2) && isFinite(hi2) && lo2 > 0 && lo2 < hi2) {
      bands =
        "[(" +
        block.getFieldValue("LO") +
        ", " +
        block.getFieldValue("HI") +
        "), (" +
        block.getFieldValue("LO2") +
        ", " +
        block.getFieldValue("HI2") +
        ")]";
      threshs =
        "[" +
        block.getFieldValue("THRESH") +
        ", " +
        block.getFieldValue("THRESH2") +
        "]";
    }
    return bands + ", " + threshs;
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
