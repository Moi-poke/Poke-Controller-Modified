"""CommandPalette.py - Ctrl+K で開くコマンド検索パレット.

Window.py から呼ばれる小窓。打ちながら絞り込み、Enter で実行する。
マウスで Combobox を開いて目で探す操作を、キーボードだけで済ませる。

既存の仕組みをそのまま使うのが設計の要点。候補の並べ替えには
CommandStats の使用履歴を、表示名には CommandTags のタグを使う。
ここで独自の検索規則や表示規則を作ると、一覧側と食い違ったときに
「同じ名前で探しているのに結果が違う」という分かりにくい形になる。

実行そのものはこのモジュールでは行わない。選ばれた名前を呼び出し元
へ返すだけにして、コマンドの生成・開始は Window 側の既存経路（選択を
合わせてから startPlay）に任せる。停止や一時停止の状態管理まで持つと
二重管理になり、どちらが正か決められなくなる。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk
from collections.abc import Callable
from typing import Any

import CommandStats

# 一度に見せる候補の数。多すぎると目で追えず、少なすぎると探し直しになる。
MAX_ROWS = 12


def rankNames(
    names: list[str],
    keyword: str,
    stats: dict[str, dict],
    limit: int = MAX_ROWS,
) -> list[str]:
    """打った文字で絞り、使う可能性の高い順に並べて返す。

    並びは「前方一致 → 部分一致」を第一の基準にする。打ち始めの文字と
    頭が揃うものを先に出さないと、目当てが下の方に埋もれる。
    同じ一致の仕方どうしでは、最近使った順・実行回数の多い順にする。
    どれも同じなら名前順で、並びが毎回変わらないようにする。
    """
    word = keyword.strip().lower()
    recent = CommandStats.recentNames(stats)

    scored = []
    for name in names:
        low = name.lower()
        if word and word not in low:
            continue
        # 0 = 前方一致 / 1 = 部分一致（未入力なら差を付けない）
        head = 0 if (word and low.startswith(word)) else 1
        # 最近使ったものほど小さい値。履歴に無ければ最後尾へ回す
        order = recent.index(name) if name in recent else len(recent) + 1
        used = CommandStats.usedCount(stats, name)
        scored.append((head, order, -used, name))

    scored.sort()
    return [name for _h, _o, _u, name in scored[:limit]]


class CommandPalette:
    """コマンドを検索して選ぶ小窓。

    二重に開かないよう、呼び出し元が参照を1つ持つ前提で作る。閉じたら
    on_close で知らせ、呼び出し元の参照を落としてもらう。
    """

    def __init__(
        self,
        master: tk.Misc,
        names: list[str],
        labels: dict[str, str],
        stats: dict[str, dict],
        on_choose: Callable[[str], None],
        on_close: Callable[[], None],
    ) -> None:
        """names は素のコマンド名、labels は素の名前→表示名の対応。

        表示は labels を使うが、選ばれたときに返すのは素の名前にする。
        表示名にはタグや使用履歴が付いていて実行のたびに変わるため、
        鍵として使うと同じコマンドを指せなくなる。
        """
        self._names = names
        self._labels = labels
        self._stats = stats
        self._on_choose = on_choose
        self._on_close = on_close
        self._shown: list[str] = []

        self.win = tk.Toplevel(master)
        self.win.title("コマンドを検索")
        self.win.transient(master)
        self.win.resizable(True, False)

        frame = ttk.Frame(self.win, padding=8)
        frame.pack(fill="both", expand=True)

        self.entry_var = tk.StringVar()
        self.entry = ttk.Entry(frame, textvariable=self.entry_var, width=52)
        self.entry.pack(fill="x")
        self.entry.focus_set()

        # 候補は Listbox にする。Combobox と違い、開く操作なしに一覧が
        # 見えたまま打ち続けられる。exportselection=0 は、他のウィジェットを
        # 触ったときに選択が消えるのを防ぐため。
        self.listbox = tk.Listbox(
            frame, height=MAX_ROWS, activestyle="none", exportselection=0
        )
        self.listbox.pack(fill="both", expand=True, pady=(6, 0))

        self.status = ttk.Label(frame, text="")
        self.status.pack(anchor="w", pady=(4, 0))

        self.entry_var.trace_add("write", lambda *_a: self.refresh())
        # 打ちながら上下で選べるようにする。入力欄から手を離さずに済む。
        self.entry.bind("<Down>", self._moveDown)
        self.entry.bind("<Up>", self._moveUp)
        self.entry.bind("<Return>", self._choose)
        self.entry.bind("<KP_Enter>", self._choose)
        self.entry.bind("<Escape>", lambda _e: self.close())
        self.listbox.bind("<Double-Button-1>", self._choose)
        self.listbox.bind("<Return>", self._choose)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self.refresh()

    def refresh(self) -> None:
        """入力に合わせて候補を作り直す。"""
        word = self.entry_var.get()
        self._shown = rankNames(self._names, word, self._stats)

        self.listbox.delete(0, "end")
        for name in self._shown:
            self.listbox.insert("end", self._labels.get(name, name))
        if self._shown:
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(0)
            self.listbox.activate(0)

        # 0件のとき何も出ないと「固まった」ように見える。件数を必ず出す。
        total = len(self._names)
        if not self._shown:
            self.status.config(text=f"該当なし（全 {total} 件）")
        else:
            self.status.config(
                text=f"{len(self._shown)} / {total} 件  Enter で実行 / Esc で閉じる"
            )

    def _selectedIndex(self) -> int:
        """選択中の位置。何も選ばれていなければ -1。"""
        picked = self.listbox.curselection()
        return int(picked[0]) if picked else -1

    def _move(self, step: int) -> None:
        """候補の選択を上下に動かす。端では止める（回り込ませない）。"""
        if not self._shown:
            return
        pos = self._selectedIndex()
        pos = 0 if pos < 0 else min(max(pos + step, 0), len(self._shown) - 1)
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(pos)
        self.listbox.activate(pos)
        self.listbox.see(pos)

    def _moveDown(self, *_event: Any) -> str:
        self._move(1)
        return "break"  # 入力欄のカーソル移動へ伝えない

    def _moveUp(self, *_event: Any) -> str:
        self._move(-1)
        return "break"

    def _choose(self, *_event: Any) -> str:
        """選択中のコマンドを呼び出し元へ渡して閉じる。"""
        pos = self._selectedIndex()
        if pos < 0 or pos >= len(self._shown):
            return "break"
        name = self._shown[pos]
        self.close()
        self._on_choose(name)
        return "break"

    def close(self) -> None:
        """小窓を閉じ、呼び出し元へ知らせる（参照を落としてもらう）。"""
        self._on_close()
        self.win.destroy()

    def lift(self) -> None:
        """既に開いているときに前へ出す。"""
        self.win.lift()
        self.win.focus_force()
        self.entry.focus_set()
