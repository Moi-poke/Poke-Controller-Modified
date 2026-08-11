"""LogPane.py - ログ欄への描画.

Window.py から切り出した。ここが持つのは「キューに溜まった文字列を
Text ウィジェットへ流し込む」という一連の処理で、画面の他の部分
（カメラ・シリアル・コマンド）とは関わらない。

設計上の要点が3つある。

1. 経路を分ける
   print / 副ログ / 入力ログでキューを別にしている。同じ器に入れると、
   量の多い入力ログが上限を食い尽くしてコマンドの出力が捨てられる。

2. 溢れたら古い方を捨てる
   無制限に溜めるとメモリを食ったまま、停止後も延々と流れ続ける。
   新しい行のほうが知りたい情報なので、捨てるのは古い方。
   捨てた件数は「... N行省略 ...」として1行で知らせる。

3. 描画はまとめて行う
   write のたびに描くと print の多いコマンドで極端に重くなる。
   キューへ積むだけにして、GUI 側が一定間隔でまとめて取り出す。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from typing import Any

# ログ描画の間隔。README!C61 の知見どおり、映像(33ms)と文字情報は周期を
# 分ける。16ms だとキューが空でも毎秒60回 after が回り、映像描画と競合する。
FLUSH_INTERVAL_MS = 200
FLUSH_MAX_LINES = 512  # 1回の描画で取り出す上限（周期を伸ばした分増やす）
MAX_LINES = 5000  # ログ欄に残す行数。Text は行数に比例して重くなる
QUEUE_MAX = 20000  # 未描画の行を溜める上限。超えた分は古い方から捨てる


class DropOldestQueue(queue.Queue):
    """満杯なら古い方を捨てて入れ替えるキュー。

    無制限キューだと、print を大量に出すコマンドで GUI の取り出し速度
    （FLUSH_MAX_LINES / FLUSH_INTERVAL_MS）を超えた分が際限なく溜まり、
    メモリを食ったままコマンド停止後も延々と流れ続ける。
    新しい行のほうが知りたい情報なので、捨てるのは古い方にする。
    捨てた件数は数えておき、描画時に「... N行省略 ...」として1行で出す。
    """

    def __init__(self, maxsize: int = QUEUE_MAX) -> None:
        super().__init__(maxsize=maxsize)
        self._dropped = 0
        self._drop_lock = threading.Lock()

    def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
        """満杯なら最古の1件を捨ててから入れる。書き手は待たせない。"""
        while True:
            try:
                # put_nowait / get_nowait は内部で self.put / self.get を
                # 呼ぶ実装のため、put を上書きした本クラスでは無限再帰に
                # なる。基底の put / get を block=False で直に呼ぶ。
                super().put(item, block=False)
                return
            except queue.Full:
                try:
                    super().get(block=False)
                except queue.Empty:
                    continue
                with self._drop_lock:
                    self._dropped += 1

    def take_dropped(self) -> int:
        """前回の呼び出し以降に捨てた件数を返して 0 に戻す。"""
        with self._drop_lock:
            dropped, self._dropped = self._dropped, 0
        return dropped


# print() の内容を受け渡すキュー。
text_queue: queue.Queue = DropOldestQueue()

# 入力ログ専用のキュー。print と混ぜないことで、コマンドの出力が
# 入力ログに押し流されるのを防ぐ。上限も別に持たせ、入力ログが
# 溢れてもコマンドの出力は失われないようにする。
input_log_queue: queue.Queue = DropOldestQueue()

# 副ログ（ログタブの下側）用のキュー。print と混ぜないことで、
# 「残しておきたい結果」が通常の進捗表示に押し流されないようにする。
sub_log_queue: queue.Queue = DropOldestQueue()


class QueueStdoutRedirector:
    """print() をキューに積むだけの標準出力リダイレクタ.

    ウィジェットへの書き込みは GUI スレッド側がまとめて行う。
    write のたびに描画すると print が多いコマンドで極端に重くなる。
    """

    def __init__(self, text_widget: Any = None) -> None:
        # text_widget は使わないが、旧コードとの互換のため引数だけ残す
        self.text_widget = text_widget
        self.buffer: queue.Queue = text_queue

    def write(self, string: str) -> None:
        self.buffer.put(string)

    def flush(self) -> None:
        pass


def emitInputLog(text: str) -> None:
    """入力ログ1行を専用キューへ積む。

    呼び出し元はコマンドのスレッドやシリアル送信の経路なので、ここで
    ウィジェットを触ってはいけない（tkinter はスレッドセーフでない）。
    キューへ入れるだけにして、描画は GUI スレッドが行う。
    """
    input_log_queue.put(text + "\n")


def trim(area: tk.Text) -> None:
    """ログ欄の行数に上限を設ける。

    tk.Text は行数に比例して重くなる（README!C63）。上限が無いと
    長時間実行した終盤ほど insert のたびの再描画が遅くなる。
    超えた分は先頭から捨てる。state は呼び出し側で normal にしてある。
    """
    # "行.桁" 形式。末尾に空行が付くので実行数は -1
    total = int(area.index("end-1c").split(".")[0])
    if total > MAX_LINES:
        area.delete("1.0", f"end-{MAX_LINES}l")


def flushQueue(q: queue.Queue, area: tk.Text, autoscroll: bool = True) -> None:
    """キューの内容を1つの Text へまとめて書き出す。

    update_idletasks() は呼ばない。次の after で待ちに入れば tkinter
    が自然に描くので不要で、映像描画の after と重なると描画が二重に
    走る。see("end") もスクロール計算が乗るため、末尾を見ているときだけ
    呼ぶ（過去ログを遡っている最中に勝手に飛ばされるのも防げる）。
    """
    lines = []
    while len(lines) < FLUSH_MAX_LINES:
        try:
            lines.append(q.get_nowait())
        except queue.Empty:
            break

    dropped = 0
    if isinstance(q, DropOldestQueue):
        dropped = q.take_dropped()

    if not lines and not dropped:
        return

    # 末尾を見ているか、書き換える前に判定する
    at_bottom = area.yview()[1] >= 0.999

    area.configure(state="normal")
    if dropped:
        area.insert("end", f"... {dropped} 行省略 ...\n")
    if lines:
        area.insert("end", "".join(lines))
    trim(area)
    if at_bottom and autoscroll:
        area.see("end")
    area.configure(state="disabled")


def clearAreas(areas: list) -> None:
    """渡された Text の中身を消す。

    「ログ」タブは上下2枚あるので両方を消す。片方だけ残ると、
    どちらを消したのか分からず紛らわしい。
    """
    for target in areas:
        target.configure(state="normal")
        target.delete("1.0", "end")
        target.configure(state="disabled")
