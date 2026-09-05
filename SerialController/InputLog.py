#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""InputLog.py - シリアル送信行から入力イベントを起こしてログに出す.

Sender.writeRow() が送る1行（Keys.SendFormat.convert2str の出力）を feed() に
渡すだけで、前回の状態との差分から press / release を組み立て、押していた時間を
添えて出力する。判定材料は「実際に送った1行」だけなので、GUI・キーボード・
Python コマンドのどの経路の操作でも同じように記録できる。

表示はテンプレート文字列で組み替える。リネームツールの yyyy/mm/dd と同じ発想で、
{name} や {dur} といった差し込み欄を並べ替えれば書式が変わる。
角括弧 [...] で囲んだ範囲は、中の差し込み欄が空のときだけ丸ごと消える。
press には押下時間が無いので、[...] で括れば1つの書式で press/release を賄える。
角括弧そのものを出したいときは [[ ]] と二重にする。
"""

from __future__ import annotations

import datetime
import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# 送信フォーマットの定義（Keys.SendFormat.convert2str と対で保つ）
# ---------------------------------------------------------------------------

BUTTON_NAMES = (
    "Button.Y",
    "Button.B",
    "Button.A",
    "Button.X",
    "Button.L",
    "Button.R",
    "Button.ZL",
    "Button.ZR",
    "Button.MINUS",
    "Button.PLUS",
    "Button.LCLICK",
    "Button.RCLICK",
    "Button.HOME",
    "Button.CAPTURE",
)

HAT_NAMES = (
    "Hat.TOP",
    "Hat.TOP_RIGHT",
    "Hat.RIGHT",
    "Hat.BTM_RIGHT",
    "Hat.BTM",
    "Hat.BTM_LEFT",
    "Hat.LEFT",
    "Hat.TOP_LEFT",
    "Hat.CENTER",
)

HAT_CENTER = 8
NEUTRAL = 128
BUTTON_SHIFT = 2  # 下位2bitは L/R スティックの変更フラグ
FLAG_R_STICK = 0x1
FLAG_L_STICK = 0x2

STICK_DEADZONE = 0.05  # これ以下の倒し量は中立とみなす（0.0〜1.0）
CHANGE_MIN_DEG = 15.0  # 傾けたままの向き変更をイベントにする最小角度

# 向きが同じでも倒し量がこれだけ変われば記録する（0.0〜1.0）。
# 角度だけを見ていると「真上に浅く倒す→深く倒す」が1行も出ない。
# 0 にすると倒し量の変化では記録しない（従来の挙動）。
CHANGE_MIN_MAG = 0.25

# スティックの向きを Keys.Direction の名前へ戻すための対応表。
# 角度は Keys.py の Direction.UP = 90 などと同じ定義に揃える。
DIRECTION_ANGLES = (
    ("RIGHT", 0.0),
    ("UP_RIGHT", 45.0),
    ("UP", 90.0),
    ("UP_LEFT", 135.0),
    ("LEFT", 180.0),
    ("DOWN_LEFT", -135.0),
    ("DOWN", -90.0),
    ("DOWN_RIGHT", -45.0),
)

# 8方向のいずれかとみなす許容差(度)。45度間隔の半分なので、どの角度も
# 必ずどれか1つに寄る。これを超える中間角度は名前を付けず座標で出す。
DIRECTION_SNAP_DEG = 22.5

# これ以上倒していれば「目一杯倒した」とみなす。Keys 側の既定は 1.0 で、
# 実際の送信値は丸めの都合で 0.99 程度になるため少し余裕を持たせる。
DIRECTION_FULL_MAG = 0.95

# 倒したまま向きを変え続けたとき、「回した」とみなす累積回転量(度)。
# 270度あれば、ぐるりと回す意図だったと判断してよい。これ未満は
# 「倒しながら向きを直した」程度なので、最後の向きだけを見る。
ROTATE_MIN_DEG = 270.0


# ---------------------------------------------------------------------------
# イベント
# ---------------------------------------------------------------------------

# 実時刻の型。def 行が長くなりすぎるので短い別名を用意する
# （以前は wall: float と書いてあったが実際は datetime）。
WallTime = datetime.datetime


@dataclass
class InputEvent:
    """1つの操作。press / release / change のいずれか。"""

    action: str  # PRESS / RELEASE / CHANGE
    kind: str  # button / hat / stick
    name: str  # Button.A / Hat.TOP / Stick.LEFT
    at: float  # time.perf_counter() の値
    wall: WallTime  # 実時刻
    duration: float | None = None  # RELEASE のときだけ入る（秒）
    x: int | None = None
    y: int | None = None
    deg: float | None = None
    mag: float | None = None
    raw: str = ""
    held: tuple = ()
    # 倒している間の軌跡。スティックの RELEASE でだけ入る。
    from_deg: float | None = None  # 倒し始めた向き
    max_mag: float | None = None  # その間の最大の倒し量
    turn: float | None = None  # 累積の回転量（度。+が反時計回り）
    moves: int = 0  # 向きが変わった回数


# ---------------------------------------------------------------------------
# 書式（テンプレート）
# ---------------------------------------------------------------------------

PRESETS = {
    "simple": "{time} {mark} {name}[ {dir}][ ({dur})][ ※{turn}]",
    "detail": (
        "{time:HH:mm:ss.fff} {event:<7} {name:<14}"
        "[ {dir}][ dur={dur:>7}][ deg={deg:6.1f} mag={mag:.2f}]"
        "[ held={held}][ ※{turn}]"
    ),
    "compact": "{time:mm:ss.fff} {mark} {short}[ +{dur:ms}ms]",
    "csv": (
        "{time:yyyy/MM/dd HH:mm:ss.fff},{event},{kind},{name},"
        "{dir},{dur:s.3},{deg:.1f},{turn}"
    ),
    "command": "self.press({cmd}[, duration={dur:s.2}])[  # {turn}]",
    "command_raw": "self.press({raw_cmd}[, duration={dur:s.2}])[  # {turn}]",
    "raw": "{time} {event:<7} {name} <- {raw}",
}

# プリセットごとの既定の対象イベント。press を組み立てる書式は
# 「離した時に1行」が自然なので絞る。
PRESET_ACTIONS = {"command": ("RELEASE",), "command_raw": ("RELEASE",)}

# 設定画面に出す説明。書式そのものは PRESETS が持つので、ここは用途だけ。
PRESET_LABELS = {
    "simple": "標準（時刻・記号・ボタン名・押下時間）",
    "detail": "詳細（イベント種別・角度・同時押しまで出す）",
    "compact": "簡易（短い名前とミリ秒だけ）",
    "csv": "CSV（表計算へ貼る用。カンマ区切り）",
    "command": "コマンド（self.press(...)。方向は Direction.UP に丸める）",
    "command_raw": "コマンド・座標そのまま（倒した角度と量を丸めない）",
    "raw": "生データ（送信した1行を併記。不具合調査用）",
}

# 差し込み欄の一覧。設定画面のヘルプに出す（欄名, 説明）。
# resolve() が解釈する名前と対で保つこと。
FIELD_HELP = (
    ("{time}", "時刻。{time:HH:mm:ss.fff} のように書式を付けられる"),
    ("{date}", "日付。既定は yyyy/MM/dd"),
    ("{elapsed}", "起動してからの経過時間"),
    ("{mark}", "押下 v / 解放 ^ / 変化 ~ の記号"),
    ("{event}", "PRESS / RELEASE / CHANGE"),
    ("{name}", "Button.A のような完全な名前"),
    ("{short}", "A のような短い名前。スティックは L:UP_RIGHT の形"),
    ("{dir}", "スティックを倒した向き。Direction.UP の形。ボタンでは空"),
    ("{cmd}", "press に渡せる名前。ボタンはボタン名、スティックは向き"),
    ("{raw_dir}", "倒した向きを丸めずに Direction(Stick.LEFT, 27, 0.09) 形式で"),
    ("{raw_cmd}", "{cmd} の丸めない版。実際の角度と倒し量をそのまま出す"),
    ("{turn}", "スティックを回した操作の説明。回していなければ空"),
    ("{deg2}", "倒し始めたときの向き（度）"),
    ("{kind}", "button / hat / stick の別"),
    ("{dur}", "押していた時間。解放時のみ。{dur:ms} で数値だけ"),
    ("{held}", "そのとき押しっぱなしのボタン一覧"),
    ("{count}", "同時に押している数"),
    ("{deg}", "スティックの角度（度）"),
    ("{mag}", "スティックの倒し量（0.0〜1.0）"),
    ("{x}", "スティックの X（0〜255）"),
    ("{y}", "スティックの Y（0〜255）"),
    ("{raw}", "実際に送った1行"),
)

DEFAULT_FORMAT = PRESETS["simple"]

MARKS = {"PRESS": "v", "RELEASE": "^", "CHANGE": "~"}

# --- 流量の制御 -------------------------------------------------------------
# 入力ログは1操作で press と release の2行が出る。連打やスティック操作では
# 毎秒数十行になり、同じ欄に出る他のログ（コマンドの print・システム
# メッセージ）が押し流されて読めなくなる。以下で「出す行そのもの」を減らす。

# 同じ操作がこの間隔以内に「連続して」繰り返されたら1行にまとめる（秒）。
# 間に別の操作が入った時点で集約は打ち切る（押した順を崩さないため）。
# 0 にすると集約しない。
REPEAT_WINDOW = 0.5

# まとめた行を出すまでの猶予（秒）。この時間だけ次の同じ操作を待ってから
# 「A x12」の形で出す。短すぎると集約されず、長すぎると表示が遅れて
# 「押したのにログが出ない」と見える。
REPEAT_FLUSH = 0.4

# 1秒あたりに出してよい行数の上限。超えた分は捨て、落ち着いてから
# 「... N行省略 ...」と1行で知らせる。GUI が固まるのを防ぐ最後の砦。
# 左右への高速往復は毎秒100行に達することがあり、40では6割が捨てられて
# 「正確に記録できない」状態になっていた。描画は200ms間隔でまとめて
# 行うので、200行/秒でも1回あたり40行と現実的な量に収まる。
MAX_LINES_PER_SEC = 200

_TIME_MAP = {
    "yyyy": "%Y",
    "yy": "%y",
    "MM": "%m",
    "dd": "%d",
    "HH": "%H",
    "mm": "%M",
    "ss": "%S",
}
_TIME_RE = re.compile("yyyy|yy|MM|dd|HH|mm|ss")
_FIELD_RE = re.compile(r"\{(\w+)(?::([^{}\[\]]*))?\}")
_GROUP_RE = re.compile(r"\[([^\[\]]*)\]")

_ESC_OPEN = "\x00"  # [[ の退避先。省略ブロックの解析から外すために使う
_ESC_CLOSE = "\x01"

DEFAULT_TIME_PATTERN = "HH:mm:ss.fff"
DEFAULT_DATE_PATTERN = "yyyy/MM/dd"


def format_time(wall: WallTime, pattern: str) -> str:
    """yyyy/MM/dd HH:mm:ss.fff 形式のパターンを実際の文字列にする。"""
    if not pattern:
        pattern = DEFAULT_TIME_PATTERN
    if "%" in pattern:
        return wall.strftime(pattern)
    # fff は strftime に無いので、先に実数字へ置き換えてしまう
    pattern = pattern.replace("fff", f"{wall.microsecond // 1000:03d}")
    return wall.strftime(_TIME_RE.sub(lambda m: _TIME_MAP[m.group(0)], pattern))


_ALIGN_RE = re.compile(r"^(.?[<>^])?(\d+)$")


def _event_mag(ev: InputEvent) -> float | None:
    """その行に書くべき倒し量を選ぶ。

    RELEASE は離した瞬間の座標が中立なので、倒していた間の最大を使う。
    PRESS / CHANGE は「その瞬間」の値がそのまま入っているのでそちらを
    使う。ここを一律に max_mag にすると、一度深く倒してから浅くした
    直後の CHANGE が、実際より深い値で出てしまう。
    """
    if ev.action == "RELEASE" and ev.max_mag is not None:
        return ev.max_mag
    return ev.mag if ev.mag is not None else ev.max_mag


def snap_direction(deg: float | None) -> str:
    """角度を8方向のうち最も近いものの名前へ丸める。

    向きの判定は「表示名を作る」「集約の単位を決める」の2か所で要る。
    同じ計算を両方に書くと、丸め方を変えたとき片方だけ直す事故が起きる
    ので、ここへ集約する。返すのは RIGHT / UP_LEFT のような素の名前で、
    Direction. や R_ の接頭辞は呼び出し側が付ける。
    """
    if deg is None:
        return ""
    best, gap = "", 360.0
    for label, base in DIRECTION_ANGLES:
        diff = abs((deg - base + 180.0) % 360.0 - 180.0)
        if diff < gap:
            best, gap = label, diff
    return best


def direction_gap(deg: float | None) -> float:
    """snap_direction が選んだ向きから、実際の角度が何度ずれているか。"""
    if deg is None:
        return 360.0
    label = snap_direction(deg)
    for name, base in DIRECTION_ANGLES:
        if name == label:
            return abs((deg - base + 180.0) % 360.0 - 180.0)
    return 360.0


def direction_raw(ev: InputEvent) -> str:
    """スティックの向きを、8方向へ寄せずに Direction(...) の形で返す。

    direction_name() は「目一杯・8方向に近い」ときだけ Direction.UP の
    ような名前へ丸める。読むぶんには分かりやすいが、実際に倒した角度と
    倒し量は失われる。手ぶれや微妙な傾きまで含めて、操作をそのまま
    再現したいときはこちらを使う。

        Direction.UP
          → Direction(Stick.LEFT, 90, 1.00)

    どちらも Keys.Direction の引数そのままなので、貼れば動く。
    書式の {raw_dir} / {raw_cmd} から呼ばれる。
    """
    if ev.kind != "stick":
        return ""
    mag = _event_mag(ev)
    if ev.deg is None or not mag:
        return ""
    return f"Direction({ev.name}, {ev.deg:.0f}, {mag:.2f})"


def direction_name(ev: InputEvent) -> str:
    """スティックの向きを Keys.Direction の書き方へ戻す。

    Stick.LEFT / Stick.RIGHT は「どちらのスティックか」でしかなく、
    どちらへ倒したかを持たない。そのままログに出しても情報が無く、
    self.press(Stick.LEFT) は Keys に無い書き方なので貼っても動かない。
    実際に押せる名前（Direction.UP など）へ直す。

    離した瞬間の座標は中立(128,128)なので、その値をそのまま使うと
    向きが失われる。RELEASE では「離す直前に倒していた向き」を
    _diff_stick が入れてくるので、そちらを見る。

    8方向のどれにも寄らない角度や、途中までしか倒していない場合は、
    Direction(Stick.LEFT, 30, 0.6) の形で組み立てて返す。こちらも
    Keys.Direction の引数そのままなので、コピーすれば動く。
    """
    if ev.kind != "stick":
        return ""
    # 倒し量の選び方は _event_mag に集約している（RELEASE は最大値、
    # PRESS / CHANGE はその瞬間の値）。
    mag = _event_mag(ev)
    if ev.deg is None or not mag:
        return ""
    # 丸め方は snap_direction / direction_gap に集約している。
    prefix = "R_" if ev.name.endswith("RIGHT") else ""
    label = snap_direction(ev.deg)
    if direction_gap(ev.deg) <= DIRECTION_SNAP_DEG and mag >= DIRECTION_FULL_MAG:
        return f"Direction.{prefix}{label}"

    # 8方向に寄らない中間や半倒し。丸めない形と同じなので使い回す。
    return direction_raw(ev)


def short_name(ev: InputEvent) -> str:
    """短い表示名。スティックは「どちら側＋倒した向き」にする。

    単に name の末尾を取ると Stick.LEFT が "LEFT" になり、左スティックを
    右上へ倒しても "LEFT" と出る。左へ倒したと読めてしまい、短い書式
    （compact）ではこれが唯一の手がかりなので誤読が避けられない。

        Stick.LEFT を右上へ  → L:UP_RIGHT
        Stick.RIGHT を上へ   → R:UP
        倒していない/不明     → L（向きが取れないときは側だけ）

    ボタンと十字キーは従来どおり末尾を返す（Button.A → A）。
    """
    tail = ev.name.split(".")[-1]
    if ev.kind != "stick":
        return tail
    side = "R" if ev.name.endswith("RIGHT") else "L"
    label = snap_direction(ev.deg) if _event_mag(ev) else ""
    return f"{side}:{label}" if label else side


def is_rotation(ev: InputEvent) -> bool:
    """倒したままぐるりと回した操作かどうか。"""
    return bool(ev.turn is not None and abs(ev.turn) >= ROTATE_MIN_DEG)


def rotation_text(ev: InputEvent) -> str:
    """回した操作を1行の説明にする。回していなければ空。

    1周回す操作は、どの Direction 1つでも表せない。press を並べて
    再現するものでもないので、何をしたのかが分かる説明を残す。
    """
    if not is_rotation(ev) or ev.turn is None:
        return ""
    turns = abs(ev.turn) / 360.0
    way = "反時計回り" if ev.turn > 0 else "時計回り"
    return f"{ev.name} を{way}に {turns:.1f}周（{ev.from_deg:.0f}°→{ev.deg:.0f}°）"


def command_target(ev: InputEvent) -> str:
    """press に渡せる形の名前を返す（ボタン・十字キーはそのまま）。"""
    if ev.kind == "stick":
        return direction_name(ev)
    return ev.name


def command_raw_target(ev: InputEvent) -> str:
    """press に渡せる形の名前を返す（スティックは丸めずに座標で）。

    command_target() との違いはスティックだけ。8方向の名前へ寄せず、
    実際に倒した角度と倒し量をそのまま Direction(...) で出す。
    """
    if ev.kind == "stick":
        return direction_raw(ev)
    return ev.name


def format_duration(seconds: float | None, spec: str) -> str:
    """押下時間を書式に従って文字列にする。None なら空文字。

    spec は ms / s / s.2 / ms.1 / auto のほか、'>7' のような桁揃えだけの指定も
    受け付ける（その場合は auto の結果を指定幅に寄せる）。
    """
    if seconds is None:
        return ""
    align = _ALIGN_RE.match(spec) if spec else None
    if align:  # 桁揃えのみ。中身は auto で作る
        return format(format_duration(seconds, ""), spec)
    if not spec or spec == "auto":
        if seconds < 1.0:
            return f"{seconds * 1000:.0f}ms"
        return f"{seconds:.2f}s"
    if spec == "ms":
        return f"{seconds * 1000:.0f}"
    if spec == "s":
        return f"{seconds:.3f}"
    if spec.startswith("s."):
        try:
            return "{:.{}f}".format(seconds, int(spec[2:]))
        except (TypeError, ValueError):
            return f"{seconds:.2f}s"
    if spec.startswith("ms."):
        try:
            return "{:.{}f}".format(seconds * 1000, int(spec[3:]))
        except (TypeError, ValueError):
            return f"{seconds * 1000:.0f}ms"
    try:
        return format(seconds, spec)
    except (TypeError, ValueError):
        return str(seconds)


def _unescape(text: str) -> str:
    """退避しておいた [[ ]] を本来の角括弧へ戻す。"""
    return text.replace(_ESC_OPEN, "[").replace(_ESC_CLOSE, "]")


def _fmt(value: Any, spec: str) -> str:
    """None は空文字、それ以外は書式指定に従って文字列化する。"""
    if value is None:
        return ""
    return format(value, spec) if spec else str(value)


class LogFormatter:
    """テンプレート文字列を1度だけ解析し、イベントを行文字列にする。"""

    def __init__(self, template: str = DEFAULT_FORMAT) -> None:
        self.set_template(template)

    def set_template(self, template: str) -> None:
        """プリセット名またはテンプレート文字列を設定する。"""
        self.template = PRESETS.get(template, template)
        escaped = self.template.replace("[[", _ESC_OPEN).replace("]]", _ESC_CLOSE)
        self._segments = self._compile(escaped)

    def _compile(self, template: str) -> list:
        """[ ] の省略ブロックと { } の差し込み欄に分解する。"""
        segments = []
        pos = 0
        for group in _GROUP_RE.finditer(template):
            if group.start() > pos:
                head = template[pos : group.start()]
                segments.append((False, self._split_fields(head)))
            segments.append((True, self._split_fields(group.group(1))))
            pos = group.end()
        if pos < len(template):
            segments.append((False, self._split_fields(template[pos:])))
        return segments

    @staticmethod
    def _split_fields(text: str) -> list:
        """文字列を「素の文字」と「(欄名, 書式)」の並びにする。"""
        parts = []
        pos = 0
        for m in _FIELD_RE.finditer(text):
            if m.start() > pos:
                parts.append(_unescape(text[pos : m.start()]))
            parts.append((m.group(1), m.group(2) or ""))
            pos = m.end()
        if pos < len(text):
            parts.append(_unescape(text[pos:]))
        return parts

    def format(self, ev: InputEvent, started: float) -> str:
        """イベント1件をテンプレートに流し込む。"""
        out = []
        for optional, parts in self._segments:
            text, filled, has_field = [], False, False
            for part in parts:
                if isinstance(part, str):
                    text.append(part)
                    continue
                has_field = True
                value = self.resolve(ev, started, part[0], part[1])
                if value:
                    filled = True
                text.append(value)
            if optional and has_field and not filled:
                continue  # 中身が空の [ ] ブロックは丸ごと落とす
            out.append("".join(text))
        return "".join(out)

    @staticmethod
    def resolve(ev: InputEvent, started: float, name: str, spec: str) -> str:
        """差し込み欄1つを文字列にする。未知の欄名はそのまま返して気づけるようにする。"""
        if name == "time":
            return format_time(ev.wall, spec or DEFAULT_TIME_PATTERN)
        if name == "date":
            return format_time(ev.wall, spec or DEFAULT_DATE_PATTERN)
        if name == "elapsed":
            return format_duration(ev.at - started, spec)
        if name in ("dur", "duration"):
            return format_duration(ev.duration, spec)
        if name in ("event", "action"):
            return _fmt(ev.action, spec)
        if name == "mark":
            return _fmt(MARKS.get(ev.action, "?"), spec)
        if name == "name":
            return _fmt(ev.name, spec)
        if name == "short":
            return _fmt(short_name(ev), spec)
        if name == "dir":
            return _fmt(direction_name(ev) or None, spec)
        if name == "cmd":
            return _fmt(command_target(ev) or None, spec)
        if name == "raw_dir":
            return _fmt(direction_raw(ev) or None, spec)
        if name == "raw_cmd":
            return _fmt(command_raw_target(ev) or None, spec)
        if name == "turn":
            return _fmt(rotation_text(ev) or None, spec)
        if name == "deg2":
            return _fmt(ev.from_deg, spec)
        if name == "kind":
            return _fmt(ev.kind, spec)
        if name == "raw":
            return _fmt(ev.raw, spec)
        if name == "held":
            return _fmt(", ".join(ev.held) if ev.held else None, spec)
        if name == "count":
            return _fmt(len(ev.held), spec)
        if name == "deg":
            return _fmt(ev.deg, spec)
        if name == "mag":
            return _fmt(ev.mag, spec)
        if name == "x":
            return _fmt(ev.x, spec)
        if name == "y":
            return _fmt(ev.y, spec)
        return "{" + name + "}"


# ---------------------------------------------------------------------------
# 書式の下見（設定画面用）
# ---------------------------------------------------------------------------


def sample_events() -> list[InputEvent]:
    """書式の見本を作るための、決め打ちのイベント列を返す。

    設定画面で「この書式だとこう出る」を見せるために使う。実機の操作を
    待たずに確かめられるよう、押下・解放・十字キー・スティックの4種を
    ひと通り含める。時刻に実時刻を使うのは、{time} の書式指定をその場で
    試せるようにするため。
    """
    now = time.perf_counter()
    wall = datetime.datetime.now()
    raw = "0x0004 8 80 80 80 80"
    return [
        InputEvent(
            "PRESS", "button", "Button.A", now, wall, raw=raw, held=("Button.A",)
        ),
        InputEvent(
            "RELEASE",
            "button",
            "Button.A",
            now + 0.082,
            wall,
            duration=0.082,
            raw=raw,
            held=(),
        ),
        InputEvent(
            "PRESS", "hat", "Hat.TOP", now + 0.20, wall, raw=raw, held=("Hat.TOP",)
        ),
        InputEvent(
            "RELEASE",
            "stick",
            "Stick.LEFT",
            now + 1.35,
            wall,
            duration=1.35,
            x=128,
            y=128,
            deg=90.0,
            mag=1.0,
            max_mag=1.0,
            from_deg=90.0,
            turn=0.0,
            raw=raw,
            held=(),
        ),
        # マウスで1周回した例。Direction 1つでは表せない操作
        InputEvent(
            "RELEASE",
            "stick",
            "Stick.LEFT",
            now + 2.10,
            wall,
            duration=0.71,
            x=128,
            y=128,
            deg=177.0,
            mag=1.0,
            max_mag=1.0,
            from_deg=15.0,
            turn=523.0,
            moves=15,
            raw=raw,
            held=(),
        ),
    ]


def preview_lines(template: str, actions: Any = None) -> list[str]:
    """テンプレートを見本イベントに当てて、出力される行を返す。

    設定画面はこれを呼ぶだけでよい。書式が壊れていても例外を投げず、
    その旨を1行返す。入力の途中は必ず壊れた状態を通るので、打つたびに
    例外が飛ぶと使いものにならない。
    """
    try:
        formatter = LogFormatter(template)
        started = time.perf_counter()
        wanted = tuple(actions) if actions else None
        return [
            formatter.format(ev, started)
            for ev in sample_events()
            if wanted is None or ev.action in wanted
        ]
    except Exception as e:
        return [f"(この書式は使えません: {e})"]


# ---------------------------------------------------------------------------
# 差分の検出とログ出力
# ---------------------------------------------------------------------------


class InputLogger:
    """送信行の差分から press / release を組み立ててログへ流す。"""

    def __init__(
        self,
        template: str = DEFAULT_FORMAT,
        emit: Callable[[str], None] | None = None,
        enabled: bool = True,
        log_stick_change: bool = False,
        deadzone: float = STICK_DEADZONE,
        actions: Any = None,
    ) -> None:
        self.formatter = LogFormatter(template)
        self.emit = emit if emit is not None else print
        self.enabled = enabled
        self.log_stick_change = log_stick_change
        self.deadzone = deadzone
        self.actions = None
        self.set_format(template, actions)
        self.started = time.perf_counter()
        # 状態を守るロック。再入不可(Lock)なので、
        # 「ロックを持ったまま _emit_limited / _write を呼ばない」
        # ことを全経路で守る（呼ぶと同じスレッドで二重取得になり固まる）。
        # 出す行はロック内で作ってリストへ溜め、抜けてから書き出す。
        self._lock = threading.Lock()

        # 連打の集約用。直前に出そうとした行と、その繰り返し回数
        self.repeat_window = REPEAT_WINDOW
        self.repeat_flush = REPEAT_FLUSH
        # 保留中の反復。キーは _collapse_key が作る
        # （スティックは向きまで含む）。値は text/count/emitted/at/seq。
        self._pending: dict[tuple, dict[str, Any]] = {}
        self._seq = 0  # 保留の登録順。出力順を押した順に保つ

        # 流量制限用。1秒ごとに出した行数を数え、超えた分は捨てる
        self.max_lines_per_sec = MAX_LINES_PER_SEC
        self._window_start = 0.0
        self._window_count = 0
        self._dropped = 0
        self._reset_state()

    # -- 設定 ---------------------------------------------------------------

    def set_format(self, template: str, actions: Any = None) -> None:
        """表示書式を差し替える。プリセット名でも生のテンプレートでもよい。

        actions に ("PRESS",) のように渡すと、その種別だけを出力する。
        省略時はプリセット既定（command は RELEASE のみ、他は全種別）に従う。
        """
        self.formatter.set_template(template)
        if actions is None:
            actions = PRESET_ACTIONS.get(template)
        if isinstance(actions, str):
            actions = tuple(a.strip().upper() for a in actions.split(",") if a.strip())
        self.actions = tuple(actions) if actions else None

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    # -- 状態 ---------------------------------------------------------------

    def _reset_state(self) -> None:
        self._btn = 0
        self._hat = HAT_CENTER
        self._stick = {
            "Stick.LEFT": (NEUTRAL, NEUTRAL),
            "Stick.RIGHT": (NEUTRAL, NEUTRAL),
        }
        self._since: dict[str, float] = {}  # 押し始めた時刻 {name: perf_counter}
        # 倒している間の軌跡 {stick名: {from,last,max_mag,turn,moves}}。
        # 離した瞬間の座標は中立なので、向きはここから取り出す。
        self._track: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        """保持している押下状態を捨てる（'end' 受信やポート再接続のとき）。

        解放イベントは集約を通さず直接書き出す。ここを通る時点で操作は
        終わっているので待つ意味が無く、保留にすると「切断したのに後から
        ログが出る」ように見える。
        """
        with self._lock:
            events = self._release_all(
                time.perf_counter(), datetime.datetime.now(), "end"
            )
            self._reset_state()
            # 溜まっていた保留を先に出し切る（順序を保つため）
            pending = self._pop_pending(list(self._pending))

        for line in pending:
            self._emit_limited(line)

        for ev in events:
            if self.actions is not None and ev.action not in self.actions:
                continue
            try:
                self._emit_limited(self.formatter.format(ev, self.started))
            except Exception:
                pass

    # -- 入口 ---------------------------------------------------------------

    def feed(self, row: str) -> None:
        """送信した1行を渡す。Sender.writeRow から毎回呼ぶ。"""
        if not self.enabled or not row:
            return
        if row.strip() == "end":
            self.reset()
            return
        with self._lock:
            events = self._diff(row.strip())
        self._output(events)

    def _output(self, events: list) -> None:
        """イベントを行にして出す。連打はまとめ、出しすぎは抑える。"""
        for ev in events:
            if self.actions is not None and ev.action not in self.actions:
                continue
            try:
                text = self.formatter.format(ev, self.started)
            except Exception:  # 表示の失敗で操作を止めない
                continue
            self._emit_collapsed(ev, text)

    @staticmethod
    def _collapse_key(ev: InputEvent) -> tuple:
        """「同じ操作の繰り返し」とみなす単位を決める。

        ボタンや十字キーは (種別, 名前) で足りるが、スティックは足りない。
        Stick.LEFT という名前は「どちらのスティックか」でしかないため、
        左右へ高速に往復させると全部が同じキーになり、『同じ操作の反復』
        と誤判定されて x5 のようにまとめられ、往復の内容が消えていた。

        そこで倒した向きを 8方向へ丸めてキーへ加える。丸めるのは、
        角度そのものを使うと1度違うだけで別キーになり、手ぶれのたびに
        1行出て集約が意味を失うため。倒し量は含めない（同じ方向で深さ
        だけ変える操作まで別扱いにすると細かすぎる）。
        """
        if ev.kind != "stick" or ev.deg is None:
            return (ev.action, ev.kind, ev.name)

        return (ev.action, ev.kind, ev.name, snap_direction(ev.deg))

    def _emit_collapsed(self, ev: InputEvent, text: str) -> None:
        """同じ操作の繰り返しだけをまとめ、初回は必ず即座に出す。

        連打すると同じ行が並び、他のログが押し流される。そこで反復は
        「A 押下 x11」の形にまとめる。ただし、まとめてよいのは同じ操作の
        繰り返しだけで、違う操作は待たせてはいけない。

        以前は初回も保留していたため、a押下→b押下→b解放→a解放のように
        別々の操作を続けた場合、どれも「反復待ち」に入ったまま画面に出ず、
        最大 repeat_flush 秒あとにまとめて現れていた。操作した瞬間に
        見えないログは、押し順を確かめる用途では使いものにならない。

        そこで「初回は即座に出し、2回目以降だけ数える」形にした。
        emitted に出した数を持たせ、確定時は未出力ぶん(count - emitted)
        だけを出す。これで初回が二重に出ることもない。

        さらに、別の操作が来た時点で他の保留をすべて確定させる。
        以前は repeat_window が過ぎるまで待っていたため、A と B を
        交互に押すと2回目以降が保留に残り、実際に押した順ではなく
        登録順であとからまとめて出ていた。
        """
        if self.repeat_window <= 0:
            self._emit_limited(text)
            return

        now = time.perf_counter()
        key = self._collapse_key(ev)
        stale = []
        with self._lock:
            slot = self._pending.get(key)
            if slot is not None and now - slot["at"] <= self.repeat_window:
                # 同じ操作の反復。ここでは出さず数だけ増やす
                slot["count"] += 1
                slot["at"] = now
                slot["text"] = text
                return

            # 間隔が空いた同じ操作は、前の分を確定してから積み直す
            if slot is not None:
                stale += self._pop_pending([key])

            self._seq += 1
            self._pending[key] = {
                "text": text,
                "count": 1,
                "emitted": 1,
                "at": now,
                "seq": self._seq,
            }
            # 別の操作が来た時点で、他の保留はすべて確定させる。
            # 時間の経過を待つと、押した順に出せなくなる。
            others = [k for k in self._pending if k != key]
            stale += self._pop_pending(others)

        for line in stale:
            self._emit_limited(line)
        self._emit_limited(text)  # 初回は待たせずに出す

    def _pop_pending(self, keys: list) -> list:
        """保留を取り出して行にする（呼び出し側でロック済み）。

        まだ出していないぶん(count - emitted)だけを出す。初回は即時に
        出してあるので、反復が無ければ何も返らない。押された順に並べたい
        ので、登録した順番(seq)で整列する。時刻ではなく連番を使うのは、
        perf_counter の分解能が粗い環境で同時刻になっても順序が保たれる
        ようにするため。
        """
        keys.sort(key=lambda k: self._pending[k]["seq"])
        lines = []
        for key in keys:
            slot = self._pending.pop(key, None)
            if slot is None:
                continue
            extra = slot["count"] - slot["emitted"]
            if extra <= 0:
                continue
            text = slot["text"]
            # 繰り返しの示し方は書式に合わせる。command 書式では
            # 「x17」ではなく pressRep(...) にしないと貼って動かない。
            lines.append(self._repeat_text(text, extra))
        return lines

    @staticmethod
    def _split_first_arg(args: str) -> tuple[str, str]:
        """引数の並びを「第1引数」と「それ以降」に分ける。

        単純に ', ' で切ると Direction(Stick.LEFT, 27, 0.09) のように
        引数自体が括弧を持つ場合に中身で切れてしまう。括弧の深さを
        数えて、いちばん外側のカンマだけを区切りとして扱う。
        """
        depth = 0
        for i, ch in enumerate(args):
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "," and depth == 0:
                return args[:i], args[i + 1 :].lstrip()
        return args, ""

    def _repeat_text(self, text: str, count: int) -> str:
        """同じ操作が続いたことを、書式に合った書き方で表す。

        既定は「... x17」。ただし command 書式のときは、その行を
        コマンドへ貼ることが目的なので、貼って動く形でなければ
        意味がない。self.press(A) を17回という意図は pressRep で
        表せるので、第1引数の直後へ回数を差し込む。

            self.press(Direction.UP, duration=0.08)
              → self.pressRep(Direction.UP, 17, duration=0.08)

        引数が括弧を含む場合（command_raw 書式）も壊さないこと。
        以前は ', ' で切っていたため
            self.press(Direction(Stick.LEFT, 27, 0.09), ...)
        が Direction(Stick.LEFT, 17, 27, 0.09) となり、角度も倒し量も
        ずれたうえ引数の数が合わず貼っても動かなかった。
        """
        if count <= 1:
            return text

        # 末尾のコメント（回した説明など）は分けておき、最後に戻す
        body, sep, comment = text.partition("  # ")
        head = "self.press("
        if not body.startswith(head) or not body.endswith(")"):
            return f"{text} x{count}"

        target, rest = self._split_first_arg(body[len(head) : -1])
        if rest:
            out = f"self.pressRep({target}, {count}, {rest})"
        else:
            out = f"self.pressRep({target}, {count})"
        return out + (sep + comment if sep else "")

    def flush(self) -> None:
        """反復が途切れた操作を確定させる。

        初回は即時に出しているので、ここで出るのは「2回目以降がある
        もの」だけ。GUI が一定間隔で呼ぶことで、連打をやめた後に
        「x11」が取り残されずに表示される。
        """
        deadline = time.perf_counter() - self.repeat_flush
        with self._lock:
            keys = [k for k, v in self._pending.items() if v["at"] <= deadline]
            lines = self._pop_pending(keys)
        for line in lines:
            self._emit_limited(line)

    def _emit_limited(self, text: str) -> None:
        """1秒あたりの行数を制限して出す。

        集約しても、次々に別の操作が来る場合は行数が減らない。GUI が
        描画で詰まると操作そのものが重くなるため、上限を超えた分は捨てて
        件数だけ覚えておき、落ち着いたところで「N行省略」と1行で伝える。
        """
        now = time.perf_counter()
        with self._lock:
            if now - self._window_start >= 1.0:
                dropped, self._dropped = self._dropped, 0
                self._window_start = now
                self._window_count = 0
            else:
                dropped = 0

            over = self._window_count >= self.max_lines_per_sec
            if self.max_lines_per_sec > 0 and over:
                self._dropped += 1
                return
            self._window_count += 1

        if dropped:
            self._write(f"... 入力ログ {dropped} 行省略 ...")
        self._write(text)

    def _write(self, text: str) -> None:
        """実際の出力。emit の失敗で操作を止めない。"""
        try:
            self.emit(text)
        except Exception:
            pass

    # -- 解析 ---------------------------------------------------------------

    def _parse(self, row: str) -> tuple[Any, ...] | None:
        """'0x00fc 8 80 80' を (btn, hat, lx, ly, rx, ry) にする。

        スティックは変化したぶんだけ送られてくる可変長なので、
        位置ではなく先頭のフラグを見てどちらの値かを決める。
        """
        token = row.split()
        if len(token) < 2:
            return None
        flags = int(token[0], 16)
        hat = int(token[1])
        if not 0 <= hat < len(HAT_NAMES):
            hat = HAT_CENTER
        lx, ly = self._stick["Stick.LEFT"]
        rx, ry = self._stick["Stick.RIGHT"]
        i = 2
        if flags & FLAG_L_STICK and len(token) >= i + 2:
            lx, ly = int(token[i], 16), int(token[i + 1], 16)
            i += 2
        if flags & FLAG_R_STICK and len(token) >= i + 2:
            rx, ry = int(token[i], 16), int(token[i + 1], 16)
            i += 2
        return flags >> BUTTON_SHIFT, hat, lx, ly, rx, ry

    def _diff(self, row: str) -> list:
        parsed = self._parse(row)
        if parsed is None:
            return []
        btn, hat, lx, ly, rx, ry = parsed
        now, wall = time.perf_counter(), datetime.datetime.now()
        events = []
        events += self._diff_buttons(btn, now, wall, row)
        events += self._diff_hat(hat, now, wall, row)
        events += self._diff_stick("Stick.LEFT", lx, ly, now, wall, row)
        events += self._diff_stick("Stick.RIGHT", rx, ry, now, wall, row)
        held = self._held_names()
        for ev in events:
            ev.held = held
        return events

    def _held_names(self) -> tuple:
        return tuple(sorted(self._since))

    def _make(
        self,
        action: str,
        kind: str,
        name: str,
        now: float,
        wall: WallTime,
        row: str,
        **kw: Any,
    ) -> InputEvent:
        return InputEvent(
            action=action, kind=kind, name=name, at=now, wall=wall, raw=row, **kw
        )

    def _press(
        self, kind: str, name: str, now: float, wall: WallTime, row: str, **kw: Any
    ) -> InputEvent:
        self._since[name] = now
        return self._make("PRESS", kind, name, now, wall, row, **kw)

    def _release(
        self, kind: str, name: str, now: float, wall: WallTime, row: str, **kw: Any
    ) -> InputEvent:
        since = self._since.pop(name, None)
        duration = None if since is None else now - since
        return self._make(
            "RELEASE", kind, name, now, wall, row, duration=duration, **kw
        )

    def _diff_buttons(self, btn: int, now: float, wall: WallTime, row: str) -> list:
        changed = btn ^ self._btn
        self._btn = btn
        if not changed:
            return []
        events = []
        for i, name in enumerate(BUTTON_NAMES):
            bit = 1 << i
            if not changed & bit:
                continue
            if btn & bit:
                events.append(self._press("button", name, now, wall, row))
            else:
                events.append(self._release("button", name, now, wall, row))
        return events

    def _diff_hat(self, hat: int, now: float, wall: WallTime, row: str) -> list:
        if hat == self._hat:
            return []
        before, self._hat = self._hat, hat
        events = []
        if before != HAT_CENTER:
            events.append(self._release("hat", HAT_NAMES[before], now, wall, row))
        if hat != HAT_CENTER:
            events.append(self._press("hat", HAT_NAMES[hat], now, wall, row))
        return events

    def _diff_stick(
        self, name: str, x: int, y: int, now: float, wall: WallTime, row: str
    ) -> list:
        """スティックの変化を press / release / change に振り分ける。

        倒している間は「軌跡」を貯める。マウス操作では倒したまま向きが
        変わり続けるため、離した瞬間の値（中立）だけを見ると、どちらへ
        倒していたのかが完全に失われる。実機で self.press(Stick.LEFT)
        としか出なかったのはこれが原因。

        貯めるのは4つだけにする。押し始めの向き・直前の向き・最大の
        倒し量・累積の回転量。全部の点を持つとメモリも表示も膨れるうえ、
        ログとして知りたいのは「どこからどこへ、どれだけ回したか」に
        尽きるため。
        """
        before = self._stick[name]
        if before == (x, y):
            return []
        self._stick[name] = (x, y)
        was = self._tilted(*before)
        is_now = self._tilted(x, y)
        deg, mag = self.angle(x, y)

        if is_now and not was:
            self._track[name] = {
                "from": deg,
                "last": deg,
                "max_mag": mag,
                "turn": 0.0,
                "moves": 0,
                # CHANGE の比較基準。押し始めの値から始める
                "emit_deg": deg,
                "emit_mag": mag,
            }
            return [
                self._press("stick", name, now, wall, row, x=x, y=y, deg=deg, mag=mag)
            ]

        if was and not is_now:
            # 離した。座標は中立なので、軌跡から「倒していた向き」を渡す
            track = self._track.pop(name, None)
            if track is None:
                last_deg, max_mag, turn, moves = None, None, None, 0
            else:
                last_deg = track["last"]
                max_mag = track["max_mag"]
                turn = track["turn"]
                moves = track["moves"]
            return [
                self._release(
                    "stick",
                    name,
                    now,
                    wall,
                    row,
                    x=x,
                    y=y,
                    deg=last_deg,
                    mag=max_mag,
                    from_deg=None if track is None else track["from"],
                    max_mag=max_mag,
                    turn=turn,
                    moves=moves,
                )
            ]

        if was and is_now:
            track = self._track.get(name)
            if track is not None:
                # 最短回りの差分を足していく。+180/-180 をまたいでも
                # 一気に 350 度回ったことにならないようにするため。
                step = (deg - track["last"] + 180.0) % 360.0 - 180.0
                track["turn"] += step
                track["last"] = deg
                track["max_mag"] = max(track["max_mag"], mag)
                track["moves"] += 1
            if not self.log_stick_change:
                return []

            # 前回「記録した」ときからの差で見る。直前の1行との差だと、
            # ゆっくり動かしたとき1回ぶんが常に閾値未満になり、どれだけ
            # 動かしても永久に記録されない。基準は押し始めの値から始まり、
            # 1行出すたびに更新する。
            base = track if track is not None else {}
            fallback = self.angle(*before)
            last_deg = base.get("emit_deg", fallback[0])
            last_mag = base.get("emit_mag", fallback[1])

            gap = abs((deg - last_deg + 180.0) % 360.0 - 180.0)
            # 倒し量だけが変わる操作（真上に浅く→深く）も拾う。
            # 角度だけを見ていると、この間ずっと1行も出なかった。
            mag_gap = abs(mag - last_mag)
            turned = gap >= CHANGE_MIN_DEG
            pushed = CHANGE_MIN_MAG > 0 and mag_gap >= CHANGE_MIN_MAG
            if turned or pushed:
                if track is not None:
                    track["emit_deg"] = deg
                    track["emit_mag"] = mag
                return [
                    self._make(
                        "CHANGE",
                        "stick",
                        name,
                        now,
                        wall,
                        row,
                        x=x,
                        y=y,
                        deg=deg,
                        mag=mag,
                        from_deg=base.get("from"),
                        max_mag=base.get("max_mag"),
                        turn=base.get("turn"),
                        moves=base.get("moves", 0),
                    )
                ]
        return []

    def _tilted(self, x: int, y: int) -> bool:
        return self.angle(x, y)[1] > self.deadzone

    @staticmethod
    def angle(x: int, y: int) -> tuple[float, float]:
        """送信値 (x, y) から角度[度]と倒し量[0-1]を求める。

        受け取るのは「実際にシリアルへ送った値」であって、Direction が
        内部で持つ値ではない。この2つは y の向きが逆になっている。

        Keys.Direction は数学の座標系で作る（上が +90度）:
            dir.y = 127.5*sin(角度) + 127.5   → UP のとき 255
        ところが SendFormat.setAnyDirection が送信時に反転する:
            format['ly'] = 255 - dir.y        → UP のとき 0
        つまり送信値は画面座標系（上が小さい）。ここへ来るのは後者なので、
        dy = NEUTRAL - y として上を正に戻す。

        2026/08/08 に一度 dy = y - NEUTRAL へ変えたが、これは誤り。
        Direction 側の定義だけを見て、SendFormat の反転を見落としていた。
        実機で「上に入れたのに Direction.DOWN と出る／左右は正しい」
        という形で表面化した（y だけ反転するので左右は影響を受けない）。
        """
        dx, dy = x - NEUTRAL, NEUTRAL - y
        deg = math.degrees(math.atan2(dy, dx))
        mag = min(math.hypot(dx, dy) / NEUTRAL, 1.0)
        return deg, mag

    def _release_all(self, now: float, wall: WallTime, row: str) -> list:
        """押しっぱなしの操作をすべて解放イベントにする。

        'end' 受信や切断のときに呼ぶ。ここで出さないと、次に接続した
        ときまで「押したまま」の状態が残り、最初の release の押下時間が
        異常な値になる。
        """
        events = []
        for name in sorted(self._since):
            if name.startswith("Button."):
                kind = "button"
            elif name.startswith("Hat."):
                kind = "hat"
            else:
                kind = "stick"
            events.append(self._release(kind, name, now, wall, row))
        return events
