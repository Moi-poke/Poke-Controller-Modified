# Blockly を外部エージェントで作成・編集する

コンパクトな JSON 仕様または保存済み workspace から、本物の Blockly で Python を生成し、`.blockly.json` と `.py` の対を保存する。
通常の Python コマンドを直接書く作業や、GUI のドラッグ操作確認はこの手順の対象外。
導入は [外部 AI の利用手順](../AI_AUTOMATION.md)、各引数・操作 JSON・結果の読み方は [CLI 契約](references/cli.md) を参照する。

## 前提

- リポジトリ指定の Python / uv 環境と PATH 上の `node` を使い、コマンドはリポジトリルートで実行する。
- ファイルや親フォルダはエージェントの通常のファイル編集で用意する。空白を含むパスは利用シェルに合わせて引用する。
- 実画像を使う場合は、画像ファイルを開いて意味を読めること。閲覧できなければ対象を推測せず、不足を報告する。
- `frame` / `live` / `trial --allow-input` は、その clone の本体と対象プロファイルの Blockly エディタを利用者が開く必要がある。
- エディタ起動ダイアログの「URLをコピー」からパスを除いた origin を取り、`--server` に渡す。固定ポート・LAN 公開・別プロファイルの探索は使わない。

生成・検査・保存・ローカル画像照合・試行 preview は実機なしで動く。
preview にも `--server` と `--max-seconds` は必要だが、本体へ接続せず入力もしない。
`--app-dir` は別置き場の完全なアプリ配置（`assets/blockly` と `Template/` を含む）を指す場合だけ使う。
`live` に `--app-dir` はなく、対象は `--server` で決まる。

## 作成ループと権限

1. 開始画面・成功／失敗画面・送る入力・打切り条件を特定する。ゲーム仕様や画面の意味を推測で確定せず、意図が不明な点だけ利用者に聞く。
2. 対象 clone の `catalog` で最新のブロック・テンプレートを確認し、目的に応じて下記の新規・保存済み・ライブの経路を選ぶ。
3. 生成コードを読み、検査と必要な認識準備を行う。画像認識は [vision ガイド](references/vision.md) に従う。
4. 実機へ送る場合だけ、コード・対象 URL・上限秒数を示して明示許可を得る。変更版や失敗後の再試行は前の許可に含めない。
5. 保存した対と検証範囲を報告する。人が実エディタで開いて編集できるかの確認と、実機での確認を区別する。

画像の読取り・切出し・照合・検査は Switch 入力を送らない。
本体への入力送信は `trial --allow-input` だけを使い、許可した版の digest を渡す。
`--allow-input` は同意の記録であってサンドボックスではない。
時間上限は GUI の見回りと協調停止によるもので、GUI／OS 停止時のハードな期限保証はない。
本体の Esc／停止ボタンも案内し、許可なく HOME・設定変更・購入などへ動かさない。

## 新規作成

保存済み作品の正本は **`.blockly.json`**、`.py` は派生物、コンパクト仕様は新規作成用の入力。
`.py` を直接書かない。
新規は `catalog` → 仕様 → `check` → `build --write` の順に進める。

```text
uv run --frozen python tools/blockly_agent.py catalog --format markdown
```

[作例](examples/) から近いものを選び、[仕様](references/spec.md) に従って `work/spec.json` と親フォルダを用意する。
トップレベルは `stem` / `name` / `tags` / `do` / `subs`。
たとえば A を 3 回押す新規作品は次の入力にする。

```json
{"stem":"ThreeA","name":"Aを3回押す","do":[{"repeat":3,"do":[{"press":"A"}]}]}
```

```text
uv run --frozen python tools/blockly_agent.py check work/spec.json --json
uv run --frozen python tools/blockly_agent.py build work/spec.json --write --json
uv run --frozen python tools/blockly_agent.py render SerialController/Commands/PythonCommands/ThreeA.blockly.json --json
```

`check` の `code` と `workspace` を確認する。
`build --write` は検査後に `Commands/PythonCommands/<stem>.py` と同名 `.blockly.json` を保存する。
同名作品を上書きする意図は先に確認する。
`build --json` の envelope 全体ではなく、その `workspace` が workspace 入力になる。

## 保存済み作品の部分編集

GUI 編集済み作品を古い仕様から作り直さない。
ID・配置・コメント・変数を保持した workspace を読み、必要なフィールド／接続だけを変更する。

```text
uv run --frozen python tools/blockly_agent.py check MyBlock.blockly.json --workspace --json
```

返った `workspaceSha256` を控え、対象ブロックを確認する。
`work/` を含め `work/ops.json` を通常のファイル編集で用意する（同種が複数なら文書順 0 始まりの `occurrence` または確認済みの `blockId` で指定）。

