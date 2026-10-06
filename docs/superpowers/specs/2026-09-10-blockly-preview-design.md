# TODO-23B: ブロック内プレビュー 設計書

- 日付: 2026-09-10
- 前提: Phase B完成（modal編集まで）。spec `docs/superpowers/specs/2026-09-09-blockly-template-capture-design.md`。
- 方式: テンプレ画像の配信経路＋ブロック側ダミーFieldImage。表示のみで保存物不変。

## 1. 目的

vision系ブロックのTEMPLATE欄に指定中の画像を、ブロック内に縮小表示する。名前の打ち間違いに気づけるようにする。あくまでプレビューのため画質・厳密さは求めない。

## 2. スコープ

- 本書: `GET /template_image`＋vision系6種のプレビュー表示＋読込直後の張り直し。
- やらないこと: 生成コードへの反映、編集・削除、欠損時の復旧、精度保証。

## 3. アーキテクチャ

- 生成の単一真実はvendored JS（`SerialController/assets/blockly/pokecon_blocks.js`）。
- Python側は画像配信のみ。新規ファイルなし（`ui/blockly_editor.py`に受け口追加だけ）。`core/`は不変。
- 表示は各ブロック内のダミー入力に置く`FieldImage`（初期は透明placeholder）。TEMPLATE欄のvalidatorで画像URLへ更新する。

## 4. コンポーネント

- `GET /template_image?name=...`: `Template/`配下に拘束し生PNGを返す。名前検査は`blockly_capture.validate_template_name`と同規則＋存在検査。欠損・不正時はJSON失敗応答（`/frame`と同型）。
- ブロック側: TEMPLATE欄を持つvision系4種（contains/wait_appear/wait_gone/position。wait_stable・colorはTEMPLATE欄が無いため対象外）にダミー`PREVIEW`欄。TEMPLATE欄のvalidatorで`./template_image?name=<値>`へ更新。表示幅の上限は120px（高さは比率維持）。欠損時はplaceholderのまま。
- 読込直後の張り直し: `editor.html`のload直後に全ブロックを走査し、TEMPLATE値からpreviewのsrcを再設定する（保存データのURL陳腐化対策）。
- 生成コードへの混入なし（ダミー入力は生成器が無視する）。

## 5. データフロー

- 編集時: TEMPLATE欄を変更→validator→preview更新（画像取得失敗時はplaceholderのまま）。
- 保存時: 従来どおり名前のみ。画像本体は保存物に含めない。
- 読込時: workspace復元→全ブロックのpreviewをTEMPLATE値から張り直す。

## 6. エラー処理

- 画像なし・読込失敗: placeholderのまま＋ console への警告のみ（保存は塞がない）。
- `color`ブロック（TEMPLATE欄なし）は対象外。
- 旧フローへの影響なし（追加のみ）。

## 7. テスト・成功基準

- 自動: node vmでvalidator更新・欠損時placeholder・生成コード不変（既存probeの厳密コード比較が検出網）。`GET /template_image`の実HTTP（PNG/404）。Gate全緑。
- 手動（ユーザ環境）: TEMPLATE欄に既存画像名→縮小表示が出る→存在しない名→placeholderのまま→保存物の`.py`不変。
- 成功基準: 自動Gate緑＋手動の表示→欠損→保存不変の確認。

## 8. 影響範囲・制約

- 触るファイル: `ui/blockly_editor.py`（`/template_image`）、`assets/blockly/pokecon_blocks.js`（PREVIEW欄＋validator）、`assets/blockly/editor.html`（張り直し）、`docs/BLOCKLY_EDITOR.md`、tests。
- 触らないもの: `core/`一式、`Commands.*`公開面、既存ブロックの生成内容、既存エンドポイントの形、`settings*.ini`。
- 規約: `SerialController/`起点の絶対import、相対import禁止。コメント・利用者文面は日本語。4スペース。CDN禁止。資源パスは`WindowUtils.APP_DIR`起点。サーバスレッドからtkinterを触らない。

## 9. 決定記録

- 対象: TEMPLATE欄を持つvision系4種（contains/wait_appear/wait_gone/position。wait_stable・colorはTEMPLATE欄が無いため対象外）。
- 幅上限: 120px（あくまでプレビューのため）。
- 保存物に画像を含めない（名前のみ。配布zipの肥大化を避ける）。
