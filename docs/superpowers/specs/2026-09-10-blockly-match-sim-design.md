# TODO-23A: 照合シミュレーション 設計書

- 日付: 2026-09-10
- 前提: Phase B完成（modal編集まで）。spec `docs/superpowers/specs/2026-09-09-blockly-template-capture-design.md`。
- 方式: サーバ側cv2・実行時と同一条件。対象画像は最新キャプチャ既定＋手動アップロード併設。

## 1. 目的

テンプレート画像が特定の画像内でどれくらいのthresholdで見つかるかを、Blockly編集画面の中で試せるようにする。実行時（`isContainTemplate`系）と同じ照合条件で一致度を表示し、しきい値決めの手がかりにする。

## 2. スコープ

- 本書: `POST /match`＋`services/blockly_match.py`＋modal内UI（threshold・結果重ね描き・一致度表示・画像アップロード・自動再照合・検知数・サイズ表示・ROI限定）。
- やらないこと: Python呼び出し例の自動生成、ヒートマップ、精度保証、既存フロー変更、2値化・マスク（実行時条件に無いため）。

## 3. アーキテクチャ

- 照合条件は実行時と同一（`core/CommandVision.py`の`isContainTemplate`と同義）: `TM_CCOEFF_NORMED`、既定グレー、`nan_to_num`、`minMaxLoc`、しきい値比較。
- Python側は検証＋照合のみ。新設は`services/blockly_match.py`だけ。`core/`は不変。
- テンプレ読込は`core`の`_imread_or_raise`を再利用する（存在・破損の文言も実行時と同じにする）。
- 画像源は二系統。`frame`: サーバが`get_frame()`を都度取得（実行時と同条件）。`upload`: ブラウザ送付のPNG（JSON内base64、localhostのため許容）。

## 4. コンポーネント

- `POST /match`: 要求`{template, threshold, use_gray, source, image?, crop?}`。`source`は`frame`/`upload`。`threshold`は数値0〜1（既定0.7）。`use_gray`は既定True。`crop`は任意の実画像画素`[x1, y1, x2, y2]`（赤枠相当。範囲外・逆転は失敗応答）。名前検査は`blockly_capture.validate_template_name`と同規則＋存在検査。応答`{ok, score, matched, count, rect, message}`。`rect`は実画像画素の`{x, y, width, height}`（crop時は全画面系に戻す）、`score`は最大一致度（0〜1）、`count`は重複除去後の一致箇所数（上限20、`findAllTemplates`と同一則）。
- modal内UI: thresholdスライダ（0〜1 step 0.01、既定0.7）・テンプレ選択（modal内dropdown、`./templates`再取得）・use_gray切替（既定ON）・テンプレ寸法表示（`WxH`）・`[赤枠内のみで照合]`・結果欄（一致度％・件数・`img[y1:y2, x1:x2]`式・モード表示）。`[照合テスト]`ボタンは置かない。`<input type=file>`の画像選択は自動で試す（保存対象は従来どおりframe側、区別表示する）。
- 自動再照合: threshold入力・テンプレ選択・use_gray切替・ドラッグ確定・数値反映・画像表示・modal表示のたびに走る。連続入力は約250msデバウンス、連番トークンで陳腐応答を捨てる。結果は次回実行まで残す。
- 失敗時: テンプレ欠損・画像なし・不正thresholdは失敗応答＋赤字表示。重ね描きは消す。

## 5. データフロー

- frame系: modal表示・画像表示・threshold入力・テンプレ選択・use_gray切替・ドラッグ確定→自動で`POST /match`（画像なし）→サーバが最新frame取得→照合→重ね描き＋結果欄。連続入力は250ms後に1回だけ送り、古い応答は捨てる。
- upload系: ファイル選択→modal画像に表示→自動で`POST /match`（base64付き）→同上。
- ROI限定: 赤枠あり＋チェックONで`crop`付き送信。サーバは切出し内で照合し座標を全画面系に戻す。赤枠なし・範囲不正時は全体照合に切り替える旨を表示する。

## 6. エラー処理

- カメラなし（frame系）: `ok:false`＋`FRAME_FAIL_MESSAGE`流用。赤字表示。
- 不正入力: テンプレ名の空・`..`・絶対パス・`:`・存在なし、thresholdの非数値・範囲外、cropの形式・順序・範囲外は保存せず失敗応答。
- 画像デコード失敗（upload破損時）: 失敗応答＋取り直し案内。
- 旧フローへの影響なし（追加のみ）。

## 7. テスト・成功基準

- 自動: `tests/test_blockly_match.py`（同一切り出しでscore≈1.0・件数1・ノイズでミス・crop限定と座標戻し・拒否則・実カメラなしfake注入）。`tests/test_blockly_editor.py`拡張（`/match`の実HTTP：frame正常・upload正常・crop正常・失敗系・count同梱）。JSは静的markup検査＋自動実行の配線probe（node vm）。Gate全緑。
- 手動（ユーザ環境）: frame取得→自動で一致度・件数・重ね描きが出る→thresholdを振ると追従する→テンプレ切替・グレー切替・ROI限定・uploadでも同様。旧フロー不変。
- 手動（ユーザ環境）: frame取得→照合→一致度と重ね描きが出る→thresholdを振ってmatchedが切り替わる→upload画像でも同様。旧フロー不変。
- 成功基準: 自動Gate緑＋手動の照合→表示→threshold切替の往復確認。マッチ精度自体は対象外。

## 8. 影響範囲・制約

- 触るファイル: `services/blockly_match.py`（新規）、`ui/blockly_editor.py`（`/match`）、`assets/blockly/editor.html`（modal内区画）、`docs/BLOCKLY_EDITOR.md`、tests。
- 触らないもの: `core/`一式、`Commands.*`公開面、既存エンドポイントの形、`settings*.ini`。
- 規約: `SerialController/`起点の絶対import、相対import禁止。コメント・利用者文面は日本語。4スペース。CDN禁止。資源パスは`WindowUtils.APP_DIR`起点。`services/`で`os.chdir`しない。サーバスレッドからtkinterを触らない。

## 9. 決定記録

- エンジン: サーバ側cv2（ブラウザ側OpenCV.js案を退けた。約8MB同梱が要るうえ実行時との条件一致が崩れるため）。
- 画像源: 最新キャプチャ既定＋手動アップロード併設（frame単独案を拡張。保存対象との混同防止に区別表示する）。
- threshold既定: 0.7（ブロック生成の既定と一致）。
- Python呼び出し例の自動生成は作らない（ブロック生成が既にあるため）。
- `[照合テスト]`ボタンは置かない（自動再照合に一本化。明示実行が要る場面は取り直し・選択変更が兼ねる）。
- 件数則は`findAllTemplates`と同一（近傍半分幅で間引き・上限20）。core不変のためservice側に複写し出典を明記する。
- 寸法表示はサーバを足さず`Image()`で`/template_image`を読む。
