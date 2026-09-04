#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import time
from collections import OrderedDict
from enum import Enum, IntEnum, IntFlag, auto
from typing import Any, List, Optional
from logging import getLogger, DEBUG, NullHandler


class Button(IntFlag):
    Y = auto()
    B = auto()
    A = auto()
    X = auto()
    L = auto()
    R = auto()
    ZL = auto()
    ZR = auto()
    MINUS = auto()
    PLUS = auto()
    LCLICK = auto()
    RCLICK = auto()
    HOME = auto()
    CAPTURE = auto()


class Hat(IntEnum):
    TOP = 0
    TOP_RIGHT = 1
    RIGHT = 2
    BTM_RIGHT = 3
    BTM = 4
    BTM_LEFT = 5
    LEFT = 6
    TOP_LEFT = 7
    CENTER = 8


class Stick(Enum):
    LEFT = auto()
    RIGHT = auto()


class Tilt(Enum):
    UP = auto()
    RIGHT = auto()
    DOWN = auto()
    LEFT = auto()
    R_UP = auto()
    R_RIGHT = auto()
    R_DOWN = auto()
    R_LEFT = auto()


# direction value definitions
MIN_VAL = 0
CENTER = 128
MAX_VAL = 255


# serial format
class SendFormat:
    def __init__(self) -> None:

        self._logger = getLogger(__name__)
        self._logger.addHandler(NullHandler())
        self._logger.setLevel(DEBUG)
        self._logger.propagate = True

        # This format structure needs to be the same as the one written in Joystick.c
        self.format = OrderedDict([
            ('btn', 0),  # send bit array for buttons
            ('hat', Hat.CENTER),
            ('lx', CENTER),
            ('ly', CENTER),
            ('rx', CENTER),
            ('ry', CENTER),
        ])

        self.L_stick_changed = False
        self.R_stick_changed = False
        self.Hat_pos = Hat.CENTER

    def setButton(self, btns: List[Any]) -> None:
        for btn in btns:
            self.format['btn'] |= btn

    def unsetButton(self, btns: List[Any]) -> None:
        for btn in btns:
            self.format['btn'] &= ~btn

    def resetAllButtons(self) -> None:
        self.format['btn'] = 0

    def setHat(self, btns: List[Any]) -> None:
        # self._logger.debug(btns)
        if not btns:
            self.format['hat'] = self.Hat_pos
        else:
            self.Hat_pos = btns[0]
            self.format['hat'] = btns[0]  # takes only first element

    def unsetHat(self) -> None:
        # if self.Hat_pos is not Hat.CENTER:
        self.Hat_pos = Hat.CENTER
        self.format['hat'] = self.Hat_pos

    def setAnyDirection(self, dirs: List[Any]) -> None:
        for dir in dirs:
            if dir.stick == Stick.LEFT:
                if self.format['lx'] != dir.x or self.format['ly'] != 255 - dir.y:
                    self.L_stick_changed = True

                self.format['lx'] = dir.x
                self.format['ly'] = 255 - dir.y  # NOTE: y axis directs under
            elif dir.stick == Stick.RIGHT:
                if self.format['rx'] != dir.x or self.format['ry'] != 255 - dir.y:
                    self.R_stick_changed = True

                self.format['rx'] = dir.x
                self.format['ry'] = 255 - dir.y

    def unsetDirection(self, dirs: List[Any]) -> None:
        if Tilt.UP in dirs or Tilt.DOWN in dirs:
            self.format['ly'] = CENTER
            self.format['lx'] = self.fixOtherAxis(self.format['lx'])
            self.L_stick_changed = True
        if Tilt.RIGHT in dirs or Tilt.LEFT in dirs:
            self.format['lx'] = CENTER
            self.format['ly'] = self.fixOtherAxis(self.format['ly'])
            self.L_stick_changed = True
        if Tilt.R_UP in dirs or Tilt.R_DOWN in dirs:
            self.format['ry'] = CENTER
            self.format['rx'] = self.fixOtherAxis(self.format['rx'])
            self.R_stick_changed = True
        if Tilt.R_RIGHT in dirs or Tilt.R_LEFT in dirs:
            self.format['rx'] = CENTER
            self.format['ry'] = self.fixOtherAxis(self.format['ry'])
            self.R_stick_changed = True

    # Use this to fix an either tilt to max when the other axis sets to 0
    def fixOtherAxis(self, fix_target: int) -> int:
        if fix_target == CENTER:
            return CENTER
        else:
            return 0 if fix_target < CENTER else 255

    def resetAllDirections(self) -> None:
        self.format['lx'] = CENTER
        self.format['ly'] = CENTER
        self.format['rx'] = CENTER
        self.format['ry'] = CENTER
        self.L_stick_changed = True
        self.R_stick_changed = True
        self.Hat_pos = Hat.CENTER

    def convert2str(self) -> str:
        str_format = ''
        str_L = ''
        str_R = ''
        str_Hat = ''
        space = ' '

        # set bits array with stick flags
        send_btn = int(self.format['btn']) << 2
        # send_btn |= 0x3
        if self.L_stick_changed:
            send_btn |= 0x2
            str_L = format(self.format['lx'], 'x') + space + format(self.format['ly'], 'x')
        if self.R_stick_changed:
            send_btn |= 0x1
            str_R = format(self.format['rx'], 'x') + space + format(self.format['ry'], 'x')
        # if self.Hat_changed:
        str_Hat = str(int(self.format['hat']))
        # format(send_btn, 'x') + \
        # print(hex(send_btn))
        str_format = format(send_btn, '#06x') + \
                     (space + str_Hat) + \
                     (space + str_L if self.L_stick_changed else '') + \
                     (space + str_R if self.R_stick_changed else '')

        self.L_stick_changed = False
        self.R_stick_changed = False

        # print(str_format)
        return str_format  # the last space is not needed


