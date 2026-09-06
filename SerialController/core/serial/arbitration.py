#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""arbitration.py - 入力調停（誰の操作を優先するか）の判断だけを持つ。

Sender から調停の状態と判断をここへ移している。姿勢（どのボタンが
押されているか）とは別の状態なので分ける。_accept の1箇所を
書き換えれば全経路（pressButtons / releaseButtons / setHat /
sendPosture / sendNeutralAll）に効く。

方針:
  ・誰の申告かは source で分かる
  ・受理したかは bool で返す
  ・既定は "off"（全部受理）。それまでの挙動を1文字も変えないため
    （既存コマンドへの影響なし）

拒否は黙って捨てない。必ず知らせる。黙って無視すると
「スクリプトが動かない」と見え、原因が分からなくなる。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from logging import DEBUG, NullHandler, getLogger
from typing import Any

# 入力調停（Input Control Arbitration）の既定。
#   "off"    … 調停しない。全部受理する（本家と同じ挙動）
#   "human"  … 人の手入力を優先。人が触った直後はスクリプトの申告を拒否
#   "script" … スクリプトを優先。スクリプトが書いた直後は人の申告を拒否
# 既定は off とする。本家の挙動を変えないためであり、実行中に
#   人の操作を拒否するかどうかは利用者が画面から選ぶ。script を選べば
#   一時停止で操作でき、そのとき自動側の押下は退避される。
#   解放と中立の申告はどの設定でも拒否しない。拒否すると押しっぱなしが
#   残るため、安全に関わる申告は調停より優先する。
ARBITRATION_MODE = "off"
# 選べる調停の名前。画面の候補もここから引く（書き並べない）。
ARBITRATION_MODES = ("off", "human", "script")
# 横取りが続く時間(秒)。優先された側が書いてからこの間、反対側を拒否する。
# NanoKVM の既定(2秒)に合わせた。短すぎると取り合いが収まらず、
# 長すぎると「操作できない時間」が体感で分かるほど伸びる。
ARBITRATION_COOLDOWN = 2.0
# 人の手による操作とみなす申告者。これ以外（script / None）は自動側とみなす。
HUMAN_SOURCES = ("mouse", "keyboard", "gui")
# 拒否の通知を間引く間隔(秒)。連打や毎フレームの申告で通知が溢れるのを防ぐ。
REJECT_NOTIFY_INTERVAL = 1.0


def list_arbitration_modes() -> tuple:
    """選べる調停の名前を返す（画面の候補づくり用）。"""
    return ARBITRATION_MODES


def resolve_arbitration_mode(name: Any, logger: Any = None) -> str:
    """設定ファイル由来の名前を、実際に使える名前へ解決する。

    知らない名前は既定（off）へ落とし、理由を残す。設定ファイルは人が
    手で書き換えられるため、綴りの誤りで黙って別の挙動になることを避け
    る。Transport の resolve_transport_name と同じ作法である。
    """
    text = str(name).strip().lower() if name is not None else ""
    if text in ARBITRATION_MODES:
        return text
    message = (
        "入力調停の設定 "
        + repr(name)
        + " は知らない名前です。"
        + ARBITRATION_MODE
        + " として扱います"
        + "（選べるのは "
        + " / ".join(ARBITRATION_MODES)
        + "）。"
    )
    if logger is not None:
        logger.warning(message)
    else:
        print(message)
    return ARBITRATION_MODE


