#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""command_runner.py - コマンド実行の状態遷移を1つに持つ層。

Window から「走る・止まる・見張る・後始末」の手順をここへ移している。
Window に残すのは選択（一覧・絞り込み・生成）と見た目（ボタン・題名）だけ。

なぜ分けるか:
  ・実行状態（idle / running / stopping）と世代（token）の不変条件が、
    画面のあちこちに散ると、後始末の重複や取り違えが起きる。
    変える・読む場所をここへ集めれば、条件はここだけ見ればよい。
  ・tkinter を触らない。タイマーは呼び出し側が渡す schedule / cancel
    に任せ、描画は on_state_changed / on_list_refresh の合図で
    呼び出し側が行う。ヘッドレスの検証でそのまま動く。

呼び出し側（Window）が持つもの:
  ・選ぶこと（assignCommand・絞り込み・パレット）と作ること（_buildCommand）。
  ・COM 設定の写し（_snapshotSerialConfig。tk 変数を読むため GUI 側）。
  ・見た目の反映（ボタン・題名）。合図が来たら runner の状態を読む。
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from core import CommandStats
from loguru import logger

# 停止を頼んでから、戻って来ないかを見に行く間隔(ms)。
# 後始末が呼ばれない経路に入ったとき、操作だけは戻すために使う。
STOP_WATCH_MS = 500

# 停止しないことを知らせる間隔(ms)。500ms ごとの見張りで毎回出すと
# ログが埋まるため、知らせるのはこの間隔だけにする。
STOP_NOTIFY_MS = 5000


