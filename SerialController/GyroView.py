import math
import queue
import threading
import time
import tkinter as tk
from collections import deque

COL = ("#e74c3c", "#2ecc71", "#3498db")  # X, Y, Z
BG = "#1e1e1e"


class GyroView(tk.Frame):
    """ジャイロ可視化パネル（姿勢計＋ヨー方位＋角速度バー＋時系列）"""

    def __init__(
        self, master, rate_range=2000.0, history=300, alpha=0.98, fps=60, **kw
    ):
        if fps is None or fps <= 0:
            raise ValueError(f"fpsは正です: {fps!r}")
        if float(rate_range) <= 0:
            raise ValueError(f"rate_rangeは正です: {rate_range!r}")
        if int(history) < 2:
            raise ValueError(f"historyは2以上です: {history!r}")
        if not 0.0 <= float(alpha) <= 1.0:
            raise ValueError(f"alphaは0..1です: {alpha!r}")
        kw.setdefault("bg", BG)
        super().__init__(master, **kw)
        self.q = queue.SimpleQueue()  # 別スレッドから push 可能
        self.rate_range = float(rate_range)  # 表示レンジ [deg/s]
        self.alpha = float(alpha)  # 相補フィルタ係数
        self.interval = max(1, int(1000 / fps))
        self.N = int(history)
        self.pitch = self.roll = self.yaw = 0.0
        self.hist = [deque([0.0] * self.N, maxlen=self.N) for _ in range(3)]
        self.latest = (0.0, 0.0, 0.0)
        self._stop = False
        self._after_id: str | None = None
        self._build()
        self._after_id = self.after(self.interval, self._tick)

    def close(self) -> None:
        """更新ループを止める。例外は投げない。多重呼び可。"""
        self._stop = True
        after_id, self._after_id = self._after_id, None
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass

    def destroy(self) -> None:
        """破棄時に予約を消す。"""
        try:
            self.close()
        except Exception:
            pass
        super().destroy()

    # ---------- 外部API ----------
    def push(self, gx, gy, gz, ax=None, ay=None, az=None, dt=0.005):
        """gx,gy,gz: deg/s, ax,ay,az: G（任意）, dt: s"""
        self.q.put((gx, gy, gz, ax, ay, az, dt))

    def push_raw(self, ax, ay, az, gx, gy, gz, dt=0.005):
        """Switch生値int16を換算してpushする。送出値の監視用。"""
        self.push(
            gx * 0.06103,
            gy * 0.06103,
            gz * 0.06103,
            ax * 0.000244,
            ay * 0.000244,
            az * 0.000244,
            dt,
        )

    def reset_yaw(self):
        """ヨーを0に戻す。描画スレッドで実行するためキュー経由。"""
        self.q.put(None)

    # ---------- UI構築 ----------
    def _build(self):
        S = self.S = 140
        top = tk.Frame(self, bg=BG)
        top.pack(side="top", fill="x")

        # 姿勢計
        self.att = tk.Canvas(top, width=S, height=S, bg="#3a6ea5", highlightthickness=0)
        self.att.pack(side="left", padx=4)
        self.ground = self.att.create_polygon(0, 0, 0, 0, fill="#8b5a2b", outline="")
        self.hline = self.att.create_line(0, 0, 0, 0, fill="white", width=2)
        c = S / 2
        self.att.create_line(
            c - 30, c, c - 10, c, c, c + 8, c + 10, c, c + 30, c, fill="yellow", width=3
        )
        self.att_txt = self.att.create_text(
            4, S - 4, anchor="sw", fill="white", font=("", 8)
        )

        # ヨー方位
        self.yawc = tk.Canvas(top, width=S, height=S, bg=BG, highlightthickness=0)
        self.yawc.pack(side="left", padx=4)
        self.yawc.create_oval(10, 10, S - 10, S - 10, outline="#888", width=2)
        self.needle = self.yawc.create_line(
            c, c, c, 20, fill=COL[2], width=3, arrow="last"
        )
        self.yaw_txt = self.yawc.create_text(
            c, S - 4, anchor="s", fill="white", font=("", 8)
        )

        # 角速度バー
        W = 170
        self.bar = tk.Canvas(top, width=W, height=S, bg=BG, highlightthickness=0)
        self.bar.pack(side="left", padx=4)
        self.bar_cx, self.bar_half = 95, 70
        self.bars, self.bar_txt = [], []
        for i, name in enumerate("XYZ"):
            y = 25 + i * 45
            self.bar.create_text(8, y, text=name, fill=COL[i], anchor="w")
            self.bar.create_rectangle(
                self.bar_cx - self.bar_half,
                y - 8,
                self.bar_cx + self.bar_half,
                y + 8,
                outline="#555",
            )
            self.bar.create_line(self.bar_cx, y - 10, self.bar_cx, y + 10, fill="#aaa")
            self.bars.append(
                self.bar.create_rectangle(
                    self.bar_cx, y - 7, self.bar_cx, y + 7, fill=COL[i], width=0
                )
            )
            self.bar_txt.append(
                self.bar.create_text(self.bar_cx, y + 18, fill="#ccc", font=("", 8))
            )

        # 時系列チャート
        self.CW, self.CH = 2 * S + W + 24, 110
        self.chart = tk.Canvas(
            self, width=self.CW, height=self.CH, bg=BG, highlightthickness=0
        )
        self.chart.pack(side="top", pady=(6, 0))
        self.chart.create_line(
            0, self.CH / 2, self.CW, self.CH / 2, fill="#444", dash=(2, 2)
        )
        init = [0, self.CH / 2, self.CW, self.CH / 2]
        self.lines = [
            self.chart.create_line(*init, fill=COL[i], width=1) for i in range(3)
        ]

    # ---------- 更新ループ ----------
    def _tick(self):
        if self._stop:
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        try:
            got = False
            while True:
                try:
                    item = self.q.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    self.yaw = 0.0
                    got = True
                    continue
                try:
                    gx, gy, gz, ax, ay, az, dt = item
                    self._integrate(gx, gy, gz, ax, ay, az, dt)
                except Exception:
                    continue
                for h, v in zip(self.hist, (gx, gy, gz)):
                    h.append(v)
                self.latest = (gx, gy, gz)
                got = True
            if got:
                self._redraw()
        finally:
            if not self._stop:
                try:
                    if self.winfo_exists():
                        self._after_id = self.after(self.interval, self._tick)
                except Exception:
                    pass

    def _integrate(self, gx, gy, gz, ax, ay, az, dt):
        # 軸対応（実機確定 2026-10-17/18）: X=ロール・Y=ピッチ（上下）・
        # Z=ヨー（左右）。符号は実機で要確認。
        self.roll += gx * dt
        self.pitch += gy * dt
        self.yaw += gz * dt
        if ax is not None and ay is not None and az is not None:
            n = math.sqrt(ax * ax + ay * ay + az * az)
            if 0.5 < n < 1.5:  # 振り回し中は加速度補正を止める
                p_acc = math.degrees(math.atan2(-ax, math.hypot(ay, az)))
                r_acc = math.degrees(math.atan2(ay, az))
                a = self.alpha
                self.pitch = a * self.pitch + (1 - a) * p_acc
                self.roll = a * self.roll + (1 - a) * r_acc
        self.roll = (self.roll + 180.0) % 360.0 - 180.0
        self.pitch = (self.pitch + 180.0) % 360.0 - 180.0
        self.yaw = (self.yaw + 180.0) % 360.0 - 180.0

    def _redraw(self):
        S, c = self.S, self.S / 2
        # 姿勢計: pitch→上下移動, roll→逆回転
        off = max(-c, min(c, self.pitch * (c / 45.0)))
        r = math.radians(-self.roll)
        cs, sn = math.cos(r), math.sin(r)
        L = S * 2
        pts = []
        for x, y in ((-L, off), (L, off), (L, off + L), (-L, off + L)):
            pts += [c + x * cs - y * sn, c + x * sn + y * cs]
        self.att.coords(self.ground, *pts)
        self.att.coords(self.hline, *pts[:4])
        self.att.itemconfigure(
            self.att_txt, text=f"P {self.pitch:+6.1f}  R {self.roll:+6.1f}"
        )

        # ヨー
        R = c - 20
        ya = math.radians(self.yaw)
        self.yawc.coords(self.needle, c, c, c + R * math.sin(ya), c - R * math.cos(ya))
        self.yawc.itemconfigure(self.yaw_txt, text=f"Yaw {self.yaw:+6.1f}°")

        # バー
        for i, v in enumerate(self.latest):
            y = 25 + i * 45
            w = max(-1.0, min(1.0, v / self.rate_range)) * self.bar_half
            self.bar.coords(self.bars[i], self.bar_cx, y - 7, self.bar_cx + w, y + 7)
            self.bar.itemconfigure(self.bar_txt[i], text=f"{v:+7.1f} dps")

        # 時系列
        sx = self.CW / (self.N - 1)
        k = (self.CH / 2 - 4) / self.rate_range
        h2 = self.CH / 2
        for line, h in zip(self.lines, self.hist):
            pts = []
            for j, v in enumerate(h):
                v = max(-self.rate_range, min(self.rate_range, v))
                pts += [j * sx, h2 - v * k]
            self.chart.coords(line, *pts)


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Gyro Viewer demo")
    root.configure(bg=BG)
    view = GyroView(root, rate_range=500)
    view.pack(padx=8, pady=8)
    tk.Button(root, text="Yaw リセット", command=view.reset_yaw).pack(pady=(0, 8))

    def feeder():  # ダミー入力（実機受信スレッドに置き換える）
        t0, dt = time.perf_counter(), 0.005
        while True:
            t = time.perf_counter() - t0
            view.push(
                200 * math.sin(t * 1.3),
                150 * math.sin(t * 0.7),
                100 * math.sin(t * 0.4),
                dt=dt,
            )
            time.sleep(dt)

    threading.Thread(target=feeder, daemon=True).start()
    root.mainloop()
