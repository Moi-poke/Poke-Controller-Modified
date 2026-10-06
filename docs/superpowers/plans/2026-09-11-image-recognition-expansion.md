# 画像認識拡充 Implementation Plan (saved 2026-09-11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Blocklyテンプレ照合にグレー/カラー選択を付け、HSV色域フィルタをBlocklyモーダルとメイン画面の両プレビューに表示専用で付ける。

**Architecture:** 実行時 (core/CommandVision.py) は無変更 (既定 use_gray=True 維持)。純粋関数 core/preview_filter.py (tkinter-free) にHSVマスク/適用/割合を集約し、Blockly側とメイン画面の両方から使う。認識 (Camera.readFrame) には触らず表示のみ加工する。

**Tech Stack:** Python 3.12 / OpenCV / numpy / tkinter (CaptureArea) / Blockly 13.2.1 vendored JS / http.server localhost。

**Spec:** チャット承認済み短設計 (2026-09-10): (1) 4種テンプレブロックに USE_GRAY チェックボックス・既定OFF=カラー・旧保存物は省略維持、(2) フィルタ表示は OFF/対象外グレー/白黒 3種・メインはセッションのみ (ini不変)。OBS記事の「狙い色を残して他を落とす」は gray_out が対応。

## Global Constraints

- Python floor 3.12。4-space。コメント・ユーザー可視文字列は日本語。
- ruff check + ruff format --check + mypy + bounds + userapi + test 全緑 (task ci 相当)。
- 絶対importのみ (SerialController/ が sys.path 上)。相対import禁止。
- core/ はGUI非依存 (tkinter禁止・app層import禁止)。services/ もtkinter禁止・UIモジュールimport禁止。
- Commands.* 公開API凍結 (task userapi)。core/CommandVision.py の既定値・シグネチャを変えない。
- settings*.ini 書式凍結。メイン画面フィルタはセッションのみ、iniに触らない。
- Loggingは loguru が原則。ワーカーからwidget直書き禁止。
- Camera.py のMJPG→解像度順序、最新1枚キュー、seq dedupを壊さない。

---

### Task 1: core/preview_filter.py 純粋フィルタ＋単体テスト

**Files:**
- Create: SerialController/core/preview_filter.py
- Test: tests/test_preview_filter.py

**Interfaces:**
- Consumes: なし (cv2, numpy のみ)。
- Produces: validate_hsv(lower, upper)->(lo,hi) / hsv_mask(bgr,lower,upper)->mask / color_ratio(bgr,lower,upper,crop=None)->float / apply_filter(bgr,lower,upper,mode="gray_out")->ndarray (後続Task 4・6が使用)。

`validate_hsv`: H 0-179、S/V 0-255 を検査、NGは ValueError (文言「HSVは〜」「Hは0〜179」「S・Vは0〜255」)。
`hsv_mask`: H環 (赤の0またぎ) 対応のinRange。CommandVision.getColorRatio と同一条件。
`color_ratio`: crop実画素 [x1,y1,x2,y2] またはNone=全体。
`apply_filter`: 新配列を返す (入力不変)。mode gray_out=対象外グレー / mask=白黒二値。

### Task 2: テンプレ4ブロックに USE_GRAY チェック (既定カラー)＋旧保存物互換

**Files:**
- Modify: SerialController/assets/blockly/pokecon_blocks.js (4ブロック定義 + visionGray helper + 4 generator)
- Test: tests/test_blockly_browser.py

**Interfaces:**
- Consumes: なし。
- Produces: field USE_GRAY + visionGray(block) の生成文字列 (Task 3が使用)。

4ブロック (contains/wait_appear/wait_gone/position) の args0 末尾 (CAPOPEN前) に `{ type: "field_checkbox", name: "USE_GRAY", checked: false }` を追加。message0 に「グレー %N」を挿入 (既存CROP/PREVIEW/CAPOPEN順序は保つ)。
`visionGray(block)`: "TRUE"→`, use_gray=True`、"FALSE"→`, use_gray=False`、欠損/空→`""` (旧保存物互換で引数省略→実行時既定グレー維持)。新規は常に明示 (既定FALSE=カラー)。

