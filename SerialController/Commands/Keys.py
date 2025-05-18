#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import time
from collections import OrderedDict
from enum import Enum, IntEnum, IntFlag, auto
from queue import Queue
from typing import Any, Dict, List, Optional, Self, Tuple, TypeVar, Union

from Commands import Sender
from loguru import logger

# Type aliases
T = TypeVar("T")
ButtonOrHat = Union["Button", "Hat"]
DirectionOrTilt = Union["Direction", "Tilt"]
ControlInput = Union[ButtonOrHat, DirectionOrTilt]

# Constants
STICK_MIN = 0
STICK_CENTER = 128
STICK_MAX = 255
STICK_RANGE = STICK_MAX - STICK_MIN
HALF_STICK_RANGE = STICK_RANGE // 2


class Button(IntFlag):
    """Controller buttons represented as bit flags."""

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
    """Hat switch positions."""

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
    """Controller sticks."""

    LEFT = auto()
    RIGHT = auto()


class Tilt(Enum):
    """Stick tilt directions."""

    UP = auto()
    RIGHT = auto()
    DOWN = auto()
    LEFT = auto()
    R_UP = auto()
    R_RIGHT = auto()
    R_DOWN = auto()
    R_LEFT = auto()


class Direction:
    """Represents stick direction with angle and magnitude."""

    def __init__(
        self,
        stick: Stick,
        angle: Union[Tuple[int, int], float, int],
        magnification: float = 1.0,
        isDegree: bool = True,
        showName: Optional[str] = None,
        printShowName: Optional[bool] = True,
    ) -> None:
        """
        Initialize stick direction.

        Args:
            stick: LEFT or RIGHT stick
            angle: Angle in degrees or tuple of (x, y) coordinates
            magnification: Stick deflection magnitude (0.0 to 1.0)
            is_degree: Whether angle is in degrees (True) or radians (False)
            show_name: Display name for this direction
            print_show_name: Whether to print the show name
        """
        self.stick = stick
        self.angle_for_show = angle
        self.showName = showName
        self.mag = max(0.0, min(1.0, magnification))

        if isinstance(angle, tuple):
            self.x, self.y = angle
            self.showName = f"({self.x},{self.y})"
            if printShowName:
                print("押し込み量", self.showName)
        else:
            angle_rad = math.radians(angle) if isDegree else angle
            self.x = math.ceil(
                HALF_STICK_RANGE * math.cos(angle_rad) * self.mag + STICK_CENTER
            )
            self.y = math.floor(
                HALF_STICK_RANGE * math.sin(angle_rad) * self.mag + STICK_CENTER
            )

    def __repr__(self) -> str:
        """String representation of the direction."""
        mag_str = f"{self.mag:.2f}"
        if self.showName:
            return f"<{self.stick}, {self.showName}, mag={mag_str}>"
        return f"<{self.stick}, {self.angle_for_show}[deg], mag={mag_str}>"

    def __eq__(self, other: Any) -> bool:
        """Check if two directions are equal."""
        if not isinstance(other, Direction):
            return False
        return (
            self.stick == other.stick
            and self.angle_for_show == other.angle_for_show
            and self.mag == other.mag
        )

    def __hash__(self) -> int:
        return hash((self.stick, self.angle_for_show, self.mag))

    def get_tilting(self) -> List[Tilt]:
        """Get list of tilt directions for this stick position."""
        tilting = []
        if self.stick == Stick.LEFT:
            if self.x < STICK_CENTER:
                tilting.append(Tilt.LEFT)
            elif self.x > STICK_CENTER:
                tilting.append(Tilt.RIGHT)

            if self.y < STICK_CENTER - 1:
                tilting.append(Tilt.DOWN)
            elif self.y > STICK_CENTER - 1:
                tilting.append(Tilt.UP)
        else:  # RIGHT stick
            if self.x < STICK_CENTER:
                tilting.append(Tilt.R_LEFT)
            elif self.x > STICK_CENTER:
                tilting.append(Tilt.R_RIGHT)

            if self.y < STICK_CENTER - 1:
                tilting.append(Tilt.R_DOWN)
            elif self.y > STICK_CENTER - 1:
                tilting.append(Tilt.R_UP)
        return tilting

    @classmethod
    def from_angle(
        cls,
        stick: Stick,
        angle: Union[float, int],
        name: str,
        magnification: float = 1.0,
        isDegree: bool = True,
    ) -> Self:
        """Create direction from angle with given name."""
        return cls(
            stick, angle, magnification=magnification, isDegree=isDegree, showName=name
        )

    # for IDE
    UP: "Direction"
    RIGHT: "Direction"
    DOWN: "Direction"
    LEFT: "Direction"
    UP_RIGHT: "Direction"
    DOWN_RIGHT: "Direction"
    DOWN_LEFT: "Direction"
    UP_LEFT: "Direction"

    R_UP: "Direction"
    R_RIGHT: "Direction"
    R_DOWN: "Direction"
    R_LEFT: "Direction"
    R_UP_RIGHT: "Direction"
    R_DOWN_RIGHT: "Direction"
    R_DOWN_LEFT: "Direction"
    R_UP_LEFT: "Direction"