# This class handle L stick and R stick at any angles
class Direction:
    def __init__(self, stick: "Stick", angle: float, magnification: float = 1.0,
                 isDegree: bool = True, showName: Optional[str] = None) -> None:

        self._logger = getLogger(__name__)
        self._logger.addHandler(NullHandler())
        self._logger.setLevel(DEBUG)
        self._logger.propagate = True

        self.stick = stick
        self.angle_for_show = angle
        self.showName = showName
        if magnification > 1.0:
            self.mag = 1.0
        elif magnification < 0:
            self.mag = 0.0
        else:
            self.mag = magnification

        if isinstance(angle, tuple):
            # assuming (X, Y)
            self.x = angle[0]
            self.y = angle[1]
            self.showName = '(' + str(self.x) + ', ' + str(self.y) + ')'
            print('押し込み量', self.showName)
        else:
            angle = math.radians(angle) if isDegree else angle

            # We set stick X and Y from 0 to 255, so they are calculated as below.
            # X = 127.5*cos(theta) + 127.5
            # Y = 127.5*sin(theta) + 127.5
            self.x = math.ceil(127.5 * math.cos(angle) * self.mag + 127.5)
            self.y = math.floor(127.5 * math.sin(angle) * self.mag + 127.5)

    def __repr__(self) -> str:
        if self.showName:
            return "<{}, {}>".format(self.stick, self.showName)
        else:
            return "<{}, {}[deg]>".format(self.stick, self.angle_for_show)

    def __eq__(self, other: object) -> bool:
        if not type(other) is Direction:
            return False

        if self.stick == other.stick and self.angle_for_show == other.angle_for_show:
            return True
        else:
            return False

    def getTilting(self) -> List[Any]:
        tilting = []
        if self.stick == Stick.LEFT:
            if self.x < CENTER:
                tilting.append(Tilt.LEFT)
            elif self.x > CENTER:
                tilting.append(Tilt.RIGHT)

            if self.y < CENTER - 1:
                tilting.append(Tilt.DOWN)
            elif self.y > CENTER - 1:
                tilting.append(Tilt.UP)
        elif self.stick == Stick.RIGHT:
            if self.x < CENTER:
                tilting.append(Tilt.R_LEFT)
            elif self.x > CENTER:
                tilting.append(Tilt.R_RIGHT)

            if self.y < CENTER - 1:
                tilting.append(Tilt.R_DOWN)
            elif self.y > CENTER - 1:
                tilting.append(Tilt.R_UP)
        return tilting