class CommandRunner:
    """実行の状態機械。idle / running / stopping の3状態だけを持つ。

    画面の表示ではなくここを唯一の情報源にする（表示は結果）。
    """

    def __init__(
        self,
        stats: dict[str, dict],
        schedule: Callable[[int, Callable[[], None]], Any],
        cancel: Callable[[Any], None],
        notify_user: Callable[[str], None],
        on_state_changed: Callable[[], None],
        on_list_refresh: Callable[[], None],
    ) -> None:
        """stats は使用履歴の辞書（CommandStats が読む実体）。

        schedule(ms, fn) は ms 後に fn を GUI スレッドで走らせるもの
        （Window は root.after を渡す）。cancel はその予約の取り消し。
        notify_user は利用者への1行通知（Window は print を渡す）。
        on_state_changed は状態が変わった合図、on_list_refresh は
        一覧の作り直しの合図。描画自体は呼び出し側が行う。
        """
        self._stats = stats
        self._schedule = schedule
        self._cancel = cancel
        self._notify = notify_user
        self._on_state_changed = on_state_changed
        self._on_list_refresh = on_list_refresh
        # 実行状態。idle / running / stopping の3つ。
        self._state = "idle"
        # 実行の世代。Start のたびに1つ進める。後始末が「どの実行に対する
        # ものか」を見分けるために使う（同じ番号のときだけ画面へ反映する）。
        self._run_token = 0
        # 停止を待っている秒数。0 なら待っていない。題名に出して
        # 「終了するしかない」状態が見て分かるようにする。
        self._stop_waited = 0
        # いま走らせている（または止めかけている）コマンド。
        self._running: Any = None
        # 開始時に渡された送り先。停止要求（end）に同じものを渡す。
        self._ser: Any = None
        # 停止の見張りの予約。終了時に取り消せるよう覚えておく。
        self._watch_id: Any = None
        # 終了処理が始まったか。始まったら worker からの後始末は
        # 受け付けない（画面はもう畳む段なので）。
        self._closing = False
        # 使用履歴に触ったか。書き出しは終了時に1回だけ行う。
        self.stats_dirty = False

    # -- 読み取り -----------------------------------------------------------

    @property
    def state(self) -> str:
        """実行状態（idle / running / stopping）。"""
        return self._state

    @property
    def stop_waited(self) -> int:
        """停止を待っている秒数（ms 単位の蓄積）。0 なら待っていない。"""
        return self._stop_waited

    @property
    def running_command(self) -> Any:
        """いま走らせているコマンド。空きなら None。"""
        return self._running

    def is_busy(self) -> bool:
        """コマンドが走っている、または停止処理の最中かを返す。

        以前はボタンの表示文字（"Start" かどうか）で判定していた。
        表示は状態を映すためのもので、状態そのものではない。文言を
        変えた・開始の途中で例外が出た・外から表示を触った、のどれでも
        判定が狂う。状態はここだけで持つ。
        """
        return self._state != "idle"

    # -- 開始・停止 ---------------------------------------------------------

    def request_start(self, command: Any, ser: Any) -> None:
        """選択中のコマンドを開始する。

        command は呼び出し側が選んで作った実体、ser は送り先
        （未接続なら None）。COM 設定の写しは呼び出し側が済ませておく
        （tk 変数を読むため、ここでは触らない）。
        順序が重要。先に状態を実行中へ倒してから start する。
        逆にすると、ごく短いコマンドが start の中で終わった場合に、
        後始末が先に走り、あとから実行中の見た目へ書き換えてしまう。
        """
        # 二重起動を断る。Tk は同じボタンのコールバックを並行実行しないが、
        # F6・パレット・外部呼び出しからも入って来られる。
        if self.is_busy():
            self._notify("すでにコマンドが動いています")
            logger.warning("A command is already running")
            return

        # 旧コードはメッセージを出すだけで先へ進み、None.NAME で落ちていた
        if command is None:
            self._notify("No commands have been assigned yet.")
            logger.warning("No commands have been assigned yet.")
            return

        # シリアル未接続でも開始は妨げない。従来はここに判定が無く、
        # 画像認識だけのコマンドや机上検証（verify_all）は繋がなくても
        # 最後まで走っていた。ここで一律に止めると、既存のコマンドが
        # 動かなくなる。既存コマンドは REQUIRES_SERIAL を書いていないので、
        # 既定は「不要」でなければ後方互換が壊れる。
        # 明示的に REQUIRES_SERIAL = True と書いたコマンドだけを止める。
        if getattr(command, "REQUIRES_SERIAL", False):
            if ser is None or not ser.isOpened():
                message = "このコマンドは COM ポートの接続が必要です"
                self._notify(message)
                logger.warning(message)
                return
        elif ser is None or not ser.isOpened():
            # 止めはしないが、黙って始めると「動いているのに何も起きない」
            # に見える。操作を送る段で失敗することを先に知らせておく。
            self._notify("注意: COMポートが未接続です（操作の送信はできません）")

        message = f"Start {command.NAME}"
        self._notify(message)
        logger.info(message)
        # 前回の実行を先に読む。record より後だと今回の分で上書きされる。
        cmd_name = str(command.NAME)
        before = CommandStats.summary(self._stats, cmd_name)
        if before:
            self._notify(f"  ({before})")

        # 先に実行中へ倒す。start はスレッドを起こすので、戻ったときには
        # もう終わっていることがある。あとから実行中の見た目にすると、
        # 終了後の後始末を上書きして Stop 表示のまま固まる。
        self._running = command
        self._ser = ser
        self._state = "running"
        self._on_state_changed()

        # 実行ごとに世代を進める。後始末（stop_post）が自分の世代の
        # ものかを見分けるために使う。番号が無いと、止まりきらなかった
        # 前回のスレッドが後から後始末を呼んだときに、いま走っている
        # コマンドの画面を「空き」へ戻してしまう。
        self._run_token += 1
        token = self._run_token

        try:
            started = command.start(ser, lambda: self.stop_post(token))
        except Exception:
            # スレッドを起こす前に落ちると後始末も呼ばれない。ここで戻す。
            self._notify("コマンドを開始できませんでした")
            logger.error(traceback.format_exc())
            self.post_on_gui()
            return

        # start が例外を出さずに「何もしなかった」場合を拾う。
        # PythonCommand は前のスレッドが生きていれば False、MCU コマンドは
        # ポート未接続で False を返す。どちらも後始末は呼ばれないため、
        # ここで戻さないと画面だけ実行中のまま固まる。
        # 判定は is False で行う。start を上書きしている既存のコマンドは
        # 戻り値を返さない（None）ため、not started で見ると全て失敗扱いに
        # なってしまう。None は従来どおり「開始できた」とみなす。
        if started is False:
            self._notify("コマンドを開始できませんでした")
            logger.warning(f"Failed to start: {cmd_name}")
            self.post_on_gui()
            return

        # 使用履歴は開始できたあとで数える。開始前に足すと、起動に失敗した
        # 回数まで「実行回数」に混ざる。ファイルへ書くのは終了時に1回だけ。
        CommandStats.record(self._stats, cmd_name)
        self.stats_dirty = True
        # 記録は辞書へ即時入るので、選択肢もその場で作り直せる。
        # Reload を待つと「さっき使ったのに最近使ったに出ない」ことになる。
        self._on_list_refresh()

    def request_stop(self) -> None:
        """実行中のコマンドへ停止を要求する。

        停止したことにするのは、後始末（stop_post）が呼ばれたとき。
        ただし end() が例外を投げた・コマンドが後始末を呼ばない・
        外部I/O で抜けられない、のいずれでも呼ばれない。その場合に
        ボタンが disabled のまま操作を受け付けなくなるため、見張りを置く。
        """
        command = self._running
        if command is None:
            return
        if self._state != "running":
            # 停止の最中・空きの Stop は受け付けない。二重に end() を
            # 呼ぶ意味が無い（Esc 側は既にこの条件で弾いている）。
            return
        message = f"Stop {command.NAME}"
        self._notify(message)
        logger.info(message)
        self._state = "stopping"
        self._on_state_changed()
        # 一時停止のまま止めると、退避と「人へ渡した」印が残る。止める
        # 前に戻す。resume は停止中でなければ何もしない。渡しっぱなし
        # のまま次を実行すると、調停が効かない（handed_over 残留）。
        resume = getattr(command, "resume", None)
        if callable(resume):
            try:
                resume()
            except Exception:
                logger.error(traceback.format_exc())
        try:
            command.end(self._ser)
        except Exception:
            logger.error(traceback.format_exc())
            self._notify("停止要求で例外が発生しました")
            self.post_on_gui(self._run_token)
            return
        self._watch_id = self._schedule(
            STOP_WATCH_MS, lambda: self._watch_stopped(0, self._run_token)
        )

    # -- 見張り・後始末 -----------------------------------------------------

    def _watch_stopped(self, waited: int = 0, token: int | None = None) -> None:
        """停止を頼んだのに戻って来ない場合、操作だけは戻す。

        スレッドが生きているかどうかは実体に聞く。生きていれば、まだ
        待つ。死んでいるのに後始末が来ていないなら、後始末が呼ばれない
        経路に入ったということなので、こちらで画面を空きへ戻す。

        生きている間は待ち続ける。時間で打ち切って画面だけ空きへ戻すと、
        止まっていないスレッドと次に始めたコマンドが同じシリアルを同時に
        操作することになる。Python のスレッドは外から安全に殺せないので、
        「勝手に戻す」よりも「戻せないことを伝える」ほうが安全側になる。
        代わりに、待っていることと経過を一定間隔で知らせる。

        token は Stop を始めた時点の世代番号。見張りが遅れて発火した
        ときに、すでに次の実行が始まっていれば触らない。
        """
        if token is not None and token != self._run_token:
            logger.debug("古い実行の見張りを終了しました")
            return

        if self._state != "stopping":
            return
        command = self._running
        thread = getattr(command, "thread", None) if command is not None else None
        if thread is not None and thread.is_alive():
            waited += STOP_WATCH_MS
            if waited % STOP_NOTIFY_MS == 0:
                message = (
                    f"コマンドが停止しません（経過 {waited // 1000}秒）。"
                    "外部の入出力を待っている可能性があります"
                )
                self._notify(message)
                logger.warning(message)
                self._stop_waited = waited
                self._on_state_changed()
            self._watch_id = self._schedule(
                STOP_WATCH_MS, lambda: self._watch_stopped(waited, token)
            )
            return
        self._stop_waited = 0
        message = "コマンドの後始末が呼ばれませんでした。操作を戻します"
        self._notify(message)
        logger.warning("postProcess was not called. restoring the UI")
        self.post_on_gui(token)

    def stop_post(self, token: int | None = None) -> None:
        """コマンド終了後の後始末。

        呼び出し元は PythonCommandBase の _cleanup で、コマンドを走らせて
        いるワーカースレッドから直接呼ばれる。描画は GUI スレッドへ
        渡すため schedule(0) で積む（呼び出し側は root.after を渡す）。
        """
        if token is not None and token != self._run_token:
            # 止まりきらなかった前回のスレッドが、いまごろ後始末を呼んで
            # きた場合。すでに別のコマンドが走っているので画面は触らない。
            logger.debug("古い実行の後始末を無視しました")
            return
        if self._closing:
            # 終了処理が始まっている。ここで積むと、破棄の直前に割り込んで
            # 破棄途中の部品を触ることがある。画面はもう畳む段なので不要。
            logger.debug("終了処理中のため後始末を省きました")
            return
        try:
            self._schedule(0, lambda: self.post_on_gui(token))
        except Exception:
            # 終了処理の最中に終わった場合。画面はもう無いので何もしない。
            # tkinter の TclError / RuntimeError を名指ししないのは、
            # この層が tkinter を import しないため。握る範囲は
            # 予約の失敗に限られ、後始末の本体は別経路で守られる。
            logger.debug("予約に失敗したため後始末を省きました")

    def cancel_watch(self) -> None:
        """停止の見張りの予約を取り消す（終了時に呼ぶ）。"""
        watch_id, self._watch_id = self._watch_id, None
        if watch_id is None:
            return
        try:
            self._cancel(watch_id)
        except Exception:
            pass

    def notify_closing(self) -> None:
        """終了処理が始まったことを知らせる。以後の後始末は受け付けない。"""
        self._closing = True

    def shutdown(self, ser: Any) -> bool:
        """終了に先立ってコマンドを止める。

        alive は「停止を要求されていないか」でしかない。finish() も
        sendStopRequest() もスレッドが抜ける前に False にするため、
        alive=False でもスレッドが後始末の途中ということがある。
        そこだけを見て戻ると、直後に閉じたシリアルへ書きに行く。
        判断はスレッドの生存で行う。

        止まりきるまでは待たない。長い wait や外部I/O の最中だと
        いつ抜けるか読めず、待つと画面が固まったように見える。
        スレッドは daemon なので、抜けきらなくてもプロセスは終わる。
        ここでの目的は、閉じたシリアルへ書きに行くのを減らすこと。

        戻り値は「止まりきったか」。止まらなかった場合は呼び出し側
        で live の送出を止め、閉じた線への書き込みを減らす。
        残りの書き込み自体は無害（Transport._write が閉じた線への
        書き込みを例外として握り、live worker も送出失敗を数えるだけ）。
        """
        self.cancel_watch()
        command = self._running
        if command is None:
            return True

        thread: Any = getattr(command, "thread", None)
        running = thread is not None and thread.is_alive()
        if not running and not getattr(command, "alive", False):
            return True

        if getattr(command, "alive", False):
            try:
                command.end(ser)
            except Exception as e:
                logger.warning(f"停止要求で例外: {e}")

        # 後始末（キーを離す・postProcess）が走る余地を与える。待ちは
        # 短く区切る。ここで長く待つと終了操作そのものが固まって見える。
        if running:
            thread.join(timeout=1.0)
            if thread.is_alive():
                self._notify("コマンドが停止しないまま終了します")
                logger.warning("Command did not stop in time. exiting anyway")
                # 生き残りが閉じたシリアルへ書き続けないよう、live の
                # 送出だけはここで止める。スレッド自体は daemon なので
                # プロセス終了と共に終わる。
                try:
                    if ser is not None:
                        ser.discardLive()
                        ser.stopLiveWorker(0.5)
                except Exception as e:
                    logger.warning(f"live worker の停止で例外: {e}")
                return False
        return True

    # -- 内部 ---------------------------------------------------------------

    def post_on_gui(self, token: int | None = None) -> None:
        """後始末の本体。必ず GUI スレッドで動かすこと。

        token は「どの実行の後始末か」を表す世代番号。積んでから
        実際に走るまでの間に、次の実行が始まっていることがある。
        積む時点だけで見ても足りず、走る時点でも見る必要がある。
        （実行1の後始末が積まれたまま見張りが先に画面を戻し、
        利用者が実行2を始めたあとで積まれていた分が走ると、動いて
        いる実行2の画面が空きへ戻り Start が押せてしまう）

        状態を戻すことを最優先にする。一覧の作り直しは付随処理なので、
        そこで例外が出ても状態は戻っていなければならない。以前は
        ひと続きだったため、履歴の更新で落ちると Stop 表示のまま
        操作を受け付けなくなる余地があった。
        """
        if token is not None and token != self._run_token:
            logger.debug("古い実行の GUI 後処理を無視しました")
            return

        # 同じ実行について複数回呼ばれても、一覧の作り直しまでは
        # 繰り返さない。stop_post 経由と見張り経由の両方から来ることが
        # あるため。状態の復元は冪等なので通す。
        already_idle = self._state == "idle"

        self._state = "idle"
        self._stop_waited = 0
        self._running = None
        self._on_state_changed()

        # ここから先は無くても操作できる処理。失敗しても状態は戻す。
        if already_idle:
            # すでに戻っている＝別経路で後始末済み。一覧の作り直しを
            # 二重に走らせても実害は無いが、選択の復元が二度動くため省く。
            return
        try:
            # 走行中は選択を動かさないため一覧の更新を見送っている。
            # 空いたこの時点で一覧を見直し、「最近使った」「よく使う」の
            # 並びと絞り込みを実際の履歴に合わせる。
            self._on_list_refresh()
        except Exception:
            logger.error(traceback.format_exc())
            self._notify("コマンド一覧の更新に失敗しました（操作は続けられます）")
