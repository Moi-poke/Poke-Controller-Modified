// 編集画面の状態判定（描画・DOMなし）。editor.html から使い、
// 判定そのものは node の vm と本物の Blockly で確かめる。
// 置き場を分けるのは、inline script の検証スタブ（最小DOM）に
// 判定の正しさまで背負わせないため。
(function () {
  "use strict";

  if (typeof Blockly === "undefined") {
    return;
  }

  // 宙に浮いたブロックの無効理由。Blockly既定の孤立理由と同じ名にして、
  // 標準の disableOrphans と付け外しを共有する。
  var ORPHAN_REASON = "ORPHANED_BLOCK";

  var PokeconEditor = {
    // 保存物に関わる事象か。選択・クリック・表示位置などUIだけの事象は
    // 未保存表示を汚さない（選んだだけで「未保存」になっていた）。
    isContentEvent: function (e) {
      return !!e && !e.isUiEvent;
    },

    // 保存物の比較鍵。読込直後にも遅れて事象が届くため、事象の有無ではなく
    // 中身（位置を含む）で未保存を判定する。
    stateKey: function (ws) {
      try {
        return JSON.stringify(Blockly.serialization.workspaces.save(ws));
      } catch (e) {
        return "";
      }
    },

    // プログラム・サブルーチン定義の外に置かれ、上か左につなぎ口を持つ
    // 塊の先頭。生成するとモジュール直下に `self.` が出て読込で落ちる。
    orphanBlocks: function (ws) {
      var tops = [];
      try {
        tops = ws.getTopBlocks(false) || [];
      } catch (e) {
        tops = [];
      }
      return tops.filter(function (b) {
        return !!(b.previousConnection || b.outputConnection) && !b.isShadow();
      });
    },

    // 浮いた塊を無効にし、つなぎ直した塊は有効へ戻す（手動の無効は触らない）。
    // 生成器は無効ブロックを飛ばして「次」へ進むため、塊の後続まで無効にする。
    // Blockly標準の disableOrphans は描画用ワークスペース専用のため自前で持つ。
    syncOrphans: function (ws) {
      var want = {};
      PokeconEditor.orphanBlocks(ws).forEach(function (top) {
        for (var b = top; b; b = b.getNextBlock()) {
          want[b.id] = true;
        }
      });
      // 付け外しは取り消し履歴に積まない（Blockly標準の disableOrphans と同じ）。
      // 積むと「元に戻す」で外した操作を戻すたびに無効化がやり直され、
      // 取り消しが進まなくなる上、やり直し履歴も消える。
      var Ev = Blockly.Events;
      var recording = Ev && typeof Ev.getRecordUndo === "function" ? Ev.getRecordUndo() : null;
      try {
        if (recording !== null) {
          Ev.setRecordUndo(false);
        }
        ws.getAllBlocks(false).forEach(function (b) {
          var has =
            typeof b.hasDisabledReason === "function" &&
            b.hasDisabledReason(ORPHAN_REASON);
          var on = !!want[b.id];
          if (has !== on && typeof b.setDisabledReason === "function") {
            b.setDisabledReason(on, ORPHAN_REASON);
          }
        });
      } finally {
        if (recording !== null) {
          Ev.setRecordUndo(recording);
        }
      }
    },

    // 変更のたびに浮き判定をやり直す。ドラッグ中は確定まで待つ。
    attachOrphanGuard: function (ws) {
      var busy = false;
      ws.addChangeListener(function (e) {
        if (busy || !PokeconEditor.isContentEvent(e)) {
          return;
        }
        if (typeof ws.isDragging === "function" && ws.isDragging()) {
          return;
        }
        busy = true;
        try {
          PokeconEditor.syncOrphans(ws);
        } finally {
          busy = false;
        }
      });
    },

    // 画像を選んでいない（TEMPLATE欄が空の）有効な画像認識ブロック。
    // 空のまま保存すると実行時に必ず失敗するため、保存前に止める。
    blocksMissingTemplate: function (ws) {
      return ws.getAllBlocks(false).filter(function (b) {
        if (!b.isEnabled() || typeof b.getField !== "function" || !b.getField("TEMPLATE")) {
          return false;
        }
        return !String(b.getFieldValue("TEMPLATE") || "").trim();
      });
    },

    // 試し実行のコード。各ブロックの手前に目印（本体の services/blockly_run が
    // 差し込む関数）を置き、実行中のブロックを光らせ・区切りで止められるようにする。
    // opts.blockId があればそのブロック（only なら単独、無ければ後続も）だけを
    // do() に置く。成功なら {code}、走らせられなければ {error}。
    // 保存物・取り消し履歴は変えない（一時的な付け外しは事象を止めて戻す）。
    trialCode: function (ws, gen, opts) {
      opts = opts || {};
      var programs = ws.getTopBlocks(false).filter(function (b) {
        return b.type === "pokecon_program" && b.isEnabled();
      });
      if (programs.length !== 1) {
        return {
          error: programs.length
            ? "プログラムは1個までにしてください"
            : "「プログラム」ブロックがありません（試し実行はプログラムの形で走らせます）",
        };
      }
      var program = programs[0];
      var target = null;
      if (opts.blockId) {
        target = ws.getBlockById(opts.blockId);
        if (!target) {
          return { error: "ブロックが見つかりません" };
        }
        if (target.type === "pokecon_program") {
          target = null;
        } else if (target.outputConnection) {
          return { error: "値のブロックは単独では試せません（使っている側のブロックで試してください）" };
        }
      }
      if (target) {
        var root = target.getRootBlock();
        if (root && root.type === "pokecon_sub_def") {
          var args = String(root.getFieldValue("ARGS") || "")
            .split(/[,、]/)
            .filter(function (s) {
              return s.trim();
            });
          if (args.length) {
            return { error: "引数のあるサブルーチンの中は単独では試せません（呼ぶ側から試してください）" };
          }
        }
      }
      if (target && looseFlowBlock(target, !!opts.only)) {
        return { error: "「中断・次へ」は繰り返しの中でしか使えません（繰り返しのブロックごと試してください）" };
      }
      var Ev = Blockly.Events;
      var lifted = [];
      var suppressed = [];
      var code = "";
      Ev.disable();
      try {
        // プログラム外で灰色にしたブロックも試せるよう、走らせる分だけ一時的に戻す。
        if (target && target.type !== "pokecon_sub_def") {
          for (var b = target; b; b = opts.only ? null : b.getNextBlock()) {
            if (typeof b.hasDisabledReason === "function" && b.hasDisabledReason(ORPHAN_REASON)) {
              b.setDisabledReason(false, ORPHAN_REASON);
              lifted.push(b);
            }
          }
        }
        // 目印は do()・サブルーチンの中だけに付ける（外に出ると self が無い）。
        ws.getTopBlocks(false).forEach(function (t) {
          if (t.type === "pokecon_program" || t.type === "pokecon_sub_def") {
            suppressed.push([t, t.suppressPrefixSuffix]);
            t.suppressPrefixSuffix = true;
          }
        });
        gen.STATEMENT_PREFIX = "_pokecon_step(self, %1)\n";
        if (target) {
          gen.pokeconBodyOverride = function (g) {
            if (target.type === "pokecon_sub_def") {
              return g.statementToCode(target, "DO");
            }
            return g.prefixLines(g.blockToCode(target, !!opts.only) || "", g.INDENT);
          };
        }
        // workspaceToCode は外に置いた塊も直下へ出すため、プログラムだけを生成する。
        gen.init(ws);
        var line = gen.blockToCode(program);
        if (Array.isArray(line)) {
          line = line[0];
        }
        code = gen.finish(line || "");
        code = code.replace(/^\s+\n/, "").replace(/\n\s+$/, "\n").replace(/[ \t]+\n/g, "\n");
      } finally {
        // 戻しは1つずつ握る（途中で落ちても事象を止めたままにしない）。
        try {
          gen.STATEMENT_PREFIX = null;
          delete gen.pokeconBodyOverride;
          suppressed.forEach(function (pair) {
            pair[0].suppressPrefixSuffix = pair[1];
          });
          lifted.forEach(function (b) {
            try {
              b.setDisabledReason(true, ORPHAN_REASON);
            } catch (e) {
              /* 消えたブロックは戻せない */
            }
          });
        } finally {
          Ev.enable();
        }
      }
      return { code: code };
    },

    // 新規の初期形。空のキャンバスから始めると、何を置けばよいか分からない。
    defaultState: function () {
      return {
        blocks: {
          languageVersion: 0,
          blocks: [
            {
              type: "pokecon_program",
              x: 40,
              y: 40,
              fields: { NAME: "新しいコマンド", TAGS: "blockly" },
            },
          ],
        },
      };
    },

    // 記録した手順を pokecon_press ブロックの並びにして挿入する。
    // steps は [{target, duration, wait}]（services/blockly_record の形）。
    // target は Button.A / Hat.TOP / Direction.UP の形で、BUTTON 欄の書式に
    // 合わせる（ボタンだけ素の名 A、方向は Hat./Direction. のまま）。
    // 挿入位置は、選択中の文ブロックがあればその直後（元の後続は最後の
    // 新ブロックの後ろへつけ直す）、無ければプログラムの DO の末尾、
    // プログラムが無ければ空いている所。全体を1つの取り消し単位にする。
    // 作ったブロックの配列を返す（描画なしで検証できる）。
    insertSteps: function (ws, steps, selected) {
      var made = [];
      if (!steps || !steps.length) {
        return made;
      }
      var Ev = Blockly.Events;
      var prevGroup = Ev && typeof Ev.getGroup === "function" ? Ev.getGroup() : null;
      if (Ev && typeof Ev.setGroup === "function") {
        Ev.setGroup(true);
      }
      try {
        steps.forEach(function (s) {
          var b = ws.newBlock("pokecon_press");
          var target = String((s && s.target) || "A");
          // ボタンだけ素の名（Button.A → A）、方向はそのまま。
          var field = target;
          if (target.indexOf("Button.") === 0) {
            field = target.slice(7);
          }
          try {
            b.setFieldValue(field, "BUTTON");
          } catch (e) {
            try {
              b.setFieldValue("A", "BUTTON");
            } catch (e2) {
              /* 欄が無ければそのまま */
            }
          }
          ["DURATION", "WAIT"].forEach(function (name) {
            var key = name === "DURATION" ? "duration" : "wait";
            var v = s ? Number(s[key]) : NaN;
            if (!isFinite(v)) {
              return;
            }
            try {
              b.setFieldValue(String(v), name);
            } catch (e) {
              /* 欄が無ければそのまま */
            }
          });
          try {
            if (typeof b.initSvg === "function") {
              b.initSvg();
            }
            if (typeof b.render === "function") {
              b.render();
            }
          } catch (e) {
            /* 描画なしの検証では無視する */
          }
          made.push(b);
        });
        for (var i = 0; i + 1 < made.length; i++) {
          try {
            made[i].nextConnection.connect(made[i + 1].previousConnection);
          } catch (e) {
            /* つながなければ単体で置く */
          }
        }
        var first = made[0];
        var last = made[made.length - 1];
        var anchor =
          selected && !selected.isShadow && !selected.isShadow() &&
          typeof selected.getNextBlock === "function" &&
          (selected.previousConnection || selected.nextConnection) &&
          !selected.outputConnection &&
          selected.type !== "pokecon_program"
            ? selected
            : null;
        if (anchor) {
          // 選択中の直後。元の後続は最後の新ブロックの後ろへつけ直す。
          var tail = null;
          try {
            tail = anchor.getNextBlock();
          } catch (e) {
            tail = null;
          }
          try {
            anchor.nextConnection.connect(first.previousConnection);
          } catch (e) {
            /* つながなければ単体で置く */
          }
          if (tail) {
            try {
              last.nextConnection.connect(tail.previousConnection);
            } catch (e) {
              /* つながなければ単体で置く */
            }
          }
          return made;
        }
        var programs = [];
        try {
          programs = (ws.getTopBlocks(false) || []).filter(function (b) {
            return b.type === "pokecon_program";
          });
        } catch (e) {
          programs = [];
        }
        if (programs.length) {
          var input = null;
          try {
            input = programs[0].getInput("DO");
          } catch (e) {
            input = null;
          }
          var conn = input ? input.connection : null;
          var head = null;
          try {
            head = conn ? conn.targetBlock() : null;
          } catch (e) {
            head = null;
          }
          if (!head) {
            try {
              conn.connect(first.previousConnection);
            } catch (e) {
              /* つながなければ単体で置く */
            }
          } else {
            var end = head;
            try {
              while (end.getNextBlock()) {
                end = end.getNextBlock();
              }
              end.nextConnection.connect(first.previousConnection);
            } catch (e) {
              /* つながなければ単体で置く */
            }
          }
          return made;
        }
        // プログラムが無ければ空いている所へ置く（互いにはつなぐ）。
        var baseX = 80;
        var baseY = 80;
        try {
          first.moveBy(baseX, baseY);
        } catch (e) {
          /* 位置が動かせなくても続ける */
        }
        return made;
      } finally {
        if (Ev && typeof Ev.setGroup === "function") {
          Ev.setGroup(prevGroup);
        }
      }
    },
  };

  // 繰り返しの外へ出てしまう「中断・次へ」。試す範囲（target 以下、only でなければ
  // 後続も）の中で、包む繰り返しが範囲内に無いものを1つ返す（無ければ null）。
  // 単独で生成すると `break` が繰り返しの外に出て文法エラーになるため。
  var LOOP_TYPES = {
    controls_repeat_ext: true,
    controls_repeat: true,
    controls_whileUntil: true,
    controls_for: true,
    controls_forEach: true,
  };
  function looseFlowBlock(target, only) {
    var inRange = {};
    for (var b = target; b; b = only ? null : b.getNextBlock()) {
      b.getDescendants(false).forEach(function (d) {
        inRange[d.id] = d;
      });
    }
    var found = null;
    Object.keys(inRange).forEach(function (id) {
      var f = inRange[id];
      if (found || f.type !== "controls_flow_statements") {
        return;
      }
      for (var p = f.getSurroundParent(); p && inRange[p.id]; p = p.getSurroundParent()) {
        if (LOOP_TYPES[p.type]) {
          return;
        }
      }
      found = f;
    });
    return found;
  }

  Blockly.PokeconEditor = PokeconEditor;
})();
