# ARCHITECTURE

Poke-Controller Modified の構成メモ。詳細な開発手順は `AGENTS.md`、
自動化コマンドの作り方は本家 wiki を参照。

## 層構造

```
ユーザースクリプト (Commands/PythonCommands/, Commands/McuCommands/)
        │  PythonCommand / ImageProcPythonCommand / McuCommand
        ▼
実行制御・操作・画像認識 (Commands/PythonCommandBase.py ほか)
        │  press / hold / wait / isContainTemplate
        ▼
core/  …… GUI 非依存の純粋ロジック（tkinter・アプリ層の import 禁止）
        │  serial/: Sender 本体・Arbiter（入力調停）・encoding（行書式）
        │  transport/: base（抽象）・text_serial・registry（名前から選択）
        │  Keys: Button/Direction/Hat/Stick（行組み立ては encoding へ一本化）
        │  WakeLink: 応答つき通信（O行・設定確認用）
        │  Camera: 最新1枚だけを配る capture
        │  InputLog / CommandLoader / Utility / CommandVision ほか
        ▼
ファーム (Arduino Leonardo / Pico；専用品は別 repo Moi-poke/pico-wakeCon)
```

エントリは `SerialController/__main__.py`（`python -m SerialController`）と
`Window.py` 直起動（`launcher.py` が使う従来経路）の2口で、どちらも
`Window.main()` へ集まる。利用者スクリプトの公開面（`Commands.Keys` /
`Commands.PythonCommandBase` / `Commands.McuCommandBase` /
`Commands.WakeLink`）と `settings*.ini` の書式は凍結。書式知識の実体は
`config.py`（Tk なし）にあり、`Settings.GuiSettings` は Tk との鏡と
入出力の手順だけを持つ。

GUI 層（`Window.py`・`GuiAssets.py`・`Settings.py` ほか）は `core/` の利用者。
`Window.py` は起動の組立・設定の出し入れ・終了処理だけを持ち、画面の
部品ごとの手順は `ui/` の Mixin（`camera_panel` / `serial_panel` /
`command_panel` / `log_panel`）に分かれる。Mixin は `self` 越しに触る
属性を宣言しておく（mypy のため）。`ui/` から `Window` 本体の import は
禁止（循環になる）。実行の手順は `services/`（`command_runner.py`: 起動・
停止・見張り・後始末の状態機械、`serial_service.py`: Sender の所有・
接続・切替・キーボードの寿命管理）へ委ねる。`services/` は tkinter を
import しない。逆向きの依存は `tools/check_core.py`（`task bounds`）で
禁止している。

## core/ への移し方

- 同名ファイルで `core/` へ移動し、旧位置には再公開シムだけ残す
  （`from core.X import Y as Y` 形式）。`from Commands.X import ...` は
  そのまま動く。新規コードは `core` から読む
- `core/` 内の相互参照は `core.*` の絶対 import にする（相対 import 禁止）

## 送信の経路（2系統）

- legacy（Leonardo 系）: `S` に相当する数字行を送りっぱなし
- Pico live: 姿勢スナップショットを 8ms スロットで送出、無変化時は
  88ms で再送（200ms watchdog 対策）。8ms 未満の押下はPC側タイミング
  (S行で押して待って離す) で送る (pico-wakeCon にQ/Rは無いためQ経路は持たない)

## コマンドの発見と実行

`CommandLoader` が `PythonCommands/`・`McuCommands/` 配下を import し、
`NAME` を持つ基底クラスの派生だけを拾う。1ファイルの import 失敗は
他を巻き込まない。`reload()` で実行中に再読込できるため、
コマンドファイルにモジュールレベルの副作用を置かないこと。

## 設定と並列起動

`Settings.GuiSettings` が `settings[.profile].ini` を読み書きする。
`launcher.py` がプロファイルを選んで別プロセスで `Window.py` を起こし、
`pokecon[.profile].lock` で二重起動を防ぐ。

## ログ

`loguru` でファイル（`log/`）と標準出力へ出す。GUI のログ欄は
キュー経由で描画する（ワーカースレッドから widget を触らない）。

## 検証の考え方

`task ci`（ruff check / format --check / mypy / bounds / userapi / test）が
緑であること。実機なしで回せる純粋ロジックは `tests/` の pytest で
確認する（例: Transport の間引き、`CommandRunner` の状態遷移、
未知 capability の legacy 等価動作）。
COM ポート・キャプチャボードが要る動作は実機でのみ確認できる。
