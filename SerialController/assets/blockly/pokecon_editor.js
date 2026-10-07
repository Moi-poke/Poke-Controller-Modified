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

    // 組立中の問題一覧（VS Code の「問題」パネル相当）。
    // 戻り値は [{level, message, blockId}]（error を先、同じ重さは
    // ワークスペースの並び順）。手で無効にしたブロックとその中は数えない。
    // 文言は保存前の検査（editor.html の doSave・validateCropsLocal）と同じ
    // 言い回しにする（保存を押したときと食い違わないように）。
    problems: function (ws) {
      var errors = [];
      var warns = [];
      var all = [];
      try {
        all = ws.getAllBlocks(false) || [];
      } catch (e) {
        all = [];
      }
      // 手で無効にしたブロックと、その中（包む側が無効）は数えない。
      // 包む側は getSurroundParent でたどる（文・値の入れ子用）。
      function chainActive(b) {
        for (
          var p = b;
          p;
          p = typeof p.getSurroundParent === "function" ? p.getSurroundParent() : null
        ) {
          try {
            if (!p.isEnabled()) {
              return false;
            }
          } catch (e) {
            return false;
          }
        }
        return true;
      }
      // 画像未選択は既存の判定を使い、無効な分だけ除く。
      var missingTpl = {};
      PokeconEditor.blocksMissingTemplate(ws).forEach(function (b) {
        if (b && chainActive(b)) {
          missingTpl[b.id] = true;
        }
      });
      // 浮きの先頭。手で無効にした塊は除くが、自動で灰色にした分
      // （ORPHAN_REASON）は「外にある」警告の対象に残す。
      var orphanIds = {};
      PokeconEditor.orphanBlocks(ws).forEach(function (top) {
        var on = false;
        try {
          on = top.isEnabled();
        } catch (e) {
          on = false;
        }
        if (!on) {
          var auto = false;
          try {
            auto =
              typeof top.hasDisabledReason === "function" &&
              top.hasDisabledReason(ORPHAN_REASON);
          } catch (e) {
            auto = false;
          }
          if (!auto) {
            return;
          }
        }
        orphanIds[top.id] = true;
      });
      // 塊の先頭をたどる（文・値の入れ子と次接続の上流）。
      // 外の塊の中は実行されないため、画像・範囲・呼出のerrorは出さず
      // 外のwarnだけにする（重複定義・プログラム重複は全体に影響するため残す）。
      function chunkTop(b) {
        var t = b;
        var guard = 0;
        var moved = true;
        while (moved && guard++ < 1000) {
          moved = false;
          var s = null;
          try {
            s =
              typeof t.getSurroundParent === "function"
                ? t.getSurroundParent()
                : null;
          } catch (e) {
            s = null;
          }
          if (s) {
            t = s;
            moved = true;
            continue;
          }
          var pv = null;
          try {
            pv =
              typeof t.getPreviousBlock === "function"
                ? t.getPreviousBlock()
                : null;
          } catch (e) {
            pv = null;
          }
          if (pv) {
            t = pv;
            moved = true;
          }
        }
        return t;
      }
      function inOrphanChunk(b) {
        var t = null;
        try {
          t = chunkTop(b);
        } catch (e) {
          return false;
        }
        return !!(t && orphanIds[t.id]);
      }
      // 定義名の集計（無効な定義は数えない）。呼出・重複の判定に使う。
      var defCount = {};
      all.forEach(function (b) {
        if (!b || b.type !== "pokecon_sub_def" || !chainActive(b)) {
          return;
        }
        var name = "";
        try {
          name = String(b.getFieldValue("NAME") || "");
        } catch (e) {
          name = "";
        }
        if (!name) {
          return;
        }
        defCount[name] = (defCount[name] || 0) + 1;
      });
      var dupSeen = {};
      // 範囲欄の規則は editor.html の validateCropsLocal と同じ
      // （空は全体で正常・x1,y1,x2,y2・x2>x1・y2>y1）。
      var CROP_RE = /^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$/;
      var programsSeen = 0;
      all.forEach(function (b) {
        if (!b) {
          return;
        }
        if (orphanIds[b.id]) {
          warns.push({
            level: "warn",
            message: "プログラムの外にあるため実行されません（つなぎ直すと実行されます）",
            blockId: b.id,
          });
        }
        if (!chainActive(b)) {
          return;
        }
        if (b.type === "pokecon_program") {
          programsSeen++;
          if (programsSeen > 1) {
            errors.push({
              level: "error",
              message: "「プログラム」は1個までにしてください",
              blockId: b.id,
            });
          }
          var head = null;
          try {
            var input = typeof b.getInput === "function" ? b.getInput("DO") : null;
            head = input && input.connection ? input.connection.targetBlock() : null;
          } catch (e) {
            head = null;
          }
          if (!head) {
            warns.push({
              level: "warn",
              message: "プログラムの中身が空です（操作を並べてください）",
              blockId: b.id,
            });
          }
          return;
        }
        // 外の塊の中は実行されないため、画像・範囲・呼出のerrorは出さない
        // （外のwarnは上で済み。重複定義・プログラム重複は全体に影響するため残す）。
        var inOrphan = inOrphanChunk(b);
        if (missingTpl[b.id] && !inOrphan) {
          errors.push({
            level: "error",
            message: "画像を選んでいません（ブロックの画像欄で選んでください）",
            blockId: b.id,
          });
        }
        var hasCrop = false;
        try {
          hasCrop = typeof b.getField === "function" && !!b.getField("CROP");
        } catch (e) {
          hasCrop = false;
        }
        if (hasCrop && !inOrphan) {
          var crop = "";
          try {
            var raw = b.getFieldValue("CROP");
            crop = String(raw == null ? "" : raw).trim();
          } catch (e) {
            crop = "";
          }
          if (crop) {
            var m = crop.match(CROP_RE);
            if (!m || !(+m[3] > +m[1] && +m[4] > +m[2])) {
              errors.push({
                level: "error",
                message: "範囲（CROP）の書式が正しくありません（例: 10,20,110,120）",
                blockId: b.id,
              });
            }
          }
        }
        if (
          (b.type === "pokecon_sub_call" || b.type === "pokecon_sub_call_value") &&
          !inOrphan
        ) {
          var called = "";
          try {
            called = String(b.getFieldValue("NAME") || "");
          } catch (e) {
            called = "";
          }
          if (called && !defCount[called]) {
            errors.push({
              level: "error",
              message: "未定義のサブルーチンです: self." + called + "()",
              blockId: b.id,
            });
          }
          return;
        }
        if (b.type === "pokecon_sub_def") {
          var defined = "";
          try {
            defined = String(b.getFieldValue("NAME") || "");
          } catch (e) {
            defined = "";
          }
          // 同名のうち2個目以降だけ出す（1個目は正しい置き場所のため）。
          if (defined && defCount[defined] > 1) {
            dupSeen[defined] = (dupSeen[defined] || 0) + 1;
            if (dupSeen[defined] > 1) {
              errors.push({
                level: "error",
                message: "サブルーチン名が重複しています: " + defined,
                blockId: b.id,
              });
            }
          }
        }
      });
      if (!programsSeen) {
        errors.unshift({
          level: "error",
          message: "「プログラム」ブロックがありません（置いて、その中に操作を並べてください）",
          blockId: null,
        });
      }
      return errors.concat(warns);
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

    // 生成コードと行ごとの出どころ（MakeCodeの対応表示に相当）。
    // 戻り値は { code, lines }。code は通常の生成と1文字も違わない。
    // 各文ブロックのコードを開始・終了の印（コメント行）で囲んで生成し、
    // 印を取り除きながら持ち主を積み下ろしする。前後で囲むため、入れ子の
    // 後の else: や return・def の行も外側の持ち主へ正しく戻る（「次の印
    // まで同じ ID」だと直前の内側ブロックの行になってしまう）。
    // 印は STATEMENT_PREFIX/SUFFIX ではなく scrub_ で足す。controls_if や
    // break は前置きの印を自前で出し（break は外側の繰り返しの分まで）、
    // SUFFIX があると controls_if が else: pass を足し、印があると
    // addLoopTrap が空の本体に印を入れて pass が消える。どれも通常の生成と
    // 食い違うか、開始と終了の数が合わなくなる。scrub_ は文ブロックごとに
    // 1回、自身のコードを受けて次のブロックをつなぐ前に呼ばれるので、
    // そこで囲めば必ず対になる。
    // 印はコメント行のため、生成器の import 判定（codeOnly はコメントを
    // 除いてから調べる）には影響しない。プログラム・サブルーチン定義の
    // 自身は囲まない（中身の行だけが持ち主を持つ）。生成後は scrub_ を
    // 必ず元へ戻す。
    codeMap: function (ws, gen) {
      var START = "# @@pokecon:%1@@\n";
      var END = "# @@/pokecon:%1@@\n";
      var MARK_RE = /^[ \t]*# @@(\/?)pokecon:(?:"([^"]+)"|'([^']+)')@@[ \t]*$/;
      var OUTER = { pokecon_program: true, pokecon_sub_def: true };
      var hadOwnScrub = Object.prototype.hasOwnProperty.call(gen, "scrub_");
      var prevScrub = gen.scrub_;
      var marked = "";
      try {
        gen.scrub_ = function (block, code, thisOnly) {
          // 値ブロック（出力つき）は文の一部なので囲まない。
          if (
            typeof code === "string" &&
            !block.outputConnection &&
            !OUTER[block.type]
          ) {
            code = gen.injectId(START, block) + code + gen.injectId(END, block);
          }
          return prevScrub.call(gen, block, code, thisOnly);
        };
        marked = gen.workspaceToCode(ws);
      } finally {
        try {
          if (hadOwnScrub) {
            gen.scrub_ = prevScrub;
          } else {
            delete gen.scrub_;
          }
        } catch (e) {
          /* 戻せなくても続ける */
        }
      }
      // 開始の印で持ち主を積み、終了の印で下ろす。各行は今いちばん内側の
      // 持ち主のもの。空行はどのブロックにも属さない扱いにする。
      var out = [];
      var ids = [];
      var stack = [];
      marked.split("\n").forEach(function (ln) {
        var m = ln.match(MARK_RE);
        if (m) {
          if (m[1]) {
            stack.pop();
          } else {
            stack.push(m[2] || m[3] || null);
          }
          return;
        }
        out.push(ln);
        ids.push(
          /^[ \t]*$/.test(ln) || !stack.length ? null : stack[stack.length - 1]
        );
      });
      return { code: out.join("\n"), lines: ids };
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
