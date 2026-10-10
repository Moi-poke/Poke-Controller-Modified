#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gamepad.py - PC に接続したコントローラを Switch コントローラとして扱う。

Xbox 系など XInput に対応したコントローラの入力を一定間隔で読み取り、
Switch 側の姿勢へ差分申告する。キーボード操作（Keyboard.py の
SwitchKeyboardController）と対になる「人の手入力」の1つである。

なぜ別クラスか:
  ・キーボードは OS 全体の打鍵をイベントで拾う（pynput の Listener）。
    ゲームパッドには押下イベントが無いため、こちらから定期的に
    読みに行く（ポーリング）必要がある。待ち方からして違う。
  ・設定の持ち方も違う。キーボードはキーと操作の対応表（KeyMap-*）
    を持つが、ゲームパッドは配置が固定のため対応表を持たない。

送り口:
  ・ボタン・十字キーは Sender の差分申告（pressButtons /
    releaseButtons / holdHat / releaseHat）を通す。送り主は
    "gamepad" であり、入力調停では人の手入力として扱われる。
  ・スティックは setStick を通す（調停の対象外・常時受理）。
  ・申告のあとは sendPosture で1行にまとめて送る。項目ごとに送ると
    行数が増え、送信の遅延予算に響む（仮想コントローラと同じ理由）。

速さの考え方:
  ・読み取りは 8ms（125Hz）で回る。USB の HID 報告が 8ms 周期のため、
    これより細かく読んでも新しい報告は来ない。逆に粗いと変化の検出が
    遅れる。報告が変わっていなければ申告の計算自体を省く。
  ・読み取りスレッドは Tk に触らない。Tk の描画（200ms ポンプ）や
    ログキューとは独立に回るため、GUI の重さに引きずられない。
  ・送信は live worker（8ms スロット）が運ぶ。読み取りスレッドは
    姿勢へ申告するだけで、シリアルの書き込みを待たない。

終わり方:
  ・stop() ではポーリングを止め、押していたものをすべて離して
    中立へ戻す。押しっぱなしのまま止めると、Switch 側に残る。
  ・読み取りスレッドの例外は握って止めない。抜き差しの瞬間に
    DLL 側が投げても、次の読み取りで復帰できる。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from Commands.Keys import Button, Hat
from core.gamepad_map import (
    CENTER,
    MappedInput,
    diff_mapped,
    map_state,
)
from core.pad_source import PadSource
from core.xinput import PadState
from loguru import logger

# 送り主の名札。入力調停では人の手入力として扱われる。
SOURCE = "gamepad"

# 読み取り間隔(秒)。USB の HID 報告は 8ms（125Hz）が標準であり、
# Switch 2 Pro コンの有線も実測で約 250Hz の報告を出す。読み取りは
# 8ms にし、パッド側の変化を取りこぼさない。XInputGetState 自体は
# 0.02ms 程度で終わるため、CPU 負荷は問題にならない。
# 送信は live worker が 8ms スロットで運ぶため、読み取りを上げても
# 線が詰まることはない（新規エッジは即時送出、無変化は間引き）。
POLL_INTERVAL = 0.008

# 向きの集合から Hat の値への対応。仮想コントローラと同じ表である。
# 十字キーは「どれか一つの値」であり、ボタンのようなビット列ではない。
# 上下同時・左右同時のように打ち消し合う組み合わせや 3 つ以上の同時押しは
# 表に無く、その場合は中立へ倒す（実機の十字キーでも相反する方向は
# 同時に入らない）。
_HAT_COMBO: dict[tuple[str, ...], str] = {
    (): "CENTER",
    ("UP",): "TOP",
    ("RIGHT",): "RIGHT",
    ("DOWN",): "BTM",
    ("LEFT",): "LEFT",
    ("RIGHT", "UP"): "TOP_RIGHT",
    ("DOWN", "RIGHT"): "BTM_RIGHT",
    ("DOWN", "LEFT"): "BTM_LEFT",
    ("LEFT", "UP"): "TOP_LEFT",
}


