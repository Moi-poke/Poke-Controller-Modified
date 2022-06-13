#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import time
from collections import OrderedDict
from enum import Enum, IntEnum, IntFlag, auto
import queue
from logging import getLogger, DEBUG, NullHandler
import json


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
min = 0
center = 128
max = 255


# serial format
class SendFormat:
    def __init__(self):

        self._logger = getLogger(__name__)
        self._logger.addHandler(NullHandler())
        self._logger.setLevel(DEBUG)
        self._logger.propagate = True

        # This format structure needs to be the same as the one written in Joystick.c
        self.format = OrderedDict([
            ('btn', 0),  # send bit array for buttons
            ('hat', Hat.CENTER),
            ('lx', center),
            ('ly', center),
            ('rx', center),
            ('ry', center),
        ])

        self.L_stick_changed = False
        self.R_stick_changed = False
        self.Hat_pos = Hat.CENTER

    def setButton(self, btns):
        for btn in btns:
            self.format['btn'] |= btn

    def unsetButton(self, btns):
        for btn in btns:
            self.format['btn'] &= ~btn

    def resetAllButtons(self):
        self.format['btn'] = 0

    def setHat(self, btns):
        # self._logger.debug(btns)
        if not btns:
            self.format['hat'] = self.Hat_pos
        else:
            self.Hat_pos = btns[0]
            self.format['hat'] = btns[0]  # takes only first element

    def unsetHat(self):
        # if self.Hat_pos is not Hat.CENTER:
        self.Hat_pos = Hat.CENTER
        self.format['hat'] = self.Hat_pos

    def setAnyDirection(self, dirs):
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

    def unsetDirection(self, dirs):
        if Tilt.UP in dirs or Tilt.DOWN in dirs:
            self.format['ly'] = center
            self.format['lx'] = self.fixOtherAxis(self.format['lx'])
            self.L_stick_changed = True
        if Tilt.RIGHT in dirs or Tilt.LEFT in dirs:
            self.format['lx'] = center
            self.format['ly'] = self.fixOtherAxis(self.format['ly'])
            self.L_stick_changed = True
        if Tilt.R_UP in dirs or Tilt.R_DOWN in dirs:
            self.format['ry'] = center
            self.format['rx'] = self.fixOtherAxis(self.format['rx'])
            self.R_stick_changed = True
        if Tilt.R_RIGHT in dirs or Tilt.R_LEFT in dirs:
            self.format['rx'] = center
            self.format['ry'] = self.fixOtherAxis(self.format['ry'])
            self.R_stick_changed = True

    # Use this to fix an either tilt to max when the other axis sets to 0
    def fixOtherAxis(self, fix_target):
        if fix_target == center:
            return center
        else:
            return 0 if fix_target < center else 255

    def resetAllDirections(self):
        self.format['lx'] = center
        self.format['ly'] = center
        self.format['rx'] = center
        self.format['ry'] = center
        self.L_stick_changed = True
        self.R_stick_changed = True
        self.Hat_pos = Hat.CENTER

    def convert2str(self):
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
    def __init__(self, stick, angle, magnification=1.0, isDegree=True, showName=None):

        self._logger = getLogger(__name__)
        self._logger.addHandler(NullHandler())
        self._logger.setLevel(DEBUG)
        self._logger.propagate = True

        self.stick = stick
        self.angle_for_show = angle
        self.showName = showName
        if magnification is not None:
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

    def __repr__(self):
        if self.showName:
            return "<{}, {}>".format(self.stick, self.showName)
        else:
            return "<{}, {}[deg]>".format(self.stick, self.angle_for_show)

    def __eq__(self, other):
        if not type(other) is Direction:
            return False

        if self.stick == other.stick and self.angle_for_show == other.angle_for_show:
            return True
        else:
            return False

    def getTilting(self):
        tilting = []
        if self.stick == Stick.LEFT:
            if self.x < center:
                tilting.append(Tilt.LEFT)
            elif self.x > center:
                tilting.append(Tilt.RIGHT)

            if self.y < center - 1:
                tilting.append(Tilt.DOWN)
            elif self.y > center - 1:
                tilting.append(Tilt.UP)
        elif self.stick == Stick.RIGHT:
            if self.x < center:
                tilting.append(Tilt.R_LEFT)
            elif self.x > center:
                tilting.append(Tilt.R_RIGHT)

            if self.y < center - 1:
                tilting.append(Tilt.R_DOWN)
            elif self.y > center - 1:
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
    def __init__(self, ser):

        self._logger = getLogger(__name__)
        self._logger.addHandler(NullHandler())
        self._logger.setLevel(DEBUG)
        self._logger.propagate = True

        self.q = queue.Queue()
        self.ser = ser
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
        self.controller_state = ControllerState()
        self.isShowInput = False

    def input(self, btns, ifPrint=True, isHold=False):
        self._pushing = dict(self.format.format)
        if not isinstance(btns, list):
            btns = [btns]

        self.controller_state.updateState(btns, isHold=isHold, isInput=True)
        for btn in self.holdButton:
            if not btn in btns:
                btns.append(btn)

        self.format.setButton([btn for btn in btns if type(btn) is Button])
        self.format.setHat([btn for btn in btns if type(btn) is Hat])
        self.format.setAnyDirection([btn for btn in btns if type(btn) is Direction])

        self.ser.writeRow(self.format.convert2str())
        self.input_time_0 = time.perf_counter()
        res = self.controller_state.getState(necessary="all")
        self.controller_state.resetChangeState()
        try:
            if self.isShowInput.get():
                self.show_input(res)
        except:
            pass

        # self._logger.debug(f": {list(map(str,self.format.format.values()))}")

    def inputEnd(self, btns, ifPrint=True, unset_hat=True):
        # print(btns)
        # self._logger.debug(f"input end: {btns}")
        self.pushing2 = dict(self.format.format)

        self.ed = time.perf_counter()
        if not isinstance(btns, list):
            btns = [btns]
        # self._logger.debug(btns)

        # get tilting direction from angles
        tilts = []
        for direction in [btn for btn in btns if type(btn) is Direction]:
            tiltings = direction.getTilting()
            for tilting in tiltings:
                tilts.append(tilting)
        # self._logger.debug(tilts)

        self.format.unsetButton([btn for btn in btns if type(btn) is Button])
        if unset_hat:
            self.format.unsetHat()
        self.format.unsetDirection(tilts)
        self.controller_state.updateState(btns, isHold=False, isInput=False, )
        self.ser.writeRow(self.format.convert2str())
        res = self.controller_state.getState(necessary="all")
        self.controller_state.resetChangeState()
        try:
            if self.isShowInput.get():
                self.show_input(res)
        except:
            pass

    def hold(self, btns):
        if not isinstance(btns, list):
            btns = [btns]

        for btn in btns:
            if btn in self.holdButton:
                print('Warning: ' + btn.name + ' is already in holding state')
                self._logger.warning(f"Warning: {btn.name} is already in holding state")
                return

            self.holdButton.append(btn)
        self.input(btns, isHold=True)

    def holdEnd(self, btns):
        if not isinstance(btns, list):
            btns = [btns]

        for btn in btns:
            self.holdButton.remove(btn)

        self.inputEnd(btns)

    def end(self):
        self.ser.writeRow('end')

    def show_input(self, res):
        if res != "{}":
            for k, v in res.items():
                if k == "Release":
                    for k_, v_ in v.items():
                        print(f"{k_}: {v_}")
                elif k == "Hat":
                    print(f"{k}.{list(v.keys())[0]}:\t{list(v.values())[0]}")


