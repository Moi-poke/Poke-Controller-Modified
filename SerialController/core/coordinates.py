"""キャプチャ座標と表示座標の変換層。tkinter にも numpy にも触れない。

この層が持つのは「届いたフレームの大きさ」と「表示面の大きさ」の 2 つだけ。
倍率は保存せず、変換のたびにこの 2 つから求める。gui 側が倍率を手で持つと、
切り出すフレームと別の大きさから添字を計算する経路が増えてしまうため。

枠（core/）に置いてあるので task bounds を通る。gui 側のどの部品からも
tkinter を触らずに同じ変換が使える。
"""

from __future__ import annotations

from dataclasses import dataclass


def _scale(numer: tuple[int, int], denom: tuple[int, int]) -> tuple[float, float]:
    """要素ごとに割り算する。分母が 0 のときは 1.0（等倍）を返す。

    分母が 0 になるのは「その方向の基準がまだ無い」ときだけなので、座標
    変換そのものは成立させ、クリックや保存の呼び出しを止めない。

    分子が 0 のときは 0.0 が返り、ここでは 1.0 に置き換えない。置き換える
    と、フレームが届いていない場合に「どのクリックでも左上の画素を返す」
    という静かな誤答が生まれる。0.0 のほうが「何も無い」ことが明白で、
    添字も 0 のままなので、無関係な画素を拾いに行くこともない。
    """

    def one(a: int, b: int) -> float:
        return a / b if b else 1.0

    return one(numer[0], denom[0]), one(numer[1], denom[1])


@dataclass(frozen=True, slots=True)
class CoordinateMapper:
    """表示座標とキャプチャ座標の相互変換。

    ``capture_size`` は **届いたフレーム** の大きさ ``(幅, 高さ)`` であって、
    カメラに要求した大きさではない。numpy の ``frame.shape`` は
    ``(高さ, 幅, 通道数)`` なので、ここでは転置した形で持つ。

    要求した大きさと届いた大きさが食い違うと、切り出す対象は実フレームなの
    なのに添字だけが別の大きさに引き算される。すると範囲内に収まったまま
    別の画素を返し、どこにも例外が出ない。だから ``capture_size`` は常に
    配列が届いたフレームそのものの形から取る。

    ``display_size`` は表示面の大きさ ``(幅, 高さ)``。
    """

    capture_size: tuple[int, int]
    display_size: tuple[int, int]

    def to_capture(self, x: int, y: int, *, clamp: bool = False) -> tuple[int, int]:
        """表示座標 → キャプチャ座標。

        丸めは 0 方向への切り捨て。分母は表示面なので、表示面がまだ 0 の
        ときだけ倍率 1.0 になる。

        ``clamp`` は 1 画素を配列から切り出すときだけ立てる。境界は
        ``capture_size`` から取るので、倍率と境界は同じ 1 つの情報から計算
        され、食い違いが構造的に起こりえない。

        ``capture_size`` の幅か高さが 0 のときは切り出せる画素が 1 つも
        ないため上限を適用しない。残る保証は「負にならない」だけだが、
        これを外すと負の添字がスライスの末尾を回り込み、無関係な端の画素を
        黙って読む。
        """
        scale_x, scale_y = _scale(self.capture_size, self.display_size)
        px, py = int(x * scale_x), int(y * scale_y)
        if not clamp:
            return px, py
        width, height = self.capture_size
        return (
            min(max(px, 0), width - 1) if width > 0 else max(px, 0),
            min(max(py, 0), height - 1) if height > 0 else max(py, 0),
        )

    def to_display(self, x: int, y: int) -> tuple[int, int]:
        """キャプチャ座標 → 表示座標。

        丸めは ``to_capture`` と同じく 0 方向への切り捨て。分母は
        キャプチャ面なので、キャプチャ面がまだ 0 のときだけ倍率 1.0 になる。

        表示側は配列の添字にならない（枠を描くだけ）ので、上限のクランプ
        は持たない。必要なら MotionRangeSS 側が表示座標を締めている。
        """
        scale_x, scale_y = _scale(self.display_size, self.capture_size)
        return int(x * scale_x), int(y * scale_y)
