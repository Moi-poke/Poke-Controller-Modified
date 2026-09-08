# Blocklyエディタ（TODO-20基盤）

ブラウザで操作を組み立て、Pythonコマンドとして保存する。生成はBlockly→Python片方向のみ（逆変換なし）。

使い方： メニュー→コマンド→「Blocklyエディタ...」→ブラウザで編集→保存ボタン→約1秒で一覧に反映（窓を閉じる必要なし。実行中は開けない・作り直さない）。

再編集： ページ下の「保存済み」から選び「開く」（編集中の内容があるときは確認する）。削除： 同じく選んで「削除」（確認のうえ `.py` と編集データの対を消す）。一覧に出るのは `.blockly.json` 付き（再編集可能分）のみ。

保存物： `Commands/PythonCommands/<保存名>.py`と同名`.blockly.json`の対。保存名は英字・数字・`_`（例: MyBlock）。`.blockly.json`は一覧を壊さない（`.py`のみ走査）。

ブロック： program（NAME＋DO）、press（ボタン14種＋長さ＋待ち）、wait（秒）。繰り返し・条件は標準ブロックを使う。

画像認識： 「画像認識」分類に6種。`contains`（値・標準ifの条件に直結）と`position`（値）は値型、`wait_appear`・`wait_gone`・`wait_stable`は文型、`color`（値・HSV上下限＋割合）は値型。vision系が1つでも混ざると生成コードは`ImageProcPythonCommand`＋`__init__(cam, gui)`になり、カメラが自動で渡る（混ざらなければ従来通り）。テンプレ欄の`範囲`は空＝全体または`x1,y1,x2,y2`。

テンプレ画像： `Template/`以下の画像が候補に出る（`画像`欄→`選択中へ反映`で選択中ブロックへ入る）。素名（例: `a.png`）は保存できるが共有画像扱いで配布zipに含まれない。配布する場合は`Template/<名>/...`に置く。

配布： zipには`.py`と同名`.blockly.json`が対で入る（あれば再編集可）。manifest v0は不変。

資産： `SerialController/assets/blockly/`にvendored（Blockly 13.2.1、CDN禁止）。VERSIONに版を記録。入手元は https://registry.npmjs.org/blockly/-/blockly-13.2.1.tgz から該当5点のみ（手順はplan参照）。

検証： `uv run --frozen pytest tests/test_blockly_assets.py tests/test_blockly_validate.py tests/test_blockly_save.py tests/test_blockly_templates.py tests/test_blockly_browser.py tests/test_blockly_editor.py tests/test_pack_blockly.py -q`
