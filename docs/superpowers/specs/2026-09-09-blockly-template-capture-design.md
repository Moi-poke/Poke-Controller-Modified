# TODO-22 Phase B: テンプレ画像作成 設計書

- 日付: 2026-09-09
- 前提: Phase A完成（`dff2924`、反映修正含む）。spec `docs/superpowers/specs/2026-09-08-blockly-vision-design.md`、plan `docs/superpowers/plans/2026-09-08-blockly-vision.md`。
- 方式: 案A（編集画面に同居・サーバ側crop・取得関数を注入）。ROI切出しのデスクトップ前例（`GuiAssets.StartRangeSS`系：表示→実座標スケーリング＋`saveCapture(crop_ax)`）をブラウザ版にする。

## 1. 目的

ブラウザのBlockly編集画面でキャプチャ画像から矩形を切り出してテンプレート画像（`Template/`以下）を作り、そのままvisionブロックのテンプレ欄へ反映できるようにする。生成はBlockly→Python片方向のみを維持する。

## 2. スコープ

- 本書: 「テンプレ作成」区画＋`GET /frame`＋`POST /template`＋連番保存＋キャッシュ無効化＋候補更新。
- やらないこと: 別ページ化、ブラウザ側crop、マスク画像作成、既存画像の編集・削除、精度保証。

## 3. アーキテクチャ（§1承認済み）

- 生成の単一真実はvendored JS（`SerialController/assets/blockly/`）。
- Python側は検証＋保存＋一覧＋取得・切出しのみ。新設は`services/blockly_capture.py`だけ。`core/`は不変。
- カメラ到達: `ui/blockly_editor.open_blockly_editor(...)`に`get_frame: Callable[[], bytes | None]`（PNGバイト列、撮れなければNone）を追加注入。`Menubar`側で最新1枚のPNG化関数を渡す。uiはWindow/Cameraの型を知らないまま。
- 画面は`editor.html`内「テンプレ作成」区画に同居（別ページなし）。

## 4. コンポーネント（§2承認済み）

- `GET /frame`: 最新1枚をPNGで返す。撮れなければ`ok:false`＋理由（未接続/Disable/停止。文言は既存`_readFrameOrRaise`流用）。
- 「テンプレ作成」区画: `[キャプチャ取得]`→画像表示→マウスドラッグで矩形（実画像換算で8px未満の辺がある矩形は無効）→保存名入力→`[切出し保存]`。
- `POST /template`: 保存名＋正規化矩形（配信フレーム相対の`{x, y, width, height}`小数0〜1）を受ける。サーバ側で元画像サイズに戻してcrop・PNG保存。保存先は`Template/blockly/<名>.png`固定、重複は`_2`・`_3`連番。`..`・絶対パス・`:`は拒否（Phase Aの検査と同規則）。
- 保存後にテンプレートキャッシュ無効化＋`/templates`再取得で候補更新。

## 5. データフロー（§3承認済み）

- 取得: `[キャプチャ取得]`→`GET /frame`→画像表示（前回矩形はクリア）。連打しても最新を取り直すだけ。
- 選択: 画像上でドラッグ→矩形表示。ドラッグし直しで更新。画像外はみ出しは画像内に丸める。
- 保存: `[切出し保存]`→`POST /template`（保存名＋正規化矩形）→応答の確定パス表示→`/templates`再取得→`tpllist`候補へ。以降はPhase Aの選択中反映フローにそのまま使える。

## 6. エラー処理（§4承認済み）

- カメラなし: `/frame`は`ok:false`＋理由。区画に赤字表示し、保存操作はできない状態に見せる。
- 不正入力: 保存名の空・`..`・絶対パス・`:`は保存せず失敗応答。矩形の逆転・ゼロ面積・範囲外は失敗応答（画像外はみ出しのみ丸めて救う）。
- 保存失敗: ディスクエラー時は失敗応答＋ログ。半端ファイルは残さない（Phase Aの原子保存と同方式）。
- 旧資産・既存フローへの影響なし（追加のみ）。

## 7. テスト・成功基準（§5承認済み）

- 自動: `tests/test_blockly_capture.py`（一覧・crop座標計算・連番・拒否則・原子性。実カメラなし、`get_frame`は注入fake）。`tests/test_blockly_editor.py`拡張（`/frame`・`/template`の実HTTP：正常・失敗系）。ドラッグ矩形の純粋部はnode vmで検証。Gate全緑（ruff check＋format --check＋mypy＋bounds＋userapi＋pytest）。
- 手動（ユーザ環境、カメラ隣接）: 取得→ドラッグ→保存→候補に出る→visionブロックのテンプレ欄に反映→実行してマッチすること。旧フロー不変。
- 成功基準: 自動Gate緑＋手動の取得→保存→反映→実行往復の確認。マッチ精度自体は対象外。

## 8. 影響範囲・制約

- 触るファイル: `services/blockly_capture.py`（新規）、`ui/blockly_editor.py`（`get_frame`引数＋`/frame`・`/template`）、`assets/blockly/editor.html`（区画）、`Menubar.py`（取得関数の注入）、`docs/BLOCKLY_EDITOR.md`、tests。
- 触らないもの: `core/`一式、`Commands.*`公開面、既存ブロック・既存エンドポイントの形、`settings*.ini`。
- 規約: `SerialController/`起点の絶対import、相対import禁止。コメント・利用者文面は日本語。4スペース。CDN禁止。資源パスは`WindowUtils.APP_DIR`起点。`services/`で`os.chdir`しない。サーバスレッドからtkinterを触らない。

## 9. 決定記録

- 保存先: 自動連番（パス入力＋上書き確認案を退けた。保存先フォルダは`Template/blockly/`固定を仮定→specレビューで確定させる）。
- アプローチ: 案A（分離B・ブラウザ側crop Cを退けた。往復最小・再エンコード回避のため）。
