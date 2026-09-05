"""TagEditor.py - コマンドのタグを編集する小窓.

Window.py から切り出した。切り出せたのは、155行のうち画面の状態（self.*）
を触るのが4箇所しかなかったため。親ウィンドウ・開いている小窓の記憶・
一覧の作り直し・選択中の名前の4つだけを外から受け取れば、残りは小窓の
中で完結する。

タグの出どころは3つ（コードの TAGS / 置き場所のフォルダ名 / tags.json）
あるが、この画面から変えられるのは tags.json だけ。保存するとそのコマンド
は以後 JSON が優先される。これを画面に明記しないと、コードを直したのに
反映されないという分かりにくい状態になる。

保存そのものは CommandTags へ委ねる。ここで json を直接書くと、読む側と
書く側で書式が食い違ったときに原因が追えなくなる。読み書きは1箇所に置く。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
from typing import Any, Callable

import CommandTags
from CommandTags import TAG_UNCLASSIFIED
from loguru import logger

# チェックボックスを横に並べる数。増やすと窓が横に伸びて画面からはみ出す。
COLUMNS = 4


def splitTags(text: str) -> list[str]:
    """入力欄の文字列をタグの一覧へ直す。

    全角の読点も区切りとして受ける。日本語入力のまま打つと「、」になる
    ことが多く、そのままでは1つの長いタグとして保存されてしまう。
    入力の揺れは、受け取る側で吸収する。
    """
    return [t.strip() for t in text.replace("、", ",").split(",") if t.strip()]


class TagEditor:
    """タグ編集の小窓。1つだけ開く前提で使う。"""

    def __init__(
        self,
        master: Any,
        name: str,
        table: dict[str, list[str]],
        onSaved: Callable[[], None],
        onClose: Callable[[], None],
    ) -> None:
        """name のタグを編集する窓を開く。

        table は「表示名 → タグ一覧」の対応表。既にあるタグの候補を
        ここから集めるので、Python 側と MCU 側で別の表が渡される。
        onSaved は保存後に一覧を作り直すためのもの。ここで直接
        setCommandItems を呼ばないのは、呼び出し元の都合をこの窓が
        知らずに済むようにするため。
        """
        self._name = name
        self._table = table
        self._onSaved = onSaved
        self._onClose = onClose

        self.win = tk.Toplevel(master)
        self.win.title(f"タグの編集 - {name}")
        self.win.transient(master)
        self.win.resizable(True, False)

        frame = ttk.Frame(self.win, padding=10)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=name).pack(anchor="w")
        ttk.Label(
            frame,
            text="タグをカンマ区切りで入力します（空にすると指定を取り消します）",
        ).pack(anchor="w", pady=(4, 0))

        current_tags = [t for t in table.get(name, []) if t != TAG_UNCLASSIFIED]
        self._var = tk.StringVar(value=", ".join(current_tags))
        self.entry = ttk.Entry(frame, textvariable=self._var, width=48)
        self.entry.pack(fill="x", pady=6)
        self.entry.focus_set()

        # いま何が効いているかを出す。JSON で上書きされているのか、
        # コードやフォルダ由来なのかが分からないと直す場所を誤る。
        if name in CommandTags.loadOverrides():
            source = "現在: Commands/tags.json の指定が効いています"
        else:
            source = "現在: コードの TAGS とフォルダ名から決まっています"
        ttk.Label(frame, text=source).pack(anchor="w")

        # 既存のタグはチェックで付け外しできるようにする。手で打ち直すと
        # 表記ゆれ（半角/全角・送り仮名）で別のタグが増えていくため。
        # 入力欄は残す。ここにしか無い新しいタグを足す口が要る。
        self._known = sorted(
            {t for tags in table.values() for t in tags if t != TAG_UNCLASSIFIED}
        )
        self._checked = {t: tk.BooleanVar(value=t in current_tags) for t in self._known}
        # 入力欄とチェックは互いを更新するので、再入を止める必要がある。
        self._syncing = False

        if self._known:
            ttk.Label(frame, text="既にあるタグ（クリックで付け外し）").pack(
                anchor="w", pady=(6, 0)
            )
            # 数が増えても縦に伸び続けないよう、折り返して並べる
            known_f = ttk.Frame(frame)
            known_f.pack(fill="x")
            for pos, tag in enumerate(self._known):
                ttk.Checkbutton(
                    known_f,
                    text=tag,
                    variable=self._checked[tag],
                    command=self._syncFromChecks,
                ).grid(row=pos // COLUMNS, column=pos % COLUMNS, sticky="w", padx=2)

        self._var.trace_add("write", lambda *_a: self._syncToChecks())

        button_f = ttk.Frame(frame)
        button_f.pack(fill="x", pady=(10, 0))
        ttk.Button(button_f, text="保存", command=self.save).pack(side="right", padx=2)
        ttk.Button(button_f, text="閉じる", command=self.close).pack(
            side="right", padx=2
        )

        self.win.bind("<Return>", lambda _e: self.save())
        self.win.bind("<Escape>", lambda _e: self.close())
        self.win.protocol("WM_DELETE_WINDOW", self.close)

    def _syncFromChecks(self) -> None:
        """チェックの状態を入力欄へ反映する。

        入力欄を正とし、チェックはその編集手段という位置づけにする。
        2つを別々に読むと、保存時にどちらが正か決められなくなる。
        チェックに無い自由入力のタグは、そのまま後ろへ残す。
        """
        if self._syncing:
            return
        self._syncing = True
        try:
            picked = [t for t in self._known if self._checked[t].get()]
            extra = [t for t in splitTags(self._var.get()) if t not in self._known]
            self._var.set(", ".join(picked + extra))
        finally:
            self._syncing = False

    def _syncToChecks(self) -> None:
        """入力欄を直接編集したとき、チェックの側を追従させる。

        片方だけ更新すると、見えている状態と保存される内容が食い違う。
        対になっているものは同時に更新する。
        """
        if self._syncing:
            return
        self._syncing = True
        try:
            now = set(splitTags(self._var.get()))
            for t in self._known:
                if self._checked[t].get() != (t in now):
                    self._checked[t].set(t in now)
        finally:
            self._syncing = False

    def save(self) -> None:
        """入力内容を tags.json へ書き、一覧を作り直す。"""
        tags = splitTags(self._var.get())
        current = CommandTags.loadOverrides()
        if tags:
            current[self._name] = tags
        else:
            # 空で保存＝JSON の指定を消す。コードとフォルダ由来へ戻す。
            current.pop(self._name, None)
        if not CommandTags.saveOverrides(current):
            tkmsg.showerror(
                "タグの保存", "書き込みに失敗しました。ログを確認してください。"
            )
            return
        self.close()
        # 収集からやり直す。ここを通さないと、保存はできているのに
        # 一覧のタグが古いままになる。
        self._onSaved()

    def close(self) -> None:
        """窓を閉じる。呼び出し元の記憶も消してもらう。"""
        self._onClose()
        self.win.destroy()

    def lift(self) -> None:
        """既に開いているときに前へ出す。"""
        self.win.lift()
        self.win.focus_force()
        self.entry.focus_set()


def openEditor(
    master: Any,
    name: str,
    table: dict[str, list[str]],
    onSaved: Callable[[], None],
    onClose: Callable[[], None],
) -> TagEditor | None:
    """タグ編集の窓を開く。名前が空なら開かずに None を返す。

    選択の検査をここへ寄せたのは、呼び出し側が毎回同じ判定を書かずに
    済むようにするため。開けない理由はログにも残す。
    """
    if not name:
        print("No command is selected.")
        logger.warning("No command is selected.")
        return None
    return TagEditor(master, name, table, onSaved, onClose)
