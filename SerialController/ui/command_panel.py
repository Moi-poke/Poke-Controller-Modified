#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""command_panel.py - コマンド枠の組み立てと選択・実行の画面側。

PokeControllerApp に混ぜて使う（多重継承）。選ぶこと（一覧・
絞り込み・生成）と見た目の反映だけを持ち、開始・停止の手順そのものは
services.CommandRunner が持つ。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk
import traceback
from typing import Any

import CommandPalette
import TagEditor
import WindowUtils
from Commands import McuCommandBase, PythonCommandBase
from GuiAssets import CaptureAreaProxy
from core import CommandStats, CommandTags, Utility as util
from core.CommandLoader import CommandLoader
from core.CommandTags import TAG_ALL
from loguru import logger
from services.command_runner import CommandRunner
from services.serial_service import SerialService


class CommandPanelMixin:
    """コマンドパネルMixin。単体では使わない。"""

    frame_1: Any
    root: Any
    settings: Any
    serial: SerialService
    runner: CommandRunner
    command_stats: dict[str, dict]
    py_loader: Any
    mcu_loader: Any
    py_classes: list[type]
    mcu_classes: list[type]
    py_map: dict[str, type]
    mcu_map: dict[str, type]
    py_tags: dict[str, list[str]]
    mcu_tags: dict[str, list[str]]
    py_all_names: list[str]
    mcu_all_names: list[str]
    _shown_names: dict[Any, dict[str, str]]
    _tag_editor: Any
    _palette: Any
    cur_command: Any
    py_cur_command: Any
    mcu_cur_command: Any
    camera: Any
    preview: Any
    audio_service: Any
    _running_command: str
    _paused: bool
    open_folder_img: Any
    command_lf: Any
    Commands_f: Any
    Commands_2_f: Any
    Command_nb: Any
    py_name: Any
    py_cb: Any
    mcu_name: Any
    mcu_cb: Any
    filter_f: Any
    search_name: Any
    search_entry: Any
    tag_name: Any
    tag_cb: Any
    tagEditButton: Any
    OpenCommandDirButton: Any
    reloadCommandButton: Any
    startButton: Any
    pauseButton: Any
    com_port: Any
    com_port_name: Any
    OpenCommandDir: Any
    _currentBaudRate: Any
    _on_setting_changed: Any
    _update_title: Any

    def _build_command_frame(self) -> None:
        self.command_lf = ttk.Labelframe(self.frame_1)
        self.Commands_f = ttk.Frame(self.command_lf)
        self.Commands_2_f = ttk.Frame(self.command_lf)

        self.Command_nb = ttk.Notebook(self.Commands_f)
        self.py_name = tk.StringVar()
        self.py_cb = ttk.Combobox(self.Command_nb)
        self.py_cb.config(state="readonly", textvariable=self.py_name)
        self.py_cb.pack(side="top")
        self.Command_nb.add(self.py_cb, padding="5", text="Python Command")

        self.mcu_name = tk.StringVar()
        self.mcu_cb = ttk.Combobox(self.Command_nb)
        self.mcu_cb.config(state="readonly", textvariable=self.mcu_name)
        self.mcu_cb.pack(side="top")
        self.Command_nb.add(self.mcu_cb, padding="5", text="Mcu Command")

        # 検索とタグの絞り込み。コマンドの Notebook の真上へ置く。
        # 探す→選ぶ が上から下へ並ぶので、視線が戻らない。
        # 絞り込みは名前引きの上で動くため、表示が変わっても起動する
        # コマンドは取り違えない。
        self.filter_f = ttk.Frame(self.Commands_f)
        self.search_name = tk.StringVar()
        self.search_entry = ttk.Entry(self.filter_f, textvariable=self.search_name)
        self.search_entry.pack(side="left", fill="x", expand=True, padx="2")
        # 打つそばから絞る。確定を待つと、目当てが出るまで見えない。
        self.search_entry.bind("<KeyRelease>", self.onCommandFilterChanged, add="")
        # Enter で入力を終えて検索欄から抜ける。フォーカスが残ったままだと
        # キーボード操作でコントローラを動かす打鍵が検索欄へ入ってしまう。
        self.search_entry.bind("<Return>", self._leaveSearchBox, add="")
        self.search_entry.bind("<KP_Enter>", self._leaveSearchBox, add="")
        # Esc は絞り込みを解除して抜ける。迷子になったときの戻り道。
        self.search_entry.bind("<Escape>", self.clearCommandFilter, add="")

        self.tag_name = tk.StringVar(value=TAG_ALL)
        self.tag_cb = ttk.Combobox(self.filter_f, width=12)
        self.tag_cb.config(state="readonly", textvariable=self.tag_name)
        self.tag_cb["values"] = [TAG_ALL]
        self.tag_cb.pack(side="left", padx="2")
        self.tag_cb.bind("<<ComboboxSelected>>", self.onCommandFilterChanged, add="")

        # タグの編集。選択中のコマンドに対して開く。タグは絞り込みの
        # すぐ隣にあるほうが「絞れない→付ける」の流れが切れない。
        self.tagEditButton = ttk.Button(self.filter_f)
        self.tagEditButton.config(text="タグ", width=5, command=self.openTagEditor)
        self.tagEditButton.pack(side="left", padx="2")

        # コマンドフォルダを開くボタン。Notebook を縦積みにしたので、
        # 横に並べる置き場所は絞り込みの段しかない。右端へ寄せる。
        self.OpenCommandDirButton = ttk.Button(self.filter_f)
        self.OpenCommandDirButton.config(
            image=self.open_folder_img, command=self.OpenCommandDir
        )
        self.OpenCommandDirButton.pack(side="left", padx="2")

        # Notebook より先に pack して、絞り込みを上の段に置く。
        self.filter_f.pack(fill="x", expand=False, padx="5", pady="2", side="top")
        self.Command_nb.pack(fill="both", expand=True, padx="5", pady="5", side="top")

        # タブを切り替えたら、そのタブ側の一覧へ絞り込みをかけ直す。
        self.Command_nb.bind(
            "<<NotebookTabChanged>>", self.onCommandFilterChanged, add=""
        )

        self.reloadCommandButton = ttk.Button(self.Commands_2_f)
        self.reloadCommandButton.config(text="Reload", command=self.reloadCommands)
        self.reloadCommandButton.grid(column=0, padx="5", pady="5", row=1, sticky="ew")

        self.startButton = ttk.Button(self.Commands_2_f)
        self.startButton.config(text="Start", command=self.startPlay)
        self.startButton.grid(column=1, padx="5", pady="5", row=1, sticky="ew")

        # 一時停止。Start/Stop の隣に置く。停止と紛らわしくならないよう
        # 実行中だけ押せる状態にする。
        self.pauseButton = ttk.Button(self.Commands_2_f)
        self.pauseButton.config(
            text="Pause", command=self.togglePause, state="disabled"
        )
        self.pauseButton.grid(column=2, padx="5", pady="5", row=1, sticky="ew")

        self.Commands_f.pack(
            fill="both", expand=True, padx="5", pady="5", anchor=tk.E, side="top"
        )
        self.Commands_2_f.pack(
            fill="none", expand=True, padx=5, pady=5, anchor=tk.E, side="top"
        )
        self.command_lf.config(height="200", text="Command")
        self.command_lf.grid(column=2, padx="5", row=1, rowspan=2, sticky="nsew")

    def _bind_keys(self) -> None:
        self.root.bind("<Key-F5>", self.ReloadCommandWithF5)
        self.root.bind("<Key-F6>", self.StartCommandWithF6)
        self.root.bind("<Key-Escape>", self.StopCommandWithEsc)
        self.root.bind("<Key-F7>", self.PauseCommandWithF7)
        self.root.bind("<Control-k>", self.openCommandPalette)
        self.root.bind("<Control-K>", self.openCommandPalette)
        logger.debug("Bind F5 / F6 / F7 / Escape / Ctrl+K keys")

    def loadCommands(self) -> None:
        """コマンドを読み込む。パスは import 名になるので相対で渡す。

        Utility.getModuleNames は受け取ったパスの区切りを "." へ置き換えて
        そのまま import 名にする。ここへ絶対パスを渡すと、Windows では
        "c:.PokeCon.....Commands.PythonCommands.MashA" という名前になり、
        先頭の "c:" をパッケージとして探しに行って全件が
        ModuleNotFoundError になる。例外は Utility 側で握られるため
        起動は成功し、「コマンドが1つも出ない」形でしか現れない。

        import 名はカレントディレクトリからの相対でなければならない。
        __main__ で os.chdir(BASE_DIR) しているので、ここは相対のまま
        で BASE_DIR を指す。フォルダを開くボタン（絶対パスでよい）とは
        用途が違うので、同じ書き方に揃えてはいけない。
        """
        python_dir = util.ospath("Commands/PythonCommands")
        mcu_dir = util.ospath("Commands/McuCommands")
        self.py_loader = CommandLoader(python_dir, PythonCommandBase.PythonCommand)
        self.mcu_loader = CommandLoader(mcu_dir, McuCommandBase.McuCommand)

        self.py_classes = self.py_loader.load()
        self.mcu_classes = self.mcu_loader.load()
        if not self.py_classes and not self.mcu_classes:
            # 全滅は「壊れたコマンドが1つある」とは症状が違う。
            # 黙って空の一覧を出すと原因に辿り着けないので知らせる。
            message = f"コマンドを1つも読み込めませんでした（{python_dir} / {mcu_dir}）"
            print(message)
            logger.error(message)
        self.setCommandItems()
        self.assignCommand()

    def setCommandItems(self) -> None:
        """対応表とタグを作り直し、絞り込みの選択肢を整える。

        表示位置(current())でクラスを引くと、並び替えや絞り込みを入れた
        瞬間に「選んだものと違うコマンドが起動する」ことになる。しかも
        画面には正しい名前が出たままなので気づけない。名前を鍵にする。
        """
        self.py_map = CommandTags.buildCommandMap(self.py_classes)
        self.mcu_map = CommandTags.buildCommandMap(self.mcu_classes)

        overrides = CommandTags.loadOverrides()
        self.py_tags = {
            name: CommandTags.collectTags(cls, name, overrides)
            for name, cls in self.py_map.items()
        }
        self.mcu_tags = {
            name: CommandTags.collectTags(cls, name, overrides)
            for name, cls in self.mcu_map.items()
        }

        self.py_all_names = list(self.py_map.keys())
        self.mcu_all_names = list(self.mcu_map.keys())

        self._refreshTagChoices()
        self.applyCommandFilter(keep_selection=False)

    def _refreshTagChoices(self) -> None:
        """絞り込みの選択肢を作り直す。並びは CommandTags 側で決める。"""
        choices = CommandTags.sortTagChoices([self.py_tags, self.mcu_tags])
        # 履歴由来の仮想タグ（最近使った / よく使う）を後ろへ足す。
        # 実体のタグではないので保存も編集もされず、コマンド側に何も
        # 書かなくても効く。該当が無いときは選択肢に出さない。
        choices = choices + CommandStats.choices(self.command_stats)
        self.tag_cb["values"] = choices
        if self.tag_name.get() not in choices:
            self.tag_name.set(TAG_ALL)

    def applyCommandFilter(self, keep_selection: bool = True) -> None:
        """検索語とタグで一覧を絞り込み、名前順に並べ替えて反映する。

        両方のタブを毎回そろえて更新する。片方だけ直すと、もう一方は
        古い一覧のまま残り、タブを切り替えた瞬間に表示と対応表が食い違う。
        絞り込んだ結果と表示位置は連動しないが、クラスは名前で引くので
        取り違えは起きない（それが名前引きへ変えた理由でもある）。
        """
        keyword = self.search_name.get().strip().lower()
        tag = self.tag_name.get() or TAG_ALL

        for combo, names, tag_table in (
            (self.py_cb, self.py_all_names, self.py_tags),
            (self.mcu_cb, self.mcu_all_names, self.mcu_tags),
        ):
            shown: list[tuple[str, str]] = []
            for name in names:
                tags = tag_table.get(name, [])
                # 絞り込みの判定にだけ仮想タグを混ぜる。表示の前置は
                # 実体のタグだけにして、履歴で見た目が変わらないようにする。
                matched = tags + CommandStats.virtualTags(self.command_stats, name)
                if tag != TAG_ALL and tag not in matched:
                    continue
                label = CommandTags.displayName(name, tags)
                # 使用履歴を名前の後ろへ添える。前置しないのは、並べ替えが
                # タグ順であることを崩さないため。検索は label 全体を見るので、
                # 日付や回数でも絞り込める（副次的だが実用になる）。
                used = CommandStats.summary(self.command_stats, name)
                if used:
                    label = f"{label}  — {used}"
                if keyword and keyword not in label.lower():
                    continue
                shown.append((label, name))

            shown.sort(key=lambda pair: pair[0])
            # 選択の維持は素の名前で行う。表示名には使用履歴（前回・回数）
            # を添えており、実行するたびに文字列が変わる。表示名で突き合わせ
            # ると、走らせた直後に選択が先頭へ飛んでしまう。
            before_name = self._selectedName(combo)
            self._shown_names[combo] = dict(shown)
            combo["values"] = [label for label, _ in shown]
            # 素の名前 → 新しい表示名。選び直しに使う
            relabel = {name: label for label, name in shown}

            # 走行中は選択を動かさない。いま走っているコマンドと画面の
            # 表示が食い違うと、Stop が何に対する操作なのか分からなくなる。
            if self.runner.is_busy():
                if before_name in relabel:
                    combo.set(relabel[before_name])
            elif keep_selection and before_name in relabel:
                combo.set(relabel[before_name])
            elif shown:
                combo.current(0)
            else:
                # 0件でも前の表示が残ると「選べているのに動かない」に
                # 見える。空にして、選べていないことを見た目に出す。
                combo.set("")

        self.assignCommand()

    def onCommandFilterChanged(self, *event: Any) -> None:
        """検索欄・タグ・タブの切り替えから呼ばれる。"""
        self.applyCommandFilter()

    def clearCommandFilter(self, *event: Any) -> str:
        """検索語とタグを既定へ戻し、検索欄から抜ける。

        Esc は停止(StopCommandWithEsc)にも割り当ててある。検索欄に
        いるあいだは絞り込みの解除として使い、"break" を返して停止側へ
        伝わらないようにする。打ち間違いで走行中のコマンドが止まると
        困るため、ここで確実に止める。
        """
        self.search_name.set("")
        self.tag_name.set(TAG_ALL)
        self.applyCommandFilter()
        self._leaveSearchBox()
        return "break"

    def _leaveSearchBox(self, *event: Any) -> Any:
        """検索欄からフォーカスを外す。

        入れっぱなしだと、キーボード操作でコントローラを動かすつもりの
        打鍵が検索欄へ入ってしまう。Enter で「入力を終える」操作として
        抜けられるようにした。
        """
        self.Command_nb.focus_set()
        return "break"

    def openCommandPalette(self, *event: Any) -> str:
        """Ctrl+K でコマンドを検索して実行する小窓を開く。

        Combobox を開いて目で探す操作を、キーボードだけで済ませる。
        候補・表示名・並び順はすべて一覧側と同じ材料（タグと使用履歴）を
        使う。ここで独自の規則を作ると、同じ名前で探しているのに一覧と
        結果が違う、という分かりにくい状態になる。

        走行中は開かない。選び直せてしまうと、いま走っているコマンドと
        画面の表示が食い違い、Stop が何に対する操作か分からなくなる。
        """
        if self.runner.is_busy():
            print("実行中はコマンドを切り替えられません")
            return "break"

        if self._palette is not None:
            self._palette.lift()
            return "break"

        # いま見えているタブを対象にする。Python と Mcu で候補が別なので、
        # 画面と違うタブのコマンドを出すと選んだあとに取り違える。
        names = self.py_all_names
        tag_table = self.py_tags
        if self.Command_nb.index(self.Command_nb.select()) != 0:  # type: ignore
            names = self.mcu_all_names
            tag_table = self.mcu_tags

        # 表示名は一覧と同じ組み立て方にする（タグ前置＋使用履歴）
        labels = {}
        for name in names:
            tags = tag_table.get(name, [])
            label = CommandTags.displayName(name, tags)
            used = CommandStats.summary(self.command_stats, name)
            labels[name] = f"{label}  — {used}" if used else label

        def onClose() -> None:
            self._palette = None

        self._palette = CommandPalette.CommandPalette(
            self.root,
            list(names),
            labels,
            self.command_stats,
            self._runFromPalette,
            onClose,
        )
        return "break"

    def _runFromPalette(self, name: str) -> None:
        """パレットで選ばれたコマンドを一覧へ反映してから実行する。

        ここで直接コマンドを生成せず、既存の経路（表示を合わせてから
        startPlay）に通す。生成と開始を2か所に持つと、停止や一時停止の
        状態管理が二重になり、どちらが正か決められなくなる。
        """
        combo = self.py_cb
        if self.Command_nb.index(self.Command_nb.select()) != 0:  # type: ignore
            combo = self.mcu_cb

        # 絞り込みで一覧から外れていると選べないので、先に解除する。
        # 検索欄に文字が残ったままだと、実行後の一覧が空に見えて戸惑う。
        if self.search_name.get() or self.tag_name.get() != TAG_ALL:
            self.clearCommandFilter()

        # 素の名前 → いまの表示名。表示名は使用履歴で変わるため毎回引き直す
        relabel = {n: lb for lb, n in self._shown_names.get(combo, {}).items()}
        label = relabel.get(name)
        if label is None:
            print(f"コマンドが見つかりません: {name}")
            logger.warning(f"Command not found in the list: {name}")
            return

        combo.set(label)
        self.assignCommand()
        self.startPlay()

    def openTagEditor(self, *event: Any) -> None:
        """選択中のコマンドのタグを編集する小窓を開く。

        窓の中身は TagEditor.py へ切り出した。ここに残すのは「どの
        タブが選ばれているか」「開いている窓を1つに保つ」という、
        この画面にしか分からない判断だけにする。
        """
        combo = self.py_cb
        table = self.py_tags
        if self.Command_nb.index(self.Command_nb.select()) != 0:  # type: ignore
            combo = self.mcu_cb
            table = self.mcu_tags

        # 開いている最中に本体を触られると、保存時に食い違う。
        if self._tag_editor is not None:
            self._tag_editor.lift()
            return

        def onClose() -> None:
            self._tag_editor = None

        self._tag_editor = TagEditor.openEditor(
            self.root,
            self._selectedName(combo),
            table,
            self.setCommandItems,
            onClose,
        )

    def _selectedName(self, combo: ttk.Combobox) -> str:
        """Combobox の表示（タグ前置つき）から、素のコマンド名へ戻す。

        表示と鍵を分けておかないと、タグを付け替えただけで別物として
        扱われる。引くのは常に素の名前にする。
        """
        label = combo.get()
        return self._shown_names.get(combo, {}).get(label, label)

    def _restoreSelection(
        self, combo: ttk.Combobox, mapping: dict[str, type], target: type | None
    ) -> None:
        """リロード前に選んでいたクラスと同じ名前のものを選び直す。"""
        if target is None:
            return
        wanted = CommandTags.commandName(target)
        for label, name in self._shown_names.get(combo, {}).items():
            if name == wanted and label in combo["values"]:
                combo.set(label)
                return

    def assignCommand(self) -> None:
        """選択クラスが変わった場合だけ、選択時インスタンスを作り直す。"""
        if self.runner.is_busy():
            return

        def ensure(current: Any, selected: Any) -> Any:
            if selected is None:
                return None
            if current is not None and type(current) is selected:
                return current
            return self._buildCommand(selected)

        mcu_class = self.mcu_map.get(self._selectedName(self.mcu_cb))
        self.mcu_cur_command = ensure(self.mcu_cur_command, mcu_class)
        py_class = self.py_map.get(self._selectedName(self.py_cb))
        self.py_cur_command = ensure(self.py_cur_command, py_class)

        if self.Command_nb.index(self.Command_nb.select()) == 0:  # type: ignore
            self.cur_command = self.py_cur_command
        else:
            self.cur_command = self.mcu_cur_command
        enabled = self.cur_command is not None
        self.startButton["state"] = "normal" if enabled else "disabled"

    def _buildCommand(self, cmd_class: Any) -> Any:
        """コマンドを1つ生成する。失敗したら None を返す。

        ダイアログを出すコマンドはワーカースレッドから tk を触ることに
        なるため、生成した時点で GUI のルートを渡しておく。画像認識の
        コマンドは gui（プレビュー）を受け取れるが、通常のコマンドは
        受け取れず、渡し口が無いままだった。属性で渡せば全種類に効く。
        """
        if cmd_class is None:
            return None
        try:
            if issubclass(cmd_class, PythonCommandBase.ImageProcPythonCommand):
                # 旧: except TypeError で握っていたため、コマンド内部で起きた
                # TypeError まで「古い形式」と誤判定し引数1つで再生成していた。
                # シグネチャを見て渡せる引数の数を先に決める。
                # preview は代理越しに渡す。ワーカーから Canvas を直接触ると
                # Tk のスレッド制約に触れるため、描画系は GUI スレッドへ
                # 回す（CaptureAreaProxy）。
                gui = self.preview
                if gui is not None:
                    gui = CaptureAreaProxy(self.root, gui)
                if WindowUtils.acceptsGuiArg(cmd_class):
                    command = cmd_class(self.camera, gui)
                else:
                    command = cmd_class(self.camera)
                # 画像＋音声の併用コマンドには音声源を属性で渡す。
                # gui_root と同じく、渡せなくても致命扱いにしない。
                # 画像コマンドは _initAudio を通らないため _sound_triggers
                # が無い。AudioMixin 持ちだけ空リストを補う（他に影響させない）。
                try:
                    command.audio = self.audio_service.capture
                    if hasattr(command, "onSoundDetected") and not hasattr(
                        command, "_sound_triggers"
                    ):
                        command._sound_triggers = []
                except Exception as e:
                    logger.debug(f"audio を渡せませんでした: {e}")
            elif issubclass(cmd_class, PythonCommandBase.AudioPythonCommand):
                audio = self.audio_service.capture
                if WindowUtils.acceptsAudioArg(cmd_class):
                    command = cmd_class(audio)
                else:
                    command = cmd_class()
            else:
                command = cmd_class()
        except Exception:
            name = getattr(cmd_class, "NAME", getattr(cmd_class, "__name__", "?"))
            logger.error(traceback.format_exc())
            print(f"コマンドの初期化に失敗しました: {name}")
            return None

        # ダイアログを GUI スレッドで作らせるための足がかり。
        # PythonCommandBase._guiRoot がここを最初に見る。
        # McuCommand はダイアログを出さないので渡す意味が無く、
        # 無用な属性を生やさないよう対象を絞る。
        if isinstance(command, PythonCommandBase.PythonCommand):
            # 代入できないコマンド（__slots__ や __setattr__ を持つもの）が
            # あるため握る。ここは初期化の失敗として扱ってはいけない。
            # 失敗を致命扱いにすると __slots__ のコマンドが選べなくなり、
            # 以前に直した後方互換の破壊を再発させる。
            # 渡せなくても従来どおり動く（ダイアログが呼び出し元の
            # スレッドで作られるだけで、これは以前と同じ）。
            try:
                command.gui_root = self.root
            except Exception as e:
                # ログ欄にも出す。debug だけだと、ダイアログまわりで
                # 妙な挙動を追うときに手がかりが残らない。
                name = getattr(cmd_class, "NAME", getattr(cmd_class, "__name__", "?"))
                message = f"gui_root を渡せませんでした（{name}）: {e}"
                print(message)
                logger.debug(message)
        # 設定はここでは渡さない。以前は command.settings = self.settings と
        # 参照ごと渡していたが、それでは reload_com_port が tk 変数の get()
        # をワーカースレッドから呼ぶことになり、Tcl を別スレッドで触る形に
        # なっていた。COM の設定は Start の直前に GUI スレッド
        # で _snapshotSerialConfig() が通常の Python 値へ写す。生成から Start
        # までに COM ポートを選び直される可能性があるため、渡す時点は生成時
        # ではなく Start 直前でなければならない。

        return command

    def _snapshotSerialConfig(self, command: Any) -> None:
        """COM の設定を通常の Python 値へ写してコマンドへ渡す。

        必ず GUI スレッド（Start のコールバック）から呼ぶこと。tk 変数の
        get() は Tcl インタプリタを呼ぶため、コマンド側のワーカー
        スレッドから読むと Tkinter のスレッド制約に触れる。ここで int /
        str へ落としてしまえば、以後は Tk と無関係な値になる。

        写す時点が Start の直前であることも要点。コマンドの生成は選択時に
        行われるので、生成時に渡すと、そのあと COM ポートを選び直しても
        古い値のまま残る。Start のたびに上書きする。

        値そのものは画面の変数（self.com_port など）から取る。settings は
        起動時に読んだ内容で、画面で選び直した分は _on_setting_changed()
        を通るまで入らない。いま繋いでいる先と一致するのは画面側。
        """
        try:
            config = {
                "com_port": int(self.com_port.get()),
                "com_port_name": str(self.com_port_name.get()),
                "baud_rate": self._currentBaudRate(),
            }
        except (tk.TclError, ValueError) as e:
            # 空欄や未選択のときは変換に失敗する。ここで止めはしない
            # （COM を使わないコマンドまで動かせなくなる）。渡さなければ
            # reload_com_port が理由を出して False を返す。
            logger.warning(f"COM の設定を写せませんでした: {e}")
            return

        try:
            command.serial_config = config
        except Exception:
            # __slots__ や __setattr__ を持つコマンドには渡せない。
            # gui_root と同じ扱いで、渡せなくても実行そのものは続ける。
            logger.debug(f"serial_config を渡せませんでした: {type(command)}")

    def _apply_runner_state(self) -> None:
        """runner の状態を実行中の見た目へ映す（状態変更の合図で呼ばれる）。

        状態の持ち主は runner。ここは表示だけを直す。
        一時停止に対応しない種類（MCU コマンド）では Pause を押せない
        ようにする。押せるのに「対応していません」とだけ出るのは、
        壊れて見える。
        """
        state = self.runner.state
        if state == "running":
            command = self.runner.running_command
            self.startButton["text"] = "Stop"
            self.startButton["command"] = self.stopPlay
            self.startButton["state"] = "normal"
            self.reloadCommandButton["state"] = "disabled"
            supports_pause = callable(getattr(command, "togglePause", None))
            self.pauseButton["text"] = "Pause"
            self.pauseButton["state"] = "normal" if supports_pause else "disabled"
            self._running_command = str(getattr(command, "NAME", ""))
            self._paused = False
        elif state == "stopping":
            self.startButton["state"] = "disabled"
            if self._paused:
                self._paused = False
                self.pauseButton["text"] = "Pause"
        else:
            self.startButton["text"] = "Start"
            self.startButton["command"] = self.startPlay
            self.startButton["state"] = "normal"
            self.reloadCommandButton["state"] = "normal"
            self.pauseButton["text"] = "Pause"
            self.pauseButton["state"] = "disabled"
            self._paused = False
            self._running_command = ""
        self._update_title()

    def _refresh_after_run(self) -> None:
        """実行終了後の一覧の作り直し（runner からの合図で呼ばれる）。

        走行中は選択を動かさないため見送っていた分を、空いた時点で
        実際の履歴に合わせる。失敗時の通知は runner 側が行う。
        """
        self._refreshTagChoices()
        self.applyCommandFilter()

    def reloadCommands(self) -> None:
        """リロード後も同じコマンドが選ばれた状態に戻す。

        復元は表示位置ではなく名前で行う。絞り込みや並び替えが入ると
        位置は当てにならないうえ、タグを前置した表示名も変わりうる。
        """
        # 実行中の再ロードを断る。ボタンは disabled にしてあるが、
        # F5 のキーバインドは生きているため、ここで塞がないと通る。
        # 走っているインスタンスは古いクラス定義を持ったまま、モジュール
        # 側のグローバルやクラス変数だけが新しくなり、新旧が混在する。
        if self.runner.is_busy():
            print("実行中はコマンドを再ロードできません")
            logger.warning("Reload is unavailable while a command is running")
            return

        old_py = self.py_map.get(self._selectedName(self.py_cb))
        old_mcu = self.mcu_map.get(self._selectedName(self.mcu_cb))

        self.py_classes = self.py_loader.reload()
        self.mcu_classes = self.mcu_loader.reload()

        self.setCommandItems()
        self._restoreSelection(self.py_cb, self.py_map, old_py)
        self._restoreSelection(self.mcu_cb, self.mcu_map, old_mcu)
        self.assignCommand()
        print("Finished reloading command modules.")
        logger.info("Reloaded commands.")

    def startPlay(self, *event: Any) -> None:
        """選択中のコマンドを開始する。

        選ぶこと（assignCommand）と COM 設定の写しだけをここで行い、
        開始の手順そのものは runner が持つ。写しは Start の直前に
        行う。生成時に渡すと、そのあと COM ポートを選び直しても
        古い値のまま残る。
        """
        self.assignCommand()
        if self.cur_command is not None:
            self._snapshotSerialConfig(self.cur_command)
        self.runner.request_start(self.cur_command, self.serial.sender)

    def stopPlay(self) -> None:
        """実行中のコマンドへ停止を要求する（手順は runner が持つ）。"""
        self.runner.request_stop()

    def stopPlayPost(self, token: int | None = None) -> None:
        """後方互換の入口。後始末は runner が受け付ける。

        実行中のコマンド（の _cleanup）が直接呼ぶ場合があるため
        名前だけ残す。新規のコードは runner.stop_post を使うこと。
        """
        self.runner.stop_post(token)

    def _stopPlayPostOnGui(self, token: int | None = None) -> None:
        """後方互換の入口。実体は runner が持つ。"""
        self.runner.post_on_gui(token)

    def ReloadCommandWithF5(self, *event: Any) -> None:
        # 実行中の可否は reloadCommands 側で判断する。入口ごとに条件を
        # 書くと、片方だけ直したときに挙動が食い違う。
        self.reloadCommands()

    def StartCommandWithF6(self, *event: Any) -> None:
        if self.runner.is_busy():
            print("Command is now working!")
            logger.debug("Command is now working!")
            return
        self.startPlay()

    def StopCommandWithEsc(self, *event: Any) -> None:
        # 停止処理の最中は受け付けない。二重に end() を呼ぶ意味が無い。
        if self.runner.state == "running":
            self.runner.request_stop()

    def togglePause(self) -> None:
        """実行中のコマンドを一時停止／再開する。

        停止（Stop）はコマンドを終わらせるため、次に動かすときは最初
        からやり直しになる。長い手順の途中で少し手を離したいだけの
        ときに使えないので、状態を保ったまま足止めする口を分けて置く。

        一時停止に対応しない種類ではボタン自体を disabled にしてある
        （_apply_runner_state）。ここへ来るのは F7 の打鍵だけ。
        """
        cmd = self.cur_command
        if cmd is None or not getattr(cmd, "alive", False):
            return
        if not callable(getattr(cmd, "togglePause", None)):
            # MCU コマンドなど、一時停止に対応しない種類
            print("This command does not support pause.")
            return
        paused = cmd.togglePause()
        self.pauseButton["text"] = "Resume" if paused else "Pause"
        self._paused = paused
        self._update_title()

    def PauseCommandWithF7(self, *event: Any) -> None:
        if self.runner.state == "running":
            self.togglePause()