# Predefined directions
DIRECTION_DEFINITIONS: Dict[str, Tuple[Stick, Union[float, int]]] = {
    "UP": (Stick.LEFT, 90),
    "RIGHT": (Stick.LEFT, 0),
    "DOWN": (Stick.LEFT, -90),
    "LEFT": (Stick.LEFT, -180),
    "UP_RIGHT": (Stick.LEFT, 45),
    "DOWN_RIGHT": (Stick.LEFT, -45),
    "DOWN_LEFT": (Stick.LEFT, -135),
    "UP_LEFT": (Stick.LEFT, 135),
    "R_UP": (Stick.RIGHT, 90),
    "R_RIGHT": (Stick.RIGHT, 0),
    "R_DOWN": (Stick.RIGHT, -90),
    "R_LEFT": (Stick.RIGHT, -180),
    "R_UP_RIGHT": (Stick.RIGHT, 45),
    "R_DOWN_RIGHT": (Stick.RIGHT, -45),
    "R_DOWN_LEFT": (Stick.RIGHT, -135),
    "R_UP_LEFT": (Stick.RIGHT, 135),
}

# Create class attributes for each predefined direction
for name, (stick, angle) in DIRECTION_DEFINITIONS.items():
    setattr(Direction, name, Direction.from_angle(stick, angle, name))


try:
    from numba import config, njit

    config.DEBUG = False
    config.DEBUG_JIT = False

    @njit
    def _format_cmd_parts_jit(
        btn_value: int,
        lx: int,
        ly: int,
        rx: int,
        ry: int,
        left_changed: bool,
        right_changed: bool,
    ) -> tuple:
        """スティックデータのフォーマット処理をJIT化した純粋関数"""
        parts_values = []
        send_btn = btn_value << 2

        if left_changed:
            send_btn |= 0x2
            parts_values.extend([lx, ly])

        if right_changed:
            send_btn |= 0x1
            parts_values.extend([rx, ry])

        return send_btn, parts_values

except ImportError:

    def _format_cmd_parts_jit(
        btn_value: int,
        lx: int,
        ly: int,
        rx: int,
        ry: int,
        left_changed: bool,
        right_changed: bool,
    ) -> tuple:
        """スティックデータのフォーマット処理を通常の関数として定義"""
        parts_values = []
        send_btn = btn_value << 2

        if left_changed:
            send_btn |= 0x2
            parts_values.extend([lx, ly])

        if right_changed:
            send_btn |= 0x1
            parts_values.extend([rx, ry])

        return send_btn, parts_values


