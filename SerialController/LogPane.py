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
import time
import tkinter as tk
from typing import Any

# ログ描画の間隔。README!C61 の知見どおり、映像(33ms)と文字情報は周期を
# 分ける。16ms だとキューが空でも毎秒60回 after が回り、映像描画と競合する。
FLUSH_INTERVAL_MS = 200
FLUSH_MAX_LINES = 512  # 1回の描画で取り出す上限（周期を伸ばした分増やす）
MAX_LINES = 5000  # ログ欄に残す行数。Text は行数に比例して重くなる
QUEUE_MAX = 20000  # 未描画の行を溜める上限。超えた分は古い方から捨てる

#: 部分行の救済条件。print(end="") の進捗表示など、改行を出さずに溜め
#: 続ける書き手がある。flush() まで画面に出ないと進んでいるか分からない。
#: 改行が無いまま次のどちらかを超えたら1putで救済放出する。
#: ・量: PARTIAL_FLUSH_CHARS 文字（巨大な1行を溜め込まない）
#: ・古さ: PARTIAL_FLUSH_AGE_S 秒（少しずつの trickle を置き去りにしない。
#:   判定は次の write 時に行うため、書き手が止まった分は flush() が出す）
#: 通常の行（改行あり）は従来どおり1行1putで、合流の性能は変わらない。
PARTIAL_FLUSH_CHARS = 4096
PARTIAL_FLUSH_AGE_S = 1.0


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


class ResultForwardQueue(DropOldestQueue):
    """print2 の受け口。中身は持たず、水準「結果」の行として text_queue へ回す。

    結果は上の欄（時系列）と下の欄（残す一覧）の両方に出す。キューを
    別に持つと、取り出しの周期（200ms）の中で print との前後が崩れ、
    下の欄から上の欄へ飛んだ先が実際の時点とずれる。print と同じ
    キューへ同じ順で入れれば、前後は書いた順のまま保たれる。
    行き先は put の時点の text_queue（テストで差し替えても追従する）。
    """

    def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
        if isinstance(item, str):
            from core.log_model import entries_for

            for entry in entries_for(item, "result", "all"):
                text_queue.put(entry)
        else:
            text_queue.put(item)


# 副ログ（print2）の受け口。PythonCommandBase._subLogQueue が参照する名前。
sub_log_queue: queue.Queue = ResultForwardQueue()


def queues_idle_hint() -> bool:
    """全キュー空の目安。空ならTrue（Tcl往復を省ける）。

    qsizeは目安であり、競合で0でも行が残る場合は次周期（200ms）で拾う
    だけで欠落はしない（疑わしければFalse＝drainする）。捨て件数も安い
    錠確認だけ行い、あればFalseにする（省略行を出す必要があるため）。
    Tclは触らない。
    """
    try:
        if text_queue.qsize() != 0:
            return False
        if sub_log_queue.qsize() != 0:
            return False
        if input_log_queue.qsize() != 0:
            return False
    except Exception:
        return False
    for q in (text_queue, sub_log_queue, input_log_queue):
        if isinstance(q, DropOldestQueue):
            try:
                with q._drop_lock:
                    if q._dropped != 0:
                        return False
            except Exception:
                return False
    return True


class StampedLine(str):
    """書かれた時刻を持つ文字列。比較や連結は普通の str と同じ。

    ログ欄の時刻は「画面に出た時刻」ではなく「書かれた時刻」にしたい。
    描画は 200ms ごとにまとめて行うので、取り出した時点で時刻を付けると
    最大 200ms ずれ、print2 や logger（書いた時点で時刻を持つ）と前後が
    食い違って見える。str の派生にしておけば、キューの中身を文字列として
    扱う既存の受け手はそのまま動く。
    """

    ts: float

    def __new__(cls, text: str, ts: float) -> StampedLine:
        obj = super().__new__(cls, text)
        obj.ts = ts
        return obj


