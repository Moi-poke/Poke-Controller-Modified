# Blocklyエディタ（TODO-20基盤）

ブラウザで操作を組み立て、Pythonコマンドとして保存する。生成はBlockly→Python片方向のみ（逆変換なし）。

使い方： メニュー→コマンド→「Blocklyエディタ...」→ブラウザで編集→保存ボタン→約1秒で一覧に反映（窓を閉じる必要なし。実行中は開けない・作り直さない）。

再編集： ページ下の「保存済み」から選び「開く」（編集中の内容があるときは確認する）。削除： 同じく選んで「削除」（確認のうえ `.py` と編集データの対を消す）。一覧に出るのは `.blockly.json` 付き（再編集可能分）のみ。

保存物： `Commands/PythonCommands/<保存名>.py`と同名`.blockly.json`の対。保存名は英字・数字・`_`（例: MyBlock）。`.blockly.json`は一覧を壊さない（`.py`のみ走査）。

ブロック： program（NAME＋DO）、press（ボタン14種＋長さ＋待ち）、wait（秒）。繰り返し・条件は標準ブロックを使う。画像認識ブロックはTODO-22で後付け。

資産： `SerialController/assets/blockly/`にvendored（Blockly 13.2.1、CDN禁止）。VERSIONに版を記録。入手元は https://registry.npmjs.org/blockly/-/blockly-13.2.1.tgz から該当5点のみ（手順はplan参照）。

検証： `uv run --frozen pytest tests/test_blockly_assets.py tests/test_blockly_validate.py tests/test_blockly_save.py -q`