### Task 3: Blocklyモーダルにグレー同期＋色フィルタ区画 (JS/HTMLのみ)

**Files:**
- Modify: SerialController/assets/blockly/editor.html

**Interfaces:**
- Consumes: Task 2の USE_GRAY。
- Produces: DOM ID capfilt_*＋POST /color_ratio・POST /filter_preview へのfetch (Task 4が受け口)。

capmodalbox 内に色フィルタ行 (capfilt_mode/off/gray_out/mask、caph1/caps1/capv1/caph2/caps2/capv2、capfiltratio、capfiltimg) を追加。capFiltParams/capFilt/capFiltSoon (250ms debounce) を追加。capgray変更→capBlockSyncでUSE_GRAYへ反映。capBlockGrayInit / capBlockColorInit を追加し capOpenBlockModal から呼ぶ。capOpenBlockModal のガードを TEMPLATE持ち or H1持ちに緩和し区画表示切替。

### Task 4: services/blockly_color.py＋/color_ratio・/filter_preview 受け口

**Files:**
- Create: SerialController/services/blockly_color.py
- Modify: SerialController/ui/blockly_editor.py
- Test: tests/test_blockly_color.py (新規)＋tests/test_blockly_editor.py 追記

**Interfaces:**
- Consumes: Task 1の preview_filter、既存 blockly_match.decode_frame_png/decode_upload_array/parse_crop。
- Produces: HTTP POST /color_ratio→{ok,message,ratio}、POST /filter_preview→{ok,message,ratio,image(base64 png)}。

`parse_hsv_pair`、`ratio_on_png(frame_png, lower, upper, crop)->(ratio,err)`、`filter_png(frame_png, lower, upper, mode, crop=None)->(png,ratio,err)`。mode不正文言「modeは gray_out / mask で送ってください」。blockly_editor は /match と同一の source 分岐 (upload→decode_upload_array、frame→_GET_FRAME)。上限 MAX_UPLOAD_BODY_BYTES 再利用。失敗文言は「色を見られません:」で区別。

### Task 5: pokecon_vision_color にCROP対応＋モーダル連携

**Files:**
- Modify: SerialController/assets/blockly/pokecon_blocks.js (color定義+generator)
- Test: tests/test_blockly_browser.py

message を「色 下限 %1 %2 %3 上限 %4 %5 %6 割合 %7 範囲 %8 %9」に拡張し CROP + CAPOPEN を追加。generator は CROP 正規表現に合致時のみ crop=[x1,y1,x2,y2]、空は []。

### Task 6: メイン画面プレビューに表示専用フィルタ

**Files:**
- Modify: SerialController/GuiAssets.py (CaptureArea: _filter_* 状態 + _filter_buf + setPreviewFilter/clearPreviewFilter + _convert フック)
- Modify: SerialController/ui/camera_panel.py (色フィルタチェック＋設定ダイアログ、セッションのみ)
- Test: tests/test_convert.py 追記

`setPreviewFilter(enabled, lower, upper, mode)` は validate_hsv + mode検査し _last_frame_seq=None でdedup無効化。`_convert` は resize後の表示サイズに apply_filter→_filter_buf→BGR2RGB。入力frame不変。setShowsize は _filter_buf を作り直す (_allocBuffers 経由)。

### Task 7: 文書・ゲート

**Files:**
- Modify: docs/BLOCKLY_EDITOR.md
- Test: 全ゲート (ruff check / format --check / mypy / bounds / userapi / pytest)

テンプレ4種グレー欄 (既定OFF=カラー・旧保存物は省略=グレー維持)、color範囲対応、色フィルタ3種＋割合表示、メイン画面フィルタは表示のみ・セッションのみ、を追記。
