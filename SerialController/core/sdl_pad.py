#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sdl_pad.py - SDL GameController の読み取り（Tkなし）。

XInput に対応していないコントローラ（Switch Pro コンなど）を
読むための口。core/xinput.py の XInputReader と同じ PadState を
返すため、呼び出し側はどちらも同じに扱える。

なぜ GameController API か:
  ・生の joystick は個体ごとにボタン番号・軸順が違う。Pro コンと
    Xbox 系で同じ番号が別の操作を指すため、番号決め打ちでは
    「割り当てがめちゃくちゃ」になる。
  ・SDL の GameController は機種ごとの配置データベースを持ち、
    a/b/x/y・十字キー・スティック・トリガを正規の意味で読める。
    Pro コンも Xbox 系も同じ意味で読めるため、割当は1つで済む。
  ・Pro コンの A/B と X/Y は Switch 本体準拠の位置で読める。
    （SDL が配置を吸収するため、呼び出し側で入れ替えない）。

標準ボタン（SDL_GameControllerButton 順）:
  0 A・1 B・2 X・3 Y・4 BACK・5 GUIDE・6 START・7 左押し込み・
  8 右押し込み・9 LB・10 RB・11 十字上・12 十字下・13 十字左・
  14 十字右・15 MISC・16〜19 パドル・20 タッチパッド
標準軸: 0 左X・1 左Y・2 右X・3 右Y・4 左トリガ・5 右トリガ。
  Y は上が負。トリガは 0.0（離し）〜1.0（押し）。

値の換算:
  ・スティック -1.0〜1.0 → SHORT（-32767〜32767）。PadState の
    形に合わせるためであり、0〜255 への換算は gamepad_map が行う。
  ・トリガ 0.0〜1.0 → 0〜255。0.0（離し）が 0 になる。
  ・CAPTURE と HOME は標準ボタンに無い。Pro コンの CAPTURE と
    HOME は MISC/GUIDE に出る個体があるため、MISC→CAPTURE・
    GUIDE→HOME に読み替える。
  ・packet は SDL に無いため、変化のたびに進める自前の連番を使う。
    同じ内容なら進めない（XInput の dwPacketNumber と同じ意味）。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from core.xinput import MAX_PADS, PadState, pressed_names
from loguru import logger

# SDL初期化に失敗したときの再試行間隔(秒)。一度の失敗を永久に
# 引きずると、以降ずっと「ゲームパッドが見つかりません」になる。
ENSURE_RETRY_S = 2.0

# 未接続が続いたときに、掴んだ実体と列挙キャッシュを捨てるまでの
# 時間(秒)。HIDの奪い合いなどで SDL の列挙が古くなったままになる
# 実測がある。SDL 全体は落とさない（下の invalidate を参照）。
RESET_MISSING_S = 2.0

# GameController の open に失敗した次の再試行までの時間(秒)。
# read は 8ms 周期で呼ばれるため、待たずに諦めて次の周期へ回す。
OPEN_RETRY_S = 0.5

# GameController 標準ボタン番号 → XInput と同じ名前。
# Pro コンの CAPTURE/HOME は標準に無いため、MISC→CAPTURE・
# GUIDE→HOME に読み替える（Pro コンではその個体が多い）。
_GC_BUTTON_NAMES: dict[int, str] = {
    0: "A",
    1: "B",
    2: "X",
    3: "Y",
    4: "BACK",
    5: "GUIDE_HOME",
    6: "START",
    7: "LEFT_THUMB",
    8: "RIGHT_THUMB",
    9: "LEFT_SHOULDER",
    10: "RIGHT_SHOULDER",
    11: "DPAD_UP",
    12: "DPAD_DOWN",
    13: "DPAD_LEFT",
    14: "DPAD_RIGHT",
    15: "MISC_CAPTURE",
}


# ボタン番号 → ビット（xinput.py と同じ値）。pressed 集合だけでなく
# buttons ビット列も埋めるため（検証・表示用）。
_BUTTON_BITS: dict[str, int] = {
    "DPAD_UP": 0x0001,
    "DPAD_DOWN": 0x0002,
    "DPAD_LEFT": 0x0004,
    "DPAD_RIGHT": 0x0008,
    "START": 0x0010,
    "BACK": 0x0020,
    "LEFT_THUMB": 0x0040,
    "RIGHT_THUMB": 0x0080,
    "LEFT_SHOULDER": 0x0100,
    "RIGHT_SHOULDER": 0x0200,
    "A": 0x1000,
    "B": 0x2000,
    "X": 0x4000,
    "Y": 0x8000,
}


