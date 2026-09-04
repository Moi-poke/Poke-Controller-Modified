"""プロコンの色を GUI で選んで変える。

画面から色見本を選び、OK を押すと Pico へ O 行を1本送る。
色はフラッシュに保存されるので、Pico の電源を切っても残る。

反映のタイミング:
    Switch 側で登録し直すまで変わらないことがある。
    変わらないときは、Switch のコントローラー登録を解除して繋ぎ直す。

相手:
    pico-wakecon の O 行（ui.c handle_line）。旧 C と同書式で
    先頭1字を読み飛ばすため、O で送る。C は取込（秒数）のため、
    色を C で送ると取込が始まってしまう。書式は hid.c の
    probe_parse_c_line と対（4つの16進 / 応答は color 行）。
"""

from Commands.PythonCommandBase import PythonCommand
from Commands.WakeLink import query


# 色の並び: 本体 / ボタン / 左グリップ / 右グリップ
#   本体色とボタン色は switchbrew の公式配色をそのまま使う。
#   グリップの組み合わせは、公式の左右セットか、見栄えで選んだもの。
PRESET = {
    # --- 公式の配色（switchbrew） -------------------------------------
    "公式 ネオン (青/赤)": ("313131", "0f0f0f", "0ab9e6", "ff3c28"),
    "公式 スプラ2 (緑/桃)": ("313131", "0f0f0f", "1edc00", "ff3278"),
    "公式 あつ森 (緑/水)": ("313131", "0f0f0f", "82ff96", "96f5f5"),
    "公式 ゼルダSS (青/紫)": ("313131", "0f0f0f", "2d50f0", "500fc8"),
    "公式 フォートナイト (黄/青)": ("313131", "0f0f0f", "ffcc00", "0084ff"),
    "公式 スプラ3 (紫/黄)": ("28282d", "0f0f0f", "6455f5", "c3fa05"),
    "公式 ポケモンSV (橙/紫)": ("313131", "0f0f0f", "f07341", "9650aa"),
    "公式 ツムツム (紫/桃)": ("313131", "0f0f0f", "b400e6", "ff3278"),
    "公式 イーブイ/ピカチュウ": ("313131", "0f0f0f", "c88c32", "ffdc00"),
    "公式 グレー (標準)": ("828282", "0f0f0f", "828282", "828282"),
    "公式 ホワイト (有機EL)": ("e6e6e6", "323232", "e6e6e6", "e6e6e6"),

    # --- パステル ------------------------------------------------------
    "パステル 桜": ("ffe4ec", "8a5a6a", "ffafaf", "ffd1e8"),
    "パステル 若草": ("eaffee", "4a6a55", "bcffc8", "d8f5b8"),
    "パステル 空": ("e8f6ff", "4a5f7a", "a8d8ff", "c8e8f5"),
    "パステル 藤": ("f5eaff", "6a5a7a", "f0cbeb", "d8c8f5"),
    "パステル 檸檬": ("fffbe0", "6a6a4a", "f5ff82", "ffe8a8"),
    "パステル ミント": ("e4fff8", "3f6f66", "b8f5e8", "a8e8d8"),
    "パステル 珊瑚": ("fff0ea", "7a5548", "ffc8b0", "ffe0c8"),

    # --- ビビッド ------------------------------------------------------
    "ビビッド 紅蓮": ("1a0505", "ff3c28", "ff0a28", "ff6400"),
    "ビビッド electric": ("050a1a", "00e6ff", "0a3cff", "00e6ff"),
    "ビビッド 毒": ("0a1a05", "c3fa05", "1edc00", "b400e6"),
    "ビビッド 極彩": ("0f0f14", "ffffff", "ff0080", "00e6c8"),
    "ビビッド 夕焼け": ("1a0a14", "ffcc00", "ff3278", "faa005"),
    "ビビッド 深海": ("050f1e", "00c8ff", "1473fa", "00e6c8"),

    # --- 落ち着いた色 --------------------------------------------------
    "シック 墨": ("1e1e1e", "8c8c8c", "323232", "323232"),
    "シック 珈琲": ("2d2119", "c8a878", "4a3728", "5a4433"),
    "シック 深緑": ("14231a", "8cc8a0", "1e3f2d", "28503c"),
    "シック 葡萄": ("1e1428", "b48ccd", "32234a", "3c2d5a"),
    "シック 群青": ("141e32", "8caadc", "1e2d50", "28375f"),

    # --- 既定 ----------------------------------------------------------
    "既定 (いまのプロコン)": ("323232", "0f0f0f", "ffffff", "ffffff"),
}

# 色の並びの名前（表示に使う）
PART_NAMES = ("本体", "ボタン", "左グリップ", "右グリップ")

