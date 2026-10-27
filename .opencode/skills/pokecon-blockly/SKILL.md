---
name: pokecon-blockly
description: Poke-Controller Modified の Blockly プログラムを、コンパクトな JSON 仕様からヘッドレスに組み立てて `.blockly.json` と `.py` の対として保存する手順。新しい Blockly マクロの作成、既存 Blockly 保存物の修正、画像認識・音声・サブルーチンを含む自動化の組み立てに使う。
---

# Poke-Controller Blockly

Blockly の見た目を手で組み立てる代わりに、小さな宣言的仕様を書き、本物の Blockly でコード生成して保存する。生成と検査はアプリ本体を起動せずに行える。

## 使う場面

- 「Blockly で〜を作って」「自動で Blockly スクリプトを組みたい」
- 「既存の Blockly 保存物（`.blockly.json`）を直したい」
- 「画像認識マクロを Blockly で組みたい」「音検知や設定入力を Blockly で組みたい」

使わない場面:

- 通常の Python コマンドを新規に書く（`Commands/PythonCommands/` に直接 `.py` を置く話）。
- 実機を動かすこと自体、GUI の操作確認（それは本体や [gui-probe](../gui-probe/SKILL.md) の担当）。

## 前提

- Python 3.12 と `uv`（コマンドは `uv run --frozen ...` を基本にする）。
- `node`（Blockly のヘッドレス生成に必要。PATH 上に無いと `check` / `render` が失敗する）。
- アプリ本体・カメラ・シリアル実機は不要。`check` と `render` はヘッドレスで完結する。

## 正本の規律

- `.py` を直接書かない。正本はコンパクト仕様と `.blockly.json`。
- 保存は必ず `build --write` 経由。`build --write` は検査を通してから `Commands/PythonCommands/<stem>.py` と `<stem>.blockly.json` の対を書く。
- 既存の保存物を上書きするときは、先にユーザーの意図を確認する。同名の `.py` があるからといって黙って置き換えない。
- 画像テンプレートを推測で作らない。`Template/` の既存ファイルから選ぶ。
- 検査に落ちたら、エラーを消すために仕様を歪めない。原因（ブロック種・フィールド・入力の型・サブプログラム名）を直す。

## 手順

1. カタログと画像を見る。
   ```sh
   uv run --frozen python tools/blockly_agent.py catalog --format markdown
   ls SerialController/Template
   ```
   `references/blocks.md` は生成物なので、手で編集しない。必要なら `catalog --format json` で機械可読の一覧を見る。
2. 仕様を書く。まずは [`examples/`](examples/) の近いものをコピーする。トップレベルは `stem` / `name` / `tags` / `do` / `subs`。
3. 検査する。
   ```sh
   uv run --frozen python tools/blockly_agent.py check my_spec.json
   uv run --frozen python tools/blockly_agent.py check my_spec.json --json
   ```
   `--json` の `code` で生成 Python を確認できる。`workspace` は保存される `.blockly.json` の中身。
4. エラーを直す。候補付きのエラー（未知ブロック・未知フィールド・未知入力）は、カタログの正しい名前へ寄せる。型違いは値入力の型（Boolean/Number/String）を直す。
5. 保存する。`stem` が仕様に無い場合は `--stem` を付ける。
   ```sh
   uv run --frozen python tools/blockly_agent.py build my_spec.json --write
   ```
   既存ファイルを上書きする場合は、この前にユーザーへ確認する。
6. 確認する。
   ```sh
   uv run --frozen python tools/blockly_agent.py render SerialController/Commands/PythonCommands/MyBlock.blockly.json
   uv run --frozen pytest tests -k blockly -q
   ```
   `render` は保存済み `.blockly.json` から Python を出し直す。`task test -k blockly` でもよい。

`--app-dir` はテストや別置き場の `SerialController/` を指すときだけ使う（通常は既定の `SerialController/`）。オプションはサブコマンドの後ろに置く。

## 画像テンプレート

- `TEMPLATE` は `SerialController/Template/` からの相対名。例: `controller_check/btn_a.png`。
- 候補は `references/blocks.md` の「画像テンプレート」節、`ls SerialController/Template`、または `catalog --format markdown` で確認する。
- `TEMPLATE` が空、または `Template/<名前>` が無いと `check` が落ちる。
- 適切な画像が無ければ、ブロックの 📷 調整（Blockly エディタ）でユーザーに切り出してもらう。勝手にダミー画像を作らない。
- `CROP` は空か `x1,y1,x2,y2`（実画素、`x2>x1`、`y2>y1`）。`THRESHOLD` は 0〜1。

## サブルーチン

- トップレベルの `subs` に `name` / `args`（配列またはカンマ文字列）/ `do` / 任意の `return` を書く。
- 呼出は文なら `call`、値なら `call_value`。引数は最大3個。
- 定義名の重複、未定義呼出、循環呼出、引数の数・名前の不一致は `check` が落とす。
- サブルーチン名・引数名は識別子（`do`・`self`・`__init__` などは不可）。

## 失敗時の直し方

- 未知ブロック → エラーの「似た名前」と `references/blocks.md` を見て、カタログにある種へ直す。
- 未知のフィールド・入力 → 同じくカタログの `fields` / `inputs` を見る。表示専用の `PREVIEW` / `CAPOPEN` は仕様に書いても無視される。
- 型違い（`この入力は Boolean 型です` など）→ 値の種類を合わせる。真偽値なら `true` / `false` か真偽を返すブロック、数値なら数値か数値ブロック。
- 未定義のサブルーチン → `subs` に定義を足すか、`call` / `call_value` の名前を直す。
- 引数の数が違う → 定義の `args` と呼出の `args` を揃える。
- 画像が無い → `Template/` の実在名に変える。無ければユーザーに撮ってもらう。
- `CROP` / `THRESHOLD` → 書式と範囲を直す。`break` / `continue` は文の並びの最後に置く。
- エラーを消すために条件や処理を削らない。直せないときは、何が足りないかをユーザーに伝える。

## 実機・GUI

- `check` と `render` は実機なしで実行できる。
- 試し実行・操作の記録は、アプリ本体が起動していてユーザーが許可したときだけ行う。
- 実画面・ドラッグ操作の確認は Blockly エディタか [gui-probe](../gui-probe/SKILL.md) に委ねる。ハードウェアが無い環境では「実機確認は未実施」と明記する。
- 実機なしでどこまで検証できるか、実機で何を確かめるかは [docs/BLOCKLY_AGENT_VERIFICATION.md](../../../docs/BLOCKLY_AGENT_VERIFICATION.md) に層分けしてある。

## 参照

- [`references/spec.md`](references/spec.md) — コンパクト仕様 v1 の完全な説明。
- [`references/blocks.md`](references/blocks.md) — 生成されたブロック・フィールド・入力・テンプレート一覧。
- [`examples/`](examples/) — `check` が通る作例（basic / flow / vision / subroutine / audio / dialog）。
- 実装: [`tools/blockly_agent.py`](../../../tools/blockly_agent.py)、[`SerialController/services/blockly_author.py`](../../../SerialController/services/blockly_author.py)。
