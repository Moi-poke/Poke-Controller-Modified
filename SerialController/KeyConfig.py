#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KeyConfig.py - キーボードの割り当てを設定する画面.

Switch のボタン / 左スティック(Direction) / 十字キー(Hat) に、どの
キーボードキーを対応させるかを決める。設定は settings.ini の KeyMap-*
セクションへ書き、実際の変換は Keyboard.SwitchKeyboardController が行う。

旧実装からの主な変更点:
  - 項目ごとに Label と Entry を手書きしていた約200行を KEY_ITEMS の
    データ定義＋ループへ置き換えた（項目を1行足せば画面に出る）。
  - Hat の値が '10000' のようなプレースホルダで、実際のキーとして
    機能していなかった。押したキーをそのまま記録する形に統一した。
  - Direction（左スティック）の画面が無く、settings.ini にだけ
    KeyMap-Direction が存在していた。タブとして追加した。
  - 同じキーを複数の操作へ割り当てても検出できなかった。重複は色と
    メッセージで知らせる。
  - settings.ini を configparser で直接上書きしていたため、GUI 側で
    変更した他の設定を古い内容で潰す危険があった。Settings 経由にした。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
from collections.abc import Callable
from typing import Any, NamedTuple

from Settings import GuiSettings
from loguru import logger
from pynput import keyboard

# Joy-Con の色。左右で色を分けると、どちら側のボタンか一目で分かる
COLOR_L = "#00c3e3"
COLOR_R = "#ff4554"
COLOR_HAT = "#7f8c8d"
COLOR_STICK = "#f0932b"
COLOR_TEXT_ON_DARK = "#ffffff"
COLOR_DUPLICATE = "#ffd7d7"  # 重複しているキーの背景
COLOR_WAITING = "#fff6b7"  # 入力待ちの背景

FONT_LABEL = "{游ゴシック} 11 {bold}"
FONT_HINT = "{游ゴシック} 9"

# 押されても割り当てとして受け付けないキー。GUI の操作に使うため。
BLOCKED_KEYS = frozenset({"Key.tab", "Key.esc", "Key.enter"})


class KeyItem(NamedTuple):
    """設定できる操作1つ分の定義。

    section : settings.ini のセクション名
    name    : セクション内のキー名（例 "Button.A"）
    label   : 画面に出す名前
    color   : ラベルの背景色
    """

    section: str
    name: str
    label: str
    color: str

    @property
    def dark(self) -> bool:
        """背景が濃い色か（文字色を白にするかの判定）。"""
        return self.color in (COLOR_R, COLOR_HAT, COLOR_STICK)


class TabSpec(NamedTuple):
    """タブ1枚分の定義。"""

    title: str
    hint: str
    items: tuple[KeyItem, ...]


def _button(name: str, label: str, color: str) -> KeyItem:
    return KeyItem("KeyMap-Button", f"Button.{name}", label, color)


def _hat(name: str, label: str) -> KeyItem:
    return KeyItem("KeyMap-Hat", f"Hat.{name}", label, COLOR_HAT)


def _direction(name: str, label: str) -> KeyItem:
    return KeyItem("KeyMap-Direction", f"Direction.{name}", label, COLOR_STICK)


