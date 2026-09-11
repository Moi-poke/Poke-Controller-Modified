# Blocklyエディタ（TODO-20基盤）

ブラウザで操作を組み立て、Pythonコマンドとして保存する。生成はBlockly→Python片方向のみ（逆変換なし）。

使い方： メニュー→コマンド→「Blocklyエディタ...」→ブラウザで編集→保存ボタン→約1秒で一覧に反映（窓を閉じる必要なし。実行中は開けない・作り直さない）。

再編集： ページ下の「保存済み」から選び「開く」（編集中の内容があるときは確認する）。削除： 同じく選んで「削除」（確認のうえ `.py` と編集データの対を消す）。一覧に出るのは `.blockly.json` 付き（再編集可能分）のみ。

保存物： `Commands/PythonCommands/<保存名>.py`と同名`.blockly.json`の対。保存名は英字・数字・`_`（例: MyBlock）。`.blockly.json`は一覧を壊さない（`.py`のみ走査）。

ブロック： program（NAME＋DO）、press（ボタン14種＋長さ＋待ち）、wait（秒）。繰り返し・条件・数値は標準ブロックを使う。

画像認識： 「画像認識」分類に6種。`contains`（値・標準ifの条件に直結）と`position`（値）は値型、`wait_appear`・`wait_gone`・`wait_stable`は文型、`color`（値・HSV上下限＋割合＋範囲（CROP、空＝全体））は値型。テンプレ4種（`contains`・`wait_appear`・`wait_gone`・`position`）にグレー欄あり（チェックON＝グレー・OFF＝カラー、既定OFF＝カラー）。旧保存物（`.blockly.json`）を開き直すと定義既定によりカラー（`use_gray=False`）で生成される（保存済み`.py`は再保存まで不変）。vision系が1つでも混ざると生成コードは`ImageProcPythonCommand`＋`__init__(cam, gui)`になり、カメラが自動で渡る（混ざらなければ従来通り）。

欄の形式： TEMPLATE欄は候補からのコンボボックス選択（未登録名は先頭に残る。`(空欄)`で消せる）。CROP欄は空＝全体または`x1,y1,x2,y2`（実画素）。THRESHOLD欄は0〜1（既定0.7）。PREVIEW欄は指定中画像の縮小表示（幅120pxまで、出ない名は壊れ表示になることがある。表示のみで保存物・生成コードは変わらない）。

テンプレ画像： [`Template/`](../SerialController/Template)以下の画像が候補に出る（`画像`欄→`選択中へ反映`で選択中ブロックへ入る。選択が外れているときは最後に触ったブロック）。素名（例: `a.png`）は保存できるが共有画像扱いで配布zipに含まれない。配布する場合は`Template/<名>/...`に置く。

テンプレ作成： 編集画面の「テンプレ作成」区画で`[キャプチャ取得]`→拡大表示が自動で開く→ドラッグで範囲選択（枠内ドラッグで移動・赤い点8か所で大きさ変更・x/y/幅/高さの数値とスライダーでも指定可、単位は実画像の画素）→`[確定]`で区画へ反映→保存名を書いて`[切出し保存]`。拡大表示の中でも保存名を書いて`[切出し保存]`できる（開いたまま連番で保存できる）。保存先は`Template/blockly/<名>.png`固定で重複は`_2`・`_3`連番（flat-only。保存名に`/`・`\`は使えない。絶対・`..`・`:`も不可）。保存後は候補に自動で入り、画像欄に選ばれた状態になるので`選択中へ反映`でそのまま使える。実画像で8px未満の辺がある範囲は無効。カメラが無いときは取得に理由が出て保存はできない。

ブロックへの範囲反映： 画像認識ブロックの📷を押すと、拡大表示がそのブロック用に開く（明示選択のみ。未選択など対象外は案内が出て開かない。テンプレ候補の選択欄付き、範囲欄・しきい値欄の値があれば初期表示、枠の角に`x,y`・`w×h`が出て操作中の数値欄が強調される）。画像が無いときは自動でキャプチャ取得してから開く。範囲・テンプレ・しきい値を変えるたびブロックへ即時反映されるため、閉じるだけでよい（Esc・×・キャンセルで閉じる）。

照合テスト： modalを開くと自動で照合が走り、候補画像を最新キャプチャ内で探して一致度％・件数・見つかった場所を出す（青＝ヒット・赤＝ミス、試し打ちは実行時と同一条件）。テンプレ（寸法表示付き）・しきい値（既定0.7）・グレー（既定OFF＝カラー）・枠・画像の変更に追従する（連続操作は少し待ってから1回）。手持ちのスクショは画像ファイルを選ぶと自動で試す。赤枠内だけ試すときはチェックを入れる。色フィルタ3種（OFF／対象外グレー／白黒）＋割合表示で、静止画で調整→ブロックへ反映（H系・RATIO・CROP）。メイン画面の「表示フィルタ」は表示のみ（認識・保存に影響なし）。「調整...」で開く専用ウィンドウの「色補正」（ガンマ・コントラスト0起点±2・輝度・彩度・色相シフト、抽出より先にかかる）と「色抽出」（HSV範囲・3種表示）の11項目＋モードはiniの[PreviewFilter]へ保存され（調整ウィンドウを閉じたときと終了時に保存）、「既定に戻す」で一括リセットできる。ON/OFF自体はセッションのみ（起動時は常にOFF）。保存物は変わらない。

配布： zipには`.py`と同名`.blockly.json`が対で入る（あれば再編集可）。manifestはv0相当を保つ（`format`欄は将来の判別用に予約、現行は無くてもv0扱い）。

資産： [`SerialController/assets/blockly/`](../SerialController/assets/blockly)にvendored（Blockly 13.2.1、CDN禁止）。VERSIONに版を記録。入手元は https://registry.npmjs.org/blockly/-/blockly-13.2.1.tgz から該当5点のみ（手順はplan参照）。

検証： `task test`（全体）。保存時のimport検査は許可外・未知を異常にし（正本は[`core/user_api_allowlist.py`](../SerialController/core/user_api_allowlist.py)）、配布時の未知は注意に留める。Blocklyまわりは `uv run --frozen pytest tests/test_blockly_assets.py tests/test_blockly_validate.py tests/test_blockly_save.py tests/test_blockly_templates.py tests/test_blockly_capture.py tests/test_blockly_match.py tests/test_blockly_browser.py tests/test_blockly_editor.py tests/test_pack_blockly.py -q`（ブラウザ結合はnode必須、無ければskip）。
