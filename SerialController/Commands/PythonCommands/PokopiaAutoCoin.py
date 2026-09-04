#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from datetime import datetime, timedelta
import re
from typing import Any, Tuple, Optional
import cv2
from Commands.PythonCommandBase import ImageProcPythonCommand
from loguru import logger
import tkinter as tk
import numpy as np
import os
from Commands.Keys import Button, Stick, Direction, Hat


class PokopiaAutoStamp(ImageProcPythonCommand):
    NAME = "Pokopia Auto STAMP"

    NEUTRAL = "0x3 8 80 80 80 80"
    NEUTRAL2 = "0x0 8"

    BUTTON_A = "0x10 8"
    BUTTON_LCLICK = "0x1000 8"
    BUTTON_RCLICK = "0x2000 8"

    LSTICK_UP = "0x1 8 80 0"
    LSTICK_DOWN = "0x1 8 80 ff"
    LSTICK_LEFT = "0x1 8 0 80"
    LSTICK_RIGHT = "0x1 8 ff 80"

    RSTICK_UP = "0x2 8 80 0"
    RSTICK_DOWN = "0x2 8 80 ff"
    RSTICK_LEFT = "0x2 8 0 80"
    RSTICK_RIGHT = "0x2 8 ff 80"

    numbers_path = [
        "pokopia/numbers/0.png",
        "pokopia/numbers/1.png",
        "pokopia/numbers/2.png",
        "pokopia/numbers/3.png",
        "pokopia/numbers/4.png",
        "pokopia/numbers/5.png",
        "pokopia/numbers/6.png",
        "pokopia/numbers/7.png",
        "pokopia/numbers/8.png",
        "pokopia/numbers/9.png",
    ]

    ax_list = [
        [[150, 210, 220, 280], [156, 260, 308, 320]],
        [[350, 210, 425, 280], [360, 260, 516, 320]],
        [[560, 210, 625, 280], [554, 260, 716, 320]],
        [[760, 210, 825, 280], [760, 260, 922, 320]],
        [[960, 210, 1030, 280], [968, 260, 1122, 320]],
    ]

    def __init__(self, cam: Any, gui: tk.Tk):
        super().__init__(cam, gui)
        self.templates = self._load_templates(template_dir="Template/pokopia/numbers")
        self.stamp_no = 0
        self.datetime: Optional[datetime] = None

    def send_command(self, command: str, wait=0.0):
        self.keys.ser.writeRow(command)
        self.wait(wait=wait)

    def do(self) -> None:
        self.set_initial_date()
        while self.alive:
            print(f"\n-------------------------\nStamps: {self.stamp_no}")
            flag = False
            days = 1

            if self.stamp_no >= 5:
                flag = True
                days = 7
            self.increment_date(days=days)
            self.act_PC(last_flag=flag)
            self.detect_Stamp_state()

            while not self.isContainTemplate(
                template_path="pokopia/get_stamp.png",
                threshold=0.9,
                use_gray=True,
                show_value=False,
                show_position=False,
                crop=[968, 568, 1188, 616],
            ):
                self.wait(0.3)
                # 稀にスタンプ取得画面に遷移しない場合があるためその対策
                if self.isContainTemplate(
                    template_path="pokopia/pc_main.png",
                    threshold=0.8,
                    use_gray=True,
                    show_value=False,
                    show_position=False,
                    crop=[814, 666, 934, 720],
                ):
                    self.press(Direction.RIGHT)
                    self.press(Direction.DOWN)
                    self.press(Button.A)
            self.press(Button.A, wait=0.7)

            # スタンプ所得済の場合は再度日付変更
            if self.isContainTemplate(
                template_path="pokopia/already_got_stamp.png",
                threshold=0.8,
                use_gray=True,
                show_value=False,
                show_position=False,
                crop=[442, 336, 832, 394],
            ):
                print("再度日付変更します")
                self.increment_date()

            # スタンプ押し直す場合の処理
            while self.isContainTemplate(
                template_path="pokopia/restamp.png",
                threshold=0.8,
                use_gray=True,
                show_value=False,
                show_position=False,
                crop=[0, 662, 194, 720],
            ):
                self.press(Button.A, wait=0.3)
                self.stamp_no = 5  # スタンプ精算のため日付を飛ばす処理の発火用

            self.stamp_no += 1
            self.wait(2.0)
            self.press(Button.B, wait=1.0)
            self.press(Direction.RIGHT, wait=0.4)
            while not self.isContainTemplate(
                template_path="pokopia/daily.png",
                threshold=0.8,
                use_gray=True,
                show_value=False,
                show_position=False,
                crop=[558, 72, 716, 128],
            ):
                self.press(Button.A, wait=0.3)
            while not self.isContainTemplate(
                template_path="pokopia/button_B.png",
                threshold=0.8,
                use_gray=True,
                show_value=False,
                show_position=False,
                crop=[1050, 674, 1102, 716],
            ):
                self.press(Button.A, wait=0.4)

            buy_list = self.check_bought()
            self.buy_recipe(buy_list)
            while not self.isContainTemplate(
                template_path="pokopia/game_window_ZR.png",
                threshold=0.9,
                use_gray=True,
                show_value=False,
                show_position=False,
                crop=[1164, 582, 1200, 610],
            ):
                print(".", end="")
                self.press(Button.B)
                self.wait(0.5)

    def buy_recipe(self, ls: list) -> None:
        if sum(ls) == 0:
            return
        for idx, tf in enumerate(ls):
            if tf:
                print(f"{idx + 1}つめのデイリーを購入します")
                while not self.isContainTemplate(
                    template_path="pokopia/learned.png",
                    threshold=0.8,
                    use_gray=True,
                    show_value=False,
                    show_position=False,
                    crop=self.ax_list[idx][1],
                ):
                    self.press(Button.A, wait=0.4)
            self.press(Direction.RIGHT, wait=0.4)

    def check_bought(self) -> list:
        res = [False, False, False, False, False]
        for idx, ax in enumerate(self.ax_list):
            if (
                self.isContainTemplate(
                    template_path="pokopia/recipe.png",
                    threshold=0.8,
                    use_gray=True,
                    show_value=False,
                    show_position=False,
                    crop=ax[0],
                )
                or self.isContainTemplate(
                    template_path="pokopia/recipe2.png",
                    threshold=0.8,
                    use_gray=True,
                    show_value=False,
                    show_position=False,
                    crop=ax[0],
                )
                or self.isContainTemplate(
                    template_path="pokopia/recipe3.png",
                    threshold=0.8,
                    use_gray=True,
                    show_value=False,
                    show_position=False,
                    crop=ax[0],
                )
            ) and not self.isContainTemplate(
                template_path="pokopia/learned.png",
                threshold=0.8,
                use_gray=True,
                show_value=False,
                show_position=False,
                crop=ax[1],
            ):
                res[idx] = True
        print(res)
        return res

    def wait_home(self) -> None:
        while not self.isContainTemplate(
            template_path="./pokopia/NX2_Home.png",
            threshold=0.9,
            use_gray=False,
            show_value=False,
            show_position=False,
            crop=[225, 520, 315, 615],
        ):
            self.press(Button.HOME)
            self.wait(0.5)
        # print("Home detected")

    def move_to_date_change(self) -> None:
        self.send_command(self.LSTICK_LEFT, wait=0.04)
        self.send_command(self.NEUTRAL, wait=0.16)
        self.send_command(self.LSTICK_DOWN, wait=0.04)
        self.send_command(self.LSTICK_LEFT, wait=0.04)
        self.send_command(self.BUTTON_A, wait=0.80)

        while not self.isContainTemplate(
            template_path="./pokopia/NX2_DeviceUpdate.png",
            threshold=0.9,
            use_gray=True,
            show_value=False,
            show_position=False,
            crop=[472, 148, 602, 188],
        ):
            self.send_command(self.LSTICK_DOWN, wait=0.1)
        self.send_command(self.LSTICK_RIGHT, wait=0.2)
        self.send_command(self.NEUTRAL)

        self.send_command(self.LSTICK_DOWN, wait=0.1)
        self.send_command(self.RSTICK_DOWN, wait=0.1)
        self.send_command(self.LSTICK_DOWN, wait=0.1)
        self.send_command(self.RSTICK_DOWN, wait=0.1)
        self.send_command(self.LSTICK_DOWN, wait=0.1)
        self.send_command(self.RSTICK_DOWN, wait=0.1)
        self.send_command(self.LSTICK_DOWN, wait=0.1)
        self.send_command(self.NEUTRAL)
        self.send_command(self.BUTTON_A, wait=0.4)
        self.send_command(self.NEUTRAL)
        self.send_command(self.RSTICK_DOWN, wait=0.1)
        self.send_command(self.LSTICK_DOWN, wait=0.1)
        self.send_command(self.NEUTRAL)
        self.send_command(self.BUTTON_A, wait=0.3)
        self.send_command(self.NEUTRAL, wait=0.4)
        if not self.isContainTemplate(
            template_path="pokopia/check_DateChangeScreen.png",
            threshold=0.9,
            use_gray=True,
            show_value=False,
            show_position=False,
            crop=[68, 28, 302, 62],
        ):
            self.press(Button.HOME, wait=1.5)
            self.move_to_date_change()
        self.datetime = self.read_datetime()[0]

    def shift_date(self, days: int) -> None:
        _wait = 0.1
        self.send_command(self.LSTICK_RIGHT, wait=_wait)
        self.send_command(self.RSTICK_RIGHT, wait=_wait / 2)
        self.send_command(self.NEUTRAL)
        if days > 0:
            for i in range(days):
                if i % 2 == 0:
                    self.send_command(self.RSTICK_UP, wait=_wait)
                    self.send_command(self.NEUTRAL)
                else:
                    self.send_command(self.LSTICK_UP, wait=_wait)
                    self.send_command(self.NEUTRAL)
        elif days < 0:
            for i in range(-days):
                if i % 2 == 0:
                    self.send_command(self.RSTICK_DOWN, wait=_wait)
                    self.send_command(self.NEUTRAL)
                else:
                    self.send_command(self.LSTICK_DOWN, wait=_wait)
                    self.send_command(self.NEUTRAL)
        self.send_command(self.LSTICK_RIGHT, wait=0.1)
        self.send_command(self.RSTICK_RIGHT, wait=0.1)
        self.send_command(self.LSTICK_RIGHT, wait=0.1)
        self.send_command(self.BUTTON_A, wait=0.3)
        self.send_command(self.NEUTRAL)

    def set_initial_date(self) -> None:
        self.wait_home()
        self.move_to_date_change()

        assert self.datetime is not None, "self.datetime must be set by read_datetime"

        # self.datetimeの月の初め（1日）に設定
        target = self.datetime.replace(day=1)
        current = self.datetime
        days_to_shift = (target - current).days

        print(f"Current detected date: {current.strftime('%Y-%m-%d')}")
        print(
            f"Shifting to month start: {target.strftime('%Y-%m-%d')} (days {days_to_shift})"
        )

        if days_to_shift != 0:
            self.shift_date(days=days_to_shift)

        self.press(Button.HOME, wait=0.7)
        self.press(Button.HOME)
        print("Waiting", end="")
        while not self.isContainTemplate(
            template_path="pokopia/game_window_ZR.png",
            threshold=0.9,
            use_gray=True,
            show_value=False,
            show_position=False,
            crop=[1164, 582, 1200, 610],
        ):
            print(".", end="")
            self.press(Button.B)
            self.wait(0.5)
        print("\ninitialize finished")

    def increment_date(self, days: int = 1) -> None:
        self.wait_home()
        self.move_to_date_change()
        self.shift_date(days=days)
        self.press(Button.HOME, wait=0.7)
        self.press(Button.HOME)

        # if self.datetime is not None:
        #     self.datetime += timedelta(days=1)
        #     print(f"Date changed to {self.datetime.strftime('%Y-%m-%d')}")

    def act_PC(self, last_flag: bool = False) -> None:
        # ZR検知するまでb連打
        self.wait(1)
        print("Waiting day shift", end="")
        while not self.isContainTemplate(
            template_path="pokopia/game_window_ZR.png",
            threshold=0.9,
            use_gray=True,
            show_value=False,
            show_position=False,
            crop=[1164, 582, 1200, 610],
        ):
            print(".", end="")
            self.press(Button.B)
            self.wait(0.5)
        print("Complete!")
        if last_flag:
            self.press(Direction.UP)
            self.press(Button.A)
            self.set_initial_date()
            print("1つ目のスタンプ入手処理")
            while not self.isContainTemplate(
                template_path="pokopia/get_stamp.png",
                threshold=0.9,
                use_gray=True,
                show_value=False,
                show_position=False,
                crop=[968, 568, 1188, 616],
            ):
                self.wait(0.3)
            self.press(Button.A, wait=0.7)

            self.increment_date()
            self.act_PC()
        # 上入力->a button連打
        self.press(Direction.UP)
        self.press(Button.A)

    def detect_Stamp_state(self) -> None:
        while not self.isContainTemplate(
            template_path="pokopia/button_B.png",
            threshold=0.8,
            use_gray=True,
            show_value=False,
            show_position=False,
            crop=[1022, 672, 1272, 716],
        ):
            if self.isContainTemplate(
                template_path="pokopia/new_stamp.png",
                threshold=0.8,
                use_gray=True,
                show_value=False,
                show_position=False,
                crop=[278, 590, 760, 646],
            ):
                print("Get New Card.")
                self.stamp_no = 0
                self.press(Button.A)
            elif self.isContainTemplate(
                template_path="pokopia/get.png",
                threshold=0.8,
                use_gray=True,
                show_value=False,
                show_position=False,
                crop=[278, 590, 760, 646],
            ):
                self.wait(0.5)
                print("Complete Stamp Card")
                self.press(Button.A)

                print("Get New Card")
                self.stamp_no += 0
                self.press(Button.A)

            self.wait(0.4)

    def read_datetime(self, score_thr=0.9, nms_dist=15):
        """
        Returns:
            dt (datetime or None)
            digit_str (str)
            scores (list[float])
        """
        frame = self.camera.readFrame()
        if frame is None:
            return None, "", []
        roi = frame[318:375, 174:896]

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        hits = []
        for digit, tmpl in self.templates.items():
            res = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)

            while True:
                _, maxv, _, maxp = cv2.minMaxLoc(res)
                if maxv < score_thr:
                    break

                x, y = maxp
                h, w = tmpl.shape[:2]

                hits.append(
                    {
                        "digit": digit,
                        "x": x,
                        "y": y,
                        "w": w,
                        "h": h,
                        "score": float(maxv),
                    }
                )

                # 同じ場所の再検出を防ぐ（簡易NMS）
                res[y : y + h, x : x + w] = -1.0

        if not hits:
            return None, "", []

        # x方向に近いものを1つにまとめる（数字1桁=1検出）
        hits = self._merge_by_x(hits, nms_dist)

        hits.sort(key=lambda d: d["x"])

        digit_str = "".join(h["digit"] for h in hits)
        scores = [h["score"] for h in hits]

        # datetime化（12桁以上ある前提）
        dt = None
        if len(digit_str) >= 12:
            s = digit_str[:12]
            try:
                dt = datetime(
                    int(s[0:4]),
                    int(s[4:6]),
                    int(s[6:8]),
                    int(s[8:10]),
                    int(s[10:12]),
                )
            except ValueError:
                dt = None

        return dt, digit_str, scores

    # -------------------------
    # internal
    # -------------------------
    def _load_templates(self, template_dir):
        """
        0-9 の数字テンプレを grayscale で読み込む
        ファイル名に含まれる数字をラベルとして使用
        """
        templates = {}
        digit_re = re.compile(r"([0-9])")

        for fn in os.listdir(template_dir):
            if not fn.lower().endswith((".png", ".jpg", ".bmp")):
                continue

            m = digit_re.search(fn)
            if not m:
                continue

            digit = m.group(1)
            img = cv2.imread(os.path.join(template_dir, fn), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue

            templates[digit] = img

        missing = [str(i) for i in range(10) if str(i) not in templates]
        if missing:
            raise ValueError(f"テンプレ不足: {missing}")

        return templates

    def _merge_by_x(self, hits, dist):
        """
        x座標が近い検出をまとめ、scoreが最大のものを残す
        """
        hits = sorted(hits, key=lambda d: d["x"])
        merged = []

        for h in hits:
            if not merged:
                merged.append(h)
                continue

            prev = merged[-1]
            if abs(h["x"] - prev["x"]) <= dist:
                if h["score"] > prev["score"]:
                    merged[-1] = h
            else:
                merged.append(h)

        return merged
