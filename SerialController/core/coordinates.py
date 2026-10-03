"""キャプチャ座標と表示座標の変換層。tkinter にも numpy にも触れない。

この層が持つのは「届いたフレームの大きさ」「表示面の大きさ」「その中で
映像が置かれている位置」だけ。倍率は保存せず、変換のたびにこれらから
求める。gui 側が倍率を手で持つと、切り出すフレームと別の大きさから添字を
計算する経路が増えてしまうため。

映像は表示面いっぱいに貼らず、縦横比を保ったまま枠に収めて余白付きで
中央に置く。その配置は :func:`fit_rect` が 1 箇所で決めて、変換側はその
結果（``display_size`` と ``display_origin``）だけを受け取る。gui 側が
自分で余白を数えると、描画側と座標変換側で別の答えを持って食い違う。

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


def fit_rect(
    content: tuple[int, int], viewport: tuple[int, int]
) -> tuple[int, int, int, int]:
    """``content`` を縦横比のまま ``viewport`` に収め、中央に置く ``(x, y, w, h)``。

    枠いっぱいに貼り付けず、引伸ばさない。上下に余白が出るか（letterbox）
    左右に出るか（pillarbox）は、幅と高さのどちらが制限側かで決まるので、まず
    ``viewport_w * content_h <= viewport_h * content_w``（幅が制限側）で判定
    してから、制限されない側を整数演算で求める。判定は交差積でとるので、
    割り算は 1 回も発生せず、表示面が一方向に縮む場合にも余白は映像の
    周囲に出る。

    切り上げは四捨五入の整数版 ``(a + b // 2) // b``。浮動小数を使わないのは、
    この値がそのままリサイズ後のフレームの大きさになり、丸め差は 1 画素
    ずつの静かなズレとして最終フレームに残るため。

    幅か高さが 0 以下のときは (0, 0, 0, 0)。縦横比が定義できないここで
    割り算すると例外になり、例外はプレビュー更新の経路ごと止める。0 を
    返せば呼び出し側は「描くものがない」と判断できる。
    """
    content_w, content_h = content
    viewport_w, viewport_h = viewport
    if min(content_w, content_h, viewport_w, viewport_h) <= 0:
        return 0, 0, 0, 0
    if viewport_w * content_h <= viewport_h * content_w:
        width = viewport_w
        height = (viewport_w * content_h + content_w // 2) // content_w
    else:
        height = viewport_h
        width = (viewport_h * content_w + content_h // 2) // content_h
    # 四捨五入で 1 画素だけ枠をはみ出ることがあるので、枠の内側に確定する。
    width = min(width, viewport_w)
    height = min(height, viewport_h)
    return (viewport_w - width) // 2, (viewport_h - height) // 2, width, height


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

    ``display_size`` は表示面の大きさ ``(幅, 高さ)``。映像を枠に収めて
    余白付きで中央に置くとき、枠ではなく **映像そのものの矩形** の大きさが
    ここに入る（余白は含めない）。余白は :func:`fit_rect` が ``x`` / ``y`` で
    表しており、倍率に混ぜない。混ぜると余白まで一緒に拡大縮小され、クリック
    位置が 1 画素ずつずれる。

    ``display_origin`` は表示面の中で映像が置かれている左上の位置
    ``(x, y)``。既定は (0, 0) で、これは「余白がない」つまり従来の 2 つの
    情報だけで決まる振る舞いそのものである。既存の呼び出し側が origin を
    渡さなくても結果が変わらないのは、この既定値があるため。
    """

    capture_size: tuple[int, int]
    display_size: tuple[int, int]
    display_origin: tuple[int, int] = (0, 0)

    def to_capture(self, x: int, y: int, *, clamp: bool = False) -> tuple[int, int]:
        """表示座標 → キャプチャ座標。

        原点は先に引く。倍率より先に引かないと、倍率が 1.0 でないときに
        「引いた量まで一緒に縮んでしまう」ため、同じ余白でも縮尺によって
        別の場所を指す。

        丸めは 0 方向への切り捨て。分母は表示面なので、表示面がまだ 0 の
        ときだけ倍率 1.0 になる。

        ``clamp`` は 1 画素を配列から切り出すときだけ立てる。境界は
        ``capture_size`` から取るので、倍率と境界は同じ 1 つの情報から計算
        され、食い違いが構造的に起こりえない。余白を外した後の座標でクランプ
        するので、余白クリックは映像の端の画素に落ちる（余白の中ほどを
        無関係な画素として返さない）。

        ``capture_size`` の幅か高さが 0 のときは切り出せる画素が 1 つも
        ないため上限を適用しない。残る保証は「負にならない」だけだが、
        これを外すと負の添字がスライスの末尾を回り込み、無関係な端の画素を
        黙って読む。
        """
        origin_x, origin_y = self.display_origin
        scale_x, scale_y = _scale(self.capture_size, self.display_size)
        px = int((x - origin_x) * scale_x)
        py = int((y - origin_y) * scale_y)
        if not clamp:
            return px, py
        width, height = self.capture_size
        return (
            min(max(px, 0), width - 1) if width > 0 else max(px, 0),
            min(max(py, 0), height - 1) if height > 0 else max(py, 0),
        )

    def to_display(self, x: int, y: int) -> tuple[int, int]:
        """キャプチャ座標 → 表示座標。

        原点は最後に足す。映像の中身を映すだけであれば原点は不要だが、
        枠を描く側の座標は表示面が基準なので足さないと 1 回分ずれる。

        丸めは ``to_capture`` と同じく 0 方向への切り捨て。分母は
        キャプチャ面なので、キャプチャ面がまだ 0 のときだけ倍率 1.0 になる。

        表示側は配列の添字にならない（枠を描くだけ）ので、上限のクランプ
        は持たない。必要なら MotionRangeSS 側が表示座標を締めている。
        """
        scale_x, scale_y = _scale(self.display_size, self.capture_size)
        origin_x, origin_y = self.display_origin
        return int(x * scale_x) + origin_x, int(y * scale_y) + origin_y

    def length_to_capture(self, length: int) -> int:
        """表示座標での長さ → キャプチャ座標での長さ。

        点ではなく「大きさ」を変換する経路。スティック円の半径のような値は
        座標の差なので原点の値の影響を受けないが、倍率そのものは同じもの。
        ``_scale`` をそのまま使うことで、劣化時の扱い（分母 0 は 1.0）が点の
        変換と 1 箇所で一致する。

        丸めは四捨五入で、``to_capture`` の切り捨てとは逆の規則にする。
        長さには「0 方向へ向かう」という意味がないため、切り捨てると半径が
        1 画素ずつ縮んで、見た目には一回り小さい円になる。

        結果が 0 になるときは 1 を返す。半径 0 の円は、受け取った側の描画では
        何も描かれないため、点 1 つ消えたことに気づけないから。``length`` が
        0 以下のときは「長さが無い」ことをそのまま 0 で伝えるので、下限の 1
        は適用しない。
        """
        if length <= 0:
            return 0
        scale_x, _ = _scale(self.capture_size, self.display_size)
        return max(1, round(length * scale_x))