# Left stick for ease of use
Direction.UP = Direction(Stick.LEFT, 90, showName='UP')
Direction.RIGHT = Direction(Stick.LEFT, 0, showName='RIGHT')
Direction.DOWN = Direction(Stick.LEFT, -90, showName='DOWN')
Direction.LEFT = Direction(Stick.LEFT, -180, showName='LEFT')
Direction.UP_RIGHT = Direction(Stick.LEFT, 45, showName='UP_RIGHT')
Direction.DOWN_RIGHT = Direction(Stick.LEFT, -45, showName='DOWN_RIGHT')
Direction.DOWN_LEFT = Direction(Stick.LEFT, -135, showName='DOWN_LEFT')
Direction.UP_LEFT = Direction(Stick.LEFT, 135, showName='UP_LEFT')
# Right stick for ease of use
Direction.R_UP = Direction(Stick.RIGHT, 90, showName='UP')
Direction.R_RIGHT = Direction(Stick.RIGHT, 0, showName='RIGHT')
Direction.R_DOWN = Direction(Stick.RIGHT, -90, showName='DOWN')
Direction.R_LEFT = Direction(Stick.RIGHT, -180, showName='LEFT')
Direction.R_UP_RIGHT = Direction(Stick.RIGHT, 45, showName='UP_RIGHT')
Direction.R_DOWN_RIGHT = Direction(Stick.RIGHT, -45, showName='DOWN_RIGHT')
Direction.R_DOWN_LEFT = Direction(Stick.RIGHT, -135, showName='DOWN_LEFT')
Direction.R_UP_LEFT = Direction(Stick.RIGHT, 135, showName='UP_LEFT')