class SendFormat:
    """Formats controller input data for serial transmission."""

    def __init__(self) -> None:
        """Initialize with neutral position values."""
        self.format: OrderedDict = OrderedDict(
            [
                ("btn", 0),  # Button bit array
                ("hat", Hat.CENTER),
                ("lx", STICK_CENTER),
                ("ly", STICK_CENTER),
                ("rx", STICK_CENTER),
                ("ry", STICK_CENTER),
            ]
        )
        self.L_stick_changed = False
        self.R_stick_changed = False
        self.Hat_pos = Hat.CENTER

    def set_button(self, buttons: List[Button]) -> None:
        """Set button states."""
        for btn in buttons:
            self.format["btn"] |= btn

    def unset_button(self, buttons: List[Button]) -> None:
        """Unset button states."""
        for btn in buttons:
            self.format["btn"] &= ~btn

    def reset_all_buttons(self) -> None:
        """Reset all buttons to unpressed state."""
        self.format["btn"] = 0

    def set_hat(self, hat_positions: List[Hat]) -> None:
        """Set hat position (uses first position if multiple given)."""
        if hat_positions:
            self.Hat_pos = hat_positions[0]
            self.format["hat"] = self.Hat_pos

    def unset_hat(self) -> None:
        """Reset hat to center position."""
        self.Hat_pos = Hat.CENTER
        self.format["hat"] = self.Hat_pos

    def set_any_direction(self, directions: List[Direction]) -> None:
        """Set stick directions."""
        for direction in directions:
            if direction.stick == Stick.LEFT:
                if (
                    self.format["lx"] != direction.x
                    or self.format["ly"] != STICK_MAX - direction.y
                ):
                    self.L_stick_changed = True

                self.format["lx"] = direction.x
                self.format["ly"] = STICK_MAX - direction.y  # Y axis is inverted
            else:  # RIGHT stick
                if (
                    self.format["rx"] != direction.x
                    or self.format["ry"] != STICK_MAX - direction.y
                ):
                    self.R_stick_changed = True

                self.format["rx"] = direction.x
                self.format["ry"] = STICK_MAX - direction.y

    def unset_direction(self, tilts: List[Tilt]) -> None:
        """Reset specified tilt directions to neutral."""
        if any(t in tilts for t in (Tilt.UP, Tilt.DOWN)):
            self.format["ly"] = STICK_CENTER
            self.format["lx"] = self._fix_other_axis(self.format["lx"])
            self.L_stick_changed = True

        if any(t in tilts for t in (Tilt.RIGHT, Tilt.LEFT)):
            self.format["lx"] = STICK_CENTER
            self.format["ly"] = self._fix_other_axis(self.format["ly"])
            self.L_stick_changed = True

        if any(t in tilts for t in (Tilt.R_UP, Tilt.R_DOWN)):
            self.format["ry"] = STICK_CENTER
            self.format["rx"] = self._fix_other_axis(self.format["rx"])
            self.R_stick_changed = True

        if any(t in tilts for t in (Tilt.R_RIGHT, Tilt.R_LEFT)):
            self.format["rx"] = STICK_CENTER
            self.format["ry"] = self._fix_other_axis(self.format["ry"])
            self.R_stick_changed = True

    def _fix_other_axis(self, fix_target: int) -> int:
        """Adjust opposite axis when one axis is reset."""
        if fix_target == STICK_CENTER:
            return STICK_CENTER
        return 0 if fix_target < STICK_CENTER else STICK_MAX

    def reset_all_directions(self) -> None:
        """Reset all sticks to neutral position."""
        for axis in ("lx", "ly", "rx", "ry"):
            self.format[axis] = STICK_CENTER
        self.L_stick_changed = True
        self.R_stick_changed = True
        self.Hat_pos = Hat.CENTER

    def convert2str(self) -> str:
        """コントローラー入力をシリアル送信用の文字列に変換"""
        # JIT関数を直接呼び出し
        send_btn, values = _format_cmd_parts_jit(
            int(self.format["btn"]),
            self.format["lx"],
            self.format["ly"],
            self.format["rx"],
            self.format["ry"],
            self.L_stick_changed,
            self.R_stick_changed,
        )

        # 値を16進数に変換
        parts = [format(val, "02x") for val in values]

        # ヘッダーの構築
        header = [
            format(send_btn, "#06x"),  # 0x付き4桁16進数
            str(int(self.format["hat"])),
        ]

        # 最終文字列の組み立て
        full_command = [" ".join(header)]
        if parts:
            full_command.append(" ".join(parts))

        # フラグリセット
        self.L_stick_changed = False
        self.R_stick_changed = False

        return " ".join(filter(None, full_command))  # 空要素を除去して結合


