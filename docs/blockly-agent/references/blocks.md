# Blockly ブロック一覧（生成物）

このファイルは自動生成です。手で編集しないでください。

生成コマンド:

```sh
uv run --frozen python tools/blockly_agent.py catalog --format markdown --output docs/blockly-agent/references/blocks.md
```

仕様の書き方は [spec.md](spec.md)、使い方は [手順](../README.md) を参照してください。

## 画像テンプレート（`TEMPLATE` に書ける名前）

- `Network_Offline.png`
- `OP.png`
- `baby_msg.png`
- `controller_check/btn_a.png`
- `controller_check/btn_b.png`
- `controller_check/btn_lclick.png`
- `controller_check/hat_top.png`
- `controller_check/header.png`
- `dougu_to_bag.png`
- `egg_found.png`
- `egg_notice.png`
- `fell_message.png`
- `pannels.png`
- `pokopia/NX2_DeviceUpdate.png`
- `pokopia/NX2_Home.png`
- `pokopia/already_got_stamp.png`
- `pokopia/button_B.png`
- `pokopia/check_DateChangeScreen.png`
- `pokopia/coin.png`
- `pokopia/daily.png`
- `pokopia/game_window_ZR.png`
- `pokopia/get.png`
- `pokopia/get_stamp.png`
- `pokopia/learned.png`
- `pokopia/message_window.png`
- `pokopia/new_stamp.png`
- `pokopia/numbers/0.png`
- `pokopia/numbers/1.png`
- `pokopia/numbers/2.png`
- `pokopia/numbers/3.png`
- `pokopia/numbers/4.png`
- `pokopia/numbers/5.png`
- `pokopia/numbers/6.png`
- `pokopia/numbers/7.png`
- `pokopia/numbers/8.png`
- `pokopia/numbers/9.png`
- `pokopia/pc_main.png`
- `pokopia/recipe.png`
- `pokopia/recipe2.png`
- `pokopia/recipe3.png`
- `pokopia/restamp.png`
- `pokopia/stamped.png`
- `pokopia/weekend.png`
- `sample.png`
- `sample_color_HLS.png`
- `shiny_mark.png`
- `status.png`
- `zachian_part.png`

## ブロック

### 短縮形

仕様では次の短縮形を使えます（`type`/`fields`/`inputs` の汎用形も可）。

- `press` / `hold` / `release` / `stick` / `stick_off` / `wait` / `press_rep`
- `repeat` / `while` / `if`(+`else`) / `break` / `continue`
- `print` / `comment` / `finish` / `set` / `change` / `call` / `call_value`
- `screenshot` / `discord` / `dialog_choice` / `dialog_number` / `dialog_check`

## PokeCon ブロック

### `pokecon_audio_tone_contains`

指定した音域の音量がしきい値を超えたら条件成立。2つ目の音域は使わないとき 0,0 にする。

- 形: 値（Boolean）
- fields:
  - `LO` (number、既定 `3000`)
  - `HI` (number、既定 `3200`)
  - `THRESH` (number、既定 `1000000`)
  - `LO2` (number、既定 `0`)
  - `HI2` (number、既定 `0`)
  - `THRESH2` (number、既定 `0`)

### `pokecon_audio_wait_tone`

指定した音域の音が鳴るまで待つ。2つ目の音域は使わないとき 0,0 にする。

- 形: 文（前後に接続）
- fields:
  - `LO` (number、既定 `3000`)
  - `HI` (number、既定 `3200`)
  - `THRESH` (number、既定 `1000000`)
  - `TIMEOUT` (number、既定 `10`)
  - `LO2` (number、既定 `0`)
  - `HI2` (number、既定 `0`)
  - `THRESH2` (number、既定 `0`)

### `pokecon_comment`

メモとして残る。実行には影響しない。

- 形: 文（前後に接続）
- fields:
  - `TEXT` (text、既定 `メモ`)

### `pokecon_dialog_check`

実行前に確認を取る（キャンセルするとコマンドを終了）。

