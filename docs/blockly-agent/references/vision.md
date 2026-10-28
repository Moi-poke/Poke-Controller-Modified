# 画像認識の作り方

対象を「見た目」で判別するマクロを、実画像から組み立てる手順。
生成・検査・保存、ローカル画像の切出し・照合、試行 preview は実機なしで動く。
`frame` と `trial --allow-input` は、その clone の本体と対象プロファイルの Blockly エディタが必要。
preview も `--server` と `--max-seconds` を引数に取るが、接続も入力もしない。
共通の前提・権限は [手順](../README.md)、全オプションと版の区別は [CLI 契約](cli.md) を参照する。

## 1. 画像を用意する

次のどちらか。保存画像だけに限定しない。

```sh
# ユーザーが開いているエディタの現在画面（生フレーム）を保存する
uv run --frozen python tools/blockly_agent.py frame --server "<origin>" --output probe/live.png --json
```

- 本体のエディタ起動ダイアログで「URLをコピー」し、`/editor.html` 等のパスを除いた origin を `<origin>` に入れる。`--server` はユーザーが開いたエディタの origin（`http://127.0.0.1:<port>` / `http://localhost:<port>`）だけ。固定ポートや他プロファイルを探さず、LAN へ公開しない。
- カメラを直接開き直さない。取得できなければ理由が返るので、保存済みスクショの経路へ切り替える。
- 出力先の親は作られるが、既存ファイルは上書きしない（消さずに別名へ）。

画像は必ず自分で開いて意味を読む。CLI が返す `width`/`height` が原画像の画素寸法。縮小表示の座標をそのまま使わない。

## 2. 対象を切り出す

```sh
uv run --frozen python tools/blockly_agent.py template --image probe/live.png --name ready --rect x1 y1 x2 y2 --app-dir "<app>" --json
```

- `--rect` は原画像の整数画素 `x1 y1 x2 y2`（`x2>x1`, `y2>y1`, 画像内）。
- 保存先は固定で `Template/blockly/<name>.png`。8px 未満の辺は拒否。同名は `_2` などの連番になる。
- 成功 JSON の `template`（例 `blockly/ready.png`）を、以後の `TEMPLATE` と `match --template` にそのまま使う。返された実名を使い、自分で決め直さない。
- 切出し矩形（`--rect`）と、実行時の検索範囲（`CROP`）は別物。混同しない。

## 3. 正例・負例で照合する

切出し元と同じ画像だけで「成功」としない。対象が別の位置にある正例と、対象が無い負例を用意する。

```sh
uv run --frozen python tools/blockly_agent.py match --image probe/positive.png --template blockly/ready.png --threshold 0.8 --preview probe/positive-match.png --app-dir "<app>" --json
uv run --frozen python tools/blockly_agent.py match --image probe/negative.png --template blockly/ready.png --threshold 0.8 --app-dir "<app>" --json
```

読む項目:

- `score`（一致度 0〜1）と `matched`（`score >= threshold`）。低い `score` の `rect` を「検出位置」と呼ばない。`matched` と併読する。
- `rect` は原画像座標。`--crop` を付けた場合はその ROI 内の一致位置を原画像座標へ戻したもの。
- `count` は最大20件までの試し打ち。20件超の厳密な個数確認には使わない（必要なら実行時の `countTemplate(..., max_count=必要数)` を別途確認する）。
- `--preview` は原画像に検索 ROI と最大一致箇所を描く。緑＝ヒット、赤系＝ミス。画像を開いて、枠が対象の上にあるかを目で確かめる。

判定の目安:

- 正例 `matched:true`、負例 `matched:false` になるまで、切出し領域・`--crop`・`--gray/--no-gray`・`--threshold` を見直す。
- 正例と負例の `score` が近い／両方ヒットする／両方ミスするなら、その切り出しでは判別できない。特徴のある別領域を選ぶ。
- 負例が手元に無い場合、生成・保存はできるが「認識を検証済み」とは報告しない。

## 4. 色で判別する場合

広い単色領域は HSV の割合で判定する。

```sh
uv run --frozen python tools/blockly_agent.py color --image probe/live.png --lower H S V --upper H S V --ratio 0.6 --crop x1 y1 x2 y2 --preview probe/mask.png --app-dir "<app>" --json
```

- `ratio` は実測割合（0〜1）。`--ratio` は合格基準。**実測値と基準を混同しない。**
- HSV の範囲は OpenCV の値（H は 0〜179）。赤のように色相が 0 を跨ぐ場合は、`lower` の H を大きく（例 170）、`upper` を小さく（例 10）して渡す。跨ぎは既存実装が扱う。
- `--preview` はマスク画像。`--crop` を付けると ROI だけの画像になる（原画像全体ではない）。
- 画像の端に接する ROI も指定できる。範囲は画像内。

## 5. ブロックへ入れる

テンプレート方式:

```json
{"if": {"type": "pokecon_vision_contains",
        "fields": {"TEMPLATE": "blockly/ready.png", "THRESHOLD": 0.8,
                   "CROP": "x1,y1,x2,y2", "USE_GRAY": true}},
 "do": [ ... ], "else": [ ... ]}
```

色方式は `pokecon_vision_color_area` 系の `H1/S1/V1/H2/S2/V2/RATIO/CROP` に、上で決めた実測条件を入れる。`CROP` は空か `x1,y1,x2,y2`。

待ち・条件・試し打ちで同じ `TEMPLATE/THRESHOLD/CROP/USE_GRAY` を使う。片方だけ変えると、待ったのに条件で外れる（またはその逆）ことがある。

