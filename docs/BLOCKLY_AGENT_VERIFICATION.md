# Blockly エージェント authoring の検証計画

[Blockly 共通手順](blockly-agent/README.md) と [CLI](../tools/blockly_agent.py) の検証範囲を、実機なしで確認できる範囲と、実機・GUI が必要な範囲に分ける。上から順に実行し、下へ行くほど人手とハードウェアが要る。
導入は [外部 AI の利用手順](AI_AUTOMATION.md)、作例は [共通の examples](blockly-agent/examples/) を参照する。

以下の固定開発者パス、Orca、managed Chromium は過去の検証例であり、利用者の必須環境ではない。
実測欄は記載日の記録であり、文書整理時の再検証結果を意味しない。

## 共通文書移行時の確認

入口・参照の検査は次で再実行できる。

```text
uv run --frozen python tools/check_docs.py
uv run --frozen pytest tests/test_check_docs.py -q
```

共通文書への移行時には、一時 app に本物の Blockly 資産と同梱 Template をコピーし、[6 作例](blockly-agent/examples/) を実 CLI の `check` → `build --write` → `render` へ通した。
全件の成功、保存ペア、保存 `.py` と `pokecon.pySha256` の一致、render コードと保存コードの AST 一致を確認した。
同じ一時 app で待ちを 2.5 秒へ patch し、dry-run の非書込み、古い `workspaceSha256` での保存拒否と保存値の保持も確認した。
`trial --workspace --server http://127.0.0.1:1 --max-seconds 3` は `--allow-input` なしで preview とコード・digest を返した。
これは非接続 preview の確認であり、本体起動・実機入力・カメラ再取得・デモサーバー起動は行っていない。
各クライアントの指示書自動読込み、今回の GUI 表示・実機動作、remote の新規 clone は確認していない。

## 0. すでに自動化済み（実機不要）

```sh
task ci
uv run --frozen pytest tests/test_blockly_author.py tests/test_blockly_author_node.py tests/test_blockly_author_execution.py tests/test_blockly_agent_vision.py tests/test_blockly_agent_workspace.py tests/test_blockly_agent_client.py tests/test_blockly_live.py tests/test_blockly_live_http.py tests/test_blockly_run_server.py -q
```

- `test_blockly_author.py` — 仕様→workspace JSON、変数、if/else、サブルーチン、CROP/THRESHOLD、エラー候補。既存 workspace の検査・保存（ID・配置・コメント・変数の保持）と、壊れたテンプレ画像の拒否。
- `test_blockly_author_node.py` — 本物の Blockly を node で読み、カタログ・Python 生成・`L+R` スティック。
- `test_blockly_author_execution.py` — 生成した保存用コードを `blockly_run.build_trial_class` で読み、`FakeTransport` 越しに実行。入力が線へ出て「完了」になること。
- `test_blockly_agent_vision.py` — CLI の `template`/`match`/`color`/`frame`（実HTTPの `/frame` を含む）。正例・負例、ROI、プレビュー座標、色相の0跨ぎ。
- `test_blockly_agent_workspace.py` — `check/build --workspace` の保持、`patch` の部分編集（フィールド型・選択肢・CROP・変数拒否・dry-run・出力なし失敗）、`--expect-current-sha256` の版検査、手編集の拒否と `--overwrite-external`、`trial` のプレビューと digest 不一致の拒否。
- `test_blockly_agent_client.py` — `EditorClient` の origin 検査・HTTP エラー・転送不追従・送信本文。
- `test_blockly_live.py` / `test_blockly_live_http.py` — ライブ編集の版一致・貸与・競合・期限切れと、`/live/*` の実HTTP往復（CLI含む）。
- `test_blockly_run*.py` — 試し実行の runId 必須化、時間上限つき試行の本体側停止、別試行を止めないこと。
- `test_blockly_save.py` / `test_blockly_editor*.py` — 既存の保存・試し実行・画面手順。

これで「生成コードが文法的に正しい」だけでなく「本体の実行器で動く」ところまでは閉じている。残りは **実ブラウザの見た目・実ドラッグの吸着・実機**。

## 1. 実ブラウザで editor を開く（ハードウェア不要）

実際に確認した手順。tkinter 本体は起動せず、`ui.blockly_editor._Handler` だけを一時サーバとして立てる。

```python
# C:\PokeCon\_tmp_blockly_probe\server.py
import functools, http.server, os, pathlib, sys

REPO = pathlib.Path(r"C:\PokeCon\Poke-Controller-Modified")
APP = pathlib.Path(r"C:\PokeCon\_tmp_blockly_probe\app")
sys.path.insert(0, str(REPO / "SerialController"))
os.chdir(REPO / "SerialController")
import WindowUtils
WindowUtils.APP_DIR = str(APP)          # 保存先を一時 dir に固定
from ui import blockly_editor

httpd = http.server.ThreadingHTTPServer(
    ("127.0.0.1", 0), functools.partial(blockly_editor._Handler)
)
print(f"URL=http://127.0.0.1:{httpd.server_address[1]}/editor.html", flush=True)
httpd.serve_forever()
```

準備:

```sh
mkdir -p C:/PokeCon/_tmp_blockly_probe/app/assets
cp -r SerialController/assets/blockly C:/PokeCon/_tmp_blockly_probe/app/assets/blockly
cp -r SerialController/Template C:/PokeCon/_tmp_blockly_probe/app/Template
./.venv/Scripts/python.exe C:/PokeCon/_tmp_blockly_probe/server.py
```