- 形: 文（前後に接続）
- fields:
  - `VAR` (variable)
  - `TITLE` (text、既定 `確認`)
  - `LABEL` (text、既定 `送る`)
  - `DEFAULT` (checkbox、既定 `TRUE`)

### `pokecon_dialog_choice`

実行前に選択肢を選ばせる（キャンセルするとコマンドを終了）。

- 形: 文（前後に接続）
- fields:
  - `VAR` (variable)
  - `TITLE` (text、既定 `設定`)
  - `LABEL` (text、既定 `項目`)
  - `OPTIONS` (text、既定 `A,B`)
  - `DEFAULT` (text、既定 `A`)

### `pokecon_dialog_number`

実行前に数値を選ばせる（キャンセルするとコマンドを終了）。

- 形: 文（前後に接続）
- fields:
  - `VAR` (variable)
  - `TITLE` (text、既定 `設定`)
  - `LABEL` (text、既定 `個数`)
  - `MIN` (number、既定 `1`)
  - `MAX` (number、既定 `10`)
  - `DEFAULT` (number、既定 `3`)

### `pokecon_discord`

Discord にメッセージや画像を送る。

- 形: 文（前後に接続）
- fields:
  - `KIND` (text、既定 `text`)、選択肢: `text`, `image`
- inputs:
  - `CONTENT` (value)

### `pokecon_elapsed`

開始からの経過秒。条件分岐や正常終了と組み合わせて時間制限に使う。

- 形: 値（Number）

### `pokecon_finish`

コマンドを正常終了する（色違いを見つけたときなど）。

- 形: 文（前後に接続）

### `pokecon_hold`

ボタンやスティックを押したままにする（「離す」ブロックで解除）。

- 形: 文（前後に接続）
- fields:
  - `TARGET` (controller、既定 `Button.A`)、選択肢: `Button.A`, `Button.B`, `Button.X`, `Button.Y`, `Button.L`, `Button.R`, `Button.ZL`, `Button.ZR`, `Button.MINUS`, `Button.PLUS`, `Button.HOME`, `Button.CAPTURE`, `Button.LCLICK`, `Button.RCLICK`, `Hat.TOP`, `Hat.TOP_RIGHT`, `Hat.RIGHT`, `Hat.BTM_RIGHT`, `Hat.BTM`, `Hat.BTM_LEFT`, `Hat.LEFT`, `Hat.TOP_LEFT`, `Direction.UP`, `Direction.UP_RIGHT`, …（他 14 個）
  - `WAIT` (number、既定 `0.1`)

### `pokecon_hold_end`

押しっぱなしを離す。

- 形: 文（前後に接続）
- fields:
  - `TARGET` (controller、既定 `Button.A`)、選択肢: `Button.A`, `Button.B`, `Button.X`, `Button.Y`, `Button.L`, `Button.R`, `Button.ZL`, `Button.ZR`, `Button.MINUS`, `Button.PLUS`, `Button.HOME`, `Button.CAPTURE`, `Button.LCLICK`, `Button.RCLICK`, `Hat.TOP`, `Hat.TOP_RIGHT`, `Hat.RIGHT`, `Hat.BTM_RIGHT`, `Hat.BTM`, `Hat.BTM_LEFT`, `Hat.LEFT`, `Hat.TOP_LEFT`, `Direction.UP`, `Direction.UP_RIGHT`, …（他 14 個）

### `pokecon_motion`

スティックを「半径,角度」の始点から終点へ動かす。記録した動きの再生用。最後に離すか続けるかを選べる。

- 形: 文（前後に接続）
- fields:
  - `STICK` (text、既定 `LEFT`)、選択肢: `LEFT`, `RIGHT`
  - `FROM` (text、既定 `0,90`)
  - `TO` (text、既定 `127,90`)
  - `T_MS` (number、既定 `100`)
  - `END` (text、既定 `CONT`)、選択肢: `CONT`, `RELEASE`

### `pokecon_motion2`

左右のスティックを同時に、指定した時間で動かす。記録した動きの再生用。最後に離すか続けるかを選べる。

