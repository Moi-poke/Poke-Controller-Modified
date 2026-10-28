# Blockly CLI 契約

[共通手順](../README.md) から必要なコマンドだけを使う。
保存済み作品の正本は `.blockly.json`、`.py` は派生物、[コンパクト仕様](spec.md) は新規作成用入力。

## 呼出しと結果

リポジトリルートで実行する。
オプションはサブコマンドの後に置き、空白を含むパスは利用シェルに合わせて引用する。
`--app-dir "<app>"` の既定は対象 clone の `SerialController/`。別配置には `assets/blockly` と `Template/` が必要。
**`live` は `--app-dir` を受けない。**

```text
uv run --frozen python tools/blockly_agent.py --help
uv run --frozen python tools/blockly_agent.py patch --help
```

`--json` 指定時は stdout だけを JSON として読む。ログは stderr に出るため混ぜない。
現実装は全失敗経路を JSON 化していない。終了コード・stderr も確認し、空の stdout を成功と扱わない。
終了コードは 0 が成功、1 が検査・保存・通信等の失敗、2 が使い方・入力 JSON の読込み・node 不在等。
`match` の不一致は入力エラーではなく、終了 0 でも `matched:false` になり得る。

## コマンド一覧

`live` 以外は `--app-dir`、`catalog` 以外は `--json` を受ける。
`catalog` の機械可読形式は **`--format json`** であり `--json` ではない。

| コマンド | 入力・主なオプション | 代表結果と用途 |
| --- | --- | --- |
| `catalog` | `--format json\|markdown`（既定 json）、`--output <path>` | ブロックの fields / inputs / 選択肢。Markdown はテンプレ一覧も含む。output はファイルを書き出す。 |
| `check <spec>` | `--workspace` で保存済み workspace 入力 | `ok`, `errors`, `warnings`, `stem`, `workspace`, `code`。workspace 入力時は `workspaceSha256`。保存しない。 |
| `build <spec>` | `--workspace`, `--write`, `--stem`, `--expect-current-sha256`, `--overwrite-external` | write なしは workspace。仕様入力の build 単体は組立てのみなので別途 check する。write は検査後に対保存し `status:"saved"`, `py`, `json`。 |
| `patch <workspace>` | 必須 `--ops <file>`、`--dry-run` または `--write --stem <name>` または `--output <new-file>`、`--expect-current-sha256`, `--overwrite-external` | `workspace`, `workspaceSha256`, `code`, `changes`。status は `dry_run` / `saved` / `written` / `patched`。 |
| `live status` | 必須 `--server <origin>`、`--output <new-file>`, `--json` | `revision`, `dirty`, `workspace`。output は workspace だけを新規ファイルに書く。 |
| `live patch` | 必須 `--server <origin> --ops <file> --base-revision <revision>`、`--json` | `status:"applied"`, `revision`, `workspace`, `changes`, `code`。競合時は `status:"conflict"`。保存しない。 |
| `render <workspace>` | 保存済み workspace 入力 | `ok`, `code`, `errors`, `warnings`。Python を再生成する。保存しない。 |
| `trial <spec>` | 必須 `--server <origin> --max-seconds <seconds>`、`--workspace`, `--allow-input`, `--expected-sha256` | 既定は `status:"preview"`, `code`, `codeSha256`, `workspace`, `server`, `maxSeconds`。許可時だけ本体へ入力する。 |
| `frame` | 必須 `--server <origin> --output <new.png>` | `/frame` の生 PNG と `width`, `height`。本体とエディタが必要。カメラを開き直さない。 |
| `template` | 必須 `--image <file> --name <name> --rect x1 y1 x2 y2` | `Template/blockly/` に切出し画像を保存し、実名 `template` を返す。同名は連番。 |
| `match` | 必須 `--image <file> --template <relative-name>`、`--threshold`（既定 0.7）、`--gray` / `--no-gray`（既定 gray）、`--crop x1 y1 x2 y2`, `--preview <new.png>` | `score`, `matched`, `rect`, `count`, `preview`。実画像を照合し注釈画像を確認する。 |
| `color` | 必須 `--image <file> --lower H S V --upper H S V`、`--ratio`（既定 0.6）、`--crop x1 y1 x2 y2`, `--preview <new.png>` | 実測 `ratio` と合否。CLI の `--ratio` は基準であり実測値ではない。 |

`build --write` の仕様入力には仕様の `stem` または `--stem` が必要。
workspace 入力の保存には `--stem` が必要で、版ガードは workspace 保存でのみ使う。
`--overwrite-external` は既存 workspace の保存で手編集を上書きする明示許可時だけ使う。
`build --json` の envelope 全体を workspace 入力にしない。中の `workspace` をファイルに書いて渡す。

`patch` の `--dry-run` / `--write` / `--output` は併用できない。
`--expect-current-sha256` は `--write` と組み合わせる。
`--output` は新規 workspace ファイルのみを作り、保存ペアを更新しない。既存出力先は上書きしない。

## 新規と保存済み作品

新規は対象 clone の最新カタログを確認してから仕様を用意する。
[6 作例](../examples/) の `<name>` は `basic` / `flow` / `vision` / `subroutine` / `audio` / `dialog`。
次は別配置で生成・保存・再生成する一行コマンド（通常配置なら `--app-dir` は省略可）。

```text
uv run --frozen python tools/blockly_agent.py catalog --format json
uv run --frozen python tools/blockly_agent.py check docs/blockly-agent/examples/<name>.json --app-dir "<app>" --json
uv run --frozen python tools/blockly_agent.py build docs/blockly-agent/examples/<name>.json --write --app-dir "<app>" --json
uv run --frozen python tools/blockly_agent.py render "<app>/Commands/PythonCommands/<name>.blockly.json" --app-dir "<app>" --json
```

