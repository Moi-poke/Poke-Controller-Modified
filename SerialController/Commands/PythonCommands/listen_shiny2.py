#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from Commands.CommandAudio import band_power
from Commands.PythonCommandBase import AudioPythonCommand


# 色違いの音を検出する（listen_shiny.py の後継）。
#
# 旧スクリプトは PyAudio を直に掴んでFFTしていたが、こちらは本体の
# 取込（Audio欄で選んだ入力）を使う。配線・デバイス選択はGUI側に任せ、
# ここでは「どの音に反応するか」だけを書く。
#
# 色違いのｷﾗｰﾝには 3100Hz 付近と 4200Hz 付近の成分が含まれる。
# 機種・音量で絶対値は変わるため、開始直後に平常時を測って
# その何倍かを閾値にする（RATIO を上げると鈍感、下げると敏感）。
class ListenShiny2(AudioPythonCommand):
    NAME = "色違いの音を聴きたい2"

    # (帯域の下限, 上限) の一覧。両方とも閾値を超えたら検知。
    BANDS = [(3000.0, 3200.0), (4150.0, 4400.0)]
    RATIO = 5.0  # 平常時の何倍で反応するか
    WINDOW_S = 1.5  # 判定に使う区間（秒）

    def do(self):
        print("平常時の音量を測っています...")
        baseline = self._baseline()
        thresholds = [b * self.RATIO for b in baseline]
        print(f"平常値: {[f'{b:.0f}' for b in baseline]}")
        print("色違いの音を待っています（Stopで終了）...")
        while self.checkIfAlive():
            if self.isTonePresent(self.BANDS, thresholds, self.WINDOW_S):
                print("Sounds Shiny!")
                saved = self.recordClip(2.0, "shiny")
                if saved:
                    print(f"音を保存しました: {saved}")
                return
            self.wait(0.2)

    def _baseline(self):
        """平常時の帯域パワーを測る。取れなければ低めの既定値を使う。"""
        window = self.audio.readWindow(self.WINDOW_S)
        if window is None or window.size == 0:
            print("音声が取れていません。Audio欄の入力を確認してください")
            return [1.0 for _ in self.BANDS]
        return [band_power(window, 44100, lo, hi) for lo, hi in self.BANDS]
