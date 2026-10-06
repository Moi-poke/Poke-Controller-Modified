#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ログ出力のサンプル.

マクロの中で「何を」「どこへ」出すかの使い分けを、実際に流して見せる。
ログ欄の形は docs/LOG_VIEW.md を参照。

  上の欄（すべての出力） … 時系列で流れる。種類ごとに色と記号が付く。
  下の欄（結果とエラー） … 残しておきたい行だけが集まる。
                            クリックすると上の欄のその時点へ移動する。
  log フォルダのファイル … logger の出力。画面に出ない記録も残る。

| 書き方                         | 上の欄       | 下の欄 | ファイル |
|--------------------------------|--------------|--------|----------|
| print("…")                     | 通常         | ×      | ×        |
| self.print2("…")               | ▶ 結果（青） | ○      | ×        |
| print("警告: …")               | ⚠ 警告       | ○      | ×        |
| print("エラー: …")             | ✖ エラー     | ○      | ×        |
| logger.debug / info / warning  | ×            | ×      | ○        |
| logger.error / critical        | ✖ エラー     | ○      | ○        |

警告・エラーとみなすのは行頭の目印だけ（「警告:」「WARNING:」「[WARN]」
「エラー:」「失敗:」「ERROR:」「[ERROR]」）。文中に error と書いただけの
普通の文は、通常の出力のまま。
"""

import traceback

from Commands.PythonCommandBase import PythonCommand
from loguru import logger


class LoggingSample(PythonCommand):
    NAME = "ログ出力のサンプル"
    TAGS = ["Sample"]

    def do(self) -> None:
        # 1. 進捗は print。上の欄に流れ、時刻が付く。
        print("1. print は上の欄に流れます（進捗向け）")
        for i in range(1, 6):
            print(f"探索中... {i} 回目")
            self.wait(0.2)

        # 2. 同じ行が続くと1行にまとまり、「×N」で回数が出る。
        #    （「表示」メニューの「同じ行の繰り返しを ×N にまとめる」）
        print("2. 同じ行の繰り返しは ×N にまとまります")
        for _ in range(10):
            print("待機中...")
            self.wait(0.1)

        # 3. 後で見返したい結果は print2。上の欄（時系列）と下の欄の両方に出る。
        #    進捗がいくら流れても、下の欄には残る。
        print("3. print2 は下の欄にも残ります（結果向け）")
        self.print2("見つかった: 色違い 1体目")

        # 4. 警告・エラーは行頭に目印を付けて print する。色と記号が付き、
        #    下の欄へ集まり、ツールバーの ⚠ / ✖ の件数に数えられる。
        print("4. 行頭の目印で警告・エラーになります")
        print("警告: 画面が暗いため、認識の精度が落ちています")
        print("エラー: テンプレート画像が見つかりません")
        print("error という語が文中にあるだけなら通常の出力です")

        # 5. 例外の traceback を print すると、塊ごとエラーになる。
        #    下の欄には何が起きたかの1行（例外名の行）だけが集まる。
        print("5. traceback は塊ごとエラー、下の欄には例外名の1行だけ")
        try:
            int("数字ではない")
        except ValueError:
            print(traceback.format_exc())

        # 6. logger はファイル（log フォルダ）への記録。
        #    debug / info / warning は画面に出ず、error 以上は画面にも出る。
        print("6. logger はファイルへ。error 以上は画面にも出ます")
        logger.debug("DEBUG（ファイルのみ）")
        logger.info("INFO（ファイルのみ）")
        logger.warning("WARNING（ファイルのみ）")
        logger.error("ERROR（ファイルと画面）")
        logger.critical("CRITICAL（ファイルと画面）")

        self.print2("おわり: 下の欄の行をクリックすると、上の欄のその時点へ移動します")