既存作品は仕様へ逆変換せず、現 workspace の版と対象を確認する。
`work/` と操作ファイルは通常のファイル編集で先に用意する。

```text
uv run --frozen python tools/blockly_agent.py check MyBlock.blockly.json --workspace --json
uv run --frozen python tools/blockly_agent.py patch MyBlock.blockly.json --ops work/ops.json --dry-run --json
uv run --frozen python tools/blockly_agent.py patch MyBlock.blockly.json --ops work/ops.json --write --stem MyBlock --expect-current-sha256 "<workspaceSha256>" --json
```

## 操作 JSON

ファイルの形は `{"operations":[...]}`。
操作は順に適用して検査し、1 件でも不正なら保存しない。
対象は `blockId` または `selector:{"type":"...","occurrence":0}` のどちらか一方で指定する。
`occurrence` は文書順の 0 始まり。同種が複数なら workspace を読んで対象を確認する。
接続参照も `{"blockId":"..."}` または `{"selector":{...}}` を使う。

| 操作 | 項目と制約 |
| --- | --- |
| `set_field` | 対象 + `field`, `value`。型・選択肢・CROP をカタログで検査する。変数フィールドは変更できない。 |
| `set_extra_state` | 対象 + `extraState`（オブジェクトまたは null）。マージではなく置換。null は削除。例: controls_if の `{"hasElse":true}`。 |
| `create_block` | `type`、任意 `id` と `fields`。id は重複不可、省略時は生成。`parent` + `input` または `after` へ接続できる。接続先なしなら任意の `x` / `y` で独立配置。 |
| `connect` | 既存の `child` と、`parent` + `input` または `after`。型と循環を検査して接続し直す。入力名はカタログで確認する。 |
| `delete_block` | 対象。途中の文は前後をつないで抜く。入力の子があるブロックは `recursive:true` が必要で、その場合は後続も含む部分木を削除する。プログラム・サブルーチン定義は削除不可。 |

待ちの変更:

```json
{"operations":[{"op":"set_field","selector":{"type":"pokecon_wait","occurrence":0},"field":"SEC","value":2.5}]}
```

画像待ちの後ろへ待ちを挿入する例:

```json
{"operations":[{"op":"create_block","type":"pokecon_wait","id":"agent_wait","fields":{"SEC":3},"insert":true,"after":{"selector":{"type":"pokecon_vision_wait_appear","occurrence":0}}}]}
```

占有中の文列は `insert:true` で後続を残して挿入できる。値入力の置換ではない。
既存ブロックの接続例（対象の存在・入力名を先に確認する）:

```json
{"operations":[{"op":"connect","child":{"blockId":"agent_wait"},"parent":{"selector":{"type":"pokecon_program","occurrence":0}},"input":"DO","insert":true}]}
```

挿入した待ちを前後をつないで抜く例:

```json
{"operations":[{"op":"delete_block","blockId":"agent_wait"}]}
```

## 版と要約値

| 取得した値 | 渡すオプション・用途 |
| --- | --- |
| `workspaceSha256` | `--expect-current-sha256`。保存済みディスク作品の同時変更検出。 |
| `revision` | `--base-revision`。開いたライブ画面の版一致。 |
| `codeSha256` | `--expected-sha256`。許可した試行コードの一致。 |
| `pokecon.pySha256` | 保存 `.py` の手編集検出。上の版・試行ガードとは交換できない。 |

`codeSha256` は通常生成コードの LF 正規化 UTF-8 SHA256。
preview の `code` は試行用の目印を含むため再ハッシュせず、返った値をそのまま使う。
ディスクの版ガードはブラウザの未保存編集を検出しない。
ディスク変更は開いた画面に自動反映されないため、画面と共同編集するときは `live` を使う。

## ライブと限定試行

`<origin>` は、その clone の対象プロファイルで利用者が開いたエディタの「URLをコピー」から取得し、パスを除いたもの。
loopback の HTTP origin だけを渡し、固定ポートを仮定せず、別プロファイルを探したり LAN 公開したりしない。

```text
uv run --frozen python tools/blockly_agent.py live status --server "<origin>" --json
uv run --frozen python tools/blockly_agent.py live patch --server "<origin>" --ops work/ops.json --base-revision "<revision>" --json
```

ライブ適用は保存せず、人が GUI で保存する。構造変更では Undo 履歴がクリアされる。
競合なら状態を読み直して再計画し、強制上書きで回避しない。

生成・検査・保存・ローカル画像照合・試行 preview は実機不要。
`frame` / `live` / `trial --allow-input` は本体とエディタが必要。
preview は URL と上限秒数を引数として必要とするが、接続・入力はしない。

```text
uv run --frozen python tools/blockly_agent.py trial MyBlock.blockly.json --workspace --server "<origin>" --max-seconds 15 --json
uv run --frozen python tools/blockly_agent.py trial MyBlock.blockly.json --workspace --server "<origin>" --max-seconds 15 --allow-input --expected-sha256 "<codeSha256>" --json
```

2 行目は利用者がそのコード・URL・上限を明示許可した場合だけ実行する。
変更版や再試行は再許可が必要。
試行には `boundedTrial` / `runIdControl` を持つエディタを使う。
結果の `status` は `completed`（終了 0）/ `stopped` / `timed_out` / `failed` / `superseded`。
`logs`, `blockId`, `error`, `errorBlock`, `timedOut` を読む。
`--allow-input` はサンドボックスではなく、GUI／OS 停止時のハードな期限保証もない。
画像コマンドの座標・正負例・結果の読み方は [vision ガイド](vision.md) を参照する。
