#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
import datetime
import logging
import os
import platform
import time
import tkinter as tk
from collections import deque
from logging import DEBUG, INFO, NullHandler, StreamHandler, getLogger
from logging.handlers import RotatingFileHandler
from tkinter.scrolledtext import ScrolledText
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
    TYPE_CHECKING,
)

import cv2
import numpy as np
from Camera import Camera
from Commands import StickCommand, UnitCommand
from Commands.Keys import Button, Direction, KeyPress, Stick
from Commands.PythonCommandBase import PythonCommand
from Commands.Sender import Sender
from loguru import logger
from PIL import Image, ImageTk
from serial import Serial

if TYPE_CHECKING:
    from Window import LabelframeWithStickVar


isTakeLog = False
# logger_stick = getLogger(__name__)
nowtime = datetime.datetime.fromtimestamp(time.time()).strftime("%Y%m%d_%H%M%S")


# 型エイリアスの定義
Point = Tuple[int, int]
StickData = Tuple[float, float, float]  # (angle, magnitude, time_delta)


class MouseStick(PythonCommand):
    NAME = "MOUSEスティック"

    def __init__(self) -> None:
        super().__init__()

    def do(self) -> None:
        pass

    def stick(self, buttons: Any, duration: float = 0.1, wait: float = 0.1) -> None:
        self.keys.input(buttons, ifPrint=False)
        self.wait(duration)
        self.wait(wait)

    # press button at duration times(s)
    def stickEnd(self, buttons: Any) -> None:
        self.keys.inputEnd(buttons)


