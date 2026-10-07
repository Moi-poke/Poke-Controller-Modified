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
  };

  Blockly.PokeconEditor = PokeconEditor;
})();
