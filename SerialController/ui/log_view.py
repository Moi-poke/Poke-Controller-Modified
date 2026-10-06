"""log_view.py - ログを1欄ぶん表示する部品（Text ＋ スクロール ＋ 新着ボタン）.

ログ欄の上（全出力）・下（結果とエラー）・入力タブで同じ部品を使う。
欄どうしの連携（下の欄から上の欄へ飛ぶ、検索の対象を決める等）は
ui/log_panel.py が受け持ち、ここは1欄の中で完結することだけを持つ。

参考にした既存アプリの作法:
- Prism Launcher（Minecraft）のログ画面: 水準ごとの色分け、
  「Bottom」で末尾へ戻る、検索して次へ。
- ブラウザ開発ツールのコンソール: 同じ行を「×N」で1行にまとめる、
  水準での絞り込み、時刻の表示切替。
- ターミナル一般: 末尾にいる間だけ追従し、上へ読みに行ったら止まる
  （追従のチェックボックスを人に操作させない）。

性能の前提（LogPane.py の冒頭も参照）:
- 1回の取り出しで最大 FLUSH_MAX_LINES 行を、insert 1回でまとめて入れる。
- 絞り込みと時刻の表示切替は tag の elide で行う。中身を作り直さないので
  何千行あっても切替は一瞬で終わる。
- 行の上限（MAX_LINES）を超えたら先頭から捨てる。捨てた行数は _base に
  積み、外から渡された「通し行番号」を今の行番号へ変換できるようにする。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
import tkinter.ttk as ttk
from collections.abc import Callable, Sequence
from typing import Any

from core.log_model import LEVELS, LEVEL_MARKS, LogEntry, format_time

#: 欄に残す行数の既定。Text は行数に比例して重くなる（README!C63）。
DEFAULT_MAX_LINES = 5000

#: 末尾にいるとみなす yview の下端。端数の誤差を吸収する。
_BOTTOM = 0.999

#: 検索で色を付ける件数の上限。巨大な一致で GUI を止めない。
_MAX_HITS = 5000

#: 水準ごとの色。前景/背景の組は白地でコントラスト比 4.5:1 以上を確認済み。
#: 色だけに頼らないよう、重要な水準には行頭の記号（LEVEL_MARKS）も付く。
_LEVEL_STYLE: dict[str, dict[str, Any]] = {
    "debug": {"foreground": "#6B6B6B"},
    "system": {"foreground": "#4F4F9A"},
    "info": {},
    "success": {"foreground": "#1B7F3B"},
    "result": {"foreground": "#0B4F9C"},
    "warning": {"foreground": "#7A4A00", "background": "#FFF4E0"},
    "error": {"foreground": "#A0001C", "background": "#FDECEA"},
    "input": {},
}


def level_tag(level: str) -> str:
    return f"lv_{level}"


class LogView(ttk.Frame):
    """ログ1欄。append で行を足し、通し行番号を返す。

    通し行番号（abs）は「この欄に入った何行目か」で、先頭を捨てても
    変わらない。下の欄から上の欄の行を指すときの番地に使う。
    """

    def __init__(
        self,
        master: Any,
        *,
        empty_hint: str = "",
        show_time: bool = True,
        wrap: bool = True,
        group_similar: bool = True,
        max_lines: int = DEFAULT_MAX_LINES,
        on_activate: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.max_lines = max_lines
        self.group_similar = group_similar
        self.show_time = show_time
        #: 行をクリックしたときに呼ぶ（引数は通し行番号）。下の欄で使う。
        self.on_activate = on_activate
        #: Text の1行目の通し行番号。先頭を捨てるたびに増える。
        self._base = 1
        #: 直前の行（まとめる判定用）と、その繰り返し回数。
        self._last_key: tuple[str, str] | None = None
        self._last_count = 0
        #: 上へ読みに行っている間に来た行数（新着ボタンに出す）。
        self.new_count = 0
        #: 末尾を見ているか。yview() をその場で測ると、再描画前は古い値が
        #: 返り「末尾にいるのに追従しない」ことがある。Tk が描画後に呼ぶ
        #: スクロール通知（_on_yscroll）で覚えておく。
        self._following = True
        #: 下の欄の各行が指す、上の欄の通し行番号。
        self.anchors: dict[int, int] = {}
        self._hits: list[str] = []
        self._hit_index = -1
        self._jump_after: str | None = None

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        # 既定のフォント（TkFixedFont）のまま。等幅でないと時刻や
        # 入力ログの桁が揃わない。
        self.text = tk.Text(
            self,
            wrap="word",
            height=6,
            width=40,
            relief="flat",
            borderwidth=0,
            padx=4,
            pady=2,
            undo=False,
            maxundo=0,
            insertwidth=0,
            insertunfocussed="none",
            state="disabled",
            exportselection=True,
        )
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(
            yscrollcommand=self._on_yscroll, xscrollcommand=self.hbar.set
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")

        for level in LEVELS:
            self.text.tag_configure(level_tag(level), **_LEVEL_STYLE.get(level, {}))
        self.text.tag_configure("ts", foreground="#6B6B6B")
        self.text.tag_configure("mark", font="TkFixedFont")
        self.text.tag_configure("badge", foreground="#FFFFFF", background="#6B6B6B")
        # 後から作った tag ほど優先される。強調は水準の色に勝たせる。
        self.text.tag_configure("hit", background="#FFE58A")
        self.text.tag_configure("hit_cur", background="#FF9632", foreground="#000000")
        self.text.tag_configure("jump", background="#CDE8FF")
        self.text.tag_raise("sel")

        # 上へ読みに行っている間に新しい行が来たら出す。押すと末尾へ戻る。
        self.new_button = ttk.Button(self, command=self.scroll_to_end)
        self.empty_label = ttk.Label(
            self.text,
            text=empty_hint,
            foreground="#6B6B6B",
            background="#FFFFFF",
            justify="center",
            wraplength=320,
        )
        self.set_wrap(wrap)
        self.set_show_time(show_time)
        self.text.bind("<ButtonRelease-1>", self._on_click, add="+")
        # タブを開いた・欄の高さが変わった直後も、追従中なら末尾を見せる。
        self.text.bind("<Configure>", self._on_resize, add="+")
        self._update_empty()

    # -- 状態 ----------------------------------------------------------------

    def at_bottom(self) -> bool:
        """末尾を見ているか（＝新しい行に追従するか）。"""
        return self._following

    def line_count(self) -> int:
        """中身の行数（末尾の空行は数えない）。"""
        return int(self.text.index("end-1c").split(".")[0]) - 1

    def abs_of(self, line: int) -> int:
        return self._base + line - 1

    def line_of(self, abs_line: int) -> int | None:
        """通し行番号を今の行番号へ。捨てた・消した行なら None。"""
        line = abs_line - self._base + 1
        if line < 1 or line > self.line_count():
            return None
        return line

    def line_at(self, x: int, y: int) -> int:
        return int(self.text.index(f"@{x},{y}").split(".")[0])

    def line_text(self, line: int) -> str:
        """1行の本文（時刻・記号・回数を除いた、写して使う形）。"""
        start, end = f"{line}.0", f"{line}.0 lineend"
        parts: list[str] = []
        # 時刻・記号・回数の tag が付いた部分を飛ばす。dump は tag の
        # 開始/終了と文字列を順に返すので、入れ子の深さを数えて読む。
        skip = 0
        for key, value, _index in self.text.dump(start, end, text=True, tag=True):
            if key == "tagon" and value in ("ts", "mark", "badge"):
                skip += 1
            elif key == "tagoff" and value in ("ts", "mark", "badge"):
                skip = max(0, skip - 1)
            elif key == "text" and skip == 0:
                parts.append(value)
        return "".join(parts).rstrip("\n")

    def all_text(self) -> str:
        return self.text.get("1.0", "end-1c")

    def is_empty(self) -> bool:
        return self.line_count() == 0

    # -- 表示の切替 ----------------------------------------------------------

    def set_wrap(self, wrap: bool) -> None:
        """折り返す/折り返さない。折り返さないときだけ横バーを出す。"""
        self.text.configure(wrap="word" if wrap else "none")
        if wrap:
            self.hbar.grid_remove()
        else:
            self.hbar.grid(row=1, column=0, sticky="ew")

    def set_show_time(self, show: bool) -> None:
        self.show_time = show
        self._update_hanging_indent()
        # 表示するときは elide を「未指定」に戻す。False を明示すると、
        # 優先度の高い ts が水準 tag の elide=True に勝ち、絞り込みで
        # 隠した行の時刻だけが残ってしまう。
        # 型スタブは bool しか受けないが、Tk は "" を「未指定」として扱う。
        self.text.tag_configure("ts", elide="" if show else True)  # type: ignore[arg-type]

    def _update_hanging_indent(self) -> None:
        """折り返した行の続きを、時刻と記号の幅だけ下げる。

        下げないと続きが左端（時刻の列）から始まり、時刻の縦の並びが
        崩れて、どこからが次の行か読み取りにくくなる。
        """
        try:
            font = tkfont.nametofont(str(self.text.cget("font")))
        except tk.TclError:
            font = tkfont.Font(font=self.text.cget("font"))
        ts_px = font.measure(format_time(0.0) + " ") if self.show_time else 0
        for level in LEVELS:
            mark = LEVEL_MARKS.get(level, "")
            indent = ts_px + (font.measure(mark) if mark else 0)
            self.text.tag_configure(level_tag(level), lmargin2=indent)

    def set_hidden_levels(self, hidden: set[str]) -> None:
        """指定の水準の行を隠す。中身は残すので戻せば元どおり。"""
        for level in LEVELS:
            # 型スタブは bool しか受けないが、Tk は "" を「未指定」として扱う。
            self.text.tag_configure(
                level_tag(level),
                elide=True if level in hidden else "",  # type: ignore[arg-type]
            )

    def is_line_hidden(self, line: int) -> bool:
        for tag in self.text.tag_names(f"{line}.0 lineend"):
            if tag.startswith("lv_") and self.text.tag_cget(tag, "elide") in (
                "1",
                1,
                True,
            ):
                return True
        return False

    # -- 追記 ----------------------------------------------------------------

    def append(
        self, entries: Sequence[LogEntry], anchors: Sequence[int] | None = None
    ) -> list[int]:
        """行を足す。各行の通し行番号を返す（まとめた行は既存の行の番号）。

        anchors を渡すと、各行が指す先（上の欄の通し行番号）を覚える。
        """
        if not entries:
            return []
        text = self.text
        follow = self.at_bottom()
        next_line = self.line_count() + 1
        # 行ごとに [entry, 回数, 通し番号, 指す先]。まとめはここで済ませる。
        rows: list[list[Any]] = []
        # 既存の最終行へまとめた回数。前回の取り出しから続く繰り返し。
        existing_key = self._last_key
        existing_count = self._last_count
        bump_existing = 0
        abs_lines: list[int] = []
        for i, entry in enumerate(entries):
            anchor = anchors[i] if anchors is not None else None
            key = (entry.level, entry.text)
            if self.group_similar and key == self._last_key:
                if rows:
                    rows[-1][1] += 1
                    abs_lines.append(rows[-1][2])
                else:
                    bump_existing += 1
                    abs_lines.append(self.abs_of(next_line - 1))
                self._last_count += 1
                continue
            abs_line = self.abs_of(next_line + len(rows))
            rows.append([entry, 1, abs_line, anchor])
            abs_lines.append(abs_line)
            self._last_key = key
            self._last_count = 1

        text.configure(state="normal")
        try:
            if bump_existing and existing_key is not None:
                # 既存の最終行の回数を先に書き換える。この後ろに新しい行が
                # 続くと、印（badge_at）が指す行が最終行でなくなるため。
                self._write_badge(existing_count + bump_existing, existing_key[0])
            if rows:
                args: list[Any] = []
                for entry, count, _abs, _anchor in rows[:-1]:
                    args.extend(self._segments(entry, count))
                last_entry, last_count, _abs, _anchor = rows[-1]
                args.extend(self._segments(last_entry, 1, newline=False))
                text.insert("end-1c", *args)
                # 最終行の回数は印を置いてから入れる（後で書き換えるため）。
                text.mark_set("badge_at", "end-1c")
                text.mark_gravity("badge_at", "left")
                text.insert("end-1c", "\n", (level_tag(last_entry.level),))
                if last_count > 1:
                    self._write_badge(last_count, last_entry.level)
                for _entry, _count, abs_line, anchor in rows:
                    if anchor is not None:
                        self.anchors[abs_line] = anchor
            self._trim()
        finally:
            text.configure(state="disabled")

        added = len(rows)
        if follow:
            text.see("end")
        elif added or bump_existing:
            self.new_count += added
            self._show_new_button()
        self._update_empty()
        return abs_lines

    def _segments(self, entry: LogEntry, count: int, newline: bool = True) -> list[Any]:
        lv = level_tag(entry.level)
        segs: list[Any] = [format_time(entry.ts) + " ", ("ts", lv)]
        mark = LEVEL_MARKS.get(entry.level, "")
        if mark:
            segs += [mark, ("mark", lv)]
        segs += [entry.text or " ", (lv,)]
        if count > 1:
            segs += [f" ×{count}", ("badge", lv)]
        if newline:
            segs += ["\n", (lv,)]
        return segs

    def _write_badge(self, count: int, level: str) -> None:
        """最終行の「×N」を書き直す。badge_at から改行の手前までが回数の表示。"""
        text = self.text
        if "badge_at" not in text.mark_names():
            return
        text.delete("badge_at", "end-2c")
        text.insert("badge_at", f" ×{count}", ("badge", level_tag(level)))

    def _trim(self) -> None:
        excess = self.line_count() - self.max_lines
        if excess <= 0:
            return
        self.text.delete("1.0", f"{excess + 1}.0")
        self._base += excess
        for abs_line in [a for a in self.anchors if a < self._base]:
            del self.anchors[abs_line]
        if self.line_count() == 0:
            self._last_key = None

    def clear(self) -> None:
        """中身を消す。通し行番号は続きから振る（古い番地が別の行を指さない）。"""
        self._base += self.line_count()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._last_key = None
        self._last_count = 0
        self.anchors.clear()
        self._hits = []
        self._hit_index = -1
        self.new_count = 0
        self._hide_new_button()
        self._update_empty()

    # -- 追従と新着ボタン ----------------------------------------------------

    def _on_yscroll(self, first: float | str, last: float | str) -> None:
        self.vbar.set(first, last)
        # 見えていないタブの Text は高さ 1 で、末尾にいても last が 1 に
        # 届かない。そこで追従を下ろすと、タブを開いたとき古い位置が出る。
        if self.text.winfo_ismapped() and self.text.winfo_height() > 1:
            self._following = float(last) >= _BOTTOM
        if self._following and self.new_count:
            self.new_count = 0
            self._hide_new_button()

    def _on_resize(self, _event: Any = None) -> None:
        if self._following:
            self.text.see("end")

    def scroll_to_end(self) -> None:
        self.text.see("end")
        self._following = True
        self.new_count = 0
        self._hide_new_button()

    def _show_new_button(self) -> None:
        if self.new_count <= 0:
            return
        self.new_button.configure(text=f"↓ 新着 {self.new_count} 行")
        self.new_button.place(relx=1.0, rely=1.0, x=-22, y=-8, anchor="se")
        self.new_button.lift()

    def _hide_new_button(self) -> None:
        self.new_button.place_forget()

    def _update_empty(self) -> None:
        if self.is_empty() and str(self.empty_label.cget("text")):
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.empty_label.place_forget()

    def set_empty_hint(self, hint: str) -> None:
        self.empty_label.configure(text=hint)
        self._update_empty()

    # -- 移動と強調 ----------------------------------------------------------

    def jump_to_line(self, line: int) -> None:
        """その行を見える位置へ出し、しばらく目印を付ける。"""
        text = self.text
        text.tag_remove("jump", "1.0", "end")
        text.tag_add("jump", f"{line}.0", f"{line}.0 lineend +1c")
        # 前後の文脈も見えるよう、少し上から見せる。
        text.yview(f"{max(1, line - 3)}.0")
        text.see(f"{line}.0")
        if self._jump_after is not None:
            try:
                self.after_cancel(self._jump_after)
            except (tk.TclError, ValueError):
                pass
        self._jump_after = self.after(2500, self._clear_jump)

    def _clear_jump(self) -> None:
        self._jump_after = None
        try:
            self.text.tag_remove("jump", "1.0", "end")
        except tk.TclError:
            pass

    def next_tagged(self, tag: str) -> int | None:
        """次（末尾まで行ったら先頭から）の tag 付きの行へ飛ぶ。行番号を返す。"""
        text = self.text
        start = text.index("jump.last") if text.tag_ranges("jump") else None
        if start is None:
            start = text.index("@0,0")
        found = text.tag_nextrange(tag, start)
        if not found:
            found = text.tag_nextrange(tag, "1.0")
        if not found:
            return None
        line = int(str(found[0]).split(".")[0])
        self.jump_to_line(line)
        return line

    def _on_click(self, event: Any) -> None:
        if self.on_activate is None:
            return
        # 範囲選択（コピーしたい）ときは飛ばない。
        if self.text.tag_ranges("sel"):
            return
        line = self.line_at(event.x, event.y)
        if line > self.line_count():
            return
        self.on_activate(self.abs_of(line))

    # -- 検索 ----------------------------------------------------------------

    def find(self, pattern: str, backwards: bool = False) -> tuple[int, int]:
        """一致を全部塗り、次（前）の一致へ移る。(何件目, 全件) を返す。

        隠れている行（絞り込み・時刻）は Text.search が既定で飛ばすので、
        数えるのは見えている一致だけになる。
        """
        text = self.text
        prev = (
            self._hits[self._hit_index]
            if 0 <= self._hit_index < len(self._hits)
            else None
        )
        text.tag_remove("hit", "1.0", "end")
        text.tag_remove("hit_cur", "1.0", "end")
        self._hits = []
        self._hit_index = -1
        if not pattern:
            return 0, 0
        count = tk.IntVar(master=text)
        idx = "1.0"
        while len(self._hits) < _MAX_HITS:
            idx = text.search(pattern, idx, "end", nocase=True, count=count)
            if not idx:
                break
            n = count.get() or 1
            text.tag_add("hit", idx, f"{idx}+{n}c")
            self._hits.append(idx)
            idx = f"{idx}+{n}c"
        if not self._hits:
            return 0, 0
        if prev is None:
            # 初回は見えている位置から下へ探す（読んでいる場所から飛ばない）。
            origin = text.index("@0,0")
            cand = [
                i for i, h in enumerate(self._hits) if text.compare(h, ">=", origin)
            ]
            pick = cand[0] if cand else 0
            if backwards:
                pick = (pick - 1) % len(self._hits)
        elif backwards:
            before = [i for i, h in enumerate(self._hits) if text.compare(h, "<", prev)]
            pick = before[-1] if before else len(self._hits) - 1
        else:
            after = [i for i, h in enumerate(self._hits) if text.compare(h, ">", prev)]
            pick = after[0] if after else 0
        self._hit_index = pick
        cur = self._hits[pick]
        n = len(text.get(cur, f"{cur}+{len(pattern)}c"))
        text.tag_add("hit_cur", cur, f"{cur}+{n}c")
        text.see(cur)
        return pick + 1, len(self._hits)

    def clear_find(self) -> None:
        self.text.tag_remove("hit", "1.0", "end")
        self.text.tag_remove("hit_cur", "1.0", "end")
        self._hits = []
        self._hit_index = -1