class CaptureArea(tk.Canvas):
    """A custom Canvas widget for displaying camera frames and handling gamepad stick interactions.

    This widget displays video frames from a camera and allows mouse interactions to simulate
    gamepad stick movements. It supports left and right stick controls, screenshot functionality,
    and various visualization features.
    """

    DEFAULT_RADIUS: int = 60  # Default radius for stick visualization circles
    DEFAULT_FPS: int = 30  # Default frames per second for video display
    DEFAULT_DISPLAY_SIZE: Tuple[int, int] = (
        640,
        360,
    )  # Default display dimensions (width, height)

    def __init__(
        self,
        camera: Camera,
        fps: int = DEFAULT_FPS,
        is_show: Optional[tk.BooleanVar] = None,
        ser: Sender = None,
        master: Optional[LabelframeWithStickVar] = None,
        show_width: int = DEFAULT_DISPLAY_SIZE[0],
        show_height: int = DEFAULT_DISPLAY_SIZE[1],
    ) -> None:
        """Initialize the CaptureArea widget.

        Args:
            camera: Camera object for video capture
            fps: Target frames per second for display
            is_show: BooleanVar controlling visibility of camera feed
            ser: Serial communication object for stick commands
            master: Parent widget
            show_width: Width of display area in pixels
            show_height: Height of display area in pixels
        """
        super().__init__(
            master,
            borderwidth=0,
            cursor="tcross",
            width=show_width,
            height=show_height,
        )
        # Validate and initialize parameters
        if master is None:
            return

        self.master: LabelframeWithStickVar = master
        self.camera = camera
        self.ser = ser
        self.is_show_var = is_show

        # Display properties
        self.show_width = int(show_width)
        self.show_height = int(show_height)
        self.show_size = (self.show_width, self.show_height)
        self.radius = self.DEFAULT_RADIUS

        # Stick state tracking
        self.lx_init, self.ly_init = 0, 0  # Left stick initial position
        self.rx_init, self.ry_init = 0, 0  # Right stick initial position
        self.min_x, self.min_y = 0, 0  # Selection area start
        self.max_x, self.max_y = 0, 0  # Selection area end

        # Visualization elements
        self.lcircle: Optional[int] = None  # Left stick outer circle ID
        self.lcircle2: Optional[int] = None  # Left stick inner circle ID
        self.rcircle: Optional[int] = None  # Right stick outer circle ID
        self.rcircle2: Optional[int] = None  # Right stick inner circle ID

        # Data logging
        self.dq: Deque[StickData] = deque()  # Data queue for stick movements
        self.calc_time: float = time.perf_counter()  # Time tracking for logging
        self._langle: Optional[float] = None  # Last left stick angle
        self._lmag: Optional[float] = None  # Last left stick magnitude
        self._rangle: Optional[float] = None  # Last right stick angle
        self._rmag: Optional[float] = None  # Last right stick magnitude

        # Initialize logging
        self._setup_logging()

        # Set up display properties
        self.setFps(fps)
        self._setup_event_bindings()
        self._setup_display_image()

    def _setup_logging(self) -> None:
        """Configure logging handlers for stick movements."""
        self.stick_handler = StreamHandler()
        self.stick_handler.setLevel(DEBUG)

        if isTakeLog:
            filename_base = os.path.join("log", f"{nowtime}")

            # Left stick logger
            self.LS = RotatingFileHandler(
                filename=f"{filename_base}_LStick.log",
                encoding="utf-8",
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
            )
            self.LS.setLevel(logging.DEBUG)
            self.LSTICK_logger = logging.getLogger("L_STICK")
            self.LSTICK_logger.setLevel(logging.DEBUG)
            self.LSTICK_logger.addHandler(self.LS)

            # Right stick logger
            self.RS = RotatingFileHandler(
                filename=f"{filename_base}_RStick.log",
                encoding="utf-8",
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
            )
            self.RS.setLevel(logging.DEBUG)
            self.RSTICK_logger = logging.getLogger("R_STICK")
            self.RSTICK_logger.setLevel(logging.DEBUG)
            self.RSTICK_logger.addHandler(self.RS)

    def _setup_event_bindings(self) -> None:
        """Set up mouse event bindings for the widget."""
        # Control-click bindings for color sampling
        self.bind("<Control-ButtonPress-1>", self.mouseCtrlLeftPress)
        self.bind("<Control-ButtonRelease-1>", self.mouseCtrlLeftRelease)

        # Screenshot selection bindings
        self.bind("<Control-Shift-ButtonPress-1>", self.StartRangeSS)
        self.bind("<Control-Shift-Button1-Motion>", self.MotionRangeSS)
        self.bind("<Control-Shift-ButtonRelease-1>", self.ReleaseRangeSS)

    def _setup_display_image(self) -> None:
        """Initialize the disabled state image for the widget."""
        disabled_img = cv2.imread("../Images/disabled.png", cv2.IMREAD_GRAYSCALE)
        disabled_pil = Image.fromarray(disabled_img)
        self.disabled_tk = ImageTk.PhotoImage(disabled_pil)
        self.im = self.disabled_tk
        self.im_ = self.create_image(0, 0, image=self.disabled_tk, anchor=tk.NW)

    def set_camera(self, camera: Camera) -> None:
        self.camera = camera

    def set_serial(self, serial: Sender) -> None:
        self.ser = serial

    def ApplyLStickMouse(self) -> None:
        """Enable or disable left stick mouse controls based on settings."""
        if self.master.is_use_left_stick_mouse.get():
            self.BindLeftClick()
        else:
            self.UnbindLeftClick()

    def ApplyRStickMouse(self) -> None:
        """Enable or disable right stick mouse controls based on settings."""
        if self.master.is_use_right_stick_mouse.get():
            self.BindRightClick()
        else:
            self.UnbindRightClick()

    def StartRangeSS(self, event: tk.Event) -> None:
        """Begin screenshot selection area on mouse press.

        Args:
            event: Mouse event containing click position
        """
        self.ss = self.camera.readFrame()

        # Temporarily unbind stick controls if active
        if self.master.is_use_left_stick_mouse.get():
            self.UnbindLeftClick()
        if self.master.is_use_right_stick_mouse.get():
            self.UnbindRightClick()

        # Initialize selection area
        self.min_x, self.min_y = event.x, event.y
        self.delete("SelectArea")
        self.create_rectangle(
            self.min_x,
            self.min_y,
            self.min_x + 1,
            self.min_y + 1,
            outline="red",
            tags="SelectArea",
        )

        # Log position information
        ratio_x = float(self.camera.capture_size[0] / self.show_size[0])
        ratio_y = float(self.camera.capture_size[1] / self.show_size[1])
        position_info = (
            f"Mouse down: Show ({self.min_x}, {self.min_y}) / "
            f"Capture ({int(self.min_x * ratio_x)}, {int(self.min_y * ratio_y)})"
        )

        print(position_info)
        logger.info(position_info)

        # Rebind stick controls if they were active
        if self.master.is_use_left_stick_mouse.get():
            self.BindLeftClick()
        if self.master.is_use_right_stick_mouse.get():
            self.BindRightClick()

    def MotionRangeSS(self, event: tk.Event) -> None:
        """Update screenshot selection area during mouse motion.

        Args:
            event: Mouse event containing current position
        """
        # Constrain coordinates to widget bounds
        self.max_x = max(0, min(self.show_width, event.x))
        self.max_y = max(0, min(self.show_height, event.y))

        # Update selection rectangle coordinates
        self.coords(
            "SelectArea", self.min_x, self.min_y, self.max_x + 1, self.max_y + 1
        )
        self.coords(
            "SelectAreaFilled", self.min_x, self.min_y, self.max_x + 1, self.max_y + 1
        )

    def ReleaseRangeSS(self, event: tk.Event) -> None:
        """Finalize screenshot selection on mouse release.

        Args:
            event: Mouse event containing release position
        """
        ratio_x = float(self.camera.capture_size[0] / self.show_size[0])
        ratio_y = float(self.camera.capture_size[1] / self.show_size[1])

        # Log position information
        position_info = (
            f"Mouse up: Show ({self.max_x}, {self.max_y}) / "
            f"Capture ({int(self.max_x * ratio_x)}, {int(self.max_y * ratio_y)})"
        )

        print(position_info)
        logger.info(position_info)

        # Ensure min/max are properly ordered
        if self.min_x > self.max_x:
            self.min_x, self.max_x = self.max_x, self.min_x
        if self.min_y > self.max_y:
            self.min_y, self.max_y = self.max_y, self.min_y

        # Save the selected area
        self.camera.saveCapture(
            crop=True,
            crop_ax=[
                int(self.min_x * ratio_x),
                int(self.min_y * ratio_y),
                int(self.max_x * ratio_x),
                int(self.max_y * ratio_y),
            ],
        )

        # Clean up selection visualization
        self.after(250, lambda: self.delete("SelectArea"))

        # Rebind stick controls if they were active
        if self.master.is_use_left_stick_mouse.get():
            self.BindLeftClick()
        if self.master.is_use_right_stick_mouse.get():
            self.BindRightClick()

    def setFps(self, fps: int) -> None:
        """Set the frame rate for video display.

        Args:
            fps: Target frames per second
        """
        self.next_frames = int(1000 / int(fps))
        logger.info(f"FPS set to {fps}")

    def setShowsize(self, show_height: int, show_width: int) -> None:
        """Set the display dimensions for the video feed.

        Args:
            show_height: New display height in pixels
            show_width: New display width in pixels
        """
        self.show_width = int(show_width)
        self.show_height = int(show_height)
        self.show_size = (self.show_width, self.show_height)
        self.config(width=self.show_width, height=self.show_height)

        size_info = f"Show size set to {self.show_width} x {self.show_height}"
        print(size_info)
        logger.info(size_info)

    def mouseCtrlLeftPress(self, event: tk.Event) -> None:
        """Handle control-left-click for color sampling.

        Args:
            event: Mouse event containing click position
        """
        _img = cv2.cvtColor(self.camera.readFrame(), cv2.COLOR_BGR2RGB)

        if self.master.is_use_left_stick_mouse.get():
            self.UnbindLeftClick()

        x, y = event.x, event.y
        ratio_x = float(self.camera.capture_size[0] / self.show_size[0])
        ratio_y = float(self.camera.capture_size[1] / self.show_size[1])

        # Log position and color information
        position_info = (
            f"Mouse down: Show ({x}, {y}) / "
            f"Capture ({int(x * ratio_x)}, {int(y * ratio_y)})"
        )

        color_rgb = _img[int(y * ratio_y), int(x * ratio_x)]
        color_info = f"Color [R: {color_rgb[0]}, G: {color_rgb[1]}, B: {color_rgb[2]}]"

        hsv = cv2.cvtColor(_img, cv2.COLOR_RGB2HSV)
        h, s, v = hsv[int(y * ratio_y), int(x * ratio_x)]
        hsv_info = f"HSV [H: {h}, S: {s}, V: {v}]"

        print(position_info)
        print(color_info)
        print(hsv_info)
        logger.info(position_info)

    def mouseCtrlLeftRelease(self, event: tk.Event) -> None:
        """Handle control-left-click release.

        Args:
            event: Mouse event
        """
        if self.master.is_use_left_stick_mouse.get():
            self.BindLeftClick()

    def mouseLeftPress(self, event: tk.Event, ser: Sender) -> None:
        """Handle left mouse press for left stick control.

        Args:
            event: Mouse event containing press position
            ser: Serial communication object
        """
        if self.master.is_use_right_stick_mouse.get():
            self.UnbindRightClick()

        self.config(cursor="dot")
        self.lx_init, self.ly_init = event.x, event.y

        # Create visualization circles for left stick
        self.lcircle = self.create_oval(
            self.lx_init - self.radius,
            self.ly_init - self.radius,
            self.lx_init + self.radius,
            self.ly_init + self.radius,
            outline="cyan",
            tags="lcircle",
        )

        self.lcircle2 = self.create_oval(
            self.lx_init - self.radius // 10,
            self.ly_init - self.radius // 10,
            self.lx_init + self.radius // 10,
            self.ly_init + self.radius // 10,
            fill="cyan",
            tags="lcircle2",
        )

        # Initialize logging if enabled
        if isTakeLog:
            self.dq = deque()
            self.calc_time = time.perf_counter()
            self.dq.append((0, 0, 0))  # Initial neutral position

        self._langle = None
        self._lmag = None

    def mouseLeftPressing(self, event: tk.Event, ser: Sender, angle: float = 0) -> None:
        """Handle left mouse motion for left stick control.

        Args:
            event: Mouse event containing current position
            ser: Serial communication object
            angle: Optional fixed angle (defaults to calculating from mouse position)
        """
        # Calculate stick angle and magnitude from mouse position
        langle = np.rad2deg(np.arctan2(self.ly_init - event.y, event.x - self.lx_init))
        mag = min(
            1.0,
            max(
                0.0,
                np.hypot(self.ly_init - event.y, event.x - self.lx_init) / self.radius,
            ),
        )

        # Send stick commands and log data if needed
        self._process_stick_movement(
            stick_type="left",
            angle=langle,
            magnitude=mag,
            base_x=self.lx_init,
            base_y=self.ly_init,
            event_x=event.x,
            event_y=event.y,
            ser=ser,
        )

        # Update visualization
        self._update_stick_visualization(
            stick_type="left",
            angle=langle,
            magnitude=mag,
            base_x=self.lx_init,
            base_y=self.ly_init,
            event_x=event.x,
            event_y=event.y,
        )

        self._langle = langle
        self._lmag = mag

    def mouseLeftRelease(self, ser: Sender) -> None:
        """Handle left mouse release for left stick control.

        Args:
            ser: Serial communication object
        """
        self.config(cursor="tcross")
        self.ser.writeRow("0x2 8 80 80", is_show=False)  # Neutral position command
        self.delete("lcircle")
        self.delete("lcircle2")

        if self.master.is_use_right_stick_mouse.get():
            self.BindRightClick()

        # Finalize logging if enabled
        if isTakeLog and self.dq is not None:
            if self._langle and self._lmag:
                self.dq.append(
                    (self._langle, self._lmag, time.perf_counter() - self.calc_time)
                )
            for data in self.dq:
                self.LSTICK_logger.debug(",".join(map(str, data)))

    def mouseRightPress(self, event: tk.Event, ser: Sender) -> None:
        """Handle right mouse press for right stick control.

        Args:
            event: Mouse event containing press position
            ser: Serial communication object
        """
        if self.master.is_use_left_stick_mouse.get():
            self.UnbindLeftClick()

        self.config(cursor="dot")
        self.rx_init, self.ry_init = event.x, event.y

        # Create visualization circles for right stick
        self.rcircle = self.create_oval(
            self.rx_init - self.radius,
            self.ry_init - self.radius,
            self.rx_init + self.radius,
            self.ry_init + self.radius,
            outline="red",
            tags="rcircle",
        )

        self.rcircle2 = self.create_oval(
            self.rx_init - self.radius // 10,
            self.ry_init - self.radius // 10,
            self.rx_init + self.radius // 10,
            self.ry_init + self.radius // 10,
            fill="red",
            tags="rcircle2",
        )

        # Initialize logging if enabled
        if isTakeLog:
            self.dq = deque()
            self.calc_time = time.perf_counter()
            self.dq.append((0, 0, 0))  # Initial neutral position

        self._rangle = None
        self._rmag = None

    def mouseRightPressing(
        self, event: tk.Event, ser: Sender, angle: float = 0
    ) -> None:
        """Handle right mouse motion for right stick control.

        Args:
            event: Mouse event containing current position
            ser: Serial communication object
            angle: Optional fixed angle (defaults to calculating from mouse position)
        """
        # Calculate stick angle and magnitude from mouse position
        rangle = np.rad2deg(np.arctan2(self.ry_init - event.y, event.x - self.rx_init))
        mag = min(
            1.0,
            max(
                0.0,
                np.hypot(self.ry_init - event.y, event.x - self.rx_init) / self.radius,
            ),
        )

        # Send stick commands and log data if needed
        self._process_stick_movement(
            stick_type="right",
            angle=rangle,
            magnitude=mag,
            base_x=self.rx_init,
            base_y=self.ry_init,
            event_x=event.x,
            event_y=event.y,
            ser=ser,
        )

        # Update visualization
        self._update_stick_visualization(
            stick_type="right",
            angle=rangle,
            magnitude=mag,
            base_x=self.rx_init,
            base_y=self.ry_init,
            event_x=event.x,
            event_y=event.y,
        )

        self._rangle = rangle
        self._rmag = mag

    def mouseRightRelease(self, ser: Sender) -> None:
        """Handle right mouse release for right stick control.

        Args:
            ser: Serial communication object
        """
        self.config(cursor="tcross")
        self.ser.writeRow("3 8 80 80 80 80", is_show=False)  # Neutral position command
        self.delete("rcircle")
        self.delete("rcircle2")

        if self.master.is_use_left_stick_mouse.get():
            self.BindLeftClick()

        # Finalize logging if enabled
        if isTakeLog and self.dq is not None:
            if self._rangle and self._rmag:
                self.dq.append(
                    (self._rangle, self._rmag, time.perf_counter() - self.calc_time)
                )
            for data in self.dq:
                self.RSTICK_logger.debug(",".join(map(str, data)))

    def _process_stick_movement(
        self,
        stick_type: str,
        angle: float,
        magnitude: float,
        base_x: int,
        base_y: int,
        event_x: int,
        event_y: int,
        ser: Sender,
    ) -> None:
        """Process stick movement and send appropriate commands.

        Args:
            stick_type: "left" or "right" stick
            angle: Stick angle in degrees
            magnitude: Stick magnitude (0.0 to 1.0)
            base_x: Stick center x coordinate
            base_y: Stick center y coordinate
            event_x: Current mouse x coordinate
            event_y: Current mouse y coordinate
            ser: Serial communication object
        """
        if isTakeLog and (self._langle is not None or self._rangle is not None):
            current_time = time.perf_counter()
            if current_time - self.calc_time > 1 / 30:  # 30Hz sampling rate
                # Convert angle and magnitude to controller values
                x_val = int(128 + magnitude * 127.5 * np.cos(np.deg2rad(angle)))
                y_val = int(128 - magnitude * 127.5 * np.sin(np.deg2rad(angle)))

                # Send appropriate command based on stick type
                if stick_type == "left":
                    command = f"2 8 {hex(x_val)} {hex(y_val)}"
                else:
                    command = f"1 8 {hex(x_val)} {hex(y_val)}"

                ser.writeRow(command, is_show=False)

                self.dq.append((angle, magnitude, current_time - self.calc_time))
                self.calc_time = current_time
        elif not isTakeLog:
            # Same conversion but without logging
            x_val = int(128 + magnitude * 127.5 * np.cos(np.deg2rad(angle)))
            y_val = int(128 - magnitude * 127.5 * np.sin(np.deg2rad(angle)))

            if stick_type == "left":
                command = f"2 8 {hex(x_val)} {hex(y_val)}"
            else:
                command = f"1 8 {hex(x_val)} {hex(y_val)}"

            ser.writeRow(command, is_show=False)

    def _update_stick_visualization(
        self,
        stick_type: str,
        angle: float,
        magnitude: float,
        base_x: int,
        base_y: int,
        event_x: int,
        event_y: int,
    ) -> None:
        """Update the visualization of stick position.

        Args:
            stick_type: "left" or "right" stick
            angle: Stick angle in degrees
            magnitude: Stick magnitude (0.0 to 1.0)
            base_x: Stick center x coordinate
            base_y: Stick center y coordinate
            event_x: Current mouse x coordinate
            event_y: Current mouse y coordinate
        """
        if magnitude >= 1.0:
            # Stick is at maximum deflection - show at edge of circle
            center_x = (self.radius + self.radius // 11) * np.cos(np.deg2rad(angle))
            center_y = (self.radius + self.radius // 11) * np.sin(np.deg2rad(angle))
            circ_x_1 = base_x + center_x - self.radius // 10
            circ_x_2 = base_x + center_x + self.radius // 10
            circ_y_1 = base_y - center_y - self.radius // 10
            circ_y_2 = base_y - center_y + self.radius // 10
        else:
            # Stick follows mouse position directly
            circ_x_1 = event_x - self.radius // 10
            circ_x_2 = event_x + self.radius // 10
            circ_y_1 = event_y - self.radius // 10
            circ_y_2 = event_y + self.radius // 10

        # Update the appropriate stick visualization
        if stick_type == "left":
            self.coords("lcircle2", circ_x_1, circ_y_1, circ_x_2, circ_y_2)
        else:
            self.coords("rcircle2", circ_x_1, circ_y_1, circ_x_2, circ_y_2)

    def startCapture(self) -> None:
        """Start the video capture and display loop."""
        self.capture()

    def capture(self) -> None:
        """Capture and display video frames in a loop."""
        if self.is_show_var and not self.is_show_var.get():
            self.after(self.next_frames, self.capture)
            return

        image_bgr = self.camera.readFrame()

        if image_bgr is not None:
            # Convert and resize the image for display
            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            image_pil = Image.fromarray(image_rgb).resize(self.show_size)
            image_tk = ImageTk.PhotoImage(image_pil)

            # Update the display
            self.im = image_tk
            self.itemconfig(self.im_, image=image_tk)
        else:
            # Show disabled image if no frame is available
            self.im = self.disabled_tk
            self.itemconfig(self.im_, image=self.disabled_tk)

        # Schedule next frame capture
        self.after(self.next_frames, self.capture)

    def saveCapture(self) -> None:
        """Save the current camera frame."""
        self.camera.saveCapture()

    def ImgRect(
        self, x1: int, y1: int, x2: int, y2: int, outline: str, tag: str, ms: int
    ) -> None:
        """Draw a rectangle on the image that automatically disappears.

        Args:
            x1: Top-left x coordinate in capture coordinates
            y1: Top-left y coordinate in capture coordinates
            x2: Bottom-right x coordinate in capture coordinates
            y2: Bottom-right y coordinate in capture coordinates
            outline: Color of the rectangle outline
            tag: Tag for the rectangle elements
            ms: Milliseconds until the rectangle disappears
        """
        # Convert from capture coordinates to display coordinates
        ratio_x = float(self.show_size[0] / self.camera.capture_size[0])
        ratio_y = float(self.show_size[1] / self.camera.capture_size[1])

        # Draw white background rectangle for visibility
        self.create_rectangle(
            (x1 - 1.0) * ratio_x,
            (y1 - 1.0) * ratio_y,
            (x2 + 1.0) * ratio_x,
            (y2 + 1.0) * ratio_y,
            width=4.5,
            outline="white",
            tags=tag,
        )

        # Draw main colored rectangle
        self.create_rectangle(
            x1 * ratio_x,
            y1 * ratio_y,
            x2 * ratio_x,
            y2 * ratio_y,
            width=2.5,
            outline=outline,
            tags=tag,
        )

        # Schedule rectangle removal
        self.after(ms, lambda: self.delete(tag))

    def deleteImageRect(self, tag: str) -> None:
        """Delete a rectangle from the canvas.

        Args:
            tag: Tag of the rectangle to delete
        """
        self.delete(tag)

    def BindLeftClick(self) -> None:
        """Bind left mouse button events for left stick control."""
        self.bind("<ButtonPress-1>", lambda ev: self.mouseLeftPress(ev, self.ser))
        self.bind("<Button1-Motion>", lambda ev: self.mouseLeftPressing(ev, self.ser))
        self.bind("<ButtonRelease-1>", lambda ev: self.mouseLeftRelease(self.ser))

        logger.debug("Left stick mouse controls bound")

    def BindRightClick(self) -> None:
        """Bind right mouse button events for right stick control."""
        self.bind("<ButtonPress-3>", lambda ev: self.mouseRightPress(ev, self.ser))
        self.bind("<Button3-Motion>", lambda ev: self.mouseRightPressing(ev, self.ser))
        self.bind("<ButtonRelease-3>", lambda ev: self.mouseRightRelease(self.ser))

        logger.debug("Right stick mouse controls bound")

    def UnbindLeftClick(self) -> None:
        """Unbind left mouse button events."""
        self.unbind("<ButtonPress-1>")
        self.unbind("<Button1-Motion>")
        self.unbind("<ButtonRelease-1>")

        logger.debug("Left stick mouse controls unbound")

    def UnbindRightClick(self) -> None:
        """Unbind right mouse button events."""
        self.unbind("<ButtonPress-3>")
        self.unbind("<Button3-Motion>")
        self.unbind("<ButtonRelease-3>")

        logger.debug("Right stick mouse controls unbound")


# GUI of switch controller simulator
class ControllerGUI:
    # 定数定義
    WINDOW_SIZE = (600, 300)
    JOYCON_SIZE = (300, 300)
    # OSに応じてボタンスタイルを分岐
    if platform.system() == "Windows":
        BUTTON_STYLE = {"bg": "#343434", "fg": "white"}
    else:
        BUTTON_STYLE = {"fg": "black"}  # macOSではbgを指定しない
    COLORS = {"L": "#95f1ff", "R": "#ff6b6b"}
    OFFSET = (250, 125)
    ABXY_POSITION = (0.2, 0.3)
    HAT_POSITION = (0.2, 0.6)

    def __init__(self, root: tk.Tk, ser: Serial) -> None:
        self.ser = ser
        self.root = root
        self._setup_window()
        self._create_joycon_frames()
        self._create_abxy_buttons()
        self._create_hat_buttons()
        self._create_side_buttons()
        logger.debug("Created GUI controller")

    def _setup_window(self) -> None:
        self.window = tk.Toplevel(self.root)
        self.window.title("Switch Controller Simulator")
        self.window.resizable(False, False)

        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        x = self.OFFSET[0] + root_x
        y = self.OFFSET[1] + root_y
        self.window.geometry(f"{self.WINDOW_SIZE[0]}x{self.WINDOW_SIZE[1]}+{x}+{y}")

    def _create_joycon_frames(self) -> None:
        self.joycon_L = self._create_frame(self.COLORS["L"], 0)
        self.joycon_R = self._create_frame(self.COLORS["R"], 1)

    def _create_frame(self, color: str, column: int) -> tk.Frame:
        frame = tk.Frame(
            self.window,
            width=self.JOYCON_SIZE[0],
            height=self.JOYCON_SIZE[1],
            bg=color,
            relief="flat",
        )
        frame.grid(row=0, column=column)
        return frame

    def _create_abxy_buttons(self) -> None:
        buttons = [
            ("A", UnitCommand.A, 1, 2),
            ("B", UnitCommand.B, 2, 1),
            ("X", UnitCommand.X, 0, 1),
            ("Y", UnitCommand.Y, 1, 0),
        ]
        self._create_grid_buttons(
            self.joycon_R, buttons, self.ABXY_POSITION, self.COLORS["R"]
        )

    def _create_hat_buttons(self) -> None:
        buttons = [
            ("UP", UnitCommand.UP, 0, 1),
            ("", UnitCommand.UP_RIGHT, 0, 2),
            ("RIGHT", UnitCommand.RIGHT, 1, 2),
            ("", UnitCommand.DOWN_RIGHT, 2, 2),
            ("DOWN", UnitCommand.DOWN, 2, 1),
            ("", UnitCommand.DOWN_LEFT, 2, 0),
            ("LEFT", UnitCommand.LEFT, 1, 0),
            ("", UnitCommand.UP_LEFT, 0, 0),
        ]
        self._create_grid_buttons(
            self.joycon_L, buttons, self.HAT_POSITION, self.COLORS["L"]
        )

    def _create_grid_buttons(
        self,
        parent: tk.Frame,
        buttons: List[Tuple[str, UnitCommand.UnitCommand, int, int]],
        rel_position: Tuple[float, float],
        bg_color: str,
    ) -> None:
        frame = tk.Frame(parent, bg=bg_color, relief="flat")
        frame.place(relx=rel_position[0], rely=rel_position[1])

        for text, command, row, col in buttons:
            btn = self._create_button(
                frame, text, lambda cmd=command: cmd().start(self.ser), width=7
            )
            btn.grid(row=row, column=col)

    def _create_side_buttons(self) -> None:
        l_buttons = [
            ("L", UnitCommand.L, 30, 30, 20),
            ("ZL", UnitCommand.ZL, 30, 0, 20),
            ("LCLICK", UnitCommand.LCLICK, 120, 120, 7),
            ("MINUS", UnitCommand.MINUS, 220, 70, 5),
            ("CAP", UnitCommand.CAPTURE, 200, 270, 5),
        ]
        r_buttons = [
            ("R", UnitCommand.R, 120, 30, 20),
            ("ZR", UnitCommand.ZR, 120, 0, 20),
            ("RCLICK", UnitCommand.RCLICK, 120, 205, 7),
            ("PLUS", UnitCommand.PLUS, 35, 70, 5),
            ("HOME", UnitCommand.HOME, 50, 270, 5),
        ]
        self._create_placed_buttons(self.joycon_L, l_buttons)
        self._create_placed_buttons(self.joycon_R, r_buttons)

    def _create_placed_buttons(
        self,
        parent: tk.Frame,
        buttons: List[Tuple[str, UnitCommand.UnitCommand, int, int, int]],
    ) -> None:
        for text, command, x, y, width in buttons:
            btn = self._create_button(
                parent, text, lambda cmd=command: cmd().start(self.ser), width=width
            )
            btn.place(x=x, y=y)

    def _create_button(
        self,
        parent: Union[tk.Frame, tk.Toplevel],
        text: str,
        command: Any,
        **kwargs: Any,
    ) -> tk.Button:
        btn = tk.Button(
            parent, text=text, command=command, **{**self.BUTTON_STYLE, **kwargs}
        )
        return btn

    # プロキシメソッド
    def bind(self, event: str, func: Callable[[tk.Event], Any]) -> None:
        self.window.bind(event, func)

    def protocol(self, event: str, func: Callable[[], Any]) -> None:
        self.window.protocol(event, func)

    def focus_force(self) -> None:
        self.window.focus_force()

    def destroy(self) -> None:
        self.window.destroy()
        logger.debug("GUI controller destroyed")


# To avoid the error says 'ScrolledText' object has no attribute 'flush'
class MyScrolledText(ScrolledText):
    def flush(self) -> None:
        pass
