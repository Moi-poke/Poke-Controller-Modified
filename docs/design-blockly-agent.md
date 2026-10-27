# Blockly エージェント authoring（設計）

## 背景

Poke-Controller の Blockly は、`.blockly.json`（Blockly の workspace JSON）と同名の `.py` の対で保存する。`.py` はブラウザの `pythonGenerator.workspaceToCode()` が作り、保存時に `/save` へ workspaceJson と pythonCode の両方を渡す。この形は人間の編集には十分だが、エージェントが自動で組むには次の不足がある。

- `.blockly.json` の `next` チェーン、`inputs` の `block`/`shadow`、変数の `id` 参照を手書きする必要がある。
- `.py` を作る経路がブラウザにしかなく、エージェントは保存されたコードを検証できない。
- ブロックの fields/inputs/既定値/選択肢を机械的に引ける一覧が無い。
- 画像テンプレート・CROP・サブプログラム・設定ダイアログの前提を、保存前にまとめて検査できない。

研究（`~/research/ai-blockly-generation/`）では、次の前例が確認できた。

- LLM に Blockly XML を生成させる実装は、system prompt に XML 文法と許可ブロック一覧を埋め込む（blockly-gpt, Code Maker for toio）。
- TutoriaLLM は workspace JSON をサーバ側のヘッドレス `Blockly.Workspace` に読み込み、`workspaceToCode()` でコード化する。
- agentic-scratch は生 JSON をエージェントに触らせず、typed IR → 決定的 builder → schema/graph/static 検証 → 実行検証の順に積む。
- MCP-Blockly はブロック ID 付きのネスト関数 DSL を LLM に読ませ、生成/削除/変数作成の操作を返させる。
- scratch-creater は `SKILL.md` + `references/`（ブロック参照）+ `scripts/`（pack/unpack/validate）+ templates でスキルを構成する。
- MAS Agent は自然言語から決定論的マクロを作り、リプレイで検証してから実行する。

Poke-Controller では「生 JSON を手書きさせず、小さな宣言的仕様から決定的に Blockly JSON を組み、本物の Blockly で `.py` を生成し、保存前検査を通す」形が最も安全で、既存のブラウザ編集とも共存できる。

## 成果物

1. `SerialController/core/blockly_headless.py`
   - vendored Blockly 資産を node の vm で読み、workspace JSON → Python コード、またはブロックカタログを返す。
   - node が無いときは理由付きで失敗する（アプリ本体の動作には影響しない）。
2. `SerialController/services/blockly_author.py`
   - コンパクト仕様（下記）→ workspace JSON の決定的 builder。
   - 保存前検査（workspace / dialog vars / template refs / 生成コード）と、`save_blockly` による対保存。
3. `tools/blockly_agent.py`
   - CLI: `catalog` / `check` / `build` / `render`。
   - 既定の app dir は `SerialController/`。`--app-dir` で差し替え可能（テスト用）。
4. `tools/blockly_agent_node.js`
   - node 側の入り口。`catalog` と `generate` の2モード。ブラウザ資産をそのまま使う。
5. `.opencode/skills/pokecon-blockly/`
   - `SKILL.md`（手順・規律）、`references/blocks.md`（生成カタログ）、`references/spec.md`（仕様）、`examples/*.json`。
6. `tests/test_blockly_author.py` / `tests/test_blockly_author_node.py`
   - builder の形、検査、実 Blockly での生成、保存対、カタログ。

## コンパクト仕様 v1

Blockly JSON そのものではなく、次の形を書く。

```json
{
  "stem": "MyFirstBlockly",
  "name": "はじめてのブロック",
  "tags": ["blockly", "sample"],
  "do": [
    {"press": "A", "duration": 0.1, "wait": 0.5},
    {"wait": 1},
    {"repeat": 3, "do": [{"press": "B"}]},
    {"if": {"type": "pokecon_vision_contains", "fields": {"TEMPLATE": "controller_check/btn_a.png"}},
     "do": [{"print": "見つけた"}],
     "else": [{"print": "まだ"}]}
  ],
  "subs": [
    {"name": "aisatsu", "do": [{"print": "hello"}], "return": "ok"}
  ]
}
```

- 文の並びは配列。builder が `next` チェーンを作る。
- `inputs` の文入力は配列、値入力はブロック dict またはリテラル。リテラルは入力の型（String/Number/Boolean）に合わせて `text`/`math_number`/`logic_boolean` に変換する。
- 変数は `{"set":"count","to":0}` / `{"change":"count","by":1}` / `{"var":"count"}` で参照し、builder が `variables` と `{"id": ...}` を補う。
- よく使う文には短縮形（`press` / `wait` / `repeat` / `if` / `while` / `print` / `finish` / `comment` / `call` など）を用意する。それ以外は `{"type": "...", "fields": {...}, "inputs": {...}, "extraState": {...}}` で書く。
- `stem` は保存名（`[A-Za-z_][A-Za-z0-9_]*`）。`name` は一覧に出る表示名（日本語可）。
- サブプログラムは `subs`。呼出は最大3引数（Blockly ブロックの制約）。定義名の重複・未定義呼出・循環・引数不一致は保存前検査で落とす。

## 検査

1. builder の構造検査（未知ブロック、未知入力、必須フィールド、TEMPLATE の存在、CROP 書式、サブプログラム名）。
2. `blockly_validate.validate_workspace_json` / `validate_dialog_vars`。
3. 本物の Blockly で workspace を load し、`workspaceToCode` が通るか。
4. `blockly_validate.validate_generated_code`（import 公開面、NAME/do、サブプログラムの循環・引数）。
5. `blockly_templates.validate_template_refs`（テンプレ参照の絶対パス・`..`・`:`）。
6. `save_blockly`（同じ検査を再実行し、`.py` の sha256 を `.blockly.json` に記録）。

## スキルの規律

- `.py` を直接書かない。仕様 → `check` → `build --write` の順。
- 画像テンプレートを推測で作らない。`Template/` の既存画像から選ぶ。無ければユーザーに撮ってもらう。
- 実機が要る動作（試し実行・記録）は、本体が起動していてユーザーが許可したときだけ。
- 既存の保存物を上書きするときは、ユーザーの意図を確認する。
- 検査に落ちたら、エラーを消すために仕様を変えるのではなく、原因（ブロック種・フィールド・入力の型）を直す。

## タスク分割（Orca）

- T1: 上記 1–4 + テスト。CLI と node の契約を固定する。
- T2: 上記 5 + `docs/BLOCKLY_EDITOR.md` / `AGENTS.md` の追記。T1 の CLI が現れたら examples を `check` し、`catalog` から `references/blocks.md` を作る。
- 統合: 統括役が `task ci` と CLI スモークを実行する。
