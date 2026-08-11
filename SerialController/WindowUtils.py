"""WindowUtils.py - Window.py から切り出した小道具.

ここに置くのは「画面の状態（self）を一切見ない関数」だけ。入力を与えると
出力が決まるだけの処理で、GUI と混ぜておく理由が無いものを集めている。

逆に言うと、self を見る必要が出た関数はここへ置いてはいけない。置いた
時点で Window を import し返すことになり、分離した意味が消える。
"""

from __future__ import annotations

import hashlib
import inspect
import os
import re
import subprocess
import tkinter as tk
import tkinter.ttk as ttk
from typing import Any

from loguru import logger


def listComPorts() -> list[tuple[str, str]]:
    """接続されているシリアルポートを (デバイス名, 表示名) で返す。

    pyserial 同梱の list_ports を使う。カメラ名の列挙と違って
    OS ごとの分岐が要らず、Windows / macOS / Linux で同じに書ける。
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        logger.error("pyserial の list_ports が見つかりません")
        return []

    ports = []
    for port in sorted(list_ports.comports(), key=lambda p: p.device):
        desc = (port.description or "").strip()
        # 説明が device と同じだと 'COM3: COM3' と重複するので省く
        if desc and desc != port.device:
            label = f"{port.device}: {desc}"
        else:
            label = port.device
        ports.append((port.device, label))
    return ports


def portToNumber(device: str) -> int:
    """'COM3' から 3 を取り出す。COM 形式でなければ 0。

    settings.ini の com_port(int) を保つためだけの変換。
    /dev/tty.usbserial-A5 のような名前から末尾の数字を取ると
    別のデバイスを指してしまうため、COM<数字> のときだけ変換する。
    """
    matched = re.fullmatch(r"COM(\d+)", (device or "").strip(), re.IGNORECASE)
    return int(matched.group(1)) if matched else 0


def deviceKey(device_path: str) -> str:
    """DevicePath から短い識別子を作る。

    DevicePath は100文字を超えることがあり、そのままでは一覧に出せない。
    USB のインスタンス ID を含めてハッシュし、先頭6桁だけを使う。
    """
    if not device_path:
        return ""
    digest = hashlib.md5(device_path.encode("utf-8", "ignore")).hexdigest()
    return digest[:6]


def cameraLabel(cam_id: int, name: str, key: str) -> str:
    """'0: ボード名 [a1b2c3]' の形にする。同名でも見分けられる。"""
    return f"{cam_id}: {name}" + (f" [{key}]" if key else "")


def selectCombobox(combobox: ttk.Combobox, value: str) -> None:
    """設定値が候補に無くても落ちないようにする。

    ウィジェットは触るが、触るのは引数で渡されたものだけ。画面全体の
    状態は見ないので、ここへ置いても Window へ依存しない。
    """
    values = list(combobox["values"])
    if value in values:
        combobox.current(values.index(value))
    else:
        logger.warning(f"'{value}' is not in {values}. fallback to the first item.")
        combobox.current(0)


def makeLogText(holder: Any) -> tk.Text:
    """ログ表示用の Text を作る（2つの欄で同じ設定を使う）。"""
    # wrap を既定の "char" のままにすると、長い行が来るたびに折り返し
    # 計算でレイアウトを取り直す。横スクロールは holder が
    # scrolltype="both" なので既にある。
    text = tk.Text(holder.container, wrap="none")
    text.config(blockcursor="true", height="10", insertunfocussed="none", maxundo="0")
    text.config(relief="flat", state="disabled", undo="false", width="50")
    text.pack(expand="true", fill="both", side="top")
    holder.add_child(text)
    holder.config(borderwidth="1", padding="1", relief="sunken")
    return text


def acceptsGuiArg(cmd_class: type) -> bool:
    """__init__ が gui（認識位置表示用）を受け取れるかを判定する。

    旧実装は except TypeError で握っていたため、コマンド内部で起きた
    TypeError まで「古い形式」と誤判定し引数1つで再生成していた。
    シグネチャを見て渡せる引数の数を先に決める。
    """
    try:
        params = inspect.signature(cmd_class.__init__).parameters
    except (TypeError, ValueError):
        return False

    # *args を持つなら何でも渡せる
    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params.values()):
        return True

    # self / cam を除いて、あと1つ以上受け取れるか
    positional = [
        p
        for p in params.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    return len(positional) >= 3


def openDirectory(directory: str, os_name: str) -> None:
    """OS のファイラでフォルダを開く。

    os_name は platform.system() の値。self を見ないよう引数で受け取る。
    無い場所を開こうとしても落とさず、警告だけ出して何もしない。
    """
    logger.debug(f"Open folder: '{directory}'")
    if not os.path.isdir(directory):
        logger.warning(f"Directory not found: '{directory}'")
        return
    if os_name == "Windows":
        subprocess.call(["explorer", directory])
    elif os_name == "Darwin":
        subprocess.run(["open", directory])
    else:
        subprocess.run(["xdg-open", directory])
