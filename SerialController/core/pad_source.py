#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pad_source.py - コントローラ読み取り口の選択（Tkなし）。

XInput 系（Xbox など）と SDL 系（Switch Pro コンなど）の2つの
読み取り口のうち、指定番号で繋がっている方を選ぶ。呼び出し側は
read(index) / scan() / available に加え、選択肢の表示名（names）と、
抜き差しで番号が動いても同じ個体を選び続けるための識別子
（identities）を使える。

なぜ選ぶのか:
  ・XInput は Xbox 系しか読めない。Switch Pro コンは繋がって
    いても未接続として扱われ、「操作できない」の正体になる。
  ・SDL（pygame）は両方読めるが、常駐の初期化とイベントポンプが要る。
    XInput だけで足りる環境では軽い方を使う。

選び方:
  ・番号ごとに XInput → SDL の順で読み、繋がっている方を使う。
  ・一度選んだら覚える。毎回両方読むと、抜き差しのたびに遅くなる。
  ・抜けたら選び直す（繋がっていない読みが続いたら忘れる）。
  ・HID直読み（ProCon）はボタンの受け皿だが、12bitスティックの校正が
    無い。SDL の生joystick が同じ個体の軸を読めるなら、スティックだけ
    SDL（校正済み -1..1）で置き換える。
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Any

from core.sdl_pad import SdlPadReader
from core.xinput import MAX_PADS, PadState, XInputReader
from loguru import logger


