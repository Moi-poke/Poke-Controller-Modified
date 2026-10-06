# TODO-22 Phase A: Blockly画像認識ブロック 設計書

- 日付: 2026-09-08
- 前提: TODO-20基盤完成（`af72caa`）。plan `docs/superpowers/plans/2026-09-07-blockly-editor.md`、handoff `pokecon-handoff.md`。
- 方式: 案A（datalist併用・自動切替・形式のみ検証）。ROI切出しはPhase Bに分割。

## 1. 目的

ブラウザのBlocklyで画像認識つき操作（出現待ち・存在判定・消滅待ち・位置取得・安定待ち・色判定）を組み立て、`.py`＋`.blockly.json`として保存し、既存一覧から実行できるようにする。生成はBlockly→Python片方向のみ（逆変換なし）を維持する。

## 2. スコープ

- Phase A（本書）: visionブロック6種＋Template一覧選択（datalist）＋自動基底切替＋`/templates`配信＋zip配布対応（`.blockly.json`同梱・素名警告）。フルセット。
- Phase B（別設計）: キャプチャ映像表示→マウスROI選択→Template保存UI。一覧選択式の要望から派生、別サブシステム級のため分割。画像作成時の注意（サイズ・形式・`Template/<名>/`配置・キャッシュ無効化）はPhase B設計で扱う。
- やらないこと: mask/GPU/show系オプションのブロック化（既定値固定）、保存時の存在必須チェック、サーバ側コード生成、逆変換、精度保証。

## 3. アーキテクチャ（§1承認済み）

- 生成の単一真実はvendored JS（`SerialController/assets/blockly/pokecon_blocks.js`）。
- Python側は検証＋保存＋一覧のみ。新設は画像一覧・参照検査（services側）＋配信1経路（`GET /templates`）だけ。
- `core/`はtkinterなし・FS非依存を維持（存在チェックはしない）。`services/`はFS走査可。
- 既存の`.py`＋`.blockly.json`対、`reloadCommands()`、mtimeポーラー、実行中拒否はそのまま。

## 4. コンポーネント（§2承認済み）

- 新カテゴリ「画像認識」に6種（PokeCon分類へ追加、既存3種は不変）。
  - `pokecon_vision_contains`（値・Boolean）: template＋threshold。標準ifの条件に直結。生成は`self.isContainTemplate("TPL", threshold=T)`。
  - `pokecon_vision_wait_appear`（文）: template＋timeout＋threshold。生成は`self.waitTemplate("TPL", timeout=T, threshold=H)`。
  - `pokecon_vision_wait_gone`（文）: 同上。生成は`self.waitTemplateGone(...)`。
  - `pokecon_vision_position`（値）: template＋threshold。生成は`self.getTemplatePosition("TPL", threshold=H)`（中心座標またはNone）。
  - `pokecon_vision_wait_stable`（文）: quiet＋timeout。生成は`self.waitStable(quiet=Q, timeout=T)`。
  - `pokecon_vision_color`（値・Boolean）: 下限(H1,S1,V1)＋上限(H2,S2,V2)＋割合Rの7欄。生成は`self.isSimilarColor([], [H1,S1,V1], [H2,S2,V2], ratio=R)`（cropは使わない）。
- テンプレ欄は自由入力＋datalist。`GET /templates`が`Template/**/*.png|jpg|jpeg|bmp`の相対一覧（posix、ソート済み）を返す。取得失敗時は候補なしでも保存可。
- crop欄はtemplate系4ブロック共通で「空＝全体」または「x1,y1,x2,y2」テキスト1欄。空なら生成コードは`crop`引数を付けない（既定`[]`＝全体）。記入時は`crop=[x1,y1,x2,y2]`（整数4つ）を付ける。
- 生成器自動切替: `pokecon_program`がDO部コード生成後に本体文字列を検査し、vision系API名（`isContainTemplate`/`waitTemplate`/`waitTemplateGone`/`getTemplatePosition`/`waitStable`/`getColorRatio`/`isSimilarColor`）を含めばImageProcヘッダ、なければ従来ヘッダ。
  - ImageProc時: `from Commands.PythonCommandBase import ImageProcPythonCommand`＋`class BlocklyCmd(ImageProcPythonCommand)`＋`def __init__(self, cam, gui=None): super().__init__(cam, gui)`。
  - 従来時: 現行の`PythonCommand`ヘッダのまま。既存保存物は再保存まで不変。
- Python追加: `services`に`list_image_templates(app_dir) -> list[str]`＋テンプレ参照の形式検査`validate_template_refs(python_code) -> list[str]`（ASTでvision系APIの第1引数定数を取り出し、空・`..`・絶対パス・`:`付きを拒否。`core/blockly_validate.py`は変更なし）＋`ui/blockly_editor.py`に`GET /templates`。保存時は既存検証に加え本検査も通す。

