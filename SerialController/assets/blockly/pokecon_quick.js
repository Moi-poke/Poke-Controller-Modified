// クイック追加の裏側の判定（描画・DOMなし）。editor.html の窓から使い、
// 判定そのものは node の vm と本物の Blockly で確かめる。
// 打鍵だけで組み立てるため、道具箱の項目に読み・別名を付けて引けるようにする
// （道具箱の分類・影ブロックはそのまま使い、保存物の形は変えない）。
(function () {
  "use strict";

  if (typeof Blockly === "undefined") {
    return;
  }

  // 型ごとの読み・別名。見出しは漢字まじりのため、ひらがな・英語の
  // 問い合わせはここで受ける（検索時の正規化と対になる）。
  // 依頼書の最低限（おす・まつ・くりかえし・もし・がぞう・すてぃっく・
  // ひょうじ・へんすう・さぶるーちん）をすべて含む。
  var PRESS_WORDS = ["おす", "ぼたん", "press"];
  var WAIT_WORDS = ["まつ", "wait"];
  var REPEAT_WORDS = ["くりかえし", "ループ", "loop", "repeat"];
  var VISION_WORDS = ["がぞう", "テンプレ", "image", "にんしき"];
  var TONE_WORDS = ["おと", "音", "sound", "tone"];
  var FUNC_WORDS = ["さぶるーちん", "関数", "function", "かんすう"];
  var KEYWORDS = {
    pokecon_program: ["ぷろぐらむ", "program"],
    pokecon_press: PRESS_WORDS.concat(["おし"]),
    pokecon_press_rep: PRESS_WORDS.concat(["れんだ", "連打"]),
    pokecon_hold: PRESS_WORDS.concat(["hold", "おしつづける", "押し続け"]),
    pokecon_hold_end: ["ぼたん", "press", "はなす", "離す", "hold"],
    pokecon_stick: ["すてぃっく", "stick", "倒す", "たおす"],
    pokecon_wait: WAIT_WORDS.concat(["たいき"]),
    pokecon_elapsed: ["けいか", "経過", "時間", "time", "elapsed",
      "じかんぎれ", "時間切れ"],
    pokecon_finish: ["おわる", "終了", "finish", "しゅうりょう",
      "じかんぎれ", "時間切れ"],
    pokecon_comment: ["こめんと", "comment", "めも"],
    pokecon_print: ["ひょうじ", "print", "ログ", "log", "表示"],
    pokecon_screenshot: ["すくしょ", "screenshot", "しゃしん"],
    pokecon_discord: ["discord", "つうち", "通知"],
    pokecon_dialog_choice: ["せってい", "設定", "setting", "入力"],
    pokecon_dialog_number: ["せってい", "設定", "setting", "入力", "すうち"],
    pokecon_dialog_check: ["せってい", "設定", "setting", "入力", "確認"],
    pokecon_audio_tone_contains: TONE_WORDS,
    pokecon_audio_wait_tone: WAIT_WORDS.concat(TONE_WORDS),
    pokecon_vision_contains: VISION_WORDS,
    pokecon_vision_wait_appear: VISION_WORDS.concat(WAIT_WORDS),
    pokecon_vision_wait_gone: VISION_WORDS.concat(WAIT_WORDS),
    pokecon_vision_press_until: VISION_WORDS.concat(WAIT_WORDS, PRESS_WORDS),
    pokecon_vision_press_until_gone: VISION_WORDS.concat(WAIT_WORDS, PRESS_WORDS),
    pokecon_vision_position: VISION_WORDS.concat(["位置", "いち"]),
    pokecon_vision_wait_stable: VISION_WORDS.concat(WAIT_WORDS, ["静止", "せいし"]),
    pokecon_vision_wait_count: VISION_WORDS.concat(WAIT_WORDS, ["個数", "こすう"]),
    pokecon_vision_count: VISION_WORDS.concat(["個数", "こすう", "かず"]),
    pokecon_vision_color: VISION_WORDS.concat(["いろ", "色", "color"]),
    pokecon_sub_def: FUNC_WORDS.concat(["定義", "ていぎ"]),
    pokecon_sub_call: FUNC_WORDS.concat(["呼ぶ", "よぶ"]),
    pokecon_sub_call_value: FUNC_WORDS.concat(["呼ぶ", "よぶ", "値", "あたい"]),
    controls_repeat_ext: REPEAT_WORDS,
    controls_whileUntil: REPEAT_WORDS.concat(["while", "あいだ"]),
    controls_for: REPEAT_WORDS.concat(["for", "かいすう", "回数"]),
    controls_if: ["もし", "じょうけん", "if", "条件", "分岐", "ぶんき",
      "じかんぎれ", "時間切れ"],
    controls_flow_statements: ["ちゅうだん", "中断", "break", "次へ", "continue"],
    logic_compare: ["ひかく", "比較", "logic", "条件"],
    logic_operation: ["ろんり", "logic", "かつ", "または", "and", "or"],
    logic_negate: ["ひてい", "否定", "not"],
    logic_boolean: ["しんぎ", "真偽", "true", "false"],
    math_number: ["すうじ", "数字", "number"],
    math_arithmetic: ["けいさん", "計算", "たしざん", "四則", "calc"],
    math_modulo: ["じょうよ", "剰余", "mod", "あまり"],
    math_change: ["かさん", "加算", "へんこう", "へんすう"],
    math_random_int: ["らんすう", "乱数", "random"],
    math_random_float: ["らんすう", "乱数", "random", "しょうすう"],
    text: ["もじ", "文字", "text", "string"],
    text_join: ["けつごう", "結合", "join", "もじ"],
    variables_set: ["へんすう", "変数", "variable"],
    variables_get: ["へんすう", "変数", "variable"],
  };

  function keywordsFor(type) {
    var found = KEYWORDS[type];
    if (found) {
      return found.slice();
    }
    return [String(type)];
  }

  // 素の項目か（型だけで欄・つなぎが無い形）。空の fields 等は
  // 将来混ざっても素とみなすよう中身の有無で見る。
  function hasKeys(v) {
    return !!v && typeof v === "object" && Object.keys(v).length > 0;
  }

  // 影だけの穴は未記入の目安であり、つなぎ済みとみなさない。
  // 実体のブロックを含む穴だけが「組み立て済み」の印になる。
  function inputsHaveRealBlock(inputs) {
    if (!inputs || typeof inputs !== "object") {
      return false;
    }
    return Object.keys(inputs).some(function (k) {
      var hole = inputs[k];
      return !!hole && typeof hole === "object" && !!hole.block;
    });
  }

  function isPlainState(st) {
    if (!st || typeof st !== "object") {
      return true;
    }
    return !(
      hasKeys(st.fields) ||
      inputsHaveRealBlock(st.inputs) ||
      hasKeys(st.next) ||
      hasKeys(st.extraState)
    );
  }

  // 挿入用の写し。kind は道具箱の目印で保存形には無いため落とす
  // （読み側が知らない属性で止まらないように）。
  // next は後続ブロックであり保存形の一部なので写す（落とすと
  // つなぎ済み定番形のクイック挿入で後続が消える）。
  // fields 等は参照写しのため、呼出側で entries を書き換えないこと
  // （insert 側は使う前に写す）。
  function stateOf(item) {
    var s = { type: item.type };
    if (item.fields) {
      s.fields = item.fields;
    }
    if (item.inputs) {
      s.inputs = item.inputs;
    }
    if (item.next) {
      s.next = item.next;
    }
    if (item.extraState) {
      s.extraState = item.extraState;
    }
    return s;
  }

  // 見出しらしいか。空・素の型名そのものは見出しとみなさない。
  function labelLike(text, type) {
    return !!text && text !== type;
  }

  function labelOf(tmp, type) {
    var text = "";
    try {
      var b = tmp.newBlock(type);
      try {
        text = String(b.toString());
      } catch (e) {
        text = "";
      }
      if (!labelLike(text, type)) {
        // toString が型名だけのときは、欄に並ぶ文言を集める
        // （利用者に見えている文面そのもの）。
        text = b.inputList
          .map(function (input) {
            return (input.fieldRow || [])
              .map(function (f) {
                try {
                  return String(f.getText());
                } catch (e2) {
                  return "";
                }
              })
              .join(" ");
          })
          .join(" / ");
      }
    } catch (e) {
      text = "";
    }
    // 長い数は見出しを食うため落とす（例: 音の閾値 1000000）。
    text = String(text || "").replace(/[0-9]{5,}(\.[0-9]+)?/g, "");
    text = text.replace(/\s+/g, " ").trim();
    return text || String(type);
  }

  function tooltipOf(tmp, type) {
    try {
      var tip = tmp.newBlock(type).getTooltip();
      return String(tip || "");
    } catch (e) {
      return "";
    }
  }

  // 大文字小文字・全角半角（NFKC）・カタカナ/ひらがなの違いを無視する。
  function norm(s) {
    var t = String(s == null ? "" : s)
      .normalize("NFKC")
      .toLowerCase();
    return t.replace(/[ァ-ヶ]/g, function (ch) {
      return String.fromCharCode(ch.charCodeAt(0) - 0x60);
    });
  }

  // 空白区切りの語に割る。空の問い合わせは空配列（＝何も返さない）。
  function termsOf(query) {
    return norm(query)
      .split(/\s+/)
      .filter(function (w) {
        return !!w;
      });
  }

  // 並びは見出しの先頭＞見出し＞キーワード＞説明＞分類。
  // 合わない（どれかの語がどこにも無い）ときは -1。
  // 「よく使う形」のような型つき定番形（欄・実体のつなぎ済み）は、
  // 同点では素の項目（型だけ・影だけの穴）を先にする。汎い問い合わせ
  // （例: おす・まつ・くりかえし）で定番形が素を押しのけないようにする。
  function rankOf(entry, terms) {
    var label = norm(entry.label);
    var keys = (entry.keywords || []).map(norm).join(" ");
    var tip = norm(entry.tooltip || "");
    var cat = norm(entry.category || "");
    var all = label + " " + keys + " " + tip + " " + cat;
    for (var k = 0; k < terms.length; k++) {
      if (all.indexOf(terms[k]) === -1) {
        return -1;
      }
    }
    if (label.indexOf(terms[0]) === 0) {
      return 0;
    }
    if (
      terms.every(function (t) {
        return label.indexOf(t) !== -1;
      })
    ) {
      return 1;
    }
    if (
      terms.every(function (t) {
        return keys.indexOf(t) !== -1;
      })
    ) {
      return 2;
    }
    if (
      terms.every(function (t) {
        return tip.indexOf(t) !== -1;
      })
    ) {
      return 3;
    }
    return 4;
  }

  // 文の途中へ：選択中の次へつなぎ、元の後続は新しい後ろへ回す。
  // 値の穴へ：空いた値入力の先頭へ入れる（影も使用中とみなす）。
  // どちらでもなければ置いたまま（呼出側で見える範囲へ寄せる）。
  function placeAfterOrInto(sel, made) {
    if (made.previousConnection && sel.nextConnection) {
      var next = null;
      try {
        next = sel.getNextBlock();
      } catch (e) {
        next = null;
      }
      try {
        if (sel.nextConnection.isConnected()) {
          sel.nextConnection.disconnect();
        }
        sel.nextConnection.connect(made.previousConnection);
        if (next && made.nextConnection) {
          made.nextConnection.connect(next.previousConnection);
        }
        return;
      } catch (e) {
        // 形が合わなければ値の穴へ試す（下へ落ちる）。
      }
    }
    // 値の穴へは inputList の配列で探す（getInputList は描画なしに無い）。
    if (made.outputConnection && sel.inputList) {
      var inputs = sel.inputList || [];
      for (var i = 0; i < inputs.length; i++) {
        var conn = inputs[i] && inputs[i].connection;
        if (!conn || conn.targetBlock()) {
          continue;
        }
        try {
          conn.connect(made.outputConnection);
          return;
        } catch (e) {
          // 形違いは次の穴へ。
        }
      }
    }
  }

  // 文の入れ口（DO 等）の末尾。選んだのが入れ物（プログラム・繰り返し）なら
  // その中、何も合わなければ唯一のプログラムの中へ入れる。外に浮かせると
  // 灰色（プログラム外）になり、置いたのに動かないように見えるため。
  function appendInto(container, made) {
    if (!container || !made.previousConnection) {
      return false;
    }
    var inputs = container.inputList || [];
    for (var i = 0; i < inputs.length; i++) {
      var conn = inputs[i] && inputs[i].connection;
      if (!conn || conn.type !== Blockly.NEXT_STATEMENT) {
        continue;
      }
      try {
        var last = conn.targetBlock();
        if (!last) {
          conn.connect(made.previousConnection);
          return true;
        }
        while (last.getNextBlock()) {
          last = last.getNextBlock();
        }
        if (last.nextConnection) {
          last.nextConnection.connect(made.previousConnection);
          return true;
        }
      } catch (e) {
        // 形が合わなければ次の入れ口へ。
      }
    }
    return false;
  }

  function onlyProgram(ws) {
    var programs = ws.getTopBlocks(false).filter(function (b) {
      return b.type === "pokecon_program";
    });
    return programs.length === 1 ? programs[0] : null;
  }

  function moveIntoView(ws, made) {
    // 入れ先が無いときは見えている範囲の真ん中へ寄せる。
    // 描画なしでは座標が無いため何もしない（0,0 のまま）。
    try {
      if (!made || typeof made.getRelativeToSurfaceXY !== "function") {
        return;
      }
      if (made.getParent()) {
        return;
      }
      if (typeof ws.getMetrics !== "function") {
        return;
      }
      var m = ws.getMetrics();
      if (!m || !m.viewWidth) {
        return;
      }
      var xy = made.getRelativeToSurfaceXY();
      made.moveBy(m.viewLeft + m.viewWidth / 2 - xy.x, m.viewTop + m.viewHeight / 2 - xy.y);
    } catch (e) {
      // 見え方は本質ではないため落としても続ける。
    }
  }

  var PokeconQuick = {
    // 素の項目か（型だけ・影だけの穴）。順位付けと試験で同じ定義を使う。
    isPlain: isPlainState,
    // 道具箱（分類つきJSON）から候補一覧を作る。各候補は
    // { type, label, category, keywords, block } に説明と分類の色を添える
    // （tooltip・colour は窓の表示と順位付けに要るため）。
    index: function (toolbox) {
      var out = [];
      if (!toolbox || !toolbox.contents) {
        return out;
      }
      var tmp = null;
      try {
        tmp = new Blockly.Workspace();
      } catch (e) {
        tmp = null;
      }
      if (!tmp) {
        return out;
      }
      try {
        toolbox.contents.forEach(function (cat) {
          if (!cat || cat.kind !== "category" || !cat.contents) {
            return;
          }
          var name = String(cat.name || "");
          var colour = cat.colour != null ? String(cat.colour) : "";
          cat.contents.forEach(function (item) {
            if (!item || item.kind !== "block" || !item.type) {
              return;
            }
            if (!Blockly.Blocks[item.type]) {
              return;
            }
            out.push({
              type: item.type,
              label: labelOf(tmp, item.type),
              category: name,
              keywords: keywordsFor(item.type),
              block: stateOf(item),
              tooltip: tooltipOf(tmp, item.type),
              colour: colour,
            });
          });
        });
      } finally {
        try {
          tmp.dispose();
        } catch (e) {
          // 捨てられなくても候補は返す。
        }
      }
      return out;
    },

    // 空白区切りの全語を含む候補を順位付きで返す。空の問合せは空配列。
    // 同点のときは素の項目（isPlain。型だけ・影だけの穴）を先にし、
    // その後は道具箱の並び順（「よく使う形」の定番形は後ろ）。
    search: function (entries, query, limit) {
      var terms = termsOf(query);
      if (!terms.length) {
        return [];
      }
      var n = typeof limit === "number" && limit > 0 ? Math.floor(limit) : 50;
      var scored = [];
      (entries || []).forEach(function (e, i) {
        var rank = rankOf(e, terms);
        if (rank >= 0) {
          scored.push([rank, isPlainState(e && e.block) ? 0 : 1, i, e]);
        }
      });
      scored.sort(function (a, b) {
        return a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
      });
      return scored.slice(0, n).map(function (s) {
        return s[3];
      });
    },

    // ブロックを1個置く。追加は1つの取り消し単位にする。
    // 作ったブロックを返す。
    insert: function (ws, entry, selected) {
      var Ev = Blockly.Events;
      var made = null;
      var grouped = false;
      // 外側で既に束ねていれば、その束ねに入れて後で元へ戻す（壊さない）。
      var prevGroup = Ev && typeof Ev.getGroup === "function" ? Ev.getGroup() : "";
      try {
        if (Ev && typeof Ev.setGroup === "function") {
          Ev.setGroup(prevGroup || true);
          grouped = true;
        }
        // 同じ候補を使い回しても壊さないよう写しから作る。
        var state = JSON.parse(JSON.stringify(entry.block));
        made = Blockly.serialization.blocks.append(state, ws, { recordUndo: true });
        var sel = typeof selected === "string" ? ws.getBlockById(selected) : selected;
        if (sel && sel.workspace === ws && made) {
          placeAfterOrInto(sel, made);
        }
        if (made && !made.getParent() && made.previousConnection) {
          if (!(sel && sel.workspace === ws && appendInto(sel, made))) {
            appendInto(onlyProgram(ws), made);
          }
        }
        if (made) {
          moveIntoView(ws, made);
        }
      } finally {
        if (grouped) {
          try {
            Ev.setGroup(prevGroup || false);
          } catch (e) {
            // 閉じられなくても作った分は返す。
          }
        }
      }
      return made;
    },
  };

  Blockly.PokeconQuick = PokeconQuick;
})();