class QueueStdoutRedirector:
    """print() をキューに積むだけの標準出力リダイレクタ.

    ウィジェットへの書き込みは GUI スレッド側がまとめて行う。
    write のたびに描画すると print が多いコマンドで極端に重くなる。
    さらに write ごとの put（キューの mutex 往復）を減らすため、
    改行まで溜めて1行1putにまとめる。部分行は flush() で出す。
    ただし改行なしで量・古さの cap を超えた部分行は、次の write 時に
    救済放出する（PARTIAL_FLUSH_CHARS / PARTIAL_FLUSH_AGE_S）。
    """

    def __init__(self, text_widget: Any = None) -> None:
        # text_widget は使わないが、旧コードとの互換のため引数だけ残す
        self.text_widget = text_widget
        self.buffer: queue.Queue = text_queue
        # 断片化した書き込みの溜め。別スレッドから来るため錠で守る。
        self._lock = threading.Lock()
        self._partial: str = ""
        # 最初に溜めた時刻（monotonic）。部分行の古さの起点。
        # 空に戻したら None に戻す。
        self._partial_since: float | None = None

    def write(self, string: str) -> None:
        if not string:
            return
        # 改行で区切り、そろった行だけ出す。put は錠の外で行う。
        lines: list[str] = []
        with self._lock:
            if not self._partial:
                self._partial_since = time.monotonic()
            self._partial += string
            while True:
                idx = self._partial.find("\n")
                if idx < 0:
                    break
                lines.append(self._partial[: idx + 1])
                self._partial = self._partial[idx + 1 :]
            if self._partial:
                # 改行なしで cap を超えた部分行は救済放出する。
                # flush() まで待つと巨大な進捗行が画面に出ない。
                since = self._partial_since
                aged = (
                    since is not None
                    and (time.monotonic() - since) >= PARTIAL_FLUSH_AGE_S
                )
                if len(self._partial) >= PARTIAL_FLUSH_CHARS or aged:
                    lines.append(self._partial)
                    self._partial = ""
            if not self._partial:
                self._partial_since = None
        now = time.time()
        for line in lines:
            self.buffer.put(StampedLine(line, now))

    def flush(self) -> None:
        # 改行なしで残った部分行を出す。無ければ何もしない。
        pending = ""
        with self._lock:
            pending, self._partial = self._partial, ""
            self._partial_since = None
        if pending:
            self.buffer.put(StampedLine(pending, time.time()))


def emitInputLog(text: str) -> None:
    """入力ログ1行を専用キューへ積む。

    呼び出し元はコマンドのスレッドやシリアル送信の経路なので、ここで
    ウィジェットを触ってはいけない（tkinter はスレッドセーフでない）。
    キューへ入れるだけにして、描画は GUI スレッドが行う。
    """
    input_log_queue.put(StampedLine(text + "\n", time.time()))


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
    走る。自動スクロールフラグ（autoscroll）が有効なら、書き込み後に
    see("end") で末尾へ追従する。無効時は現在の表示位置を保つ。
    """
    lines: list[str] = []
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

    area.configure(state="normal")
    if dropped:
        area.insert("end", f"... {dropped} 行省略 ...\n")
    if lines:
        area.insert("end", "".join(lines))
    trim(area)
    if autoscroll:
        area.see("end")
    area.configure(state="disabled")


def drain(q: queue.Queue, limit: int = FLUSH_MAX_LINES) -> tuple[list[Any], int]:
    """キューから最大 limit 件を取り出す。(取り出した物, 捨てた件数) を返す。

    中身は文字列（print・print2・入力ログ）か LogEntry（logger など、
    水準が決まっている物）。どちらで来ても描画側（ui/log_panel.py）が
    LogEntry へそろえる。
    """
    items: list[Any] = []
    while len(items) < limit:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            break
    dropped = q.take_dropped() if isinstance(q, DropOldestQueue) else 0
    return items, dropped


def logger_sink(message: Any) -> None:
    """loguru の出力先。ERROR 以上をログ欄（上の欄）へ流す。

    従来、コマンドの実行基盤（services/command_runner.py 等）で起きた
    失敗は logger.error でファイルにしか出ず、画面では「止まった」こと
    しか分からなかった。水準つきで流し、下の欄へも集める。
    どのスレッドから呼ばれてもよい（キューへ入れるだけ）。例外は出さない。
    """
    try:
        from core.log_model import entries_for

        record = message.record
        level = "error" if record["level"].no >= 40 else "warning"
        text = str(record["message"])
        exc = record["exception"]
        if exc is not None and exc.type is not None:
            text += f"\n{exc.type.__name__}: {exc.value}"
        for entry in entries_for(text, level, "first", record["time"].timestamp()):
            text_queue.put(entry)
    except Exception:
        pass


def clearAreas(areas: list) -> None:
    """渡された Text の中身を消す。

    「ログ」タブは上下2枚あるので両方を消す。片方だけ残ると、
    どちらを消したのか分からず紛らわしい。
    """
    for target in areas:
        target.configure(state="normal")
        target.delete("1.0", "end")
        target.configure(state="disabled")
