#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""launcher.py - Poke-Controller のプロファイルを選んで起動するランチャー.

複数の Switch を並列で自動化するとき、台ごとに設定を分けるための
プロファイル（settings.<name>.ini）を作成・複製・削除し、選んだものを
Window.py --profile <name> として起動する。

  引数あり : launcher.py --profile switch1  -> 画面を出さずそのまま起動
  引数なし : 一覧から選ぶ画面を表示する

起動するとランチャー自身は終了し、Window.py に入れ替わる。並列で使うときは
ランチャーをもう一度開いて別のプロファイルを起動する。
"""

from __future__ import annotations

import argparse
import configparser
import ctypes
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.simpledialog as tksimple
import tkinter.ttk as ttk
from typing import Any

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WINDOW_SCRIPT = os.path.join(APP_DIR, "Window.py")
SETTING_GLOB = os.path.join(APP_DIR, "settings*.ini")
DEFAULT_LABEL = "(既定 / settings.ini)"

# 起動中かどうかの目印。ランチャーは終了してしまうので、
# 動いていることは子プロセス側ではなくファイルで判断する。
LOCK_PREFIX = "pokecon"
LOCK_SUFFIX = ".lock"

# 起動直後の見張り時間。これを過ぎても生きていれば GUI が出たとみなす。
# 短すぎると重い環境で誤検知し、長すぎるとランチャーが閉じるまで待たされる。
STARTUP_GRACE_SEC = 2.5


class LaunchError(RuntimeError):
    """起動に失敗したときに、その理由を持って投げる。

    子プロセスは標準出力の行き先が無いため、失敗しても画面に何も出ない。
    拾った出力をこの例外に載せて、ランチャー側で見せるために使う。
    """


# ---------------------------------------------------------------------------
# プロファイル
# ---------------------------------------------------------------------------


def settings_path(profile: str) -> str:
    """プロファイル名から settings ファイルのパスを作る。"""
    if not profile:
        return os.path.join(APP_DIR, "settings.ini")
    return os.path.join(APP_DIR, f"settings.{profile}.ini")


def profile_from_path(path: str) -> str | None:
    """settings ファイルのパスからプロファイル名を取り出す。

    settings.ini.tmp のような書き込み途中のファイルや、無関係な
    mysettings.ini を拾わないよう厳密に判定する。
    """
    base = os.path.basename(path)
    if base == "settings.ini":
        return ""
    if base.startswith("settings.") and base.endswith(".ini"):
        return base[len("settings.") : -len(".ini")]
    return None


def read_summary(profile: str) -> dict:
    """一覧に出す項目を settings ファイルから読む。壊れていても落とさない。"""
    info = {"profile": profile, "com": "", "camera": "", "key": "", "fps": ""}
    parser = configparser.ConfigParser()
    # 大文字小文字を区別する（標準のドキュメントどおりの用法）。
    parser.optionxform = str  # type: ignore[assignment, method-assign]
    try:
        parser.read(settings_path(profile), encoding="utf-8")
    except (configparser.Error, OSError, ValueError, UnicodeDecodeError):
        # 文字化けした設定ファイルは UnicodeDecodeError（ValueError 系）で
        # 壊れる。ここで落とすと一覧そのものが出なくなるため、読めない旨
        # だけ出して次へ進む。
        info["com"] = "(読めません)"
        return info
    if not parser.has_section("General Setting"):
        return info
    general = parser["General Setting"]
    com_name = general.get("com_port_name", "").strip()
    com_num = general.get("com_port", "").strip()
    if com_name:
        info["com"] = com_name
    elif com_num and com_num != "0":
        info["com"] = f"COM{com_num}"
    info["camera"] = general.get("camera_id", "")
    info["key"] = general.get("camera_key", "")
    info["fps"] = general.get("fps", "")
    return info


def list_profiles() -> list[str]:
    """存在する settings ファイルからプロファイル名を集める。"""
    found = []
    for path in glob.glob(SETTING_GLOB):
        profile = profile_from_path(path)
        if profile is not None:
            found.append(profile)
    # 既定を先頭にし、残りは名前順
    found.sort(key=lambda p: (p != "", p.lower()))
    return found


def sanitize_profile(name: Any) -> str:
    """Settings.GuiSettings と同じ規則で正規化する。

    本体側と規則がずれると、ランチャーが作ったファイルを Window.py が
    別名だと判断して読み込めなくなる。可能なら本体の実装を借りる。
    """
    try:
        sys.path.insert(0, APP_DIR)
        from Settings import GuiSettings

        return GuiSettings.sanitize_profile(name)
    except Exception:
        # Settings.py を読めない場合でも最低限そろえる
        import hashlib
        import re

        if not name:
            return ""
        cleaned = re.sub(r"[^0-9A-Za-z_-]", "_", str(name)).strip("_")
        cleaned = re.sub(r"_+", "_", cleaned)
        if not cleaned:
            digest = hashlib.md5(str(name).encode("utf-8")).hexdigest()
            cleaned = "p" + digest[:8]
        return cleaned[:32]


# ---------------------------------------------------------------------------
# 起動中の判定
# ---------------------------------------------------------------------------


def lock_path(profile: str) -> str:
    if profile:
        name = f"{LOCK_PREFIX}.{profile}{LOCK_SUFFIX}"
    else:
        name = LOCK_PREFIX + LOCK_SUFFIX
    return os.path.join(APP_DIR, name)


def _process_alive(pid: int) -> bool:
    """PID のプロセスが生きているか。

    Windows の os.kill(pid, 0) は TerminateProcess を呼ぶため使えない。
    OpenProcess で存在だけを確認する。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 別ユーザーのプロセス。存在はしている
    return True


