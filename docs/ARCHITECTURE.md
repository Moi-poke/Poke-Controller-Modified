# ARCHITECTURE

Poke-Controller Modified の構成メモ。詳細な開発手順は [`AGENTS.md`](../AGENTS.md)、
自動化コマンドの作り方は本家 wiki を参照。

## 層構造

```
ユーザースクリプト (Commands/PythonCommands/, Commands/McuCommands/)
        │  PythonCommand / ImageProcPythonCommand / AudioPythonCommand / McuCommand
        ▼
実行制御・操作・画像認識・音声検知 (Commands/PythonCommandBase.py ほか)
        │  press / hold / wait / isContainTemplate / waitTone / waitSound
        ▼
core/  …… GUI 非依存の純粋ロジック（tkinter・アプリ層の import 禁止）
        │  serial/: Sender 本体・Arbiter（入力調停）・encoding（行書式）
        │  transport/: base（抽象）・text_serial・registry（名前から選択）
        │  Keys: Button/Direction/Hat/Stick（行組み立ては encoding へ一本化）
        │  WakeLink: 応答つき通信（O行・設定確認用）
        │  Camera: 最新1枚だけを配る capture
        │  AudioCapture: 入力1本を所有し直近N秒を配る capture（音声版）
        │  audio_dsp / audio_latency: 検知・計測の純粋関数（FFT・相互相関・Tap時刻）
        │  InputLog / CommandLoader / Utility / CommandVision / CommandAudio ほか
        ▼
ファーム (Arduino Leonardo / Pico；専用品は別 repo Moi-poke/pico-wakeCon)
```

エントリは [`SerialController/__main__.py`](../SerialController/__main__.py)（`python -m SerialController`）と
[`Window.py`](../SerialController/Window.py) 直起動（[`launcher.py`](../SerialController/launcher.py) が使う従来経路）の2口で、どちらも
`Window.main()` へ集まる。利用者スクリプトの公開面（`Commands.Keys` /
`Commands.PythonCommandBase` / `Commands.McuCommandBase` /
`Commands.WakeLink` / `Commands.CommandVision` /
`Commands.CommandAudio`）と `settings*.ini` の
書式は凍結。公開面の正本は [`core/user_api_allowlist.py`](../SerialController/core/user_api_allowlist.py)。深刻度は表面で
違い、検査（[`tools/check_user_api.py`](../tools/check_user_api.py)）は違反、保存時
（[`core/blockly_validate.py`](../SerialController/core/blockly_validate.py)）は異常、配布時（[`core/pack_zip.py`](../SerialController/core/pack_zip.py)）は
未知を注意に留める（意図的な使い分け）。書式知識の実体は [`config.py`](../SerialController/config.py)（Tk なし）にあり、
`Settings.GuiSettings` は Tk との鏡と入出力の手順だけを持つ。

GUI 層（[`Window.py`](../SerialController/Window.py)・[`GuiAssets.py`](../SerialController/GuiAssets.py)・[`Settings.py`](../SerialController/Settings.py) ほか）は [`core/`](../SerialController/core) の利用者。
[`Window.py`](../SerialController/Window.py) は起動の組立・設定の出し入れ・終了処理だけを持ち、画面の
部品ごとの手順は [`ui/`](../SerialController/ui) の Mixin（`camera_panel` / `serial_panel` /
`command_panel` / `log_panel` / `audio_panel`）に分かれる。Mixin は `self` 越しに触る
属性を宣言しておく（mypy のため）。[`ui/`](../SerialController/ui) から `Window` 本体の import は
禁止（循環になる）。実行の手順は [`services/`](../SerialController/services)（`command_runner.py`: 起動・
停止・見張り・後始末の状態機械、`serial_service.py`: Sender の所有・
接続・切替・キーボードの寿命管理、`audio_service.py`: 取込口の所有・
切替・モニター再生の手順）へ委ねる。[`services/`](../SerialController/services) は tkinter を
import しない。逆向きの依存は [`tools/check_core.py`](../tools/check_core.py)（`task bounds`）で
禁止している。

## core/ への移し方

- 同名ファイルで [`core/`](../SerialController/core) へ移動し、旧位置には再公開シムだけ残す
  （`from core.X import Y as Y` 形式）。`from Commands.X import ...` は
  そのまま動く。新規コードは `core` から読む
- [`core/`](../SerialController/core) 内の相互参照は `core.*` の絶対 import にする（相対 import 禁止）

## 送信の経路（2系統）

- legacy（Leonardo 系）: `S` に相当する数字行を送りっぱなし
- Pico live: 姿勢スナップショットを 8ms スロットで送出、無変化時は
  88ms で再送（200ms watchdog 対策）。8ms 未満の押下はPC側タイミング
  (S行で押して待って離す) で送る

## コマンドの発見と実行

`CommandLoader` が `PythonCommands/`・`McuCommands/` 配下を import し、
`NAME` を持つ基底クラスの派生だけを拾う。1ファイルの import 失敗は
他を巻き込まない。`reload()` で実行中に再読込できるため、
コマンドファイルにモジュールレベルの副作用を置かないこと。

## 音声の取込の考え方

映像の `Camera`（最新1枚）に対し、音声は区間が要るため
`AudioCapture` が入力1本を所有して直近N秒のリングバッファへ書く。
検知（`AudioMixin`）もモニター再生もこの1本から読む。
デバイスをコマンドごとに開き直すと排他で競合するため、開閉の所有は
ここへ寄せる。内部レートは44.1kHz monoに統一し、デバイス自レート
（48kHz等）との差はリサンプルで吸収する。録音テンプレートは
`Template/audio/<pack>/*.wav`、録音クリップは `AudioClips/`。
設定は `[Audio]`（[`config.py`](../SerialController/config.py) の既定＋補正に乗せる）。利用者向けの
操作手順は [`docs/AUDIO.md`](AUDIO.md) を参照。

## 設定と並列起動

`Settings.GuiSettings` が `settings[.profile].ini` を読み書きする。
[`launcher.py`](../SerialController/launcher.py) がプロファイルを選んで別プロセスで [`Window.py`](../SerialController/Window.py) を起こし、
`pokecon[.profile].lock` で二重起動を防ぐ。

## ログ

`loguru` でファイル（`log/`）と標準出力へ出す。GUI のログ欄は
キュー経由で描画する（ワーカースレッドから widget を触らない）。

## 検証の考え方

`task ci`（ruff check / format --check / mypy / bounds / userapi / test）が
緑であること。実機なしで回せる純粋ロジックは [`tests/`](../tests) の pytest で
確認する（例: Transport の間引き、`CommandRunner` の状態遷移、
未知 capability の legacy 等価動作）。
COM ポート・キャプチャボードが要る動作は実機でのみ確認できる。
