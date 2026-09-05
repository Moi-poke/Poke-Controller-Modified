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
        │  Sender: 姿勢・調停・live worker
        │  Transport: 線の開閉・1行送信（TextSerial / PicoUart）
        │  Keys: Button/Direction/Hat/Stick と行の組み立て
        │  WakeLink: 応答つき通信（O行・設定確認用）
        │  Camera: 最新1枚だけを配る capture
        │  InputLog / CommandLoader / Utility / CommandVision ほか
        ▼
ファーム (Arduino Leonardo / Pico；専用品は別 repo Moi-poke/pico-wakeCon)
```

GUI 層（`Window.py`・`GuiAssets.py`・`Settings.py` ほか）は `core/` の利用者。
逆向きの依存は `tools/check_core.py`（`task bounds`）で禁止している。

## core/ への移し方

- 同名ファイルで `core/` へ移動し、旧位置には再公開シムだけ残す
  （`from core.X import Y as Y` 形式）。`from Commands.X import ...` は
  そのまま動く。新規コードは `core` から読む
- `core/` 内の相互参照は `core.*` の絶対 import にする（相対 import 禁止）

## 送信の経路（2系統）

- legacy（Leonardo 系）: `S` に相当する数字行を送りっぱなし
- Pico live: 姿勢スナップショットを 8ms スロットで送出、無変化時は
  88ms で再送（200ms watchdog 対策）。短い press は Q/R 時刻付き
  キューへ回し、無応答なら従来経路へ落とす（pico-wakeCon に Q/R は無い）

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

`task ci`（ruff check / format --check / mypy / bounds）が緑であること。
単体テストは無いため、純粋ロジックの変更は小さな実行スクリプトで
確認する（例: `Direction` の生成、`acceptsGuiArg` の新旧一致）。
COM ポート・キャプチャボードが要る動作は実機でのみ確認できる。