def running_pid(profile: str) -> int:
    """起動中なら PID を返す。落ちた後の目印は掃除して 0 を返す。"""
    path = lock_path(profile)
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as file:
            pid = int(json.load(file).get("pid", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        _remove_quietly(path)
        return 0
    if _process_alive(pid):
        return pid
    _remove_quietly(path)
    return 0


def write_lock(profile: str, pid: int) -> None:
    try:
        with open(lock_path(profile), "w", encoding="utf-8") as file:
            json.dump({"pid": pid, "started": time.time()}, file)
    except OSError:
        pass  # 目印が書けなくても起動自体は妨げない


def claim_lock(profile: str, pid: int) -> bool:
    """目印を原子的に確保する。二重起動の防止用。

    同時起動では先勝ちし、負けた側は False を返す。残っている目印が
    古いもの（プロセス死去）なら running_pid() が掃除するため、取り
    直しを1回だけ行う。上書き（write_lock）では同時起動を防げない。
    """
    path = lock_path(profile)
    payload = json.dumps({"pid": pid, "started": time.time()})
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if running_pid(profile):
                return False
            continue
        except OSError:
            return False
        try:
            os.write(fd, payload.encode("utf-8"))
        except OSError:
            try:
                os.close(fd)
            except OSError:
                pass
            return False
        os.close(fd)
        return True
    return False


def _drop_own_lock(profile: str, pid: int) -> None:
    """自分の目印だけを消す。他者の目印には触らない。

    起動に失敗したときの掃除用。無条件に消すと、同時に起動した
    別プロセスの目印まで消して二重起動を招く。
    """
    try:
        with open(lock_path(profile), encoding="utf-8") as file:
            if int(json.load(file).get("pid", 0)) != int(pid):
                return
    except (OSError, ValueError, json.JSONDecodeError):
        return
    _remove_quietly(lock_path(profile))


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 起動
# ---------------------------------------------------------------------------


def python_executable() -> str:
    """コンソールを出さない実行ファイルを優先して選ぶ。"""
    if os.name == "nt":
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if os.path.isfile(pythonw):
            return pythonw
    return sys.executable


def cleanup_boot_logs(keep_hours: float = 24.0) -> None:
    """起動時の一時ログのうち、古いものを消す。

    起動に成功したぶんは子プロセスがハンドルを握っているため、
    その場では消せない（Windows では削除できず PermissionError になる）。
    次にランチャーを開いたときにまとめて片付ける。
    """
    pattern = os.path.join(tempfile.gettempdir(), "pokecon_boot_*.log")
    limit = time.time() - keep_hours * 3600
    for path in glob.glob(pattern):
        try:
            if os.path.getmtime(path) < limit:
                os.remove(path)
        except OSError:
            pass  # 使用中や権限不足は諦めてよい


def _close_and_remove(handle: Any) -> None:
    """一時ファイルを閉じて消す。消せなくても起動処理は続ける。"""
    try:
        handle.close()
    except OSError:
        pass
    _remove_quietly(handle.name)


def _save_boot_log(profile: str, output: str, returncode: int) -> str:
    """起動に失敗したときの出力をファイルへ残し、その場所を返す。

    一時フォルダは場所が分かりにくく、掃除で消えることもあるため、
    アプリの隣の logs/ に置く。プロファイルごとに固定名にして、
    失敗のたびに上書きする（溜め込むと逆に見るべきものが分からない）。
    ここで失敗しても起動処理そのものは続けたいので握り潰す。
    """
    try:
        log_dir = os.path.join(APP_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        name = f"launch_error_{profile}.log" if profile else "launch_error.log"
        path = os.path.join(log_dir, name)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        header = (
            f"[{stamp}] profile={profile or '(既定)'} "
            f"returncode={returncode}\n"
            f"command={python_executable()} {WINDOW_SCRIPT}\n"
            "-" * 60 + "\n"
        )
        with open(path, "w", encoding="utf-8") as file:
            file.write(header)
            file.write(output or "(出力なし)\n")
        return path
    except OSError:
        return ""  # 保存できなくても起動処理は続ける


def launch(profile: str) -> int:
    """Window.py を別プロセスで起動し、その PID を返す。

    起動直後だけ見張り、すぐ落ちたらその出力を LaunchError にして返す。
    子は pythonw + 切り離しで動かすため標準出力の行き先が無く、
    import 失敗などで即死すると「何も起きない」ように見えてしまう。
    出力を一時ファイルへ逃がしておくと、その内容を原因として示せる。
    起動に成功したぶんのログは GUI のログ欄へ出るので捨ててよい。
    """
    if not os.path.isfile(WINDOW_SCRIPT):
        raise LaunchError(f"Window.py が見つかりません: {WINDOW_SCRIPT}")

    # 先に目印を確保する。同時起動では先勝ちし、負けた側はここで止まる。
    # 目印の中身は親の PID で仮置きし、子の PID が分かったら書き換える。
    mine = os.getpid()
    if not claim_lock(profile, mine):
        raise LaunchError(f"既に起動しています: {profile or '(既定)'}")

    command = [python_executable(), WINDOW_SCRIPT]
    if profile:
        command += ["--profile", profile]

    log_file = tempfile.NamedTemporaryFile(
        mode="w+",
        suffix=".log",
        prefix="pokecon_boot_",
        delete=False,
        encoding="utf-8",
        errors="replace",
    )
    kwargs: dict[str, Any] = {
        "cwd": APP_DIR,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        # 親が終了しても残るよう切り離す
        detached = 0x00000008  # DETACHED_PROCESS
        new_group = 0x00000200  # CREATE_NEW_PROCESS_GROUP
        kwargs["creationflags"] = detached | new_group
    else:
        kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(command, **kwargs)
    except OSError as error:
        _close_and_remove(log_file)
        _drop_own_lock(profile, mine)
        raise LaunchError(f"起動できませんでした: {error}") from error

    # 生きているか少しだけ見張る。ここを過ぎれば GUI が出ているとみなす
    deadline = time.monotonic() + STARTUP_GRACE_SEC
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        time.sleep(0.1)

    if process.poll() is None:
        # 起動できた。以降のログは GUI 側が受け持つ。
        log_file.close()  # 実体は次回起動時にまとめて掃除する
        write_lock(profile, process.pid)
        return process.pid

    # すぐ終わった = 失敗。何が出ていたかを読んで原因として返す
    log_file.flush()
    log_file.seek(0)
    output = log_file.read().strip()
    log_file.close()
    _drop_own_lock(profile, mine)

    # 失敗したログは消さずに残す。ダイアログは末尾数行しか出せないため、
    # 全文を見たいときに参照できる場所が要る。一時フォルダは分かりにくい
    # ので、アプリ隣の logs/ へ固定名で置き、場所も併せて知らせる。
    saved = _save_boot_log(profile, output, process.returncode)
    _remove_quietly(log_file.name)

    detail = output or f"終了コード {process.returncode} で終了しました。"
    if not output:
        # 出力が空 = 例外すら出ずに終了した。最も多いのは Window.py に
        # if __name__ == "__main__": が無く、import されただけで終わる場合。
        detail += (
            "\n出力が空でした。Window.py の末尾に "
            'if __name__ == "__main__": の節があるか確認してください。'
        )
    if saved:
        detail += f"\n\n詳細ログ: {saved}"
    raise LaunchError(detail)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


class LauncherWindow(ttk.Frame):
    """プロファイルを選んで起動する画面."""

    COLUMNS = ("profile", "com", "camera", "fps", "state")
    HEADINGS = {
        "profile": "プロファイル",
        "com": "COM ポート",
        "camera": "カメラ",
        "fps": "FPS",
        "state": "状態",
    }
    WIDTHS = {"profile": 170, "com": 140, "camera": 110, "fps": 60, "state": 110}

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=10)
        self.master = master
        self.master.title("Poke-Controller ランチャー")
        self.master.minsize(720, 340)
        self.grid(sticky=tk.NSEW)
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_widgets()
        self.refresh()

    # -- 画面 ---------------------------------------------------------------

    def _build_widgets(self) -> None:
        note = ttk.Label(
            self,
            text="起動する設定を選んでください。台ごとに分けておくと、"
            "同じPCで複数の Poke-Controller を同時に動かせます。",
            wraplength=680,
        )
        note.grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))

        self.tree = ttk.Treeview(
            self, columns=self.COLUMNS, show="headings", selectmode="browse"
        )
        for key in self.COLUMNS:
            self.tree.heading(key, text=self.HEADINGS[key])
            # tk.W / tk.CENTER はただの str のため Any で受ける。
            anchor: Any = tk.W if key in ("profile", "com") else tk.CENTER
            self.tree.column(key, width=self.WIDTHS[key], anchor=anchor)
        self.tree.grid(row=1, column=0, sticky=tk.NSEW)
        self.tree.bind("<Double-1>", self.on_start)

        bar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.grid(row=1, column=1, sticky=tk.NS)

        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=(10, 0))

        self.start_button = ttk.Button(buttons, text="起動", command=self.on_start)
        self.start_button.pack(side=tk.LEFT)
        ttk.Button(buttons, text="新規作成", command=self.on_create).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(buttons, text="複製", command=self.on_duplicate).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(buttons, text="削除", command=self.on_delete).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(buttons, text="再読込", command=self.refresh).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(buttons, text="閉じる", command=self.master.destroy).pack(
            side=tk.RIGHT
        )

        self.status = ttk.Label(self, text="")
        self.status.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))

    # -- 一覧 ---------------------------------------------------------------

    def refresh(self) -> None:
        """settings ファイルを読み直して一覧を作り直す。"""
        selected = self.selected_profile()
        self.tree.delete(*self.tree.get_children())

        profiles = list_profiles()
        if not profiles:
            # 1つも無いときは既定を作れるよう空行を出しておく
            profiles = [""]

        for profile in profiles:
            info = read_summary(profile)
            pid = running_pid(profile)
            camera = info["camera"]
            if info["key"]:
                camera = f"{camera} [{info['key']}]"
            self.tree.insert(
                "",
                tk.END,
                iid=profile or "\x00default",
                values=(
                    profile or DEFAULT_LABEL,
                    info["com"] or "-",
                    camera or "-",
                    info["fps"] or "-",
                    f"起動中 ({pid})" if pid else "停止",
                ),
            )

        children = self.tree.get_children()
        target: str | None = selected or "\x00default"
        if target not in children:
            target = children[0] if children else None
        if target:
            self.tree.selection_set(target)
            self.tree.focus(target)
        self.status.config(text=f"{len(children)} 件の設定が見つかりました。")

    def selected_profile(self) -> str | None:
        """選択中のプロファイル名。未選択なら None。既定は空文字。"""
        selection = self.tree.selection()
        if not selection:
            return None
        return "" if selection[0] == "\x00default" else selection[0]

    # -- 操作 ---------------------------------------------------------------

    def on_start(self, event: Any = None) -> None:
        profile = self.selected_profile()
        if profile is None:
            tkmsg.showinfo("確認", "起動する設定を選んでください。")
            return

        pid = running_pid(profile)
        if pid:
            message = (
                f"この設定は既に起動しています (PID {pid})。\n"
                "同じ設定を二重に起動すると、同じカメラとポートを"
                "奪い合って正しく動きません。\n\nそれでも起動しますか？"
            )
            if not tkmsg.askokcancel("既に起動しています", message):
                return

        try:
            new_pid = launch(profile)
        except LaunchError as error:
            # 子は標準出力の行き先が無いので、拾った内容をここで見せる。
            # トレースは長くなりがちなので末尾の要点だけに絞る。
            detail = str(error).strip().split("\n")
            tail = "\n".join(detail[-6:])
            tkmsg.showerror(
                "起動できませんでした",
                f"{profile or '既定'} を起動できませんでした。\n\n{tail}",
            )
            return

        label = profile or "既定"
        self.status.config(text=f"{label} を起動しました (PID {new_pid})")
        # 起動したらランチャーは役目を終える
        self.master.after(400, self.master.destroy)

    def on_create(self) -> None:
        name = tksimple.askstring(
            "新規作成",
            "プロファイル名を入力してください（例: switch1）",
            parent=self.master,
        )
        if name is None:
            return
        profile = sanitize_profile(name)
        if not profile:
            tkmsg.showwarning("作成できません", "使える文字が含まれていません。")
            return
        path = settings_path(profile)
        if os.path.exists(path):
            tkmsg.showwarning("作成できません", f"{profile} は既にあります。")
            return
        # 中身は空でよい。Window.py 側が起動時に既定値で埋める
        try:
            with open(path, "w", encoding="utf-8") as file:
                file.write("[General Setting]\n")
        except OSError as error:
            tkmsg.showerror("作成できませんでした", str(error))
            return
        self.refresh()
        self.status.config(
            text=f"{profile} を作成しました。起動すると既定値で初期化されます。"
        )

    def on_duplicate(self) -> None:
        source = self.selected_profile()
        if source is None:
            tkmsg.showinfo("確認", "複製元の設定を選んでください。")
            return
        if not os.path.isfile(settings_path(source)):
            tkmsg.showwarning("複製できません", "複製元の設定ファイルがありません。")
            return

        name = tksimple.askstring(
            "複製", "新しいプロファイル名を入力してください", parent=self.master
        )
        if name is None:
            return
        profile = sanitize_profile(name)
        if not profile:
            tkmsg.showwarning("複製できません", "使える文字が含まれていません。")
            return
        if os.path.exists(settings_path(profile)):
            tkmsg.showwarning("複製できません", f"{profile} は既にあります。")
            return

        try:
            with open(settings_path(source), encoding="utf-8") as file:
                body = file.read()
            with open(settings_path(profile), "w", encoding="utf-8") as file:
                file.write(body)
        except OSError as error:
            tkmsg.showerror("複製できませんでした", str(error))
            return

        self.refresh()
        self.status.config(
            text=f"{profile} を作成しました。カメラと COM ポートは選び直してください。"
        )

    def on_delete(self) -> None:
        profile = self.selected_profile()
        if profile is None:
            tkmsg.showinfo("確認", "削除する設定を選んでください。")
            return
        if not profile:
            tkmsg.showwarning("削除できません", "既定の設定は削除できません。")
            return
        if running_pid(profile):
            tkmsg.showwarning("削除できません", "起動中の設定は削除できません。")
            return
        if not tkmsg.askokcancel("確認", f"{profile} の設定を削除しますか？"):
            return
        _remove_quietly(settings_path(profile))
        _remove_quietly(lock_path(profile))
        self.refresh()
        self.status.config(text=f"{profile} を削除しました。")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Poke-Controller ランチャー")
    parser.add_argument(
        "--profile",
        default=None,
        help="指定するとその設定で即座に起動する（画面を出さない）",
    )
    parser.add_argument(
        "--list", action="store_true", help="設定の一覧を表示して終了する"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="既に起動していても構わず起動する",
    )
    args = parser.parse_args(argv)

    cleanup_boot_logs()  # 前回までの起動ログの残骸を片付ける

    if args.list:
        for profile in list_profiles():
            info = read_summary(profile)
            pid = running_pid(profile)
            state = f"起動中({pid})" if pid else "停止"
            name = profile or "(既定)"
            print(
                f"{name:<20} COM={info['com'] or '-':<14} "
                f"camera={info['camera'] or '-':<4} {state}"
            )
        return 0

    if args.profile is not None:
        profile = sanitize_profile(args.profile)
        pid = running_pid(profile)
        if pid and not args.force:
            # 同じ設定を二重に起動すると、同じカメラとポートを奪い合う
            print(f"既に起動しています (PID {pid}): {profile or '既定'}")
            print("二重に起動する場合は --force を付けてください。")
            return 1
        print(f"起動します: {profile or '既定'}")
        try:
            launch(profile)
        except LaunchError as error:
            print(f"起動に失敗しました:\n{error}", file=sys.stderr)
            return 1
        return 0

    root = tk.Tk()
    LauncherWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