```json
{"operations":[{"op":"set_field","selector":{"type":"pokecon_wait","occurrence":0},"field":"SEC","value":2.5}]}
```

```text
uv run --frozen python tools/blockly_agent.py patch MyBlock.blockly.json --ops work/ops.json --dry-run --json
uv run --frozen python tools/blockly_agent.py patch MyBlock.blockly.json --ops work/ops.json --write --stem MyBlock --expect-current-sha256 "<workspaceSha256>" --json
```

5 操作の詳細は [CLI の操作 JSON](references/cli.md#操作-json) に集約する。
1 件でも不正なら保存しない。
`.py` だけが手編集された `externalEdit` は通常保存を拒否する。取り込み方を利用者に確認し、明示許可後だけ `--overwrite-external` を使う。
GUI 保存物の `pokecon` 記録は除かず、そのまま渡してよい。

版ガードはディスクの保存済み作品の変更だけを検出し、ブラウザの未保存編集は検出しない。
ディスク変更が開いた画面に自動反映されるわけではない。
開いた画面と共同編集する場合は次のライブ経路を使う。
競合したら読み直して再計画し、強制上書きで回避しない。

## 開いたエディタへのライブ編集

`live status` → 操作 JSON → `live patch --base-revision` → 人が GUI 保存、の順で進める。
`<origin>` は利用者が示したエディタの origin に置き換える。

```text
uv run --frozen python tools/blockly_agent.py live status --server "<origin>" --json
uv run --frozen python tools/blockly_agent.py live patch --server "<origin>" --ops work/ops.json --base-revision "<revision>" --json
```

- `live status` の workspace から対象を選び、返った `revision` を指定する。画面の版と違えば `status:"conflict"` となり、何も変えない。再取得して再計画する。
- 適用しても保存しない。保存は人が行い、画面には「AIが編集しました（保存で確定）」と出る。
- フィールドは差分適用、構造変更は検証済み workspace の読込みになるため Undo 履歴がクリアされる。
- 同じアプリの別タブには適用しない（1 タブだけが貸与される）。エディタを閉じると使えない。
- マウス・キーボード入力の代わりに Blockly API を使う。

## 認識準備と限定試行

[vision ガイド](references/vision.md) の実画像取得 → 対象選択 → 切出し → 別の正例・負例で照合 → 条件決定を通す。
縮小表示の座標をそのまま使わず、返った原画像の `width` / `height` で画素座標へ換算する。
形・文字列の見た目はテンプレート、広い単色領域は HSV 色割合を選ぶ。
OCR・物体検出ブロックはない。
注釈画像と数値を確認し、切出し元との一致だけで認識成功としない。

`TEMPLATE` は `Template/` からの実在する相対名（例 `controller_check/btn_a.png`）。
無ければ実画像から `template` で切り出し、返った実名を使う。同名は別名保存される。
`CROP` は空か `x1,y1,x2,y2`（実画素、`x2>x1`、`y2>y1`）、`THRESHOLD` は 0〜1。
テンプレートの切出し矩形と検索範囲は別物。
同梱画像と利用者の私有画像を区別し、ダミー画像は作らない。

試行は先に preview でコードを示し、許可後だけ実機へ送る。
[版の表](references/cli.md#版と要約値) の `codeSha256` をそのまま渡し、目印を含む preview コードから再計算しない。
ログ・実行ブロック ID・前後フレームを確認し、実機なしなら「実機確認は未実施」と報告する。

## 失敗時

- 未知のブロック・欄・型は、エラーの候補とカタログの `fields` / `inputs` を確認する。表示専用 `PREVIEW` / `CAPOPEN` は無視される。
- サブルーチンは定義・呼出・引数を揃える。重複・未定義・循環・引数不一致は検査で拒否される。
- `break` / `continue` は文列の最後に置く。CROP・閾値は書式と範囲を直す。
- 画像の欠損・読取り不可・接続失敗・閲覧機能不足は理由と未確認の範囲を報告する。必要なら利用者に必要な画面を依頼する。
- 負例なしでは認識検証済みにしない。正負例を分離できなければ特徴領域・カラー／グレー・検索範囲を見直す。閾値を下げ続けて通さない。
- エラーを消すために条件や処理を削らず、ダミーで成功にしない。

## 参照

- [コンパクト仕様 v1](references/spec.md) / [CLI 契約](references/cli.md) / [画像認識](references/vision.md)
- [ブロック一覧](references/blocks.md) は生成された参考スナップショット。最新ブロック・テンプレートは対象 clone の `catalog` で確認する。手編集しない。
- [6 作例](examples/) / [検証範囲](../BLOCKLY_AGENT_VERIFICATION.md) / [人向け GUI 操作](../BLOCKLY_EDITOR.md)
- 実装: [CLI](../../tools/blockly_agent.py)、[authoring](../../SerialController/services/blockly_author.py)