def _axis_to_short(value: float) -> int:
    """-1.0〜1.0 → SHORT。範囲外は丸める。"""
    clamped = max(-1.0, min(1.0, float(value)))
    return int(round(clamped * 32767))


def _trigger_to_byte(value: float) -> int:
    """トリガ値 → 0〜255。

    生joystick は -1.0（離し）〜1.0（押し）。GameController 標準の
    0.0〜1.0 も受ける。離し側は一律 0 に潰す。
    """
    number = float(value)
    if number <= 0.0:
        return 0
    clamped = min(1.0, number)
    return max(0, min(255, int(round(clamped * 255))))


def _normalize_axis(value: Any) -> float:
    """GameController の軸値 → -1.0〜1.0。

    pygame 2.6 の get_axis は int（Sint16相当の -32768〜32767）を
    返す。-1.0〜1.0 の float だと思って掛け算すると、中立の微小値
    （例: -1324）が -1324.0 へ膨らみ、丸めで全倒れになる。
    -1〜1 の範囲ならそのまま float 化し、それ以外は Sint16 として
    32767 で割る。負側の -32768 は -1.0 に丸める。
    """
    raw = value() if callable(value) else value
    number = float(raw)  # type: ignore[arg-type]
    if isinstance(raw, float) or -1.0 <= number <= 1.0:
        # float 実装か、既に -1〜1 の範囲ならそのまま使う。
        return max(-1.0, min(1.0, number))
    return max(-1.0, min(1.0, number / 32767.0))