def hat_value_for(held: Any) -> Any:
    """向きの集合から Hat の値を決める（読むだけの純関数）。"""
    keys = tuple(sorted(d for d in held if d in ("UP", "RIGHT", "DOWN", "LEFT")))
    return getattr(Hat, _HAT_COMBO.get(keys, "CENTER"))


class SwitchGamepadController:
    """XInput パッドを Switch コントローラとして扱う読み取り器。"""

    def __init__(
        self,
        sender: Any,
        pad_index: int = 0,
        poll_interval: float = POLL_INTERVAL,
        is_active: Callable[[], bool] | None = None,
        reader: Any | None = None,
        on_display: Callable[[dict[str, Any]], None] | None = None,
        deadzone: int = 1638,
    ) -> None:
        """sender へ差分申告する。sender が None なら作れない。

        pad_index は読むパッド番号（0〜3）。poll_interval は読み取り間隔。
        is_active は読み取りを受け付けるかの判定（省略時は常時受付）。
        reader を渡すと読み口を差し替えられる（検証用。read/packet を
        持つ XInputReader・SdlPadReader・PadSource のいずれも受け付ける）。
        省略時は XInput と SDL の両方を読む PadSource を使う。
        on_display は読むたびに呼ぶ表示口（仮想パッドの鏡用）。
        読取スレッドから呼ぶため、Tk を触る処理は呼び出し側で
        root.after 経由にすること。
        deadzone は SDL 経路のスティック遊び（0〜8192、既定1638=最大値の5%）。
        XInput 経路は既定のまま変えない。
        """
        if sender is None:
            raise ValueError("sender が None です。シリアル接続後に生成してください。")
        self.sender = sender
        self.pad_index = max(0, int(pad_index))
        # 読み取り間隔の下限は 1ms。8ms 周期の報告に対して余裕を持たせ、
        # 0 以下の指定でビジーループにならないよう下駄を履かせる。
        self.poll_interval = max(0.001, float(poll_interval))
        self.is_active = is_active
        self.on_display = on_display
        # SDL 経路の遊び。範囲外は 0〜8192 に丸める。
        self.deadzone = max(0, min(8192, int(deadzone)))
        self.reader = reader if reader is not None else PadSource()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # 送った状態の記録。差分だけを申告するための土台。
        # ボタンは Switch 側の名前、Hat は向きの集合、スティックは座標。
        self._held_btn: set[str] = set()
        self._held_hat: set[str] = set()
        self._stick_xy: dict[str, tuple[int, int]] = {
            "L": (CENTER, CENTER),
            "R": (CENTER, CENTER),
        }
        self._last_mapped: MappedInput | None = None
        # 前回のパケット番号。変わっていなければ報告自体が無いため、
        # 申告の計算を省く（XInput の dwPacketNumber は変化時のみ進む）。
        self._last_packet: int | None = None
        # 前回の読み取り口。口ごとに番号空間が別のため切り替わり検出用。
        self._last_route: str | None = None
        # 繋がっていない間の通知は初回だけ出す（毎回出すとログが埋まる）。
        self._notified_missing = False
        # Windows タイマーの分解能を上げたか。上げっぱなしは消費電力に
        # 響くため、読み取り中だけ上げて stop() で必ず戻す。
        self._timer_raised = False

    # -- 寿命 ----------------------------------------------------------

    def listen(self) -> None:
        """読み取りを開始する。死んでいたら立て直す。

        二重に呼んでも1本だけ保つ。スレッドが何らかの理由で死んで
        いるのに実体が残っていると、チェックONのまま無反応になる。
        生きているときだけ再利用し、死んでいたら捨てて立て直す。
        """
        thread = self._thread
        if thread is not None and thread.is_alive():
            return
        self._thread = None
        self._stop_event.clear()
        self._begin_precise_timer()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="gamepad-poll"
        )
        self._thread.start()
        logger.debug("ゲームパッド操作を開始しました")

    def stop(self) -> None:
        """読み取りを止め、押していたものをすべて離す。"""
        self._stop_event.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        try:
            self._release_all()
        except Exception as e:
            logger.warning(f"ゲームパッドの解放で例外: {e}")
        finally:
            self._end_precise_timer()
        logger.debug("ゲームパッド操作を停止しました")

    def _begin_precise_timer(self) -> None:
        """Windows タイマーの分解能を 1ms へ上げる。上げっぱなしにしない。

        既定の 15.6ms 分解能では 8ms 周期の sleep が 16ms 刻みになり、
        実効 60Hz まで落ちる。Sender の live worker と同じ作法である。
        """
        if self._timer_raised:
            return
        try:
            import ctypes
            import os

            if os.name != "nt":
                return
            ctypes.WinDLL("winmm").timeBeginPeriod(1)
            self._timer_raised = True
        except Exception:
            self._timer_raised = False

    def _end_precise_timer(self) -> None:
        """上げた分解能を必ず戻す（上げっぱなしは消費電力に響く）。"""
        if not self._timer_raised:
            return
        try:
            import ctypes

            ctypes.WinDLL("winmm").timeEndPeriod(1)
        except Exception:
            pass
        self._timer_raised = False

    @property
    def running(self) -> bool:
        """読み取り中か。"""
        thread = self._thread
        return thread is not None and thread.is_alive()

    # -- 読み取り ------------------------------------------------------

    def _poll_loop(self) -> None:
        """一定間隔で読み、差分を申告する。例外では止まらない。

        締切方式（live worker と同じ）で刻む。sleep のずれを次回へ
        持ち越さないため、長時間回しても周期がなまらない。遅れた分は
        取り戻さず締切を引き直す（遅延の雪だるまを防ぐ）。
        GUI スレッドとは別のスレッドで回るため、Tk の描画を待たない。
        """
        import time

        deadline = time.perf_counter()
        while not self._stop_event.is_set():
            deadline += self.poll_interval
            try:
                self.poll_once()
            except Exception as e:
                logger.warning(f"ゲームパッドの読み取りで例外: {e}")
            remain = deadline - time.perf_counter()
            if remain > 0:
                self._stop_event.wait(remain)
            else:
                deadline = time.perf_counter()

    def poll_once(self) -> bool:
        """1 回だけ読み、差分を申告する。検証からも呼べる。

        戻り値は「読み取って申告したか」。門が閉じている・繋がって
        いないときは False。繋がっていない間、押していたものがあれば
        離して中立へ戻す（抜いた瞬間の押下を残さない）。
        報告が前回と同じパケット番号なら何も変わっていないため、
        申告の計算を省いて True を返す（変化の取りこぼしは無い）。
        """
        if self.is_active is not None and not self.is_active():
            return False
        try:
            state = self.reader.read(self.pad_index)
        except Exception as e:
            logger.warning(f"ゲームパッドの読み取りで例外: {e}")
            return False
        if not state.connected:
            if not self._notified_missing:
                logger.info(
                    "ゲームパッドが見つかりません。"
                    "PC にコントローラを接続してください。"
                )
                self._notified_missing = True
            if self._has_held():
                try:
                    self._release_all()
                except Exception as e:
                    logger.warning(f"ゲームパッドの解放で例外: {e}")
            self._last_packet = None
            self._emit_gone()
            return False
        self._notified_missing = False
        packet = int(getattr(state, "packet", -1))
        # 読み取り口（XInput/SDL）が切り替わったら保持を捨てる。
        # パケット番号は口ごとに別空間であり、前口の番号と偶然一致
        # しても「変化なし」と誤判定する。route が変わった回は必ず
        # 申告し直し、押しっぱなしの取り違えも防ぐ。
        route = None
        route_of = getattr(self.reader, "route_of", None)
        if callable(route_of):
            try:
                route = route_of(self.pad_index)
            except Exception:
                route = None
        if route != getattr(self, "_last_route", None):
            self._last_route = route
            self._last_packet = None
            self._last_mapped = None
            self._held_btn.clear()
            self._held_hat.clear()
            self._stick_xy = {"L": (CENTER, CENTER), "R": (CENTER, CENTER)}
        if self._last_packet is not None and packet == self._last_packet:
            return True
        self._last_packet = packet
        # SDL 経路の遊びは設定値を使う（既定 0=なし・GUI と同等）。
        if route == "sdl":
            dz = int(getattr(self, "deadzone", 0))
            mapped = map_state(state, left_deadzone=dz, right_deadzone=dz)
        else:
            mapped = map_state(state)
        # 初回安定待ちは行わない。起動直後の古い軸キャッシュは
        # SdlPadReader 側のウォームアップで捨てる。ここで見送ると、
        # 初回の中立外れが _last_mapped に残り、以後ずっと差分と
        # 判定されずスティックが反応しなくなる。
        self._apply_mapped(mapped, state)
        self._emit_display(mapped, state, route)
        return True

    def _emit_display(self, mapped: Any, state: Any, route: Any) -> None:
        """読み取り結果を表示口へ出す。例外は握って読み取りを止めない。

        出すのは Switch 側の割当結果（ボタン名・Hat向き・両スティック）
        と、切り分け用の生情報（route・pressed・パケット）である。
        申告が棄却されても表示は出す（「押しているのに効かない」と
        「読めていない」の区別が付く）。
        """
        fn = getattr(self, "on_display", None)
        if not callable(fn):
            return
        try:
            fn(
                {
                    "buttons": sorted(mapped.buttons),
                    "hat_dirs": sorted(mapped.hat_dirs),
                    "stick_l": (int(mapped.stick_l[0]), int(mapped.stick_l[1])),
                    "stick_r": (int(mapped.stick_r[0]), int(mapped.stick_r[1])),
                    "connected": True,
                    "route": route,
                    "pressed": sorted(getattr(state, "pressed", ()) or ()),
                    "packet": int(getattr(state, "packet", -1)),
                    "raw_axes": tuple(getattr(state, "raw_axes", ()) or ()),
                    "pad_name": str(self._pad_name()),
                }
            )
        except Exception as e:
            logger.warning(f"ゲームパッドの表示で例外: {e}")

    def _pad_name(self) -> str:
        """読取中のパッド名。取れなければ空文字（切り分け表示用）。"""
        try:
            names = getattr(self.reader, "names", None)
            if callable(names):
                return str(names().get(int(self.pad_index), ""))
        except Exception:
            pass
        return ""

    def _emit_gone(self) -> None:
        """未接続・停止を伝える。仮想パッドの鏡を消す用。"""
        fn = getattr(self, "on_display", None)
        if not callable(fn):
            return
        try:
            fn({"connected": False})
        except Exception as e:
            logger.warning(f"ゲームパッドの表示で例外: {e}")

    def _has_held(self) -> bool:
        """何か押している・倒しているか。"""
        return (
            bool(self._held_btn)
            or bool(self._held_hat)
            or self._stick_xy.get("L") != (CENTER, CENTER)
            or self._stick_xy.get("R") != (CENTER, CENTER)
        )

    # -- 申告 ----------------------------------------------------------

    def _apply_mapped(self, mapped: MappedInput, _state: PadState) -> None:
        """割り当て結果の差分を申告して送る。"""
        diff = diff_mapped(self._last_mapped, mapped)
        self._last_mapped = mapped
        sender = self.sender
        if sender is None:
            return
        changed = False
        # ボタン。押したものだけ押し、離したものだけ離す。
        for name in sorted(diff["press"]):
            btn = getattr(Button, name, None)
            if btn is None:
                continue
            try:
                if sender.pressButtons([int(btn)], source=SOURCE):
                    self._held_btn.add(name)
                    changed = True
            except Exception as e:
                logger.warning(f"ゲームパッドのボタン申告で例外: {e}")
        for name in sorted(diff["release"]):
            btn = getattr(Button, name, None)
            if btn is None:
                self._held_btn.discard(name)
                continue
            try:
                if sender.releaseButtons([int(btn)], source=SOURCE):
                    changed = True
            except Exception as e:
                logger.warning(f"ゲームパッドのボタン申告で例外: {e}")
            finally:
                self._held_btn.discard(name)
        # 十字キー。向きの集合が変わったときだけ申告する。
        if diff["hat_changed"]:
            try:
                if self._announce_hat(set(diff["hat_dirs"])):
                    changed = True
            except Exception as e:
                logger.warning(f"ゲームパッドの十字キー申告で例外: {e}")
        # スティック。変わった側だけ申告する（スティックは常時受理）。
        for side_key, coord_key in (("L", "stick_l"), ("R", "stick_r")):
            if not diff[coord_key + "_changed"]:
                continue
            xy = diff[coord_key]
            try:
                sender.setStick(side_key, int(xy[0]), int(xy[1]), source=SOURCE)
                self._stick_xy[side_key] = (int(xy[0]), int(xy[1]))
                changed = True
            except Exception as e:
                logger.warning(f"ゲームパッドのスティック申告で例外: {e}")
        if changed:
            try:
                sender.sendPosture(source=SOURCE)
            except Exception as e:
                logger.warning(f"ゲームパッドの送信で例外: {e}")

    def _announce_hat(self, held: set[str]) -> bool:
        """向きの集合を申告する。受理したら保持を更新して True。"""
        sender = self.sender
        if sender is None:
            return False
        hold = getattr(sender, "holdHat", None)
        release = getattr(sender, "releaseHat", None)
        if callable(hold) and callable(release):
            if held:
                ok = bool(hold(int(hat_value_for(held)), source=SOURCE))
            else:
                ok = bool(release(source=SOURCE))
        else:
            # 古い送信側（holdHat を持たない）への退避。
            set_hat = getattr(sender, "setHat", None)
            if not callable(set_hat):
                return False
            ok = bool(
                set_hat(
                    None if not held else int(hat_value_for(held)),
                    source=SOURCE,
                )
            )
        if ok:
            self._held_hat = set(held)
        return bool(ok)

    def _release_all(self) -> None:
        """押しているものをすべて離し、スティックを中立へ戻して送る。"""
        sender = self.sender
        if sender is None:
            return
        for name in sorted(self._held_btn):
            btn = getattr(Button, name, None)
            if btn is None:
                continue
            try:
                sender.releaseButtons([int(btn)], source=SOURCE)
            except Exception:
                pass
        self._held_btn.clear()
        if self._held_hat:
            self._held_hat.clear()
            try:
                release = getattr(sender, "releaseHat", None)
                if callable(release):
                    release(source=SOURCE)
                else:
                    set_hat = getattr(sender, "setHat", None)
                    if callable(set_hat):
                        set_hat(None, source=SOURCE)
            except Exception:
                pass
        for side in ("L", "R"):
            if self._stick_xy.get(side) != (CENTER, CENTER):
                try:
                    sender.setStick(side, CENTER, CENTER, source=SOURCE)
                except Exception:
                    pass
                self._stick_xy[side] = (CENTER, CENTER)
        try:
            sender.sendPosture(source=SOURCE)
        except Exception:
            pass
        self._last_mapped = None
        self._last_packet = None
        # 中立が間引きで保留されると倒したまま残る。区切りで送り切る。
        try:
            flush = getattr(sender, "flushPending", None)
            if callable(flush):
                flush()
        except Exception:
            pass
