#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配布スクリプトの導入・削除の対話手順（tkinter）。

実ファイル操作は services.script_pack が持ち、ここでは選ぶ・確かめる・
知らせるだけにする。導入後は呼び出し側の reload_commands（既存の
reloadCommands を渡す）で一覧を作り直す。実行中の可否は呼び出し側の
is_busy で見る（reloadCommands 側も塞いでいるが、入口でも断つ）。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
from collections.abc import Callable
from tkinter import filedialog
from typing import Any

from services import script_pack


def _summary_text(manifest: Any, warnings: list[str]) -> str:
    """確認ダイアログに出す要点。注意は最大5件まで出す。"""
    lines = [
        f"名前: {manifest.name}",
        f"版: {manifest.version}",
        f"作者: {manifest.author}",
        f"説明: {manifest.description}",
        f"本体: PythonCommands/{manifest.entry}",
        f"画像: {len(manifest.templates)}件",
    ]
    for warning in warnings[:5]:
        lines.append(f"注意: {warning}")
    if len(warnings) > 5:
        lines.append(f"注意: ほか {len(warnings) - 5} 件")
    return "\n".join(lines)


def install_script_zip(
    root: tk.Misc,
    app_dir: str,
    *,
    is_busy: Callable[[], bool],
    reload_commands: Callable[[], None],
) -> None:
    """配布zipを選んで導入する。終わったら一覧を作り直す。"""
    if is_busy():
        print("実行中はスクリプトを導入できません")
        tkmsg.showwarning("導入", "実行中はスクリプトを導入できません", parent=root)
        return
    zippath = filedialog.askopenfilename(
        parent=root,
        title="配布zipを選ぶ",
        filetypes=[("PokeCon配布", "*.zip"), ("すべて", "*.*")],
    )
    if not zippath:
        return
    res = script_pack.install_zip(app_dir, zippath)
    if res.status == "failed" or res.manifest is None:
        print(f"導入できません: {res.message}")
        tkmsg.showerror("導入失敗", res.message, parent=root)
        return
    if res.status == "confirm-overwrite":
        body = (
            f"{res.manifest.name} は版 {res.current_version} が導入済みです。\n"
            f"版 {res.manifest.version} に更新します。上書きしてよいですか。\n\n"
            + _summary_text(res.manifest, res.warnings)
        )
        if not tkmsg.askyesno("上書き確認", body, parent=root):
            print("導入を取り消しました")
            return
        res = script_pack.install_zip(app_dir, zippath, allow_overwrite=True)
        if res.status != "installed" or res.manifest is None:
            print(f"導入できません: {res.message}")
            tkmsg.showerror("導入失敗", res.message, parent=root)
            return
    reload_commands()
    done = res.message
    if res.backup_rel is not None:
        done += f"\n上書き前の写し: {res.backup_rel}"
    for warning in res.warnings[:5]:
        done += f"\n注意: {warning}"
    if len(res.warnings) > 5:
        done += f"\n注意: ほか {len(res.warnings) - 5} 件"
    print(done)
    tkmsg.showinfo("導入完了", done, parent=root)


def uninstall_script_dialog(
    root: tk.Misc,
    app_dir: str,
    *,
    is_busy: Callable[[], bool],
    reload_commands: Callable[[], None],
) -> None:
    """導入済みの一覧から選んで削除する小窓を開く。"""
    if is_busy():
        print("実行中はスクリプトを削除できません")
        tkmsg.showwarning("削除", "実行中はスクリプトを削除できません", parent=root)
        return
    if not script_pack.list_installed(app_dir):
        tkmsg.showinfo("削除", "導入済みパッケージはありません", parent=root)
        return
    win = tk.Toplevel(root)
    win.title("スクリプトの削除")
    win.transient(root)  # type: ignore[call-overload]  # CommandPalette.py と同じ理由（Misc 許容の stub 不整合）
    win.grab_set()
    win.resizable(False, False)
    records_box = tk.Listbox(win, width=48, height=10)
    records_box.pack(padx=10, pady=10)

    def refresh() -> None:
        records_box.delete(0, tk.END)
        for rec in script_pack.list_installed(app_dir):
            records_box.insert(tk.END, f"{rec.name} 版{rec.version}")

    def do_delete() -> None:
        selected = records_box.curselection()
        if not selected:
            return
        label = str(records_box.get(selected[0]))
        name = label.split(" ")[0]
        if not tkmsg.askyesno(
            "削除確認", f"{label} を削除します。よろしいですか。", parent=win
        ):
            return
        res = script_pack.uninstall_package(app_dir, name)
        if res.status != "removed":
            print(f"削除できません: {res.message}")
            tkmsg.showerror("削除失敗", res.message, parent=win)
            return
        print(res.message)
        reload_commands()
        refresh()

    buttons = ttk.Frame(win)
    buttons.pack(fill="x", padx=10, pady=(0, 10))
    ttk.Button(buttons, text="削除", command=do_delete).pack(side="left")
    ttk.Button(buttons, text="閉じる", command=win.destroy).pack(side="right")
    refresh()
    root.wait_window(win)