Orca のブラウザ（または Playwright）で URL を開き、次を確認する。

1. タイトルが `Blocklyエディタ - PokeCon`、分類が9個（よく使う形・基本操作・流れ・画像認識・音声・入出力・計算・論理・変数・サブルーチン）出る。
2. 初期プログラムが1個あり、`Blockly.Python.workspaceToCode` が `class BlocklyCmd(PythonCommand)` を返す。
3. 「基本操作」を開き、`Aを押す` をキャンバスへドラッグするとブロックが出る。
4. プログラムの `DO` へ接続した後、生成コードに `self.press(Button.A, ...)` が入る。
5. 保存名を入れて保存すると、`APP/Commands/PythonCommands/<stem>.py` と `.blockly.json` ができ、`pokecon.pySha256` が `.py` と一致する。

実測（2026-10-28）:

- managed Chromium（hidden）で editor が起動。分類9個・初期プログラム・`class BlocklyCmd` を確認。
- ツールボックスから `Aを押す` をキャンバスへドラッグしてブロックが出ることを確認。
- 接続はドロップ位置が吸着しなかったため `p.getInput('DO').connection.connect(...)` で補完。接続後の生成コードと保存対（sha 一致）を確認。
- **残る手動確認**: 実マウスで `DO` のノッチへ吸着するか、ブロック移動・削除・Undo の見た目。

後始末:

```sh
# サービスを止め、C:/PokeCon/_tmp_blockly_probe を削除
```

## 2. 実ブラウザで試し実行（ハードウェア不要）

1 のサーバに、`tests/test_blockly_run_server.py` の `FakeHost` / `FakeRunner` と同じものを足す。

```python
host = FakeHost()
blockly_editor._set_run_host(host)

def gui_loop():
    while not stop.is_set():
        blockly_editor.drain_gui_calls()
        time.sleep(0.01)
```

エディタで `press A` のプログラムを作り、`#runbtn` を押す。確認すること:

- 実行中のブロックが黄色くなる。
- `GET /run/state` の `result` が `完了`、`logs` に表示ブロックの内容が入る。
- `host.transport.rows` に A の入力行が出る。
- `#pausebtn` / `#stepbtn` / `#stopbtn` で一時停止・1つ進む・停止が効く。

これは「ブラウザ→受け口→本体の実行器→偽の線」の結合確認。実機の代わりに `FakeTransport` を使う。

## 3. 本体 GUI と editor の結合（ハードウェア不要）

`gui-probe` スキルの手順で、実プロファイルを汚さずに本体を起動する。

- `settings.probe.ini` を作り、`com_port = 99`、`com_port_name = COM99`、`is_show_realtime = False`、`camera_id = 99` にする。
- probe スクリプトの先頭で `WindowUtils.listComPorts = lambda: []` にする（実ポートへのフォールバック防止）。
- 本体を起動し、メニュー → コマンド → 「Blocklyエディタ...」を開く。
- エディタで保存 → 約1秒で本体のコマンド一覧に反映されること、実行中は開けないこと、閉じると一覧が作り直されることを確認する。

受け入れ: 実 COM・実カメラを掴まずに、editor の起動・保存・一覧反映・終了が通る。

## 4. 実機（COM / キャプチャボード）

実機が無いと閉じられない。次の順で、影響の小さいものから。

1. **入力だけ**: `legacy_text` か `pico_uart` で接続し、`HOME` → `wait 1` → `A` → `finish` の生成コマンドを実行。Switch のコントローラテスト画面か入力ログで、A が1回入ることを確認する。
2. **保持・連打**: `hold` / `holdEnd` / `press_rep` を実行し、押しっぱなしと連打が実機で再現することを確認する。
3. **画像認識**: キャプチャボードの実画面で `Template/controller_check/btn_a.png` を `wait_appear` に使い、タイムアウトせず一致することを確認する。`CROP` は実画面の解像度に合わせる。
4. **スクショ / Discord**: `screenshot` で保存先に画像ができ、`discord` はテスト用チャンネルへ送れることを確認する。
5. **音声**: Audio 欄を設定し、`wait_tone` / `isTonePresent` の閾値を実測で調整する。

受け入れ: 入力ログの行が期待どおり、画像認識がタイムアウトしない、スクショが保存される、例外・停止がない。

## 5. 実機が無いときの代替

- **シリアル**: com0com などの仮想 COM 対、または Pico のループバックで `Transport.open` と送信行を確認する。Switch の入力そのものは代替できない。
- **カメラ**: 仮想カメラ（OBS Virtual Camera 等）や `tests/real_camera_source.py` の録画フレームで、OpenCV の取得・テンプレ照合・スクショ保存を確認する。
- **画面**: 1〜3 のブラウザ／GUI probe で、実画面の描画と操作を確認する。実機の gameplay だけは代替できない。

## 6. 失敗時に見るもの

| 症状 | 見る場所 |
|---|---|
| editor が真っ白 | ブラウザ console、`/editor.html` の 200、`assets/blockly/*.js` の読込 |
| 保存が 403 | Host / Origin / Content-Type（`ui/blockly_editor.py` の `_refuse_foreign`） |
| 保存は通るが一覧に出ない | `Commands/PythonCommands` の `.py`、`CommandLoader.reload` |
| 試し実行が始まらない | `/run` の応答、`_RUN_HOST`、本体が実行中でないか |
| 画像が一致しない | 実画面の解像度、`CROP`、`THRESHOLD`、`Template/` の画像 |
| 入力が出ない | transport、COM、`Sender` の interval、Switch 側のサンプリング |