# handles serial input to Joystick.c
class KeyPress:
    """Handles controller input and serial communication."""

    def __init__(self, ser: Sender.Sender):
        """Initialize with serial sender instance."""
        self.queue: Queue = Queue()
        self.ser = ser
        self.format = SendFormat()
        self.hold_buttons: List[ControlInput] = []
        self.neutral_state = dict(self.format.format)
        self.input_start_time = time.perf_counter()
        self.was_neutral = True
        self.input_times: Dict[ControlInput, float] = {}  # 入力が押された時刻

    def input(
        self,
        inputs: Union[List[ControlInput], ControlInput],
        ifPrint: bool = False,
    ) -> None:
        """Send input to controller."""
        if not isinstance(inputs, list):
            inputs = [inputs]

        # Include any held buttons
        inputs.extend(self.hold_buttons)
        current_time = time.perf_counter()
        # Print press information and store input times
        for inp in inputs:
            if inp not in self.input_times:
                self.input_times[inp] = current_time
                if ifPrint:
                    if isinstance(inp, Button):
                        print(f"[ PRESS ] {Button(inp).name}")
                    if isinstance(inp, Hat):
                        print(f"[ PRESS ] {Hat(inp).name}")
                    if isinstance(inp, Direction):
                        print(f"[ PRESS ] {inp}")

        # Process different input types
        self.format.set_button([i for i in inputs if isinstance(i, Button)])
        self.format.set_hat([i for i in inputs if isinstance(i, Hat)])
        self.format.set_any_direction([i for i in inputs if isinstance(i, Direction)])

        self.ser.writeRow(self.format.convert2str())
        self.input_start_time = time.perf_counter()

    def inputEnd(
        self,
        inputs: Union[List[ControlInput], ControlInput],
        ifPrint: bool = False,
        unset_hat: bool = True,
    ) -> None:
        """End specified inputs."""
        if not isinstance(inputs, list):
            inputs = [inputs]

        current_time = time.perf_counter()

        # Get tilt directions from Direction inputs
        tilts = []
        for direction in [i for i in inputs if isinstance(i, Direction)]:
            tilts.extend(direction.get_tilting())

        self.format.unset_button([i for i in inputs if isinstance(i, Button)])
        if unset_hat:
            self.format.unset_hat()
        self.format.unset_direction(tilts)

        # Print release info and duration
        for inp in inputs:
            if inp in self.input_times:
                duration = current_time - self.input_times[inp]
                if ifPrint:
                    if isinstance(inp, Button):
                        print(f"[RELEASE] {Button(inp).name} after {duration:.3f}s")
                    if isinstance(inp, Hat):
                        print(f"[RELEASE] {Hat(inp).name} after {duration:.3f}s")
                    if isinstance(inp, Direction):
                        print(f"[RELEASE] {(inp)} after {duration:.3f}s")
                del self.input_times[inp]

        self.ser.writeRow(self.format.convert2str())

    def hold(self, inputs: Union[List[ControlInput], ControlInput]) -> None:
        """Hold inputs until explicitly released."""
        if not isinstance(inputs, list):
            inputs = [inputs]

        for inp in inputs:
            if inp in self.hold_buttons:
                name = getattr(inp, "name", getattr(inp, "show_name", str(inp)))
                logger.warning(f"{name} is already in holding state")
                return

        self.hold_buttons.extend(inputs)
        self.input(inputs)

    def holdEnd(self, inputs: Union[List[ControlInput], ControlInput]) -> None:
        """Release held inputs."""
        if not isinstance(inputs, list):
            inputs = [inputs]

        for inp in inputs:
            self.hold_buttons.remove(inp)

        self.inputEnd(inputs)

    def neutral(self) -> None:
        inputs = self.hold_buttons
        self.hold_buttons = []
        self.format.reset_all_buttons()
        self.format.reset_all_directions()
        self.inputEnd(inputs, unset_hat=True)

    def end(self) -> None:
        """End all controller inputs."""
        self.neutral()
        self.ser.writeRow("end")

    def serialcommand_direct_send(
        self, commands: List[str], wait_times: List[float]
    ) -> None:
        """Send raw serial commands with specified wait times between them."""
        for wait_time, command in zip(wait_times, commands):
            time.sleep(wait_time)
            self.ser.writeRow_wo_perf_counter(command, is_show=False)
