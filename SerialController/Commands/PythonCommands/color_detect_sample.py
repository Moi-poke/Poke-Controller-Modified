#!/usr/bin/env python3

import time
import tkinter as tk
from typing import Any

import cv2
import numpy as np
from Commands.PythonCommandBase import ImageProcPythonCommand

# from loguru import logger


def calculate_iou(box1: list, box2: list) -> float:
    """
    2つの矩形のIoU（Intersection over Union）を計算する関数
    :param box1: 矩形1 (x1, y1, x2, y2)
    :param box2: 矩形2 (x1, y1, x2, y2)
    :return: IoUの値
    """
    # 矩形の交差部分を計算
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])

    # 交差部分がない場合
    if x2 <= x1 or y2 <= y1:
        return 0.0

    # 交差部分の面積
    intersection_area = (x2 - x1) * (y2 - y1)

    # 各矩形の面積
    box1_area = (box1[2] - box1[0]) * (box1[3] - box1[1])
    box2_area = (box2[2] - box2[0]) * (box2[3] - box2[1])

    # IoUを計算
    union_area = box1_area + box2_area - intersection_area
    iou: float = intersection_area / union_area

    return iou


def filter_overlapping_boxes(boxes: list, iou_threshold: float = 0.25) -> list:
    """
    IoUを使って、重なっている矩形をフィルタリングする関数
    :param boxes: 矩形のリスト [(x1, y1, x2, y2), ...]
    :param iou_threshold: 重複と見なすIoUの閾値
    :return: フィルタリング後の矩形リスト
    """
    keep_boxes = []

    for i, box1 in enumerate(boxes):
        keep = True
        for j, box2 in enumerate(boxes):
            if i != j:
                iou = calculate_iou(box1, box2)
                if iou > iou_threshold:
                    # 面積を比較して小さい方を除去
                    box1_area = (box1[2] - box1[0]) * (box1[3] - box1[1])
                    box2_area = (box2[2] - box2[0]) * (box2[3] - box2[1])
                    if box1_area < box2_area:
                        keep = False
                        break
        if keep:
            keep_boxes.append(box1)

    return keep_boxes