## 6. 典型レシピ: マークが現れたらA、出なければ何も送らず終了

要件: 「指定マークが現れたらAを1回押して終了。10秒以内に現れなければ入力を送らず、理由を表示して終了」。

```json
{
  "stem": "ReadyThenA",
  "name": "マークが現れたらA",
  "tags": ["blockly", "vision"],
  "do": [
    {"type": "pokecon_vision_wait_appear",
     "fields": {"TEMPLATE": "blockly/ready.png", "TIMEOUT": 10,
                "THRESHOLD": 0.8, "CROP": "x1,y1,x2,y2", "USE_GRAY": true}},
    {"if": {"type": "pokecon_vision_contains",
            "fields": {"TEMPLATE": "blockly/ready.png", "THRESHOLD": 0.8,
                       "CROP": "x1,y1,x2,y2", "USE_GRAY": true}},
     "do": [
       {"press": "A", "duration": 0.1, "wait": 0},
       {"finish": true}
     ],
     "else": [
       {"print": "マークが現れませんでした"},
       {"finish": true}
     ]}
  ]
}
```

- 待ちブロックは戻り値をコードで捨てるため、「待つ→A」だけにしない。待った後に `pokecon_vision_contains` で改めて判定し、成功側だけ入力する。
- 失敗側は入力を送らず、表示して終了する。
- `TIMEOUT` は待ちの上限。`trial` の `--max-seconds` はプロセス全体の上限で別物。両方を説明する。時間上限つきで試すときは `--max-seconds` を `TIMEOUT` より十分大きくする（または試行用に `TIMEOUT` を短くした版を別に作り、その版を許可する）。`--max-seconds` が `TIMEOUT` より短いと、待ちの途中で外側の上限に当たる。

## 7. 限定試行（実機入力を伴う）

まず preview。入力は送られない。

```sh
uv run --frozen python tools/blockly_agent.py check probe/ready-spec.json --app-dir "<app>" --json
uv run --frozen python tools/blockly_agent.py trial probe/ready-spec.json --server "<origin>" --max-seconds 15 --app-dir "<app>" --json
```

preview の JSON には `status:"preview"`, `codeSha256`, `code`, `workspace`, `server`, `maxSeconds` が入る。
`codeSha256` は通常生成コードの LF 正規化 UTF-8 SHA256。
preview の `code` には試行用の目印が入るため、自分で再ハッシュせず、返った `codeSha256` をそのまま使う。

1. `code` を読み、実際に送る入力・上限・成功／失敗時の動作をユーザーへ示す。
2. ユーザーがそのコード・URL・上限を許可したら、preview の `codeSha256` をそのまま渡す。

```sh
uv run --frozen python tools/blockly_agent.py trial probe/ready-spec.json --server "<origin>" --max-seconds 15 --app-dir "<app>" --allow-input --expected-sha256 "<codeSha256>" --json
```

- `--allow-input` が無ければ、本体へ繋がず入力も送らない。
- `--expected-sha256` が現在の生成コードと違えば、送信前に拒否される。preview の後にコードを変えたら、preview からやり直す。
- サーバが `boundedTrial` と `runIdControl` を持たない（古い本体）、本体が別コマンド実行中、または試し実行中なら開始しない。
- 結果 `status`: `completed`（終了0）/ `stopped` / `timed_out` / `failed` / `superseded`。`logs`・`blockId`・`error`・`errorBlock`・`timedOut` を確認する。
- 一時停止しても CLI は勝手に再開しない。時間上限は一時停止中も進む。
- 通信が切れた・Ctrl+C・クライアント側の待ち上限超過では、自分の runId にだけ stop を1回送る。runId を受け取れていないときは、直近の試行を推測で止めない。
- `--allow-input` は同意の記録でありサンドボックスではない。上限は GUI の見回りと協調停止による。OS／GUI が固まる場合のハードな期限保証はない。本体の Esc／停止ボタンも使える。

## 8. 保存と再確認

```sh
uv run --frozen python tools/blockly_agent.py build probe/ready-spec.json --write --app-dir "<app>" --json
```

保存済み作品の正本は `.blockly.json`、`.py` は派生物、コンパクト仕様は新規作成用入力。
既存作品のしきい値・待ち時間だけを変えるなら、古い仕様から作り直さず `patch` を使う。
`check --workspace --json` の `workspaceSha256` を控え、`--expect-current-sha256` と一緒に保存する。
このガードはディスクの変更だけを検出し、ブラウザの未保存編集は検出しない。
開いた画面との共同編集は `live` を使い、人が GUI で保存する。

```sh
uv run --frozen python tools/blockly_agent.py patch MyBlock.blockly.json --ops ops.json --write --stem MyBlock --expect-current-sha256 "<workspaceSha256>" --app-dir "<app>" --json
```

画像の解像度・UI 倍率・言語が変わったら再照合する。検証済みの画像を勝手にリサイズして「通った」ことにしない。保存後は、人がエディタで開いて編集できることを確かめる。

## 報告に分けて書く

- 保存物のパス（`.blockly.json` / `.py`）
- 使った画像と条件（`TEMPLATE`, `THRESHOLD`, `CROP`, `USE_GRAY` / HSV, `RATIO`）
- 正例・負例の実測（`score` / `ratio` / `matched`）
- 実機へ送った入力とその結果
- 未実施の検証（実機・別解像度・負例なし など）

欠損画像・接続失敗・画像閲覧不可は不足を報告し、未確認の範囲を明記する。
条件削除やダミー画像で成功にしない。