class PadSource:
    """番号ごとの読み取り口の選択と読み取り。"""

    # プロセス全体で共有する読み取り口。作り直すたび SDL の列挙が
    # 追いつかず、短時間の stop→listen 繰り返しで未接続に落ちる。
    # ハンドルと初期化状態を使い回すため、共有の実体を1つだけ持つ。
    _shared_sdl: SdlPadReader | None = None
    _shared_lock = threading.Lock()

    # HID open失敗後の再試行間隔(秒)。8ms周期の読みから毎回
    # 5回×0.3秒の再試行を呼ぶと、その間の読みが止まる。
    HID_RETRY_S = 0.5

    # HID で読んでいる間に SDL を試し直す間隔(秒)。SDL の列挙は
    # 起動直後に間に合わないことがあり、その一度の失敗で HID に
    # 固定されると、校正済みの軸（SDL）へ戻れない。open は失敗時
    # 0.5秒バックオフするため、ここから試しても読みは止まらない。
    SDL_REPROBE_S = 5.0

    def __init__(
        self,
        xinput: XInputReader | None = None,
        sdl: SdlPadReader | None = None,
        hid: Any | None = None,
    ) -> None:
        self.xinput = xinput if xinput is not None else XInputReader()
        if sdl is not None:
            self.sdl = sdl
        else:
            with PadSource._shared_lock:
                if PadSource._shared_sdl is None:
                    PadSource._shared_sdl = SdlPadReader()
                self.sdl = PadSource._shared_sdl
        # HID直読みはProCon 1台のみ。遅延生成（hid無し環境では作らない）。
        self._hid_arg = hid
        self._hid: Any = hid
        self._hid_opened = hid is not None
        # 番号 → "xinput" / "sdl" / "hid"。選んでいない番号は無い。
        self._route: dict[int, str] = {}
        # HID open失敗の次回試行時刻と、SDL→HIDフォールバックのログ済み印。
        self._hid_retry_at = 0.0
        self._fallback_logged = False
        # HID で読んでいる間に SDL を試し直す次回時刻。
        self._sdl_reprobe_at = 0.0

    @property
    def available(self) -> bool:
        """どちらかの読み取り口が使えるか。SDLを先に触る。

        xinput1_4.dllを先に掴むとSDLのProCon USB接続が開けなくなる実測
        （Couldn't setup USB mode。2026-10-18）。初期化順序はSDL優先で、
        選択順序（read/scan）は従来どおりXInput優先のまま変えない。
        """
        try:
            sdl_ok = bool(self.sdl.available)
        except Exception:
            sdl_ok = False
        try:
            if self.xinput.available:
                return True
        except Exception:
            pass
        return sdl_ok

    def invalidate(self) -> None:
        """読み取り口のキャッシュを捨て、次の read で列挙し直す。

        手動の「再接続」と、SDL の列挙が空になったときの復帰で使う。
        HID のハンドルは閉じない（IMU 側と共有しており、閉じると
        ジャイロが途切れる）。閉じているかは毎回 is_open で見る。
        """
        self._route.clear()
        self._hid_retry_at = 0.0
        self._hid_opened = False
        self._fallback_logged = False
        self._sdl_reprobe_at = 0.0
        try:
            self.sdl.invalidate()
        except Exception as e:
            logger.debug(f"SDLのキャッシュ破棄で例外: {e}")

    def _read_route(self, route: str, index: int) -> PadState:
        """名前で選んだ読み取り口から1回読む。"""
        if route == "xinput":
            return self._read_xinput(index)
        if route == "sdl":
            return self._read_sdl(index)
        if route == "hid":
            return self._read_hid(index)
        return PadState()

    def _read_xinput(self, index: int) -> PadState:
        try:
            return self.xinput.read(index)
        except Exception:
            return PadState()

    def _read_sdl(self, index: int) -> PadState:
        try:
            return self.sdl.read(index)
        except Exception:
            return PadState()

    def _read_hid(self, index: int) -> PadState:
        """ProCon直読み。index 0のみ。開いていなければ開く。"""
        if int(index) != 0:
            return PadState()
        try:
            if self._hid is None:
                from core.nintendo_hid import get_shared_reader

                self._hid = get_shared_reader()
                self._hid_opened = False
            reader = self._hid
            is_opening = getattr(reader, "is_opening", None)
            if callable(is_opening):
                try:
                    # open処理中は待たない。待つと8msの読みがUSBの
                    # handshake待ち（最大1秒超）で止まる。
                    if is_opening():
                        return PadState()
                except Exception:
                    pass
            is_open = getattr(reader, "is_open", None)
            if callable(is_open):
                handle_open = bool(is_open())
                if not handle_open:
                    self._hid_opened = False
            else:
                # 判定口が無い実体は従来どおり `_hid_opened` を信じる。
                handle_open = self._hid_opened
            # 開いているハンドルにはopenを呼ばない。openは既存ハンドルを
            # 閉じて開き直すため、IMU側が開いた直後やprobe直後に呼ぶと
            # close→openの往復になり、パッド読みが未接続で返る。
            if not handle_open:
                now = time.monotonic()
                if now < self._hid_retry_at:
                    return PadState()
                opener = getattr(reader, "open", None)
                if not callable(opener):
                    return PadState()
                # 8ms周期から5回×0.3秒の再試行を呼ばない。1回だけ試し、
                # 失敗したら HID_RETRY_S 待ってから開き直す。
                if not opener(retries=1):
                    self._hid_retry_at = time.monotonic() + self.HID_RETRY_S
                    return PadState()
                self._hid_retry_at = 0.0
                self._hid_opened = True
            read_pad = getattr(reader, "read_pad", None)
            if not callable(read_pad):
                return PadState()
            state = read_pad()
            # 未接続は「ハンドルが閉じた」か「0x30が鮮度切れ」のどちらか。
            # 開いている間は open を呼ばない（動いているハンドルを閉じる）。
            # ハンドルが閉じていれば次の読みで開き直す。
            self._hid_opened = bool(getattr(state, "connected", False))
            return self._with_sdl_sticks(index, state)
        except Exception:
            return PadState()

    def _with_sdl_sticks(self, index: int, state: PadState) -> PadState:
        """HID直結の未校正スティックを SDL の校正済み軸で置き換える。

        ProCon の 0x30 12bit は 0..4095 の全域を使わない（実機実測で
        左X 456..3545）。nintendo_hid の換算は中心0x800・フル0x800
        なので、HID だけでは全倒しが約78%にしかならない。SDL の
        生joystick は同じ個体を校正済みの -1..1 で返すため、ボタン・
        トリガは HID のまま、スティックだけ SDL で補う。
        GameController を開けなくても生joystick は読めることがある。
        """
        if not bool(getattr(state, "connected", False)):
            return state
        read_axes = getattr(self.sdl, "read_axes", None)
        if not callable(read_axes):
            return state
        try:
            # HID直結は ProCon 1台。同じ番号に別機種がいても混ぜない。
            values = read_axes(int(index), name_hint="pro controller")
        except Exception:
            return state
        if values is None:
            return state
        lx, ly, rx, ry, _lt, _rt, raw_axes = values
        return replace(
            state,
            thumb_lx=int(lx),
            thumb_ly=int(ly),
            thumb_rx=int(rx),
            thumb_ry=int(ry),
            raw_axes=tuple(raw_axes),
        )

    def read(self, index: int = 0) -> PadState:
        """指定番号を読む。どれも繋がっていなければ未接続を返す。

        未選択の番号はSDL→HID→XInputの順に試す。SDL優先はXInputの
        DLL掴みが先行するとSDLのProCon USB接続が開けなくなる実測のため。
        HIDはSteam起動中のSDL不通時の受け皿（ProCon直読み）。
        一度失敗した口は同じ回では読み直さない（SDLのopen失敗は
        0.5秒バックオフするため、二重に呼ぶと無駄に待つ）。
        """
        if not 0 <= int(index) < MAX_PADS:
            return PadState()
        state = PadState()
        tried: set[str] = set()
        route = self._route.get(int(index))
        if route == "hid":
            # HID に固定されたまま校正済みの SDL 軸へ戻れないのを防ぐ。
            now = time.monotonic()
            if now >= self._sdl_reprobe_at:
                self._sdl_reprobe_at = now + self.SDL_REPROBE_S
                sdl_state = self._read_sdl(index)
                if sdl_state.connected:
                    self._route[int(index)] = "sdl"
                    return sdl_state
        if route:
            tried.add(route)
            state = self._read_route(route, index)
            if state.connected:
                return state
            self._route.pop(int(index), None)
        for name in ("sdl", "hid", "xinput"):
            if name in tried:
                continue
            state = self._read_route(name, index)
            if not state.connected:
                continue
            self._route[int(index)] = name
            if name == "hid" and not self._fallback_logged:
                self._fallback_logged = True
                logger.warning(
                    "SDLでパッドを読めないため、HID直結（ProCon）へ切り替えました"
                )
            return state
        return state

    def scan(self) -> list[int]:
        """繋がっている番号の一覧。XInput・SDL・HID の和集合。"""
        found: set[int] = set()
        for reader in (self.xinput, self.sdl):
            try:
                for i in reader.scan():
                    found.add(int(i))
            except Exception:
                continue
        # HIDはProCon 1台のみ。列挙できれば0番に載せる。
        try:
            from core.nintendo_hid import NintendoHidReader

            if NintendoHidReader.enumerate():
                found.add(0)
        except Exception:
            pass
        return sorted(found)

    def names(self) -> dict[int, str]:
        """番号 → 表示名。SDLの実名＋HID直結＋XInputの汎名。"""
        try:
            names = self.sdl.names()
            out = {int(k): str(v) for k, v in names.items()}
        except Exception:
            out = {}
        if 0 not in out:
            try:
                from core.nintendo_hid import NintendoHidReader

                devs = NintendoHidReader.enumerate()
                if devs:
                    product = ""
                    try:
                        product = str(devs[0].get("product_string", "") or "")
                    except Exception:
                        product = ""
                    out[0] = product if product else "Pro Controller (HID直結)"
            except Exception:
                pass
        for index in range(MAX_PADS):
            if index in out:
                continue
            try:
                state = self.xinput.read(index)
            except Exception:
                continue
            if state.connected:
                out[index] = "XInput パッド"
        return out

    def identities(self) -> dict[int, str]:
        """番号 → 個体識別子。一覧更新で同じ個体を選び続けるための鍵。

        SDL は接続中だけ一意な instance_id。HID直結は列挙した個体の
        パスを使う。XInput は個体を跨いで安定な ID を取れないため
        載せない（番号のまま追従する）。
        """
        try:
            out = {int(k): str(v) for k, v in self.sdl.identities().items()}
        except Exception:
            out = {}
        if 0 not in out:
            try:
                from core.nintendo_hid import NintendoHidReader

                devs = NintendoHidReader.enumerate()
                if devs:
                    raw_path = devs[0].get("path", b"") or b""
                    if isinstance(raw_path, bytes):
                        key = raw_path.hex()
                    else:
                        key = str(raw_path)
                    out[0] = f"hid:{key}" if key else "hid"
            except Exception:
                pass
        return out

    def route_of(self, index: int) -> str:
        return self._route.get(int(index), "")