- 形: 文（前後に接続）
- fields:
  - `FROM_L` (text、既定 `0,90`)
  - `TO_L` (text、既定 `127,90`)
  - `FROM_R` (text、既定 `0,0`)
  - `TO_R` (text、既定 `127,0`)
  - `T_MS` (number、既定 `100`)
  - `END` (text、既定 `CONT`)、選択肢: `CONT`, `RELEASE`

### `pokecon_press`

ボタン・十字キーを押す。スティックの方向を選ぶと、その向きへ倒して戻す。

- 形: 文（前後に接続）
- fields:
  - `BUTTON` (controller、既定 `A`)、選択肢: `A`, `B`, `X`, `Y`, `L`, `R`, `ZL`, `ZR`, `MINUS`, `PLUS`, `HOME`, `CAPTURE`, `LCLICK`, `RCLICK`, `Hat.TOP`, `Hat.TOP_RIGHT`, `Hat.RIGHT`, `Hat.BTM_RIGHT`, `Hat.BTM`, `Hat.BTM_LEFT`, `Hat.LEFT`, `Hat.TOP_LEFT`, `Direction.UP`, `Direction.UP_RIGHT`, …（他 14 個）
  - `DURATION` (number、既定 `0.1`)
  - `WAIT` (number、既定 `0.1`)

### `pokecon_press_rep`

指定した回数だけボタンを押す。

- 形: 文（前後に接続）
- fields:
  - `TARGET` (controller、既定 `Button.A`)、選択肢: `Button.A`, `Button.B`, `Button.X`, `Button.Y`, `Button.L`, `Button.R`, `Button.ZL`, `Button.ZR`, `Button.MINUS`, `Button.PLUS`, `Button.HOME`, `Button.CAPTURE`, `Button.LCLICK`, `Button.RCLICK`, `Hat.TOP`, `Hat.TOP_RIGHT`, `Hat.RIGHT`, `Hat.BTM_RIGHT`, `Hat.BTM`, `Hat.BTM_LEFT`, `Hat.LEFT`, `Hat.TOP_LEFT`, `Direction.UP`, `Direction.UP_RIGHT`, …（他 14 個）
  - `COUNT` (number、既定 `3`)
  - `DURATION` (number、既定 `0.1`)
  - `INTERVAL` (number、既定 `0.1`)
  - `WAIT` (number、既定 `0.1`)

### `pokecon_print`

ログにメッセージを出す（進捗の表示や結果の記録に使う）。

- 形: 文（前後に接続）
- fields:
  - `KIND` (text、既定 `print`)、選択肢: `print`, `print2`
- inputs:
  - `TEXT` (value)

### `pokecon_program`

コマンドの本体（1個まで）。名前は一覧に出る。中に操作を上から順に並べる。タグはカンマ区切り。

- 形: トップレベル専用
- fields:
  - `NAME` (text、既定 `ブロック作成`)
  - `TAGS` (text、既定 `blockly`)
- inputs:
  - `DO` (statement)

### `pokecon_screenshot`

カメラ画像を保存する。

- 形: 文（前後に接続）

### `pokecon_stick`

L/R スティックを8方向・強さ100%で倒す。細かい動きは「スティック軌跡」で再現できる。

- 形: 文（前後に接続）
- fields:
  - `STICK` (text、既定 `LEFT`)、選択肢: `LEFT`, `RIGHT`, `BOTH`
  - `PAD` (stickpad、既定 `90,100`)
  - `ANGLE` (number、既定 `90`)
  - `DURATION` (number、既定 `0.1`)
  - `WAIT` (number、既定 `0.1`)

### `pokecon_stick_release`

倒したスティックを中立に戻す。

- 形: 文（前後に接続）
- fields:
  - `STICK` (text、既定 `LEFT`)、選択肢: `LEFT`, `RIGHT`, `BOTH`

### `pokecon_sub_call`

サブルーチン定義を呼び出す。引数は左から順に渡る。