class SdlPadReader:
    """SDL GameController の読み取り口。

    XInputReader と同じ read(index) / scan() / available を持つ。
    pygame の初期化は初回の読み取り時に1度だけ行う。初期化に失敗
    したら以後も読まずに未接続を返す。GameController に対応して
    いないパッド（is_controller が False）は未接続として扱う。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready: bool | None = None
        self._retry_at = 0.0
        self._missing_since: float | None = None
        self._sticks: dict[int, Any] = {}
        # 生joystick だけのキャッシュ。GameController を開けない個体でも
        # 校正済みの軸は読める（HID直結の未校正スティックの代替）。
        self._raw_sticks: dict[int, tuple[Any, Any]] = {}
        # 生joystick の open 失敗の次回試行時刻。8ms周期で叩き続けない。
        self._raw_retry_at: dict[int, float] = {}
        # 名前から引いた生joystick（番号がずれた HID 補正用）。短時間だけ持つ。
        self._named_raw: dict[str, tuple[Any, float]] = {}
        # open失敗の番号ごとの次回試行時刻。8ms周期の読みから
        # sleep で待つと読み取り全体が止まるため、時刻だけ覚える。
        self._open_retry_at: dict[int, float] = {}
        # パッドごとの前回内容と連番。内容が変わったときだけ進める。
        self._last_key: dict[int, Any] = {}
        self._packet: dict[int, int] = {}
        # 読み取り済みの番号。初回だけ古いキャッシュ疑いで読み直す。
        self._seen: set[int] = set()
        # 同じ失敗の警告を毎tick出さないための記録（理由ごとに1回）。
        self._warned: set[str] = set()
        self._warn_lock = threading.Lock()

    def _warn_once(self, key: str, message: str) -> None:
        """同じ理由の警告を1回だけ出す。"""
        with self._warn_lock:
            if key in self._warned:
                return
            self._warned.add(key)
        logger.warning(message)

    def _is_fresh(self, index: int) -> bool:
        """まだ一度も読んでいない番号か。"""
        with self._lock:
            return int(index) not in self._seen

    def _mark_seen(self, index: int) -> None:
        """読み取り済みにする。"""
        with self._lock:
            self._seen.add(int(index))

    def _ensure(self) -> bool:
        """pygame と GameController を使える状態にする。成功で True。

        失敗は恒久ではない。ENSURE_RETRY_S 経過後に再試行する
        （一度のinit失敗で永久に未接続になるのを防ぐ）。
        """
        if self._ready is True:
            return True
        now = time.monotonic()
        if self._ready is False and now < self._retry_at:
            return False
        try:
            import pygame
            from pygame._sdl2 import controller as _gc

            pygame.init()
            _gc.init()
            self._ready = True
        except Exception as e:
            self._ready = False
            self._retry_at = now + ENSURE_RETRY_S
            self._warn_once(f"ensure:{e!r}", f"SDLを初期化できません: {e}")
        return self._ready

    def invalidate(self, *, close_raw: bool = True) -> None:
        """掴んだ実体と失敗の記憶を捨て、次の読みで列挙し直す。

        pygame.quit() は呼ばない。SDL はプロセス共有であり、
        ジャイロの SdlSensorReader が同じ SDL の実体を ctypes で
        掴んでいる。全体を落とすとそのハンドルが宙に浮くうえ、
        未接続が続く間の再初期化（実測1秒超）で GUI が止まる。
        ここではハンドルを閉じてキャッシュを捨てるだけに留める。
        列挙の更新は次の read の event.pump が行う。

        close_raw=False は自動の列挙やり直し用。生joystick だけを
        使う経路（HID直結のスティック補正）を止めないため、生の
        キャッシュは残す。壊れた実体は使うときに ID で弾かれる。
        """
        with self._lock:
            pairs = list(self._sticks.values())
            raw = [pair[0] for pair in self._raw_sticks.values()] if close_raw else []
            self._sticks.clear()
            if close_raw:
                self._raw_sticks.clear()
            self._named_raw.clear()
            self._open_retry_at.clear()
            self._raw_retry_at.clear()
        closed: set[int] = set()
        for gc_stick, joy_stick, _dev_id in pairs:
            for handle in (joy_stick, gc_stick):
                if id(handle) in closed:
                    continue
                closed.add(id(handle))
                try:
                    handle.quit()
                except Exception:
                    pass
        for joy_stick in raw:
            if id(joy_stick) in closed:
                continue
            closed.add(id(joy_stick))
            try:
                joy_stick.quit()
            except Exception:
                pass

    def _any_controller(self) -> bool:
        """1台でも GameController として列挙できているか。

        未接続のリセットを「選択番号が読めない」だけで行うと、
        番号がずれているだけの間ずっとキャッシュを捨て続ける。
        列挙そのものが空のときだけやり直す。
        """
        gc = self._gc()
        if gc is None:
            return False
        for index in range(MAX_PADS):
            try:
                if bool(gc.is_controller(int(index))):
                    return True
            except Exception:
                continue
        return False

    @property
    def available(self) -> bool:
        """この環境で SDL が読めるか（pygame が入っているか）。"""
        return self._ensure()

    def _gc(self) -> Any | None:
        """GameController モジュール。無ければ None。"""
        if not self._ensure():
            return None
        try:
            from pygame._sdl2 import controller as gc

            return gc
        except Exception:
            return None

    def _pump_events(self) -> None:
        """SDL に到着/離脱イベントを処理させる。失敗しても読む側は止めない。

        pump は read() だけでなく一覧の取得でも必要になる。ゲームパッド
        操作がOFFの間は read() が呼ばれず、抜き差ししても一覧が古いまま
        になるため。pygame が無い・pump が失敗する環境でも、名前の取得
        自体は続ける。
        """
        try:
            import pygame as _pygame

            _pygame.event.pump()
        except Exception:
            pass

    def names(self) -> dict[int, str]:
        """繋がっているパッド番号 → 名前。選択肢の表示用。

        read() が読める番号（GameController として認識された
        生joystick）だけを載せる。gc.get_count() は「コントローラの
        本数」であり番号の上限ではないため、番号は 0..MAX_PADS-1 を
        直接問い合わせる。読めない番号を選択肢へ出すと、選んでも
        未接続のままになる（生joystick にだけ見える個体）。
        取得の前に pump する（操作OFF中でもホットプラグを反映する）。
        """
        gc = self._gc()
        out: dict[int, str] = {}
        # pump は GameController が使えない環境でも呼ぶ（SDL の到着/離脱を
        # 処理するのは pygame 側であり、生joystick の列挙にも効く）。
        self._pump_events()
        if gc is None:
            return out
        joy: Any | None = None
        try:
            import pygame.joystick as _joy

            _joy.init()
            joy = _joy
        except Exception:
            joy = None
        for i in range(MAX_PADS):
            try:
                if not bool(gc.is_controller(i)):
                    continue
            except Exception:
                continue
            name = ""
            try:
                name = str(gc.name_forindex(i) or "")
            except Exception:
                name = ""
            if not name and joy is not None:
                try:
                    name = str(joy.Joystick(i).get_name())
                except Exception:
                    name = ""
            out[i] = name or f"パッド{i}"
        return out

    def identities(self) -> dict[int, str]:
        """繋がっているパッド番号 → 個体識別子。選択の追従用。

        instance_id は接続中だけ一意で、SDL の仕様上ほかの個体へ
        使い回されない。抜き差しで番号（index）が詰め直されても、
        同じ個体を選び続けるための鍵として使う。取れない環境・
        古い pygame では空を返す（呼び側は番号だけで追従する）。
        names() と同じく、read() が読める番号だけを載せる。
        """
        gc = self._gc()
        if gc is None:
            return {}
        self._pump_events()
        out: dict[int, str] = {}
        try:
            import pygame.joystick as _joy

            _joy.init()
        except Exception:
            return out
        for i in range(MAX_PADS):
            try:
                if not bool(gc.is_controller(i)):
                    continue
            except Exception:
                continue
            try:
                out[i] = str(int(_joy.Joystick(i).get_instance_id()))
            except Exception:
                continue
        return out

    def _raw_stick(self, index: int) -> Any | None:
        """生joystick の実体を返す。GameController の可否に依存しない。

        GameController として認識されない・open できない個体でも、
        生joystick は校正済みの軸を返すことがある（実測）。軸だけを
        読む経路と、ボタンと組にする経路の両方で使い回す。
        """
        key = int(index)
        with self._lock:
            cached = self._raw_sticks.get(key)
        if cached is not None:
            handle, dev_id = cached
            try:
                handle.get_numaxes()
                if dev_id is None or handle.get_instance_id() == dev_id:
                    return handle
            except Exception:
                pass
            with self._lock:
                self._raw_sticks.pop(key, None)
        with self._lock:
            retry_at = self._raw_retry_at.get(key, 0.0)
        if time.monotonic() < retry_at:
            return None
        try:
            import pygame.joystick as _joy

            _joy.init()
            if not 0 <= key < int(_joy.get_count()):
                with self._lock:
                    self._raw_retry_at[key] = time.monotonic() + OPEN_RETRY_S
                return None
            handle = _joy.Joystick(key)
            handle.init()
            # 開いた時点の ID を控える。番号を使い回した別個体を弾く。
            try:
                dev_id = handle.get_instance_id()
            except Exception:
                dev_id = None
        except Exception:
            with self._lock:
                self._raw_retry_at[key] = time.monotonic() + OPEN_RETRY_S
            return None
        with self._lock:
            self._raw_sticks[key] = (handle, dev_id)
            self._raw_retry_at.pop(key, None)
        return handle

    def _named_raw_stick(self, name_hint: str, *, ttl: float = 2.0) -> Any | None:
        """名前が一致する生joystick を1台だけ探す（短時間キャッシュ）。

        HID と SDL で番号がずれることがある（HIDはProCon 1台を0番に
        載せる）。一意に決まるときだけ使う。同名が複数なら混ぜない。
        """
        key = name_hint.lower()
        now = time.monotonic()
        with self._lock:
            cached = self._named_raw.get(key)
        if cached is not None and now < cached[1]:
            return cached[0]
        found: list[Any] = []
        for i in range(MAX_PADS):
            handle = self._raw_stick(i)
            if handle is None:
                continue
            try:
                if key in str(handle.get_name()).lower():
                    found.append(handle)
            except Exception:
                continue
        handle = found[0] if len(found) == 1 else None
        with self._lock:
            self._named_raw[key] = (handle, now + ttl)
        return handle

    def read_axes(
        self, index: int = 0, *, name_hint: str = ""
    ) -> tuple[int, int, int, int, int, int, tuple[float, ...]] | None:
        """生joystick の軸だけを読む。読めなければ None。

        GameController の open が失敗しても、生joystick の軸は読める
        ことがある。ProCon の HID 直読み（12bit）は校正が無く全倒しに
        届かないため、PadSource はこの値でスティックを置き換える。
        name_hint を渡すと、同じ番号の別機種を混ぜないよう名前で確かめる。
        """
        if not 0 <= int(index) < MAX_PADS:
            return None
        handle = self._raw_stick(index)
        if name_hint:
            same = False
            if handle is not None:
                try:
                    same = name_hint.lower() in str(handle.get_name()).lower()
                except Exception:
                    same = False
            if not same:
                # 番号がずれている（HIDが0番、SDLが別番号など）ことがある。
                handle = self._named_raw_stick(name_hint)
            if handle is None:
                return None
        elif handle is None:
            return None
        try:
            axes = [_normalize_axis(handle.get_axis(a)) for a in range(6)]
        except Exception:
            with self._lock:
                self._raw_sticks.pop(int(index), None)
                self._named_raw.clear()
            return None
        lx = _axis_to_short(axes[0])
        ly = _axis_to_short(-axes[1])
        rx = _axis_to_short(axes[2])
        ry = _axis_to_short(-axes[3])
        lt = _trigger_to_byte(axes[4])
        rt = _trigger_to_byte(axes[5])
        return (
            lx,
            ly,
            rx,
            ry,
            lt,
            rt,
            tuple(round(float(v), 4) for v in axes),
        )

    def _stick(self, index: int) -> tuple[Any | None, Any | None]:
        """(GameController, 生joystick) の組。掴めなければ (None, None)。

        ボタンは GameController の配置DBで正規化して読み、軸は
        生joystick から読む。GameController の軸は SDL 2.28 の
        Pro コン配置で Y が -1 に張り付く実測があるため使わない。
        生joystick の軸順は Pro コン実測で標準通り（0/1左XY・
        2/3右XY・4/5トリガ、Y上が負・トリガ-1.0離し）を確認済み。
        """
        with self._lock:
            pair = self._sticks.get(int(index))
        if pair is not None:
            gc_stick, joy_stick, dev_id = pair
            alive = False
            try:
                alive = bool(gc_stick.attached())
            except Exception:
                alive = False
            if alive:
                # 生側も触って確かめる。抜けた実体はここで例外になる。
                try:
                    joy_stick.get_numaxes()
                except Exception:
                    alive = False
            if alive:
                # 番号を使い回した別デバイスなら捨てる。SDL の ID は
                # 再接続で変わり、使い回されない（SDL_JoystickID 仕様）。
                # 古い実体のまま読むと別物の軸が残り、中立で倒れ続ける。
                try:
                    if dev_id is not None and joy_stick.get_instance_id() != dev_id:
                        alive = False
                except Exception:
                    pass
            if alive:
                return (gc_stick, joy_stick)
            with self._lock:
                self._sticks.pop(int(index), None)
        gc = self._gc()
        if gc is None:
            return (None, None)
        # 生joystick を先に開く。SDLのHIDAPI Switchドライバは
        # GameController を先に開くと生joystick の open が
        # 「Couldn't setup USB mode」で失敗する実測がある（逆順は成功）。
        # 失敗しても生の実体はキャッシュへ残す（HID直結の軸補正で使う）。
        joy_stick = self._raw_stick(index)
        if joy_stick is None:
            # 軸が無いとボタンだけ読めても申告できない。理由を1回だけ出す
            # （GameController 側の open は行わない。使えない実体を増やさない）。
            try:
                if bool(gc.is_controller(int(index))):
                    self._warn_once(
                        f"raw_open:{index}",
                        f"SDLの生joystickを開けません（パッド{index}の軸を読めません）",
                    )
                else:
                    self._warn_once(
                        f"not_controller:{index}",
                        f"SDLがパッド{index}をGameControllerとして認識しません",
                    )
            except Exception:
                pass
            return (None, None)
        # Steam起動中はHIDの奪い合いでopenが不安定になる実測（SDL#3450）。
        # read は 8ms 周期で呼ばれるため、ここで sleep して3回試すと
        # 読み取りが0.6秒止まり、その間の入力が飛ぶ。失敗は時刻だけ
        # 覚え、OPEN_RETRY_S 後に次の read が試す。
        now = time.monotonic()
        retry_at = self._open_retry_at.get(int(index), 0.0)
        if now < retry_at:
            return (None, None)
        try:
            if not bool(gc.is_controller(int(index))):
                self._warn_once(
                    f"not_controller:{index}",
                    f"SDLがパッド{index}をGameControllerとして認識しません",
                )
                return (None, None)
            opened_gc: Any = gc.Controller(int(index))
        except Exception as e:
            with self._lock:
                self._open_retry_at[int(index)] = now + OPEN_RETRY_S
            # 毎tick同じ失敗を出さない。理由（例外）ごとに1回だけ。
            self._warn_once(
                f"controller_open:{index}:{e!r}",
                f"SDLでパッド{index}を開けません: {e}",
            )
            return (None, None)
        with self._lock:
            self._open_retry_at.pop(int(index), None)
        try:
            dev_id = joy_stick.get_instance_id()
        except Exception:
            dev_id = None
        with self._lock:
            self._sticks[int(index)] = (opened_gc, joy_stick, dev_id)
        return (opened_gc, joy_stick)

    def read(self, index: int = 0) -> PadState:
        """指定番号のパッドを読む。繋がっていなければ未接続を返す。"""
        if not 0 <= int(index) < MAX_PADS:
            return PadState()
        gc = self._gc()
        if gc is None:
            return PadState()
        # イベントポンプは未接続時も必須。SDL は pump でデバイスの
        # 到着/離脱を処理するため、接続後だけ呼ぶと一度見失った後に
        # 到着イベントを拾えず永久に復帰しない。
        self._pump_events()
        gc_stick, joy_stick = self._stick(index)
        if gc_stick is None or joy_stick is None:
            now = time.monotonic()
            if self._missing_since is None:
                self._missing_since = now
            elif now - self._missing_since >= RESET_MISSING_S:
                # 未接続が続く: 1台も列挙できていないときだけ、
                # 掴んだ実体を捨てて列挙をやり直す。番号がずれている
                # だけなら捨てない（列挙は生きている）。
                self._missing_since = now
                if not self._any_controller():
                    self.invalidate(close_raw=False)
            return PadState()
        self._missing_since = None
        try:
            pressed: set[str] = set()
            for num, name in _GC_BUTTON_NAMES.items():
                try:
                    if gc_stick.get_button(num):
                        pressed.add(name)
                except Exception:
                    continue
            # 軸は生joystick から読む。GameController の軸は張り付く。
            # 生の軸順は標準通り（Y上が負・トリガ-1.0離し）。
            # -1.0〜1.0 の float で来るため正規化は素通しになる。
            axes: list[float] = []
            for axis in range(6):
                try:
                    axes.append(_normalize_axis(joy_stick.get_axis(axis)))
                except Exception:
                    # 標準軸の既定。スティックは中央、トリガは離し。
                    axes.append(0.0 if axis in (0, 1, 2, 3) else -1.0)
            lx = _axis_to_short(axes[0])
            # Y は上が負、SHORT は上が正（XInput と同じ向き）。
            ly = _axis_to_short(-axes[1])
            rx = _axis_to_short(axes[2])
            ry = _axis_to_short(-axes[3])
            lt = _trigger_to_byte(axes[4])
            rt = _trigger_to_byte(axes[5])
            # GUIDE→HOME・MISC→CAPTURE の読み替え。pressed 側の
            # 仮名を Switch 側の正名へ直す。
            if "GUIDE_HOME" in pressed:
                pressed.discard("GUIDE_HOME")
                pressed.add("HOME")
            if "MISC_CAPTURE" in pressed:
                pressed.discard("MISC_CAPTURE")
                pressed.add("CAPTURE")
            bits = 0
            for name in pressed:
                bits |= _BUTTON_BITS.get(name, 0)
            key = (
                bits,
                lt,
                rt,
                lx,
                ly,
                rx,
                ry,
                frozenset(pressed),
            )
            last = self._last_key.get(int(index))
            if last is not None and last == key:
                packet = self._packet.get(int(index), 0)
            else:
                packet = self._packet.get(int(index), 0) + 1
                self._packet[int(index)] = packet
                self._last_key[int(index)] = key
            return PadState(
                connected=True,
                buttons=bits,
                left_trigger=lt,
                right_trigger=rt,
                thumb_lx=lx,
                thumb_ly=ly,
                thumb_rx=rx,
                thumb_ry=ry,
                packet=packet,
                pressed=frozenset(pressed),
                raw_axes=tuple(round(float(v), 4) for v in axes),
            )
        except Exception:
            # 抜き差しの瞬間など。次回読み直すため実体を捨てる。
            with self._lock:
                self._sticks.pop(int(index), None)
            return PadState()

    def scan(self) -> list[int]:
        """繋がっているパッド番号の一覧。1つも無ければ空リスト。"""
        return [i for i in range(MAX_PADS) if self.read(i).connected]

    @staticmethod
    def debug_names(state: PadState) -> list[str]:
        """pressed 集合の表示用（読むだけの純関数）。"""
        return sorted(pressed_names(state.buttons))
