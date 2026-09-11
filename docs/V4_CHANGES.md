# v4.0.0 変更点（`origin/master` 基準）

`release/v4.0` がこのリポジトリの主系（`origin/master`）から変わった点を、
利用者向けに分野別でまとめたもの。開発基盤の話は末尾に短く置く。
基準の差分は73件（`git log origin/master..release/v4.0` で確認できる）。

## 新機能

### Blocklyエディタ（メニュー→コマンド→「Blocklyエディタ...」）
- ブラウザで操作を組み立て、Pythonコマンドとして保存・再編集・削除できる。
  一覧への反映は約1秒（窓を閉じる必要なし）。詳細は [docs/BLOCKLY_EDITOR.md](BLOCKLY_EDITOR.md)
- 画像認識ブロック：テンプレ選択（候補一覧＋プレビュー）・範囲指定・
  グレー／カラー切替（既定カラー）・しきい値つき。vision系を混ぜると
  自動で画像認識コマンドになる
- キャプチャ切出し：カメラ画像からドラッグで範囲を切り出してテンプレ保存
- 一致度シミュレーション：実行前に最新キャプチャ内で一致度％・件数・場所を確認
- 色フィルタの試し打ち：HSV範囲＋割合表示で、静止画で調整してブロックへ反映

### 音声対応（Audio欄）
- キャプチャボード音声の取込、特定音の検知トリガー（`waitTone`/`waitSound`）、
  ゲーム音のモニター再生、押下→検知の遅延計測。詳細は [docs/AUDIO.md](AUDIO.md)
- 開けるデバイスだけを番号付きで一覧化（同名重複に強い）。推定遅延つき

### シリアルモニタ（メニュー→「シリアルモニタ」）
- 通信ログの時系列表示、フィルタ切替、全量ログ保存。監視しても応答横取りなし。
  詳細は [docs/SERIAL_MONITOR.md](SERIAL_MONITOR.md)

### 自作スクリプトのzip配布
- `pokecon.json`＋本体＋画像を梱包。メニューから導入・削除、CLIあり。
  詳細は [docs/PACK_FORMAT_v0.md](PACK_FORMAT_v0.md)

### 表示フィルタ（カメラ行→「表示フィルタ」＋「調整...」）
- メイン画面の表示だけに効くフィルタ（認識・保存に影響なし）。
  OBSの色補正に相当する調整（ガンマ・コントラスト・輝度・彩度・色相シフト）と
  HSV色抽出（対象外グレー／白黒マスク）。設定はiniへ保存、ON/OFFは起動ごとにOFF

### WakeSetupの拡充（Switch2のwake設定＋リプレイ）
- 疎通(P)・無線ON/OFF(W)・表示切替(D/M)・破棄(X)・鍵削除(K)ボタン＋USB状態表示

### 並列起動・入力ログ・キーコンフィグ
- ランチャー／`--profile` で複数台の並列起動（設定・ロックを分離）
- ボタン入力のログ表示、キー割り当ての変更画面

### コマンド選択まわりの刷新
- 使用履歴、タグ編集、検索パレット（Ctrl+K）、実行中の一時停止／停止制御

### Pico連携（別repo pico-wakeConがファーム側）
- 有線（USBシリアル変換器）・無線（PicoのUSB直結＋Bluetooth）の2経路。
  通信方式は登録制（`legacy_text`／`pico_uart`）。入力調停（human/script/off）つき

## 改善

- 同型キャプチャボードの識別、カメラ無効（Disable）の選択

## 互換性・移行注意

- Python 3.12以降が必要（3.12.10固定）。`requirements.txt` は廃止しuv管理に一本化
- 自作スクリプト（`Commands.*`）と `settings.ini` はそのまま使える。
  新設セクション（`[Transport]` `[Arbitration]` `[Audio]` `[PreviewFilter]` 等）は
  起動時に自動補完される。移動したモジュールは互換shimあり
- Blocklyの旧保存物を開き直すと、テンプレ照合がカラー（`use_gray=False`）で
  生成される（保存済み `.py` は再保存まで不変）
- LINE通知は削除（LINE Notifyサービス終了のため）。通知はDiscord連携へ
- `video_capture_wrapper.py` は `core/Camera.py` に統合（使い方は同じ）
- `video_capture_wrapper.py` は `core/Camera.py` に統合（使い方は同じ）
- `video_capture_wrapper.py` は `core/Camera.py` に統合（使い方は同じ）

## 開発基盤（開発者向け）

- `task` ランナー＋ruff＋mypy＋単体テスト（`task ci` で一括）。CIあり
- 層分け：`core/`（GUI非依存の純粋関数）・`services/`（手順）・`ui/`（画面）。
  `Window.py` は組立専用。境界は自動検査（`task bounds`）
- 利用者スクリプト向け公開API（`Commands.*`）は凍結し、自動検査（`task userapi`）。
  詳細は [docs/ARCHITECTURE.md](ARCHITECTURE.md)・[AGENTS.md](../AGENTS.md)