class ControllerState:
    def __init__(self):
        """
        Button: {ButtonName: [isPushed, push time, release time, isHolding, isStateChanged]}
        Stick: {L/RStick: {x: 0~255(center is 128), y: 0~255(center is 128)}}
        Hat: {Position, push time,release time, isHolding, isStateChanged}
        """
        self.controller_state = {"Button": {"Y": [False, time.time(), time.time(), False, False],
                                            "B": [False, time.time(), time.time(), False, False],
                                            "A": [False, time.time(), time.time(), False, False],
                                            "X": [False, time.time(), time.time(), False, False],
                                            "L": [False, time.time(), time.time(), False, False],
                                            "R": [False, time.time(), time.time(), False, False],
                                            "ZL": [False, time.time(), time.time(), False, False],
                                            "ZR": [False, time.time(), time.time(), False, False],
                                            "MINUS": [False, time.time(), time.time(), False, False],
                                            "PLUS": [False, time.time(), time.time(), False, False],
                                            "LCLICK": [False, time.time(), time.time(), False, False],
                                            "RCLICK": [False, time.time(), time.time(), False, False],
                                            "HOME": [False, time.time(), time.time(), False, False],
                                            "CAPTURE": [False, time.time(), time.time(), False, False],
                                            },
                                 "Stick": {"LEFT": {"x": 128, "y": 128, "r": 0},
                                           "RIGHT": {"x": 128, "y": 128, "r": 0}},
                                 "Hat": ["CENTER", time.time(), time.time(), False, False]
                                 }
        self.hat_before = "CENTER"
        self.state_changed = True

    def getState(self, necessary="all"):
        if self.state_changed:
            # print("get_state:", self.controller_state)
            now_time = time.time()
            button_state_press = []
            button_state_release = {}
            button_state_holding = {}
            for k, v in self.controller_state["Button"].items():
                if v[3]:
                    button_state_holding[f"Button.{k}"] = round(now_time - v[1], 2)
                elif v[0] is True and v[4]:
                    button_state_press.append(f"Button.{k}")
                elif v[0] is False and v[4]:
                    button_state_release[f"Button.{k}"] = round(now_time - v[1], 2)
            if button_state_press:
                button_state_press = {"Press": button_state_press}
            else:
                button_state_press = {}
            if button_state_release:
                button_state_release = {"Release": button_state_release}
            else:
                button_state_release = {}
            if button_state_holding:
                button_state_holding = {"Holding": button_state_holding}
            else:
                button_state_holding = {}
            button_state = {**button_state_press, **button_state_release, **button_state_holding}
            # button_state = json.dumps(button_state, indent=2)

            direction_state = {}
            if self.controller_state["Stick"]["LEFT"]["x"] != 128 or self.controller_state["Stick"]["LEFT"]["y"] != 128:
                direction_state["L.Stick"] = self.controller_state["Stick"]["LEFT"]
            if self.controller_state["Stick"]["RIGHT"]["x"] != 128 or self.controller_state["Stick"]["RIGHT"][
                "y"] != 128:
                direction_state["R.Stick"] = self.controller_state["Stick"]["RIGHT"]
            # direction_state = json.dumps(direction_state, indent=2)

            hat_state = {}
            # if self.controller_state["Hat"][0] != "CENTER":
            if self.controller_state["Hat"][0] != self.hat_before:
                hat_state[f"Hat"] = {self.hat_before: round(
                    time.time() - self.controller_state["Hat"][1], 2
                )}
                self.hat_before = self.controller_state["Hat"][0]
        else:
            button_state = None
            direction_state = None
            hat_state = None

        if necessary is "all":
            res = {**button_state, **direction_state, **hat_state}
        elif necessary is "bd":
            res = {**button_state, **direction_state}
        elif necessary is "bh":
            res = {**button_state, **hat_state}
        elif necessary is "dh":
            res = {**direction_state, **hat_state}
        elif necessary is "b":
            res = {**button_state}
        elif necessary is "d":
            res = {**direction_state}
        elif necessary is "h":
            res = {**hat_state}
        else:
            res = {**button_state, **direction_state, **hat_state}
        self.state_changed = False

        # for k, v in self.controller_state["Button"].items():
        #     if not self.controller_state["Button"][k][2]:
        #         self.controller_state["Button"][k][1] = time.time()
        #     self.controller_state["Button"][k][3] = False
        return res

    def updateState(self, keys, isHold, isInput=True):
        self.state_changed = True
        # print(keys)
        for key in keys:
            if type(key) == Direction:
                if not isInput:
                    self.controller_state["Stick"][key.stick.name] = {"x": 128,
                                                                      "y": 128,
                                                                      "r": 0}
                else:
                    self.controller_state["Stick"][key.stick.name] = {"x": key.x,
                                                                      "y": key.y,
                                                                      "r": key.mag}
            elif type(key) == Button:

                if isInput:
                    if isHold or self.controller_state["Button"][key.name][3] is True:
                        self.controller_state["Button"][key.name][0] = True  # isPush
                        self.controller_state["Button"][key.name][1] = time.time()  # push time

                        self.controller_state["Button"][key.name][3] = True  # isHold
                        self.controller_state["Button"][key.name][4] = True  # Updated
                    else:
                        self.controller_state["Button"][key.name][0] = True  # isPush
                        self.controller_state["Button"][key.name][1] = time.time()  # push time

                        self.controller_state["Button"][key.name][3] = False  # isHold
                        self.controller_state["Button"][key.name][4] = True  # Updated
                else:
                    if isHold or self.controller_state["Button"][key.name][3]:
                        self.controller_state["Button"][key.name][0] = False  # isPush

                        self.controller_state["Button"][key.name][2] = time.time()  # release time
                        self.controller_state["Button"][key.name][3] = False  # isHold
                        self.controller_state["Button"][key.name][4] = True  # Updated
                    else:
                        self.controller_state["Button"][key.name][0] = False  # isPush

                        self.controller_state["Button"][key.name][2] = time.time()  # release time
                        self.controller_state["Button"][key.name][3] = False  # isHold
                        self.controller_state["Button"][key.name][4] = True  # Updated

                # print(f"Button {key.name}")
            elif type(key) == Hat:
                if isInput:
                    if isHold or self.controller_state["Hat"][3]:
                        self.controller_state["Hat"][0] = key.name  # position
                        self.controller_state["Hat"][1] = time.time()  # press-time

                        self.controller_state["Hat"][3] = True  # isHold
                        self.controller_state["Hat"][4] = True  # Updated
                    else:
                        self.controller_state["Hat"][0] = key.name  # position
                        self.controller_state["Hat"][1] = time.time()  # press-time

                        self.controller_state["Hat"][3] = False  # isHold
                        self.controller_state["Hat"][4] = True  # Updated
                    self.hat_before = key.name
                else:
                    if isHold or self.controller_state["Hat"][3]:
                        self.controller_state["Hat"][0] = "CENTER"  # position

                        self.controller_state["Hat"][2] = time.time()  # release-time
                        self.controller_state["Hat"][3] = False  # isHold
                        self.controller_state["Hat"][4] = True  # Updated
                    else:
                        self.controller_state["Hat"][0] = "CENTER"  # position

                        self.controller_state["Hat"][2] = time.time()  # release-time
                        self.controller_state["Hat"][3] = False  # isHold
                        self.controller_state["Hat"][4] = True  # Updated
                # print(f"Hat {key.name}")

        for k, v in self.controller_state["Button"].items():
            if v[3] is False and v[0] is True and v[4] is False:
                self.controller_state["Button"][k][3] = True

        # print(self.controller_state)

    def resetChangeState(self):
        for k, v in self.controller_state["Button"].items():
            self.controller_state["Button"][k][4] = False
        self.controller_state["Hat"][4] = False  # Updated