# 色検出のサンプル
class ColorDetectSampleCommand(ImageProcPythonCommand):
    NAME = "色検出のサンプル"

    def __init__(self, cam, gui) -> None:  # type: ignore
        super().__init__(cam, gui)

        # 初期のHSV値（ターゲット色と誤差）
        self.target_hue: int | float = 250
        self.target_sat: int | float = 200
        self.target_val: int | float = 120
        self.error_hue: int | float = 10
        self.error_sat: int | float = 105
        self.error_val: int | float = 128

    def do(self) -> None:
        self.create_slider_window()
        while self.alive:
            print(f"at time: {time.time()}")
            self.detect_color_in_frame(ms=1000)
            time.sleep(1)
            print("-----------------")

        # HSVのスライダーの変更時に呼ばれる関数

    def update_threshold(self, event: Any | None = None) -> None:
        # スライダーの値を取得
        self.target_hue = self.target_hue_slider.get()
        self.target_sat = self.target_sat_slider.get()
        self.target_val = self.target_val_slider.get()
        self.error_hue = self.error_hue_slider.get()
        self.error_sat = self.error_sat_slider.get()
        self.error_val = self.error_val_slider.get()

        self.update_color_label()

    def update_color_label(self) -> None:
        # ターゲット色と誤差に基づいてラベルの色を設定
        target_color_rgb = self.hsv_to_rgb(
            self.target_hue, self.target_sat, self.target_val
        )

        # ターゲット色をラベルの背景色として設定
        target_color_hex = "#%02x%02x%02x" % target_color_rgb
        self.color_label.config(bg=target_color_hex)

    def hsv_to_rgb(
        self, h: float | int | str, s: float | int | str, v: float | int | str
    ) -> tuple:
        # HSVをRGBに変換する関数
        h = float(h)
        s = float(s) / 255.0
        v = float(v) / 255.0

        i = int(h // 60.0) % 6
        f = (h / 60.0) - i
        p = v * (1.0 - s)
        q = v * (1.0 - f * s)
        t = v * (1.0 - (1.0 - f) * s)

        if i == 0:
            r, g, b = v, t, p
        elif i == 1:
            r, g, b = q, v, p
        elif i == 2:
            r, g, b = p, v, t
        elif i == 3:
            r, g, b = p, q, v
        elif i == 4:
            r, g, b = t, p, v
        elif i == 5:
            r, g, b = v, p, q

        return int(r * 255), int(g * 255), int(b * 255)

    def create_slider_window(self) -> None:
        # Toplevelウィンドウの作成（スライダーと検出ボタンを表示するウィンドウ）
        # self.gui は描画代理（CaptureAreaProxy）のため、実物を取り出して
        # 親にする。代理のままでは別スレッドで部品を作る形になる。
        parent = getattr(self.gui, "widget", self.gui)
        self.slider_window = tk.Toplevel(parent)
        self.slider_window.title("HSV Thresholds")
        self.slider_window.geometry("400x500")

        # 色相 (Hue) スライダー
        self.target_hue_slider = tk.Scale(
            self.slider_window,
            from_=0,
            to=360,
            orient="horizontal",
            label="Target Hue",
            command=self.update_threshold,
        )
        self.target_hue_slider.set(self.target_hue)
        self.target_hue_slider.pack()

        # 彩度 (Saturation) スライダー
        self.target_sat_slider = tk.Scale(
            self.slider_window,
            from_=0,
            to=255,
            orient="horizontal",
            label="Target Sat",
            command=self.update_threshold,
        )
        self.target_sat_slider.set(self.target_sat)
        self.target_sat_slider.pack()

        # 明度 (Value) スライダー
        self.target_val_slider = tk.Scale(
            self.slider_window,
            from_=0,
            to=255,
            orient="horizontal",
            label="Target Val",
            command=self.update_threshold,
        )
        self.target_val_slider.set(self.target_val)
        self.target_val_slider.pack()

        # 誤差 (Hue Error) スライダー
        self.error_hue_slider = tk.Scale(
            self.slider_window,
            from_=0,
            to=179,
            orient="horizontal",
            label="Hue Error",
            command=self.update_threshold,
        )
        self.error_hue_slider.set(self.error_hue)
        self.error_hue_slider.pack()

        # 誤差 (Saturation Error) スライダー
        self.error_sat_slider = tk.Scale(
            self.slider_window,
            from_=0,
            to=255,
            orient="horizontal",
            label="Sat Error",
            command=self.update_threshold,
        )
        self.error_sat_slider.set(self.error_sat)
        self.error_sat_slider.pack()

        # 誤差 (Value Error) スライダー
        self.error_val_slider = tk.Scale(
            self.slider_window,
            from_=0,
            to=255,
            orient="horizontal",
            label="Val Error",
            command=self.update_threshold,
        )
        self.error_val_slider.set(self.error_val)
        self.error_val_slider.pack()

        # 検出ボタン
        self.detect_button = tk.Button(
            self.slider_window, text="検出", command=self.detect_color_in_frame
        )
        self.detect_button.pack(pady=20)

        # 色を表示するためのラベル
        self.color_label = tk.Label(
            self.slider_window,
            text="検出色",
            width=20,
            height=2,
            relief="solid",
            bg="white",
        )
        self.color_label.pack(pady=10)

    def detect_color_in_frame(
        self,
        show_value: bool = False,
        show_position: bool = True,
        show_only_true_rect: bool = True,
        ms: int = 2000,
        crop: list = [],
        mask_path: str | None = None,
    ) -> None:
        src = self.camera.readFrame()
        if len(crop) == 4:
            src = src[crop[1] : crop[3], crop[0] : crop[2]]

        # ターゲット色と誤差からHSV範囲を計算
        lower_bound = np.array(
            [
                max(0, self.target_hue / 2 - self.error_hue),
                max(0, self.target_sat - self.error_sat),
                max(0, self.target_val - self.error_val),
            ]
        )
        upper_bound = np.array(
            [
                min(179, self.target_hue / 2 + self.error_hue),
                min(255, self.target_sat + self.error_sat),
                min(255, self.target_val + self.error_val),
            ]
        )

        hsv = cv2.cvtColor(src, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, lower_bound, upper_bound)
        # res = cv2.bitwise_and(src, src, mask=mask)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for pic, contour in enumerate(contours):
            area = cv2.contourArea(contour)
            if area > 100:
                x, y, w, h = cv2.boundingRect(contour)
                print(f"area: {area}, x: {x}, y: {y}, w: {w}, h: {h}")
                self.gui.ImgRect(
                    x, y, x + w, y + h, outline="green", tag=f"contour_{pic}", ms=ms
                )


def union_of_boxes(boxes: list, iou_threshold: float = 0.5) -> list:
    """
    重なっている矩形をユニオン矩形にマージする関数
    :param boxes: 矩形のリスト [(x1, y1, x2, y2), ...]
    :param iou_threshold: 重複と見なすIoUの閾値
    :return: マージされたユニオン矩形のリスト
    """
    merged_boxes: list = []

    for i, box1 in enumerate(boxes):
        merged = False
        for j, box2 in enumerate(merged_boxes):
            iou = calculate_iou(box1, box2)
            if iou > iou_threshold:
                # 重なっている矩形を結合する
                new_box = (
                    min(box1[0], box2[0]),  # 左上x
                    min(box1[1], box2[1]),  # 左上y
                    max(box1[2], box2[2]),  # 右下x
                    max(box1[3], box2[3]),  # 右下y
                )
                merged_boxes[j] = new_box  # 既存の矩形を更新
                merged = True
                break

        if not merged:
            # 新しい矩形として追加
            merged_boxes.append(box1)

    return merged_boxes