# 画面の構成。ここへ1行足せばタブにも保存対象にも自動で反映される。
# 並び順は実機のコントローラの配置に合わせてある。
TABS: tuple[TabSpec, ...] = (
    TabSpec(
        "ボタン",
        "各欄をクリックしてから、割り当てたいキーを押してください。",
        (
            _button("ZL", "ZL", COLOR_L),
            _button("L", "L", COLOR_L),
            _button("MINUS", "− (MINUS)", COLOR_L),
            _button("CAPTURE", "キャプチャー", COLOR_L),
            _button("LCLICK", "L スティック押込", COLOR_L),
            _button("ZR", "ZR", COLOR_R),
            _button("R", "R", COLOR_R),
            _button("PLUS", "＋ (PLUS)", COLOR_R),
            _button("HOME", "HOME", COLOR_R),
            _button("RCLICK", "R スティック押込", COLOR_R),
            _button("A", "A", COLOR_R),
            _button("B", "B", COLOR_R),
            _button("X", "X", COLOR_R),
            _button("Y", "Y", COLOR_R),
        ),
    ),
    TabSpec(
        "十字キー",
        "十字キー(Hat)の割り当てです。斜めは上下と左右の同時押しでも入ります。",
        (
            _hat("TOP", "上"),
            _hat("BTM", "下"),
            _hat("LEFT", "左"),
            _hat("RIGHT", "右"),
            _hat("TOP_RIGHT", "右上"),
            _hat("BTM_RIGHT", "右下"),
            _hat("BTM_LEFT", "左下"),
            _hat("TOP_LEFT", "左上"),
        ),
    ),
    TabSpec(
        "左スティック",
        "左スティックを倒す向きです。斜めは上下と左右の同時押しでも入ります。",
        (
            _direction("UP", "上"),
            _direction("DOWN", "下"),
            _direction("LEFT", "左"),
            _direction("RIGHT", "右"),
            _direction("UP_RIGHT", "右上"),
            _direction("DOWN_RIGHT", "右下"),
            _direction("DOWN_LEFT", "左下"),
            _direction("UP_LEFT", "左上"),
        ),
    ),
)

ALL_ITEMS: tuple[KeyItem, ...] = tuple(item for tab in TABS for item in tab.items)


def key_to_text(key: Any) -> str | None:
    """pynput のキーを settings.ini へ書く文字列にする。

    英数字は "a" のような1文字、特殊キーは "Key.up" の形。どちらでも
    ない場合（IME 経由の入力など）は None を返して割り当てを見送る。
    Keyboard._to_input_key() がこの2形式を解釈するので、書式を合わせる。
    """
    char = getattr(key, "char", None)
    if char:
        return str(char)
    name = getattr(key, "name", None)
    if name:
        return f"Key.{name}"
    return None


def text_to_display(value: str) -> str:
    """設定値を画面表示用の文字列にする。

    "Key.up" は "↑"、空文字は "(未割当)" のように、押すキーが直感的に
    分かる形へ直す。settings.ini に入る値そのものは変えない。
    """
    if not value:
        return "(未割当)"
    arrows = {
        "Key.up": "↑",
        "Key.down": "↓",
        "Key.left": "←",
        "Key.right": "→",
    }
    if value in arrows:
        return arrows[value]
    if value.startswith("Key."):
        return value[4:]
    if value.isdigit():
        # 旧 settings.ini のプレースホルダ（10000 など）。実際のキーでは
        # ないので、割り当て直しが要ることが分かる表示にする。
        return f"(未設定 {value})"
    return value