- 形: 文（前後に接続）
- fields:
  - `NAME` (text、既定 `my_sub`)、選択肢: `my_sub`
- inputs:
  - `ARG0` (value)
  - `ARG1` (value)
  - `ARG2` (value)

### `pokecon_sub_call_value`

戻り値つきサブルーチンを呼び出す（値として使う）。

- 形: 値（型自由）
- fields:
  - `NAME` (text、既定 `my_sub`)、選択肢: `my_sub`
- inputs:
  - `ARG0` (value)
  - `ARG1` (value)
  - `ARG2` (value)

### `pokecon_sub_def`

プログラムの外側に置く。呼び出しブロックから名前を選んで使う。

- 形: トップレベル専用
- fields:
  - `NAME` (text、既定 `my_sub`)
  - `ARGS` (text)
- inputs:
  - `DO` (statement)
  - `RETURN` (value)

### `pokecon_vision_color`

範囲内で指定した色（H/S/V）に入るピクセルの割合が基準以上なら条件成立。📷 調整で実画面を見ながら決められる。

- 形: 値（Boolean）
- fields:
  - `H1` (number、既定 `0`)
  - `S1` (number、既定 `0`)
  - `V1` (number、既定 `0`)
  - `H2` (number、既定 `179`)
  - `S2` (number、既定 `255`)
  - `V2` (number、既定 `255`)
  - `RATIO` (number、既定 `0.6`)
  - `CROP` (crop)
  - `CAPOPEN` (text、表示専用)

### `pokecon_vision_contains`

画面にテンプレ画像があれば条件成立。「もし」の条件に使う。📷 調整で実画面を見ながら範囲・しきい値を決められる。

- 形: 値（Boolean）
- fields:
  - `TEMPLATE` (template)、選択肢: ``
  - `PREVIEW` (text、表示専用)
  - `THRESHOLD` (number、既定 `0.7`)
  - `CROP` (crop)
  - `USE_GRAY` (checkbox、既定 `FALSE`)
  - `CAPOPEN` (text、表示専用)

### `pokecon_vision_count`

画面内でテンプレ画像が見つかった個数（数値）。

- 形: 値（Number）
- fields:
  - `TEMPLATE` (template)、選択肢: ``
  - `PREVIEW` (text、表示専用)
  - `THRESHOLD` (number、既定 `0.7`)
  - `CROP` (crop)
  - `USE_GRAY` (checkbox、既定 `FALSE`)
  - `CAPOPEN` (text、表示専用)

### `pokecon_vision_position`

テンプレ画像が見つかった位置。

- 形: 値（型自由）
- fields:
  - `TEMPLATE` (template)、選択肢: ``
  - `PREVIEW` (text、表示専用)
  - `THRESHOLD` (number、既定 `0.7`)
  - `CROP` (crop)
  - `USE_GRAY` (checkbox、既定 `FALSE`)
  - `CAPOPEN` (text、表示専用)

### `pokecon_vision_press_until`

テンプレ画像が出るまで、ボタンを押し続ける（A連打で戦闘開始を待つ等）。上限の時間で打ち切る。

- 形: 文（前後に接続）
- fields:
  - `TEMPLATE` (template)、選択肢: ``
  - `PREVIEW` (text、表示専用)
  - `TARGET` (controller、既定 `Button.A`)、選択肢: `Button.A`, `Button.B`, `Button.X`, `Button.Y`, `Button.L`, `Button.R`, `Button.ZL`, `Button.ZR`, `Button.MINUS`, `Button.PLUS`, `Button.HOME`, `Button.CAPTURE`, `Button.LCLICK`, `Button.RCLICK`, `Hat.TOP`, `Hat.TOP_RIGHT`, `Hat.RIGHT`, `Hat.BTM_RIGHT`, `Hat.BTM`, `Hat.BTM_LEFT`, `Hat.LEFT`, `Hat.TOP_LEFT`, `Direction.UP`, `Direction.UP_RIGHT`, …（他 14 個）
  - `TIMEOUT` (number、既定 `10`)
  - `THRESHOLD` (number、既定 `0.7`)
  - `CROP` (crop)
  - `USE_GRAY` (checkbox、既定 `FALSE`)
  - `CAPOPEN` (text、表示専用)