## 5. データフロー（§3承認済み）

- 起動時: `editor.html`が`./templates`をfetch→datalist反映。失敗時は自由入力のみ。
- 保存時: 従来通り`POST ./save`（stem＋workspaceJson＋pythonCode）。形式は`.py`＋`.blockly.json`対。約1秒で一覧反映。
- 実行時: `ui/command_panel._buildCommand`がImageProc系に`(camera, gui代理)`を注入、従来型は`()`のまま。カメラ未接続時は`_readFrameOrRaise`が理由明示のRuntimeError。

## 6. エラー処理（§4承認済み）

- テンプレ名は空・`..`・絶対パス・`:`付きを拒否（保存時の`validate_template_refs`＋JS保存前チェックの二段）。`pokopia/a.png`のような相対サブディレクトリは許可。存在チェックはしない。打ち間違いは実行時のFileNotFoundError（置き場所付き案内、現行`core/CommandVision.py`）で分かる。
- `/`なし素名（例: `shiny_mark.png`）は保存を通すが警告する（共有画像依存のため配布zipに含まれない）。`SaveResult`に`warnings: list[str]`を新設（既存字段は不変）。文面例: 「共有画像のため配布zipに含まれません。配布する場合は`Template/<名>/...`に置いてください」。
- 数値はJS数値欄で制限（threshold 0〜1、timeout/interval/quiet正数）。範囲外でもクラッシュせず判定が寄るだけ。
- crop不正時は`_cropOrRaise`が画面サイズ付きValueError。
- `/templates`失敗時は候補なしでも保存可。旧`.blockly.json`はそのまま読める（追加のみ）。

## 7. テスト・成功基準（§5承認済み）

- 自動: `tests/test_blockly_browser.py`拡張（node vmでvision混じり→ImageProcヘッダ＋`__init__(cam,gui)`、混じりなし→従来ヘッダ。node無しはskip）。新`tests/test_blockly_templates.py`（一覧の拡張子絞り・ソート・空ディレクトリ・`..`安全＋`validate_template_refs`の拒否/許可）。validateはvisionコード通過を確認。Gate全緑（ruff check＋format --check＋mypy＋bounds＋userapi＋pytest）。
- 手動（ユーザ環境、カメラ隣接）: 候補表示→wait_appear＋pressを組んで保存→`.py`がImageProc化・ruff通過→一覧反映→カメラあり実行（画像ありで待ち、なしで置き場所付きエラー）。旧MyBlockは従来通り動作。
- 成功基準: 自動Gate緑＋手動の保存→反映→実行往復がエラー文言含め確認できること。実画像マッチ精度自体は対象外。

## 8. 影響範囲・制約

- 触るファイル: `assets/blockly/pokecon_blocks.js`、`assets/blockly/editor.html`、`services/`（templates一覧＋参照検査）、`ui/blockly_editor.py`（`/templates`＋保存時検査呼出し＋警告表示）、`core/pack_zip.py`（同名`.blockly.json`同梱）、`services/script_pack.py`（同梱物の導入記録・削除）、`docs/BLOCKLY_EDITOR.md`、tests（browser拡張＋templates新規＋save拡張＋pack同梱）。
- 触らないもの: `core/blockly_validate.py`、`core/pack_manifest.py`（manifest v0不変、`.blockly.json`は派生成果物のため記載しない）、`settings*.ini`、`Commands.*`公開面、既存3ブロックの生成形。
- zip配布対応: 同名`.blockly.json`があればpackに同梱・導入・削除する（無い旧packは従来通り動作）。manifestの`templates`は`Template/<名>/...`形のまま。
- 規約: `SerialController/`起点の絶対import、相対import禁止。コメント・利用者文面は日本語。4スペース。CDN禁止。`Window.py`の`os.chdir(BASE_DIR)`前提で資源パスは`WindowUtils.APP_DIR`起点。

## 9. 決定記録

- 範囲: フルセット（最小1点・最小セット案を退けた）。
- 切替: 自動切替（常時ImageProc・別ブロック分離案を退けた。既存互換のため）。
- テンプレ指定: 一覧選択式（自由入力・存在チェックあり案からの発展）。ROI切出しはPhase Bへ分割。
- アプローチ: 案A（厳格検証B・常時ImageProc Cを退けた。YAGNI＋後入れ運用維持のため）。
- zip配布: `.blockly.json`同梱＋素名警告を追加（2026-09-08指摘対応）。manifest v0は不変。