class Arbiter:
    """調停の状態と判断。Sender が1つ持って申告の入口で聞く。

    錠は Sender とは別に持つ。調停の帳簿だけを守ればよく、
    姿勢の錠と混ぜると待ちが連鎖する。通知先の呼び出しは
    錠の外で行う（呼び戻しでのデッドロックを避けるため）。
    """

    def __init__(self, logger: Any = None) -> None:
        self._logger = logger if logger is not None else getLogger(__name__)
        if logger is None:
            self._logger.addHandler(NullHandler())
            self._logger.setLevel(DEBUG)
            self._logger.propagate = True
        self._lock = threading.RLock()
        self._mode: str = ARBITRATION_MODE
        self._cooldown: float = ARBITRATION_COOLDOWN
        # 最後に書いた時刻。どちらも「まだ誰も書いていない」で始める
        self._last_human: float | None = None
        self._last_auto: float | None = None
        self._notifier: Callable[[str], None] | None = None
        self._last_notify: float = 0.0
        self._suppressed: int = 0
        self._rejected: dict[str, int] = {"human": 0, "auto": 0}
        # 自動側が明示的に操作を人へ渡している最中か。
        self._handed_over = False

    def set(self, mode: str | None = None, cooldown: float | None = None) -> None:
        """調停の設定を変える。設定画面や起動引数から呼ぶ。

        mode は "off" / "human" / "script"。
          off    … 調停しない（既定。従来と同じ）
          human  … 人の手入力を優先。人が触った直後はスクリプトを拒否
          script … スクリプトを優先。スクリプトが書いた直後は人を拒否
        cooldown は横取りが続く秒数。
        """
        with self._lock:
            if mode is not None:
                mode = str(mode).lower()
                if mode not in ("off", "human", "script"):
                    raise ValueError(
                        f"unknown arbitration mode: {mode!r} (off / human / script)"
                    )
                self._mode = mode
                # 切り替えた瞬間に前の持ち主が残らないよう履歴を捨てる
                self._last_human = None
                self._last_auto = None
            if cooldown is not None:
                self._cooldown = max(0.0, float(cooldown))

    def get(self) -> dict[str, Any]:
        """現在の調停の設定と実績を返す（設定画面・確認用）。"""
        with self._lock:
            return {
                "mode": self._mode,
                "cooldown": self._cooldown,
                "rejected_human": self._rejected["human"],
                "rejected_auto": self._rejected["auto"],
            }

    def set_notifier(self, fn: Callable[[str], None] | None) -> None:
        """拒否を知らせる先を差し替える（既定は print とログ）。

        GUI ならログ欄へ、CUI なら標準出力へ、といった具合に
        呼び出し側が決められるようにする。
        """
        with self._lock:
            self._notifier = fn

    @staticmethod
    def is_human_source(source: str | None) -> bool:
        """人の手による操作か。source が無いもの(None)は自動側とみなす。"""
        return str(source).lower() in HUMAN_SOURCES

    def set_handed_over(self, handed: bool) -> None:
        """自動側が操作を人へ渡している最中かを設定する。

        一時停止のように、自動側が明示的に手を引いている間は調停を
        止める。cooldown は『最後に自動側が申告してから何秒』で測る
        ため、自動側が申告を続けている限り人は永久に通らない。停止して
        いるつもりでも、keepalive や別スレッドの申告が続けば同じである。
        時間ではなく、渡したという事実で判断する。
        """
        with self._lock:
            self._handed_over = bool(handed)
            if handed:
                # 渡した時点で自動側の履歴を捨てる。残すと、渡した直後の
                # 人の申告が『直前まで自動側が書いていた』と判定される。
                self._last_auto = None

    def is_handed_over(self) -> bool:
        """いま人へ渡している最中かを返す（読むだけ）。"""
        with self._lock:
            return bool(self._handed_over)

    def release_handover(self) -> None:
        """Stop などで渡した状態と自動側の履歴を解く。

        誰の押下も残さない経路であり、渡したままだと次の実行で
        調停が効かない。cooldown は『最後に自動側が申告してから何秒』
        で測るため、履歴を残すと停止後も人の操作が拒否される。
        """
        with self._lock:
            self._handed_over = False
            self._last_auto = None

    def accept(self, source: str | None, releasing: bool = False) -> bool:
        """その申告を受理してよいか。

        判断は対称に作ってある。優先する側が書いてから cooldown の
          間だけ、反対側の申告を拒否する。どちらを優先するかが mode。
          「所有権」という長く続く状態を持たずに済むので、状態が増えない。

        cooldown は「最後に触ってからの無操作時間」である。触り続けて
        いる間は優先が続く（スライド式）。これは意図した挙動であり、
        人が触っている間は人が取りたい、という使い方に合わせている。
        逆に言うと、優先側が触り続けると反対側は永久に通らない。
        一時停止などで明示的に手を引く場合は set_handed_over(True) で
        調停そのものを止める（時間ではなく事実で判断する）。

        releasing が真の申告は拒否しない。解放と中立を拒否すると
        押しっぱなしが残り、調停の都合で安全が損なわれる。押下を断る
        ことはできても、離す操作を断る理由は無い。

        拒否の通知は錠の外で出す。帳簿の更新だけを錠の中で行い、
        通知先の呼び出しは外で行うため、ここでは通知の要否だけを
        覚えて錠を出る。
        """
        notify: tuple[str | None, str] | None = None
        if releasing:
            return True
        with self._lock:
            if self._handed_over:
                # 自動側が手を引いている間は調停しない。時間ではなく、
                # 渡したという事実で判断する（一時停止中など）。
                return True

            if self._mode == "off":
                return True

            now = time.perf_counter()
            is_human = self.is_human_source(source)

            if self._mode == "human":
                if is_human:
                    # 人が触ったら即座に主導権を取る（横取り＝preempt）。
                    # 触り続けている間は優先が続く（スライド式・意図どおり）。
                    self._last_human = now
                    return True
                if (
                    self._last_human is not None
                    and now - self._last_human < self._cooldown
                ):
                    self._rejected["auto"] += 1
                    notify = (source, "人の操作")
                else:
                    self._last_auto = now
                    return True
            elif self._mode == "script":
                # スクリプトを優先する。書き続ける間は優先が続く
                # （スライド式・意図どおり）。
                if not is_human:
                    self._last_auto = now
                    return True
                if (
                    self._last_auto is not None
                    and now - self._last_auto < self._cooldown
                ):
                    self._rejected["human"] += 1
                    notify = (source, "スクリプト")
                else:
                    self._last_human = now
                    return True
            else:
                # 未知の mode は調停しない。黙って優先側として扱うより、
                # 通して利用者の手で止められる形にする。
                return True
        # ここへ落ちてくるのは拒否した経路だけである。受理した経路は
        #   上で True を返しているため、末尾の return True は到達不能で
        #   置かない（warn_no_return が将来の経路追加を見張る）。
        self._notify_reject(*notify)
        return False

    def _notify_reject(self, source: str | None, winner: str) -> None:
        """拒否したことを知らせる。黙って捨てない。

        ただし毎回出すと、連打や毎フレームの申告で通知が溢れて
        肝心の内容が流れる。REJECT_NOTIFY_INTERVAL の間は数だけ数え、
        次に出すときへまとめる。

        スクリプトを優先して人の操作を拒否した場合は、代わりの
        道を必ず添える。塞ぐだけで方法を書かないと、利用者からは動かな
        いとしか見えない。一時停止すれば操作でき、そのとき自動側の押下
        は退避されるため、押しっぱなしのまま渡ることもない。

        通知先の呼び出しは錠の外で行う。GUI 側の通知先が Sender へ
        呼び戻すと、錠を持ったままではデッドロックするためである。
        間引きの帳簿だけを錠の中で済ませ、文面を作ってから外へ出る。
        """
        with self._lock:
            now = time.perf_counter()
            if now - self._last_notify < REJECT_NOTIFY_INTERVAL:
                self._suppressed += 1
                return
            extra = f"（ほか {self._suppressed} 件）" if self._suppressed else ""
            self._last_notify = now
            self._suppressed = 0
            notifier = self._notifier
            mode = self._mode
            cooldown = self._cooldown
        msg = (
            f"入力調停: {source} からの操作を受け付けませんでした{extra}。"
            f"（{winner} を優先中 / mode={mode} "
            f"cooldown={cooldown}秒）"
        )
        if self.is_human_source(source):
            msg += "　一時停止すると操作できます。"
        if callable(notifier):
            notifier(msg)
            return
        print(msg)
        self._logger.info(msg)