# handles serial input to Joystick.c
class KeyPress:
    def __init__(self, ser: Any, source: str = "script") -> None:

        self._logger = getLogger(__name__)
        self._logger.addHandler(NullHandler())
        self._logger.setLevel(DEBUG)
        self._logger.propagate = True

        self.ser = ser
        # 2026/08/25 段 V-b: 誰の操作かを Sender へ伝えるための名札。
        #   入力調停（Sender._accept）はこの名札を見て受理／拒否を決める。
        #   既定は "script"。段 V-a までと同じ扱いなので、引数を渡さない
        #     既存の呼び出しは挙動が1文字も変わらない。
        #   キーボード操作用の KeyPress には Window が "keyboard" を渡す。
        #     Sender.HUMAN_SOURCES に "keyboard" が入っているため、
        #     human モードでは人の手入力として優先される。
        self.source = str(source) if source else "script"
        self.format = SendFormat()
        self.holdButton = []
        self.btn_name2 = ['LEFT', 'RIGHT', 'UP', 'DOWN', 'UP_LEFT', 'UP_RIGHT', 'DOWN_LEFT', 'DOWN_RIGHT']

        self.pushing_to_show = None
        self.pushing = None
        self.pushing2 = None
        self._pushing = None
        self._chk_neutral = None
        self.NEUTRAL = dict(self.format.format)

        self.input_time_0 = time.perf_counter()
        self.input_time_1 = time.perf_counter()
        self.inputEnd_time_0 = time.perf_counter()
        self.was_neutral = True

    def input(self, btns: Any, ifPrint: bool = True) -> None:
        """ボタン入力を送信する。

        ifPrint は現在未使用（後方互換のため引数だけ残置）。
        """
        self._pushing = dict(self.format.format)
        if not isinstance(btns, list):
            btns = [btns]
        else:
            # 呼び出し元のリストを直接 append すると、hold() が渡した
            # リストまで書き換わる副作用が出るためコピーして扱う
            btns = list(btns)

        for btn in self.holdButton:
            if btn not in btns:
                btns.append(btn)

        # 段 IV: 自分の姿勢を持たず、Sender へ「差分」を申告する。
        #   押したいものだけを渡すので、別系統が押しているボタンや
        #   触っていないスティックには影響しない（ARC-01 の解）。
        #   互換のため self.format も同じ内容へ進める（段 IV では
        #     まだ外部が読んでいる可能性を考慮）。
        self.format.setButton([btn for btn in btns if type(btn) is Button])
        self.format.setHat([btn for btn in btns if type(btn) is Hat])
        self.format.setAnyDirection([btn for btn in btns if type(btn) is Direction])

        self._applyToSender(btns)
        self._writeCurrent()
        self.input_time_0 = time.perf_counter()

        # self._logger.debug(f": {list(map(str,self.format.format.values()))}")

    def inputEnd(self, btns: Any, ifPrint: bool = True, unset_hat: bool = True) -> None:
        # self._logger.debug(f"input end: {btns}")
        self.pushing2 = dict(self.format.format)

        self.ed = time.perf_counter()
        if not isinstance(btns, list):
            btns = [btns]
        # self._logger.debug(btns)

        # get tilting direction from angles
        tilts = []
        for dir in [btn for btn in btns if type(btn) is Direction]:
            tiltings = dir.getTilting()
            for tilting in tiltings:
                tilts.append(tilting)
        # self._logger.debug(tilts)

        # 2026/08/25 段 IV-b（B1 の修正）: hold 中の Hat を守る
        #   input() には holdButton を復元する手当てがあるが、inputEnd()
        #   には無かった。そのためボタンを離すだけで unsetHat() が走り、
        #   押しっぱなしの十字キーが中立へ落ちていた（PORTBACK 8-2 の B1）。
        #   input 側と同じ「hold は守る」規則を inputEnd にも通す。
        hold_hat = next((b for b in self.holdButton if type(b) is Hat), None)

        self.format.unsetButton([btn for btn in btns if type(btn) is Button])
        if unset_hat:
            self.format.unsetHat()
            if hold_hat is not None:
                # hold 中の十字キーは中立へ戻さず、押しっぱなしを保つ
                self.format.setHat([hold_hat])
        self.format.unsetDirection(tilts)
        self._releaseFromSender(btns, tilts, unset_hat, hold_hat)
        self._writeCurrent()

    def hold(self, btns: Any) -> None:
        if not isinstance(btns, list):
            btns = [btns]

        for btn in btns:
            if btn in self.holdButton:
                print('Warning: ' + btn.name + ' is already in holding state')
                self._logger.warning(f"Warning: {btn.name} is already in holding state")
                return

            self.holdButton.append(btn)
        # 2026/08/25 段 V-d: Hat の押しっぱなしを Sender へも申告する。
        #   これが無いと、別の KeyPress が中立へ戻す申告をした時点で
        #     押しっぱなしの十字キーが落ちる（B1 の跨ぎ問題 / PORTBACK 12-2）。
        #   holdButton は各 KeyPress が自分で持つため、他系統からは見えない。
        #     Sender に預けることで、どの系統から中立が来ても守られる。
        hold_hat = next((b for b in btns if type(b) is Hat), None)
        if hold_hat is not None:
            hold = getattr(self.ser, "holdHat", None)
            if callable(hold):
                hold(int(hold_hat), source=self.source)
        self.input(btns)

    def holdEnd(self, btns: Any) -> None:
        if not isinstance(btns, list):
            btns = [btns]

        for btn in btns:
            # holdButton に無いものを remove すると ValueError で落ちるため
            # 存在チェックしてから外す（多重 holdEnd を許容する）
            if btn in self.holdButton:
                self.holdButton.remove(btn)
            else:
                self._logger.warning(f"{btn} is not in holding state")

        # 段 V-d: Hat の押しっぱなしを取り下げる。自分の分だけ外すので、
        #   他の系統がまだ押していればその向きが残る。
        if any(type(b) is Hat for b in btns):
            if not any(type(b) is Hat for b in self.holdButton):
                release = getattr(self.ser, "releaseHat", None)
                if callable(release):
                    release(source=self.source)
        self.inputEnd(btns)

    def end(self) -> None:
        """押下状態をすべて解放し、中立の入力を実際に送る。

        'end' だけでは Switch に届かない。マイコン側は 'end' を受けると
        proc_state を NONE にして pc_report を中立へ書き換えるが、その値を
        送信する経路が無い（SwitchFunction の NONE は何もしない）。結果、
        押していたボタンが Switch 側に残り、長押しとして扱われる。

        そこで中立へ戻した状態を通常の座標行として1回送る。この行は
        既存の PC_CALL 経路（数字で始まる行）で処理され、そこには
        sendReportOnly があるので確実に Switch へ届く。マイコン側へ
        手を入れる必要は無い。
        """
        self.holdButton.clear()
        # 段 V-d: end は「すべて中立」なので押しっぱなしも取り下げる。
        release = getattr(self.ser, "releaseHat", None)
        if callable(release):
            release(source=self.source)
        self.format.resetAllButtons()
        self.format.resetAllDirections()
        self.format.unsetHat()
        # 中立の座標行。resetAllDirections が両スティックの変更印を
        # 立てるので、convert2str は lx ly rx ry を含む完全な行を返す。
        self._writeNeutralAll()
        # 実行の終わりをマイコンへ知らせる（proc_state を NONE へ）
        self.ser.writeRow('end')

    def serialcommand_direct_send(self, serialcommands: List[str], waittime: List[float]) -> None:
        for wtime, row in zip(waittime, serialcommands):
            time.sleep(wtime)
            self.ser.writeRow_wo_perf_counter(row, is_show=False)
    # ------------------------------------------------------------------
    # 2026/08/25 段 III（PORTBACK 5節）: 姿勢を Sender へ委譲する
    # ------------------------------------------------------------------
    # 何をしたか:
    #   KeyPress は SendFormat（自分専用の姿勢）を持ち、そこへ書いてから
    #   convert2str() で行を組んで送っていた。この「自分専用」が ARC-01
    #   （状態の分裂）そのもので、コマンド用・GUI 用で別々の姿勢を持ち、
    #   互いの押下を消し合っていた。
    #   段 III では、姿勢の書き込み先を Sender（アプリに1つ）へ移す。
    #
    # 外向きの API は1文字も変えない:
    #   input / inputEnd / hold / holdEnd / end の名前・引数・意味は不変。
    #   既存コマンドは1行も直さずに動く（NEWAPP 2章の互換の要）。
    #
    # self.format は残す:
    #   互換のため SendFormat のインスタンスも並行して更新し続ける。
    #   ・外部が self.format.format を読んでいた場合に壊さないため
    #   ・段 III の検算で「両者が同じ行を出す」ことを見張るため
    #   段 IV で SendFormat を畳むときに、ここを外す。
    #
    # 退避:
    #   Sender が applyButtons を持たない旧版なら、従来どおり
    #   self.format.convert2str() の行を送る。挙動は完全に同じ。

    def _posture_ready(self) -> bool:
        """Sender が姿勢を持つ版か（段 I 以降か）。"""
        return all(callable(getattr(self.ser, n, None))
                   for n in ("applyButtons", "applyHat", "applyStick",
                             "_buildRow"))

    def _syncPostureFromFormat(self) -> None:
        """SendFormat の現在値を Sender の姿勢へ写す。

        変化印まで含めて写すのが要点。convert2str は「変化した側だけ」
          を行に含めるため、印を写さないと座標が落ちる。
        SendFormat 側の印は convert2str が読むと下りるので、こちらが
          先に読んでしまわないよう、写したあとで元へ戻す。
        """
        f = self.format.format
        self.ser.applyButtons(release=[0xFFFF])      # いったん全ビットを落とす
        self.ser.applyButtons(press=[int(f["btn"])])
        self.ser.applyHat(int(f["hat"]))
        # 座標は「変化印が立っている側だけ」を申告する。印が立っていない
        # 側は前回から変わっていないので、触ると余計な行が出る。
        if self.format.L_stick_changed:
            self.ser.applyStick("L", int(f["lx"]), int(f["ly"]))
        if self.format.R_stick_changed:
            self.ser.applyStick("R", int(f["rx"]), int(f["ry"]))

    def _writeCurrent(self) -> None:
        """現在の姿勢を1行にして送る。

        段 III の中核。従来は self.format.convert2str() の戻り値を
          そのまま writeRow へ渡していた。ここを Sender の姿勢経由に
          置き換える。ただし出力される行は1文字も変わらない
          （段 I の検算 339 件で確認済み）。
        """
        if not self._posture_ready():
            # 旧 Sender。従来どおり（挙動は完全に同じ）
            self.ser.writeRow(self.format.convert2str())
            return

        # 段 IV: _syncPostureFromFormat（全体の上書き）をやめた。
        #   申告は _applyToSender / _releaseFromSender が差分で済ませて
        #   いるので、ここは「今の姿勢を1行にして送る」だけでよい。
        #   これで別系統の押下を消さなくなる（ARC-01 の解）。
        self.format.convert2str()      # 互換側の印を下ろす（副作用を維持）
        self.ser.sendPosture(source=self.source)

    def _writeNeutralAll(self) -> None:
        """すべて中立へ戻した完全な行を必ず送る（end 専用）。

        _writeCurrent と分けた理由（2026/08/25 の検算で判明）:
          applyStick は「値が変わったときだけ」変化印を立てる。だから
          既に中立だった状態で end() を呼ぶと印が立たず、送信行から
          座標が落ちて "0x0000 8" になる。
          この行はマイコン側で use_left / use_right がどちらも 0 に
          なるため、スティックが更新されない。「押しっぱなしを必ず
          解除する」という end() の目的を果たせない。

        SendFormat.resetAllDirections は無条件に印を立てており、
          そちらが正しかった。Sender.releaseAll も同じ作りにしてある
          ので、end() ではそれを使う。
        """
        if not self._posture_ready():
            self.ser.writeRow(self.format.convert2str())
            return
        self.format.convert2str()      # 互換側の印も下ろす
        self.ser.sendNeutralAll(source=self.source)


    # ------------------------------------------------------------------
    # 2026/08/25 段 IV: 差分の申告（ARC-01 の解）
    # ------------------------------------------------------------------
    # 段 III との違い:
    #   段 III は _syncPostureFromFormat で「自分の SendFormat の内容を
    #   Sender へ丸ごと写す」形だった。これは姿勢の置き場所を移した
    #   だけで、書くたびに全体を上書きする点は変わっていなかった。
    #   段 IV では「押したもの・離したものだけ」を申告する。触っていない
    #   項目は Sender が持つ値のまま保たれるので、別系統の押下を消さない。
    #
    # self.format は残す:
    #   外部が読んでいた場合に壊さないため、並行して更新し続ける。
    #   ただし送信行はもう self.format からは作らない。

    def _applyToSender(self, btns: list) -> None:
        """押したものだけを Sender へ申告する（input から呼ぶ）。"""
        if not self._posture_ready():
            return
        buttons = [int(b) for b in btns if type(b) is Button]
        if buttons:
            self.ser.pressButtons(buttons, source=self.source)
        hats = [b for b in btns if type(b) is Hat]
        if hats:
            self.ser.setHat(int(hats[0]), source=self.source)
        for d in [b for b in btns if type(b) is Direction]:
            # y の反転は SendFormat.setAnyDirection と同じ規則に揃える
            #   （Direction が持つ y は上が大きいが、送信行は上が小さい）。
            side = "L" if d.stick == Stick.LEFT else "R"
            self.ser.setStick(side, int(d.x), int(255 - d.y), source=self.source)

    def _releaseFromSender(self, btns: list, tilts: list,
                           unset_hat: bool, hold_hat: Any = None) -> None:
        """離したものだけを Sender へ申告する（inputEnd から呼ぶ）。

        スティックの解除は「軸ごと」に行う。SendFormat.unsetDirection
          と同じ規則（片軸を中立にし、もう片軸は fixOtherAxis で端へ寄せる）
          を保つため、結果の座標は self.format から読み取って申告する。
          ここだけは self.format の計算結果に頼っている。段 V 以降で
            unsetDirection 相当を Sender 側へ持てば外せる。
        """
        if not self._posture_ready():
            return
        buttons = [int(b) for b in btns if type(b) is Button]
        if buttons:
            self.ser.releaseButtons(buttons, source=self.source)
        if unset_hat:
            # B1: hold 中の Hat は中立へ戻さない（input 側と同じ規則）
            self.ser.setHat(None if hold_hat is None else int(hold_hat),
                            source=self.source)
        f = self.format.format
        if any(t in tilts for t in (Tilt.UP, Tilt.DOWN, Tilt.LEFT, Tilt.RIGHT)):
            self.ser.setStick("L", int(f["lx"]), int(f["ly"]), source=self.source)
        if any(t in tilts for t in (Tilt.R_UP, Tilt.R_DOWN,
                                    Tilt.R_LEFT, Tilt.R_RIGHT)):
            self.ser.setStick("R", int(f["rx"]), int(f["ry"]), source=self.source)
