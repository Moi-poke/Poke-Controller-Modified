# Poke-Controller MODIFIED

本質的な部分はそのままに、機能を一部追加します

![](https://github.com/Moi-Poke/Poke-Controller/blob/photo/photos/poke-con-modded.png)

## 変更点

### ver4.0.0
主な変更点（詳細は [`docs/V4_CHANGES.md`](docs/V4_CHANGES.md)）
- 内部構造の大整理（[`services/`](SerialController/services)・[`ui/`](SerialController/ui)・[`core/serial/`](SerialController/core/serial)・[`core/transport/`](SerialController/core/transport) に分割、[`Window.py`](SerialController/Window.py) は組立専用に）
- Blocklyエディタ・音声対応・シリアルモニタ・スクリプトzip配布・表示フィルタの追加
- シリアル通信の堅牢化とPico連携（有線／無線）、並列起動、起動高速化
- Python 3.12以降が必要に（3.12.10固定）
- 自作スクリプト（`Commands.*`）と `settings.ini` の形式はそのまま使えます

### ver3.6.0
主な変更点
- 開発基盤の整備（`task` + ruff + mypy、型付け、コード整理）
- GUI非依存部を [`SerialController/core/`](SerialController/core) へ分離（既存の自作スクリプトはそのまま動きます）
- LINE通知の削除（LINE Notify サービス終了のため。通知はDiscord連携へ）
- Picoファーム（別repo pico-wakeCon）との通信対応
- 複数台の並列起動に対応（ランチャー / `--profile`）
- `requirements.txt` を廃止（uv管理に一本化）

### 2025/3/29 ver3.1.0公開
主な変更点
- uvを利用したパッケージ管理方式を採用
- 不要なファイルを削除
- カメラからの映像取得を都度readするのではなく、queueにある最新を取得する方式に変更
- loguruを使ったloggingに移行(途中)
- discord連携を追加。通知先の設定はメニューバーから
	- 使い方はサンプルスクリプトを参照してください。
- logエリアへのテキスト出力をqueueを用いた方式に変更。若干高速化
- スクリプト実行中の例外発生時の出力を変更

Python 3.12以降が必要です（3.12.10固定。`.python-version` 参照）。
環境構築・起動の手順は下の [Installation](#installation) を見てください。

### ~ver3.0の追加・変更点

- ログ機能の追加\
  ボタン入力をメインウィンドウに表示、加えて各関数のログをコンソール画面に表示&ファイル出力を行う\
  ある程度確認しましたがボタン入力表示にはバグがあるかもしれないので注意
- 画像認識時に該当部分を青枠で表示する機能を追加\
  `isContainTemplate`関数に`show_position=False`を引数で渡すと表示しない
- サンプルスクリプトの追加
  - InputSwitchKeyboard.py

    Switchのキーボードの自動入力のサンプルコード
  - InputSerial.py

    シリアルコードの入力自動化サンプルコード
  - LoggingSample.py

    マクロプログラム中でのログのとり方のサンプルコード

-----

### ~ver2.8の追加・変更点

- （当時）開発の都合でpythonの動作確認バージョンを3.7.xとしていました。現行は上記の通り Python 3.12以降が必要です。
- FPSの設定の追加
- 画面表示サイズの変更オプションの追加
- ログエリアはサイズの変更に応じて横方向に伸縮するように
- スティック周りの機能追加

  **スティックの傾きの強さの設定**
  - スティックの移動可能な範囲を単位円の内部と考えて、\
    傾き度合いを0以上1以下で設定可能にしました。\
    例えば`Direction(Stick.LEFT, θ, r)`\
    というコマンドは、左スティックのx,y座標が
    ```
    x=r*cosθ
    y=r*sinθ
    ```
    となるような入力をします。この場合は半径rの円となります。
  - r=1.0をデフォルト値としているので\
    `Direction(Stick.LEFT, θ)`
    と書いた場合はr=1として認識されます。\
    より詳しくはサンプルコードを同梱していますので\
    そちらとSwitch内設定のスティックの補正画面を合わせて確認してください

  **マウスでスティック操作機能**
  - マウスで直感的な操作ができそうな感じにしています。~~ラグがあるので操作は結構難しいです。~~\
    操作円の半径は変更可能なので、需要があればConfigファイルに載せます。 タッチパネル対応モニタ使用の際などの挙動は不明ですが、そちらのほうが向いているかもしれません。\
    またこの機能の追加に伴い、'Ctrl+左クリック'がクリック点座標表示になっています。
- 画面キャプチャ機能の追加
  - キャプチャしている画面部分を'Ctrl+Shift+左クリック'しながらドラッグした範囲をキャプチャすることができます。
- メニュー機能の追加

  現状は以下の機能のみ
  - LINE連携機能は削除しました（LINE Notify が2025/3/31にサービス終了のため）。通知にはDiscord連携を使ってください。

  - Pokémon Home連携

    そのうち大幅に変わるかもしれません\
    フォルム別の名前があるポケモン(ロトムなど)については現在第7世代までしか対応していません\
    [`SerialController/db/poke_form_name.csv`](SerialController/db/poke_form_name.csv)に追記することで対応可能になります
  - キーコンフィグ追加

    主要なキーのコンフィグ機能を追加しています。\
    注意点として、複数キーを同時に割り当てても、同時に入力されることはありません。少々不親切ですがお許しください\
    また、これにともなって設定ファイルの書式が変わっています。手動で書き換え可能になっています。\
     デフォルトに戻す機能はつけていないので、戻したいときは設定ファイルを消すか、Settings.pyを読んでください。
- ~~ボタン入力関数表示機能追加プログラム(作 KCT様)を組み込み~~ ver3.0以降独自の入力表示機能実装に変更
- その他GUIのブラッシュアップ
- Codeのリファクタリング

  私の開発環境の関係で全体的にPEP8準拠寄りにしました
  - タブインデントからスペース4つインデントに変更
  - 不要なimportの削除、並び替えなど最適化

## Installation

uvで管理しています。[`pyproject.toml`](pyproject.toml) + [`uv.lock`](uv.lock) が正本です（`requirements.txt` はありません）。
Python は 3.12.10 固定（`.python-version`）で、`uv sync` が用意します。

```cmd
pip install uv
uv sync
.\.venv\Scripts\activate
python .\SerialController\Window.py
```

複数台を並列で動かすときは、ランチャーを使うかプロファイルを指定します。
プロファイルごとに `SerialController/settings.<名前>.ini` が自動で作られます。

```cmd
python .\SerialController\launcher.py
python .\SerialController\Window.py --profile switch1
python .\SerialController\Window.py --profile switch1 --transport pico_uart
```

通信方式は `legacy_text`（本家 Leonardo 用）と `pico_uart`（pico-wakeCon 用）の2種です。
`--transport` を省略したときは設定ファイルの `[Transport] name` を使います。

開発用コマンドはタスクランナー `task` にまとめています。
`task` 本体は別途導入（`winget install Task.Task`）して、`task setup_dev` でフックを有効化してください。

```cmd
task app       起動する（task app PROFILE=switch1 でプロファイル指定）
task test      単体検査
task ci        一通りの検査（ruff＋整形確認＋mypy＋境界検査＋公開API検査＋単体検査）
```

`task` が無い環境では [`taskfile.yml`](taskfile.yml) の `uv run ...` を直接実行してください。

## おまけ

- 好みの表示サイズ・FPSがある場合は、Camera欄のドロップダウンで変えてください（`settings*.ini` に保存されます）。

- OpenCVで行う画像認識をNVIDIA GPU(CUDA)で動かす場合は、CUDA対応のOpenCVがあれば自動で使います（`isContainTemplateGPU` 系。見つからなければCPUに切り替わります）。\
  ただし、pip install で入る通常版のOpenCVにはCUDAが含まれていないため、使うには自分のGPUに対応したオプションでpython用のOpenCVをソースからビルドする必要があります。\
  それなりに難易度が高くかなり手間な処理になりますが、余裕がある方は試してみてください。\
  `OpenCV + CUDA (+ Windows)`
  などと検索すればビルドの解説ページが出てきます。
  
- SciPyは最短経路の算出に使用しています。\
  具体的には、Switchのソフトウェアキーボードを有向グラフに見立てて、文字入力の最適化を試みています。\
  サンプルコードを同梱しているので、興味のある方は試してみてください。

以下は本家様の説明になります。
- - -

Pythonで書く！Switchの自動化支援ソフトウェア

<!-- ALL-CONTRIBUTORS-BADGE:START - Do not remove or modify this section -->
[![All Contributors](https://img.shields.io/badge/all_contributors-4-orange.svg?style=flat-square)](#contributors-)
<!-- ALL-CONTRIBUTORS-BADGE:END -->

## セットアップと使い方

- まずはモノの準備
  - [Github - wiki](https://github.com/KawaSwitch/Poke-Controller/wiki)

- 準備ができたら進みましょう
  - [Poke-Controllerの使い方](https://github.com/KawaSwitch/Poke-Controller/wiki/Poke-Controller%E3%81%AE%E4%BD%BF%E3%81%84%E6%96%B9)

  - [デフォルトの実装コマンドの確認](https://github.com/KawaSwitch/Poke-Controller/wiki/%E3%83%87%E3%83%95%E3%82%A9%E3%83%AB%E3%83%88%E3%81%AE%E5%AE%9F%E8%A3%85%E3%82%B3%E3%83%9E%E3%83%B3%E3%83%89)

  - [新しいコマンドを作成](https://github.com/KawaSwitch/Poke-Controller/wiki/%E6%96%B0%E3%81%97%E3%81%84Python%E3%82%B3%E3%83%9E%E3%83%B3%E3%83%89%E3%81%AE%E4%BD%9C%E3%82%8A%E6%96%B9)

分からないことや改善要望などがあれば遠慮なく[Issue](https://github.com/KawaSwitch/Poke-Controller/issues)まで  
[Q&A](https://github.com/KawaSwitch/Poke-Controller/wiki/Q&A)や[解決済みIssue](https://github.com/KawaSwitch/Poke-Controller/issues?q=is%3Aissue+is%3Aclosed)なども役に立つかもしれません

## クイックビュー

簡単に機能を見てみましょう

### コマンド作成用のライブラリの提供

通常のボタン押下  
`self.press(Button.A) # Aボタンを押して離す`  
`self.press(Button.A, 0.1, 1) # Aボタンを0.1秒間押して離した後, 1秒待機`

左右スティック & HAT(十字)キー  
`self.press(Direction.RIGHT, 5) # 左スティックを右に5秒間倒す`  
`self.press(Hat.LEFT) # 十字キー左を押して離す`

同時押し  
`self.press([Button.A, Button.B]) # AボタンとBボタンを同時に押して離す`

ホールド  
`self.hold([Direction.UP, Direction.R_DOWN], wait=1) # 左スティックを上, 右スティックを下に倒して1秒待つ`  
`self.press(Button.A) # スティックを倒した状態でAボタンを押して離す`

[リファレンス](https://github.com/KawaSwitch/Poke-Controller/wiki/Python%E3%82%B3%E3%83%9E%E3%83%B3%E3%83%89_%E4%BD%9C%E6%88%90How_to)やデフォルトのコマンドなども参考にして中身を覗いてみましょう  
作成したコマンドや便利な機能は[プルリク](https://github.com/KawaSwitch/Poke-Controller/pulls)や[Issue](https://github.com/KawaSwitch/Poke-Controller/issues)で頂けると非常に喜びます

### Pythonファイル管理

作成したコマンドのclassは1つのPythonファイルの中にいくつも記述できます  
またPythonCommandsのフォルダ内であればいくつもフォルダを作成可能です  
自由に配置していきましょう

![](https://github.com/KawaSwitch/Poke-Controller/blob/photo/photos/Wiki/PythonCommandHowTo/command_file_location.PNG)

### 実行時のコマンド切替

配置したコマンド群はマウス操作で簡単に切り替えることができます

### リロード機能

Poke-Controllerを動作しながらファイルの変更を再読込して反映することができます  
こつこつデバグしたい方におすすめ！

### 画像認識

キャプチャボードでSwitchの画面を取り込めば, シリアル通信だけでは叶わない操作もできるかも  
これらもライブラリとして機能を提供しています  
`self.isContainTemplate('status.png') # テンプレートマッチング`

現在の機能([実装内容](https://github.com/KawaSwitch/Poke-Controller/wiki/%E7%94%BB%E5%83%8F%E8%AA%8D%E8%AD%98%E3%81%A8%E3%81%AF))は少ないがアップデート予定  
![リリース前GUI](https://github.com/KawaSwitch/Poke-Controller/blob/photo/photos/pokecon_gui_before_release.PNG)

### キーボード操作

キーボードをスイッチのコントローラとして使用することができます

| Switchコントローラ | キーボード |
| ---- | ---- |
| A, B, X, Y, L, R | 'a', 'b', ...キー |
| ZL | 'k'キー |
| ZR | 'e'キー |
| MINUS | 'm'キー |
| PLUS | 'p'キー |
| LCLICK | 'q'キー |
| RCLICK | 'w'キー |
| HOME | 'h'キー |
| CAPTURE | 'c'キー |
| 左スティック | 矢印キー |

割り当てはメニューの「キーコンフィグ」で変えられます。

## リリース

- 過去リリース
  - [Github - Releases](https://github.com/KawaSwitch/Poke-Controller/releases)

- 進捗状況の確認
  - [Github - Project](https://github.com/KawaSwitch/Poke-Controller/projects)

- ロードマップ
  - [リリースについて](https://github.com/KawaSwitch/Poke-Controller/wiki/About-Releases)

## 貢献

これらの貢献者に感謝します ([emoji key](https://allcontributors.org/docs/en/emoji-key)):

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tr>
    <td align="center"><a href="https://github.com/KawaSwitch"><img src="https://avatars3.githubusercontent.com/u/41296626?v=4" width="100px;" alt=""/><br /><sub><b>KawaSwitch</b></sub></a><br /><a href="https://github.com/KawaSwitch/Poke-Controller/commits?author=KawaSwitch" title="Code">💻</a> <a href="#maintenance-KawaSwitch" title="Maintenance">🚧</a> <a href="https://github.com/KawaSwitch/Poke-Controller/commits?author=KawaSwitch" title="Documentation">📖</a> <a href="#question-KawaSwitch" title="Answering Questions">💬</a></td>
    <td align="center"><a href="https://github.com/Moi-poke"><img src="https://avatars1.githubusercontent.com/u/59233665?v=4" width="100px;" alt=""/><br /><sub><b>Moi-poke</b></sub></a><br /><a href="https://github.com/KawaSwitch/Poke-Controller/commits?author=Moi-poke" title="Code">💻</a> <a href="#question-Moi-poke" title="Answering Questions">💬</a></td>
    <td align="center"><a href="https://github.com/xv13"><img src="https://avatars2.githubusercontent.com/u/47322147?v=4" width="100px;" alt=""/><br /><sub><b>xv13</b></sub></a><br /><a href="https://github.com/KawaSwitch/Poke-Controller/issues?q=author%3Axv13" title="Bug reports">🐛</a></td>
	<td align="center"><a href="https://github.com/vyPeony"><img src="https://avatars0.githubusercontent.com/u/39150264?v=4" width="100px;" alt=""/><br /><sub><b>vyPeony</b></sub></a><br /><a href="https://github.com/KawaSwitch/Poke-Controller/commits?author=vyPeony" title="Code">💻</a></td>
  </tr>
</table>

<!-- markdownlint-enable -->
<!-- prettier-ignore-end -->
<!-- ALL-CONTRIBUTORS-LIST:END -->

このプロジェクトは, [all-contributors](https://github.com/all-contributors/all-contributors)仕様に準拠しています. どんな貢献も歓迎します！

## ライセンス

本プロジェクトはMITライセンスです  
詳細は [LICENSE](https://github.com/KawaSwitch/Poke-Controller/blob/master/LICENSE) を参照ください

また, 本プロジェクトではLGPLライセンスのDirectShowLib-2005.dllを同梱し使用しています  
[About DirectShowLib](http://directshownet.sourceforge.net/)  