# ダイアログで「直接入れる」を選ぶときの見出し
CUSTOM_LABEL = "── 自分で入れる ──"


def normalize(value: object) -> str | None:
    """6桁の16進へ整える。整えられなければ None を返す。

    受け付ける形: "ff0000" / "FF0000" / "#ff0000" / "0xff0000"
    3桁（"f00"）は受け付けない。意図と違う色になる恐れがあるため。
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text.startswith("#"):
        text = text[1:]
    elif text.startswith("0x"):
        text = text[2:]
    if len(text) != 6:
        return None
    for ch in text:
        if ch not in "0123456789abcdef":
            return None
    return text


class SetProconColor(PythonCommand):
    NAME = "プロコンの色を変える"

    def do(self):
        names = list(PRESET.keys())
        first = names[0]

        # 1回目のダイアログ: 色見本から選ぶ
        picked = self.dialogue6widget(
            "プロコンの色",
            [
                ["combo", "色見本", names + [CUSTOM_LABEL], first],
                ["check", "選んだ色を確認してから送る", True],
            ],
        )
        if picked is None:
            self.print2("取り消しました。")
            self.finish()
            return

        choice = picked[0]
        confirm = bool(picked[1])

        # 2回目のダイアログ: 4色を編集する
        #   プリセットを選んだ場合は、その値を初期値として入れる。
        #   「自分で入れる」なら既定のプロコン色を初期値にする。
        base = PRESET.get(choice, PRESET["既定 (いまのプロコン)"])
        if choice == CUSTOM_LABEL or confirm:
            edited = self.dialogue6widget(
                "色を確かめる（6桁の16進）",
                [["colorentry", PART_NAMES[i], base[i]] for i in range(4)],
            )
            if edited is None:
                self.print2("取り消しました。")
                self.finish()
                return
            raw = tuple(edited[:4])
            label = choice if choice != CUSTOM_LABEL else "自分で入れた色"
        else:
            raw = base
            label = choice

        # 4つとも正しい形か確かめる。1つでも駄目なら1本も送らない。
        #   Pico 側の probe_parse_c_line も同じ作りである。
        colors = []
        for name, value in zip(PART_NAMES, raw):
            fixed = normalize(value)
            if fixed is None:
                self.print2("{} の色「{}」が読めません。".format(name, value))
                self.print2("6桁の16進で書いてください（例: ff0000）。")
                self.finish()
                return
            colors.append(fixed)

        # 送る行を組み立てる。書式は pico-wakecon の O 行と対。
        #     O <本体> <ボタン> <左> <右>
        #   C ではいけない。C は取込（秒数）のため、色を C で送ると
        #   取込スキャンが始まってしまう。
        row = "O " + " ".join(colors)

        self.print2("色を「{}」にします。".format(label))
        for name, value in zip(PART_NAMES, colors):
            self.print2("  {} #{}".format(name, value))

        transport = self._color_transport()
        if transport is not None:
            # 応答を読む形で送る。成功なら color 行、書式違いなら usage 行。
            # 旧ファーム（O を知らない版）は何も返さない。
            found = query(transport, row, ("color ", "usage:"), timeout=3.0)
            if any(line.startswith("color ") for line in found):
                self.print2("送りました。色はフラッシュに保存されます。")
                self.print2("画面に反映されないときは、Switch 側で登録を"
                            "解除して繋ぎ直してください。")
            elif any(line.startswith("usage:") for line in found):
                self.print2("Pico が書式違いを返しました。4つの6桁16進を"
                            "確かめてください。")
            else:
                self.print2("応答がありません。旧ファームの可能性があります。")
                self.print2("pico-wakecon の O 行に対応した版を使ってください。")
            self.finish()
            return

        # 送る口が無いときは従来の口で送るだけにする（応答は読めない）。
        # 既存の口をそのまま使う。direct_serial は1件ごとに停止を見る。
        self.direct_serial([row], [0.0])

        # Pico がフラッシュへ書き終えるまで少し待つ。
        self.wait(0.5)

        self.print2("送りました（応答は未確認）。色はフラッシュに保存されます。")
        self.print2("画面に反映されないときは、Switch 側で登録を解除して繋ぎ直してください。")

        self.finish()

    def _color_transport(self):
        """Sender が持つ Transport。無ければ None。"""
        keys = getattr(self, "keys", None)
        sender = getattr(keys, "ser", None)
        transport = getattr(sender, "transport", None)
        if transport is None:
            return None
        ser = getattr(transport, "ser", None)
        if ser is None:
            return None
        try:
            if not transport.is_open():
                return None
        except Exception:
            return None
        return transport