class PokeKeycon:
    """キーコンフィグのウィンドウ。

    「適用」を押すまで settings.ini へは書かない。キー割り当ては試行錯誤
    するものなので、閉じれば元に戻せるほうが扱いやすい。
    """

    def __init__(self, master: tk.Misc | None = None, profile: str = "") -> None:
        self.master = master
        self.settings = GuiSettings(profile)

        # {(section, name): 現在の割り当て}。適用するまでここだけを書き換える
        self.values: dict[tuple[str, str], str] = {}
        self.entries: dict[tuple[str, str], ttk.Entry] = {}
        self.vars: dict[tuple[str, str], tk.StringVar] = {}

        self.listener: keyboard.Listener | None = None
        self.capturing: KeyItem | None = None  # 入力待ちの項目
        self._dirty: bool = False

        self.kc = tk.Toplevel(master)
        self.kc.title("キーコンフィグ")
        self.kc.resizable(False, False)

        self._init_style()
        self._build_ui()
        self.load_config()

        # ウィンドウを閉じるときは、未適用の変更があれば確認する
        self.kc.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------
    # 画面の組み立て
    # ------------------------------------------------------------------

    def _init_style(self) -> None:
        style = ttk.Style()
        style.configure("TFrame", background="white")
        style.configure("Hint.TLabel", font=FONT_HINT, foreground="#555555")
        style.configure("Status.TLabel", font=FONT_HINT)

    def _build_ui(self) -> None:
        self.frame = ttk.Frame(self.kc, padding=10)
        self.frame.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(self.frame)
        self.notebook.pack(fill="both", expand=True)
        for spec in TABS:
            self.notebook.add(self._build_tab(spec), text=spec.title, padding=8)

        self._build_status()
        self._build_buttons()

    def _build_tab(self, spec: TabSpec) -> ttk.Frame:
        """タブ1枚を組み立てる。項目は2列に並べる。"""
        tab = ttk.Frame(self.notebook)

        hint = ttk.Label(tab, text=spec.hint, style="Hint.TLabel")
        hint.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        # 縦に長くなりすぎないよう2列（ラベル+入力欄で4カラム）に分ける
        half = (len(spec.items) + 1) // 2
        for index, item in enumerate(spec.items):
            row = index % half + 1
            col = (index // half) * 2
            self._build_row(tab, item, row, col)
        return tab

    def _build_row(self, parent: ttk.Frame, item: KeyItem, row: int, col: int) -> None:
        """1項目分のラベルと入力欄を作る。"""
        label = tk.Label(
            parent,
            text=item.label,
            background=item.color,
            foreground=COLOR_TEXT_ON_DARK if item.dark else "#000000",
            font=FONT_LABEL,
            padx=8,
            pady=4,
            anchor="e",
            width=14,
        )
        label.grid(row=row, column=col, padx=(0, 4), pady=3, sticky="ew")

        var = tk.StringVar()
        entry = ttk.Entry(
            parent, textvariable=var, state="readonly", width=14, justify="center"
        )
        entry.grid(row=row, column=col + 1, padx=(0, 16), pady=3, sticky="ew")

        key = (item.section, item.name)
        self.vars[key] = var
        self.entries[key] = entry

        # クリックで入力待ちに入り、離れたら解除する
        # item は行ごとの仮引数のため、束縛の遅延は起きない。
        entry.bind("<FocusIn>", lambda _e: self.start_capture(item))
        entry.bind("<FocusOut>", lambda _e: self.stop_capture())
        # 選択中の項目を消したいことがあるので Delete / BackSpace を受ける
        entry.bind("<Delete>", lambda _e: self.clear_item(item))
        entry.bind("<BackSpace>", lambda _e: self.clear_item(item))

    def _build_status(self) -> None:
        """操作の説明と、重複などの警告を出す欄。"""
        self.status = tk.StringVar(value="")
        self.status_label = ttk.Label(
            self.frame, textvariable=self.status, style="Status.TLabel"
        )
        self.status_label.pack(fill="x", pady=(8, 0))

    def _build_buttons(self) -> None:
        bar = ttk.Frame(self.frame)
        bar.pack(fill="x", pady=(8, 0))

        ttk.Button(bar, text="既定に戻す", command=self.reset_to_default).pack(
            side="left"
        )
        ttk.Button(bar, text="キャンセル", command=self.on_close).pack(
            side="right", padx=(8, 0)
        )
        self.apply_button = ttk.Button(bar, text="適用", command=self.apply_setting)
        self.apply_button.pack(side="right")

    # ------------------------------------------------------------------
    # 読み込み / 表示
    # ------------------------------------------------------------------

    def load_config(self) -> None:
        """settings.ini から現在の割り当てを読み込む。"""
        key_maps = self.settings.load_all_key_maps()
        for item in ALL_ITEMS:
            value = key_maps.get(item.section, {}).get(item.name, "")
            self.values[(item.section, item.name)] = value
        self._dirty = False
        self.refresh()
        logger.debug("キーコンフィグを読み込みました")

    def refresh(self) -> None:
        """表示を現在の値に合わせ、重複を色で知らせる。"""
        duplicated = self.find_duplicates()
        for item in ALL_ITEMS:
            key = (item.section, item.name)
            value = self.values.get(key, "")
            self.vars[key].set(text_to_display(value))
            self._set_entry_color(
                self.entries[key],
                COLOR_DUPLICATE if value and value in duplicated else "",
            )
        self._update_status(duplicated)

    def _set_entry_color(self, entry: ttk.Entry, color: str) -> None:
        """入力欄の背景色を変える（readonly なので readonlybackground を使う）。"""
        try:
            entry.configure(background=color or "white")
        except tk.TclError:
            # ttk のテーマによっては background を受け付けない。色が付かない
            # だけで機能には影響しないため、握って続行する。
            pass

    def find_duplicates(self) -> dict[str, list[str]]:
        """同じキーが複数の操作に割り当てられていないか調べる。

        重複したまま使うと、押したキーがどちらの操作になるかは辞書の
        並び順次第になり、原因の分からない誤動作として現れる。
        戻り値は {キー: [操作名, ...]}（2件以上のものだけ）。
        """
        used: dict[str, list[str]] = {}
        for item in ALL_ITEMS:
            value = self.values.get((item.section, item.name), "")
            if not value:
                continue
            used.setdefault(value, []).append(item.label)
        return {k: v for k, v in used.items() if len(v) > 1}

    def _update_status(self, duplicated: dict[str, list[str]]) -> None:
        if duplicated:
            first_key, first_labels = next(iter(duplicated.items()))
            names = " / ".join(first_labels)
            text = f"⚠ 同じキーが重なっています: {text_to_display(first_key)} → {names}"
            if len(duplicated) > 1:
                text += f"（他 {len(duplicated) - 1} 件）"
            self.status.set(text)
            self.status_label.configure(foreground="#c0392b")
            return

        if self.capturing is not None:
            self.status.set(
                f"「{self.capturing.label}」に割り当てるキーを押してください"
            )
            self.status_label.configure(foreground="#2980b9")
            return

        unassigned = sum(
            1 for i in ALL_ITEMS if not self.values.get((i.section, i.name))
        )
        if unassigned:
            self.status.set(
                f"未割当が {unassigned} 件あります（Delete で解除できます）"
            )
            self.status_label.configure(foreground="#555555")
        else:
            self.status.set("欄をクリックしてキーを押すと割り当てられます")
            self.status_label.configure(foreground="#555555")

    # ------------------------------------------------------------------
    # キー入力の取り込み
    # ------------------------------------------------------------------

    def start_capture(self, item: KeyItem) -> None:
        """入力待ちを開始する。

        pynput の Listener は stop() 後に再 start できないため、項目を
        選ぶたびに作り直す（Keyboard.py と同じ理由）。
        """
        self.stop_capture()
        self.capturing = item
        self._set_entry_color(self.entries[(item.section, item.name)], COLOR_WAITING)
        self.listener = keyboard.Listener(on_press=self._on_press)
        self.listener.start()
        self._update_status(self.find_duplicates())
        logger.debug(f"入力待ち: {item.name}")

    def stop_capture(self) -> None:
        """入力待ちを終了する。二重に呼ばれても安全。"""
        listener, self.listener = self.listener, None
        if listener is not None:
            listener.stop()
        self.capturing = None
        self.refresh()

    def _on_press(self, key: Any) -> None:
        """キーが押されたときに呼ばれる（pynput のスレッド）。

        tkinter はスレッドセーフではないので、ここでは値を決めるだけにして
        画面の更新は after(0) で GUI スレッドへ渡す。
        """
        item = self.capturing
        if item is None:
            return
        text = key_to_text(key)
        if text is None:
            logger.warning("解釈できないキーが押されました")
            return
        if text in BLOCKED_KEYS:
            # Tab / Esc / Enter は画面操作に使うので割り当てさせない
            logger.debug(f"割り当てできないキーです: {text}")
            return
        self.kc.after(0, lambda: self._assign(item, text))

    def _assign(self, item: KeyItem, text: str) -> None:
        """割り当てを反映する（GUI スレッドで実行される）。"""
        self.values[(item.section, item.name)] = text
        self._dirty = True
        self.refresh()
        logger.debug(f"{item.name} に {text} を割り当てました")

    def clear_item(self, item: KeyItem) -> str:
        """割り当てを解除する。Entry のバインドから呼ぶので "break" を返す。"""
        self.values[(item.section, item.name)] = ""
        self._dirty = True
        self.refresh()
        return "break"

    # ------------------------------------------------------------------
    # 保存 / 終了
    # ------------------------------------------------------------------

    def reset_to_default(self) -> None:
        """既定の割り当てへ戻す（保存はしない。適用を押すまで確定しない）。"""
        if not tkmsg.askyesno(
            "確認", "キー割り当てを既定に戻しますか？", parent=self.kc
        ):
            return
        defaults = self.settings._default_sections()
        for item in ALL_ITEMS:
            value = defaults.get(item.section, {}).get(item.name, "")
            self.values[(item.section, item.name)] = str(value)
        self._dirty = True
        self.refresh()

    def apply_setting(self) -> None:
        """settings.ini へ保存する。重複があるときは確認する。"""
        duplicated = self.find_duplicates()
        if duplicated and not self._confirm_duplicates(duplicated):
            return

        key_maps: dict[str, dict[str, str]] = {}
        for item in ALL_ITEMS:
            value = self.values.get((item.section, item.name), "")
            key_maps.setdefault(item.section, {})[item.name] = value

        # Settings 経由で書く。configparser で settings.ini を直接上書きすると
        # GUI 側で変更した他の設定（カメラ・COM ポート等）を古い値で潰す。
        self.settings.update_key_map(key_maps)
        self._dirty = False
        self.stop_capture()
        logger.info("キー割り当てを保存しました")
        tkmsg.showinfo(
            "保存しました",
            "キー割り当てを保存しました。\n"
            "反映するには Use Keyboard を切り替え直してください。",
            parent=self.kc,
        )

    def _confirm_duplicates(self, duplicated: dict[str, list[str]]) -> bool:
        """重複したまま保存してよいか確認する。保存してよければ True。"""
        lines: list[str] = []
        for value, labels in duplicated.items():
            names = " / ".join(labels)
            lines.append(f"  {text_to_display(value)} → {names}")
        detail = "\n".join(lines)
        message = (
            "同じキーが複数の操作に割り当てられています。\n"
            "このまま使うと、どちらが動くか分からない状態になります。\n\n"
            + detail
            + "\n\nそれでも保存しますか？"
        )
        return bool(tkmsg.askyesno("確認", message, parent=self.kc))

    def on_close(self) -> None:
        """閉じる。未適用の変更があれば確認する。"""
        if self._dirty:
            if not tkmsg.askyesno(
                "確認",
                "適用していない変更があります。破棄して閉じますか？",
                parent=self.kc,
            ):
                return
        self.close()

    def close(self) -> None:
        """画面を隠す（Menubar 側が参照を持ち続ける想定）。"""
        self.stop_capture()
        self.kc.withdraw()

    def destroy(self) -> None:
        self.stop_capture()
        self.kc.destroy()

    def run(self) -> None:
        self.kc.mainloop()

    def bind(self, event: str, func: Callable[..., Any]) -> None:
        self.kc.bind(event, func)

    def protocol(self, event: str, func: Callable[..., Any]) -> None:
        self.kc.protocol(event, func)

    def focus_force(self) -> None:
        self.kc.deiconify()
        self.kc.focus_force()


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    app = PokeKeycon(root)
    app.run()