### `pokecon_vision_press_until_gone`

テンプレ画像が消えるまで、ボタンを押し続ける。上限の時間で打ち切る。

- 形: 文（前後に接続）
- fields:
  - `TEMPLATE` (template)、選択肢: ``
  - `PREVIEW` (text、表示専用)
  - `TARGET` (controller、既定 `Button.A`)、選択肢: `Button.A`, `Button.B`, `Button.X`, `Button.Y`, `Button.L`, `Button.R`, `Button.ZL`, `Button.ZR`, `Button.MINUS`, `Button.PLUS`, `Button.HOME`, `Button.CAPTURE`, `Button.LCLICK`, `Button.RCLICK`, `Hat.TOP`, `Hat.TOP_RIGHT`, `Hat.RIGHT`, `Hat.BTM_RIGHT`, `Hat.BTM`, `Hat.BTM_LEFT`, `Hat.LEFT`, `Hat.TOP_LEFT`, `Direction.UP`, `Direction.UP_RIGHT`, …（他 14 個）
  - `TIMEOUT` (number、既定 `10`)
  - `THRESHOLD` (number、既定 `0.7`)
  - `CROP` (crop)
  - `USE_GRAY` (checkbox、既定 `FALSE`)
  - `CAPOPEN` (text、表示専用)

### `pokecon_vision_wait_appear`

テンプレ画像が出るまで待つ。上限の時間で打ち切る。

- 形: 文（前後に接続）
- fields:
  - `TEMPLATE` (template)、選択肢: ``
  - `PREVIEW` (text、表示専用)
  - `TIMEOUT` (number、既定 `10`)
  - `THRESHOLD` (number、既定 `0.7`)
  - `CROP` (crop)
  - `USE_GRAY` (checkbox、既定 `FALSE`)
  - `CAPOPEN` (text、表示専用)

### `pokecon_vision_wait_count`

テンプレ画像が指定の個数見つかるまで待つ（卵の数え上げ等）。上限の時間で打ち切る。

- 形: 文（前後に接続）
- fields:
  - `TEMPLATE` (template)、選択肢: ``
  - `PREVIEW` (text、表示専用)
  - `COUNT` (number、既定 `2`)
  - `TIMEOUT` (number、既定 `10`)
  - `THRESHOLD` (number、既定 `0.7`)
  - `CROP` (crop)
  - `USE_GRAY` (checkbox、既定 `FALSE`)
  - `CAPOPEN` (text、表示専用)

### `pokecon_vision_wait_gone`

テンプレ画像が消えるまで待つ。上限の時間で打ち切る。

- 形: 文（前後に接続）
- fields:
  - `TEMPLATE` (template)、選択肢: ``
  - `PREVIEW` (text、表示専用)
  - `TIMEOUT` (number、既定 `10`)
  - `THRESHOLD` (number、既定 `0.7`)
  - `CROP` (crop)
  - `USE_GRAY` (checkbox、既定 `FALSE`)
  - `CAPOPEN` (text、表示専用)

### `pokecon_vision_wait_stable`

画面の変化が「静止」秒だけ続けて止まるまで待つ（暗転・演出の終わり待ち等）。

- 形: 文（前後に接続）
- fields:
  - `QUIET` (number、既定 `0.5`)
  - `TIMEOUT` (number、既定 `10`)

### `pokecon_wait`

指定した秒数だけ何もせずに待つ。

- 形: 文（前後に接続）
- fields:
  - `SEC` (number、既定 `0.5`)

## 標準ブロック（許可分）

### `controls_flow_statements`

- 形: 文（末尾のみ）
- fields:
  - `FLOW` (text、既定 `BREAK`)、選択肢: `BREAK`, `CONTINUE`

### `controls_for`

- 形: 文（前後に接続）
- fields:
  - `VAR` (variable)
