#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import numpy as np
import tkinter as tk
from Commands.Keys import Button, Direction, Stick
from Commands.PythonCommandBase import PythonCommand


class StickLissajous(PythonCommand):
    NAME = "リサージュ曲線スティック"

    def __init__(self):
        super().__init__()

    def __post_init__(self):
        super().__post_init__()
        self.a = 2
        self.b = 3
        self.delta = np.pi / 6
        self.create_gui()

    def create_gui(self):
        root = tk.Toplevel()
        root.title("Lissajous パラメータ設定")

        tk.Label(root, text="a:").grid(row=0, column=0)
        a_var = tk.StringVar(value=str(self.a))
        a_entry = tk.Entry(root, textvariable=a_var)
        a_entry.grid(row=0, column=1)

        tk.Label(root, text="b:").grid(row=1, column=0)
        b_var = tk.StringVar(value=str(self.b))
        b_entry = tk.Entry(root, textvariable=b_var)
        b_entry.grid(row=1, column=1)

        tk.Label(root, text="delta (度):").grid(row=2, column=0)
        delta_var = tk.StringVar(value=str(np.rad2deg(self.delta)))
        delta_entry = tk.Entry(root, textvariable=delta_var)
        delta_entry.grid(row=2, column=1)

        def update_params():
            try:
                self.a = float(a_var.get())
                self.b = float(b_var.get())
                self.delta = np.deg2rad(float(delta_var.get()))
            except ValueError:
                print("無効な入力です")

        tk.Button(root, text="更新", command=update_params).grid(
            row=3, column=0, columnspan=2
        )

    def stick(self, buttons):
        self.keys.input(buttons, ifPrint=False)

    def stickEnd(self, buttons):
        self.keys.inputEnd(buttons)
        self.checkIfAlive()

    def do(self):
        steps = 48
        t_vals = np.linspace(0, 2 * np.pi, steps)
        while self.alive:
            for t in t_vals:
                x = np.sin(self.a * t + self.delta)
                y = np.sin(self.b * t)

                # 正規化付き極座標変換
                r = np.sqrt(x**2 + y**2) / np.sqrt(2)
                angle = (np.rad2deg(np.arctan2(y, x)) + 360) % 360

                self.stick(
                    Direction(Stick.LEFT, angle, r, showName="Lissajous"),
                )
                self.checkIfAlive()