- inputs:
  - `FROM` (value Number)
  - `TO` (value Number)
  - `BY` (value Number)
  - `DO` (statement)

### `controls_if`

- 形: 文（前後に接続）
- inputs:
  - `IF0` (value Boolean)
  - `DO0` (statement)

### `controls_repeat`

いくつかのステートメントを数回実行します。

- 形: 文（前後に接続）
- fields:
  - `TIMES` (number、既定 `10`)
- inputs:
  - `DO` (statement)

### `controls_repeat_ext`

いくつかのステートメントを数回実行します。

- 形: 文（前後に接続）
- inputs:
  - `TIMES` (value Number)
  - `DO` (statement)

### `controls_whileUntil`

- 形: 文（前後に接続）
- fields:
  - `MODE` (text、既定 `WHILE`)、選択肢: `WHILE`, `UNTIL`
- inputs:
  - `BOOL` (value Boolean)
  - `DO` (statement)

### `logic_boolean`

true または false を返します。

- 形: 値（Boolean）
- fields:
  - `BOOL` (text、既定 `TRUE`)、選択肢: `TRUE`, `FALSE`

### `logic_compare`

- 形: 値（Boolean）
- fields:
  - `OP` (text、既定 `EQ`)、選択肢: `EQ`, `NEQ`, `LT`, `LTE`, `GT`, `GTE`
- inputs:
  - `A` (value)
  - `B` (value)

### `logic_negate`

入力が false の場合は、true を返します。入力が true の場合は false を返します。

- 形: 値（Boolean）
- inputs:
  - `BOOL` (value Boolean)

### `logic_operation`

- 形: 値（Boolean）
- fields:
  - `OP` (text、既定 `AND`)、選択肢: `AND`, `OR`
- inputs:
  - `A` (value Boolean)
  - `B` (value Boolean)

### `math_arithmetic`

- 形: 値（Number）
- fields:
  - `OP` (text、既定 `ADD`)、選択肢: `ADD`, `MINUS`, `MULTIPLY`, `DIVIDE`, `POWER`
- inputs:
  - `A` (value Number)
  - `B` (value Number)

### `math_change`

- 形: 文（前後に接続）
- fields:
  - `VAR` (variable)
- inputs:
  - `DELTA` (value Number)

### `math_modulo`

2つの数値の割り算の余りを返す。

- 形: 値（Number）
- inputs:
  - `DIVIDEND` (value Number)
  - `DIVISOR` (value Number)

### `math_number`

- 形: 値（Number）
- fields:
  - `NUM` (number、既定 `0`)

### `math_random_float`

0.0以上で1.0未満の範囲の乱数を返します。

- 形: 値（Number）

### `math_random_int`

指定された（上下限を含む）範囲のランダムな整数を返します。

- 形: 値（Number）
- inputs:
  - `FROM` (value Number)
  - `TO` (value Number)

### `text`

- 形: 値（String）
- fields:
  - `TEXT` (text)

### `text_join`

任意の数の項目一部を一緒に接合してテキストを作成。

- 形: 値（String）
- inputs:
  - `ADD0` (value)
  - `ADD1` (value)

### `variables_get`

この変数の値を返します。

- 形: 値（型自由）
- fields:
  - `VAR` (variable)

### `variables_set`

この入力を変数と等しくなるように設定します。

- 形: 文（前後に接続）
- fields:
  - `VAR` (variable)
- inputs:
  - `VALUE` (value)

## 補足

- `controls_if` の `else` は `extraState: {"hasElse": true}` と `inputs.ELSE` で表す（仕様の `if` 短縮形が自動で行う）。
- `variables_set` / `variables_get` / `math_change` / `controls_for` の `VAR` は変数名の文字列で書き、builder が `{"id": 名前}` と `variables` を補う。
- 設定ダイアログの `VAR` は Python の変数名（文字列）のままで、`variables` には足さない。
- 画像認識ブロックの `TEMPLATE` は空にできない。`Template/<名前>` が存在すること。`CROP` は空か `x1,y1,x2,y2`、`THRESHOLD` は 0〜1。
