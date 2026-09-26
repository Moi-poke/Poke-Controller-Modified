"""Non-Windows preview surface. Draws the same picture with PhotoImage + Canvas.

Design: docs/superpowers/specs/2026-09-26-gdi-preview-renderer-design.md 8.
Windows has no GDI child window here, so the same ``PreviewRenderer`` protocol
is met by this implementation. A platform requirement, not generalisation.

It touches tkinter, so it lives in ui/ (which may, unlike services/).
"""

from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Literal

import cv2
import numpy as np
from PIL import Image, ImageTk
from core.Camera import CAPTURE_SIZE
from core.preview_renderer import TK_COLORREF, OverlayState, RenderResult
from loguru import logger

# The reverse of the single authored table, derived rather than written out
# twice: this backend needs COLORREF -> name, and a second hand-kept table could
# drift from core's without anything failing. Derived at import, so it cannot.
# The eight values are distinct, so the mapping is 1:1.
_NAME_BY_COLORREF: Final[dict[int, str]] = {
    colorref: name for name, colorref in TK_COLORREF.items()
}

#: host の束縛のうち Canvas へ転送してよい種類。ポインタとキーだけであり、
#: Tk が host 自身に擎げる構造・hover・focus 系は名指ししない。除外表は
#: 書いた者が覚えている物しか載らないが、許可表は載せない物を運ばない。
_FORWARDABLE_KINDS: Final[tuple[str, ...]] = (
    "Button",
    "ButtonRelease",
    "Motion",
    "MouseWheel",
    "Key",
    "KeyRelease",
)


def _is_forwardable(sequence: str) -> bool:
    """Tk イベント型名が許可表に完全一致するかを判定する。

    部分文字列照合（``kind in sequence``）では ``<Keymap>`` が ``Key`` に
    一致してしまう。``-`` で分割したトークンが許可表のいずれかと完全一致する
    ときだけ転送する。

    仮想イベント（``<<...>>``）は ``strip("<>")`` すると先頭の ``<`` が
    残り、型名が壊れる。仮想イベントは転送しない。
    """
    if sequence.startswith("<<"):
        return False
    tokens = sequence.strip("<>").split("-")
    return any(token in _FORWARDABLE_KINDS for token in tokens)


@dataclass(frozen=True, slots=True)
class SelfTestResult:
    """起動時 1 回の自己検査の結果。

    ``fully_occluded`` は「プレビューの 1 ピクセルも画面に出ていない」を意味する。
    ``not_run``（未接続）では判定不能なので ``None`` を返す。core 側から import
    しないのは、非 Windows で動くこの面が Win32 モジュールを巻き込まないため
    （``GuiAssets.py:305`` が遅延 import と同じ理由でそうしている）。

    GDI 面の ``SelfTestResult`` とは値域が違う。GDI 側は ``covered`` を持つが、
    この面は DC が無いためオクルージョンを測れず、``visible`` で終わる。
    """

    outcome: Literal["not_run", "not_mapped", "empty_box", "visible"]
    fully_occluded: bool | None
    clip_box: tuple[int, int, int, int] = (0, 0, 0, 0)


class PhotoImageSurface:
    """非 Windows 用の描画面。Canvas に PhotoImage を置いて同じ絵を描く。

    映像を Canvas の項目として持つので、設計 0節で問題にした「子窓が
    親より上に合成される」事情は起きない（そもそも子窓が無い）。
    60fps は出ない（設計 13節）。PreviewRenderer の形を満たすことが要件
    なので、毎フレーム PhotoImage を作り直す速度の我省略はしない。
    """

    def __init__(self, host: Any) -> None:
        self._host = host
        self._canvas: Any = None
        self._image_id: Any = None
        self._size = (0, 0)
        self._photo: Any = None
        self._overlay: OverlayState = OverlayState()
        self._rebuilding = False
        self._pending_frame = False
        self._logged_failures: set[str] = set()

    def attach(self, parent_hwnd: int, size: tuple[int, int]) -> None:
        # 子窓を持たないので親 HWND は使わない（プロトコルの形は合わせる）。
        _ = parent_hwnd
        try:
            canvas = tk.Canvas(
                self._host, borderwidth=0, highlightthickness=0, cursor=""
            )
        except tk.TclError as error:
            # 素通しにすると起動時の例外でアプリごと止まる。子窓を作れなかった
            # GDI 面と同じ扱いにする（gdi_surface.py:659-667）: ファイルに原因を
            # 残し、画面に出る 1 行も出して、未接続のまま返す。compose が no_hwnd
            # で答えるので、毎フレーム同じ失敗を撒き散らさない。
            logger.warning("プレビュー面を作れない error={} stage=attach", error)
            print("プレビュー面を作れません。映像は表示されません。")
            return
        self._canvas = canvas
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._image_id = self._canvas.create_image(0, 0, anchor=tk.NW)
        # Canvas が破棄されたら Tk 側は TclError を投げるだけになる。破棄を
        # 検出して _canvas を離すことで、以降の present/resize は未接続経路
        # （no_hwnd）に落ち、例外は漏れない。
        self._canvas.bind("<Destroy>", self._on_canvas_destroy, add="+")
        self._forward_host_binds()
        self._pending_frame = False
        self.resize(size)

    def _on_canvas_destroy(self, event: Any) -> None:
        """Canvas 破棄を検出して _canvas を離す。未接続経路へ落とすため。"""
        _ = event
        self._canvas = None
        self._image_id = None
        self._pending_frame = False

    def _forward_host_binds(self) -> None:
        """Canvas 上のポインタ操作を host の束縛へ、同じ名前で届け直す。

        GDI 面の子は外国の HWND なので Tk の当たり判定に載らず、クリックは
        必ず host（Frame）へ届く。Canvas は host の子供なので箱を覆い、
        Tk が擎ち上げるポインタ事象は全部 Canvas で止まる。host が束縛した
        名前は ``host.bind()`` で読み戻せるので、同じ名前で Canvas にも
        束縛し、届いた時点で host へ出し直す。名前を並べ直していないので、
        あとから host 側で足した束縛もここを直さずに届く。
        """
        for sequence in self._host.bind():
            if not _is_forwardable(sequence):
                continue
            self._canvas.bind(sequence, self._reemitter(sequence), add="+")

    def _reemitter(self, sequence: str) -> Callable[[Any], None]:
        """``sequence`` を host へ同じ座標で出し直す束縛を作る。

        ``event_generate`` は受け取った側のために新しい事象を作るので、
        handler が読む ``event.x/event.y`` は渡した値そのものになる。Canvas は
        host の bindtag には無いので、出し直してもこの束縛へ戻らない。
        ``when="now"`` は順序を保つためで、既定の ``tail`` だと press と
        続く motion の琶くれが起きる。

        ``MouseWheel`` は ``delta``、``Key`` は ``keysym`` を引き継ぐ。
        これらを渡さないと、ホイールが効かず、キーの判別ができない。
        """

        def _reemit(event: Any) -> None:
            kwargs: dict[str, Any] = {"x": event.x, "y": event.y, "when": "now"}
            if hasattr(event, "delta"):
                kwargs["delta"] = event.delta
            if hasattr(event, "keysym"):
                kwargs["keysym"] = event.keysym
            self._host.event_generate(sequence, **kwargs)

        return _reemit

    def resize(self, size: tuple[int, int]) -> None:
        """受け皿を新しい大きさに変える。GDI 面と同じ順番で同じ物を捨てる。

        ``gdi_surface.py:772-779`` と対で読むこと。両者は同じ
        ``PreviewRenderer`` の契約の下にあるので、片方だけが受け付ける
        大きさが残ると mac/Linux だけ描画が止まる。
        """
        if self._canvas is None or self._rebuilding:
            return
        width, height = int(size[0]), int(size[1])
        # Tk は geom が決まる前に 1x1 の <Configure> を送ってくる。1 ピクセルの
        # 受け皿を作っても絵は出ないので、大きさが決まるまで前の箱を使う。
        # 0 や負も同じ（そもそも箱にならない）。
        if width <= 1 or height <= 1 or (width, height) == self._size:
            return
        self._rebuilding = True
        try:
            # ここで設定するのは requested size であって実寸ではない。実寸は
            # geometry manager（fill=BOTH, expand=True）が決めるので、<_size>
            # を当てにした判定はしない（compose もカメラ解像度を相手にする）。
            self._canvas.config(width=width, height=height)
            # Tk が受け入れた後にだけ覚える。config が途中で失敗すると箱は
            # 前のままなのに _size だけ新しいと、compose が無い箱へ描く。
            self._size = (width, height)
        finally:
            # 作り直しの外では必ず落とす。一度でも落とさなくなると、それ以降
            # の resize が全部拒まれる。
            self._rebuilding = False

    def compose(self, frame: np.ndarray, overlay: OverlayState) -> RenderResult:
        started = time.perf_counter_ns()
        if self._canvas is None:
            self._note_failure(
                "no_hwnd", "プレビュー面が未接続で合成できない stage=compose"
            )
            return RenderResult(False, time.perf_counter_ns() - started, "no_hwnd")
        # strides[0] >= w*3 は C-contiguous でも常に成り立つので証拠にならない。
        if not frame.flags.c_contiguous:
            self._note_failure(
                "frame_not_contiguous",
                "プレビュー合成は連続した BGR を要求する stage=compose",
            )
            return RenderResult(
                False, time.perf_counter_ns() - started, "frame_not_contiguous"
            )
        frame_size = frame.shape[1::-1]
        # 判定の相手は _size でもなく canvas でもなく、カメラが返す映像の解像度
        # （CAPTURE_SIZE）である。GDI 面の core/gdi_surface.py と同じ規則。
        # _size を相手にすると、<Configure> で表示サイズが変わった途端に
        # 生きているフレームを 1 枚も描かなくなる。
        # 縮小も拡大もしない。寸法が違えば捨てる。
        if frame_size != CAPTURE_SIZE:
            self._note_failure(
                "dimension_mismatch",
                "プレビューは 1:1 しか描かないので捨てた {}x{} のまま capture={} "
                "owned={} client={}",
                frame_size[0],
                frame_size[1],
                CAPTURE_SIZE,
                self._size,
                self.client_size(),
            )
            return RenderResult(
                False, time.perf_counter_ns() - started, "dimension_mismatch"
            )
        if frame_size == self._size:
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        else:
            # 残りは黒で埋める（受け皿が大きい時）、切る（小さい時）。換算しない
            # ので GDI 面と同じ絵になる。
            self._note_failure(
                "letterboxed",
                "受け皿 {} が映像 {} と大きさ違いなので 1:1 の範囲だけ描く "
                "stage=compose",
                self._size,
                frame_size,
            )
            height = min(frame.shape[0], self._size[1])
            width = min(frame.shape[1], self._size[0])
            image = Image.new("RGB", self._size)
            image.paste(
                Image.fromarray(
                    cv2.cvtColor(frame[:height, :width], cv2.COLOR_BGR2RGB)
                ),
                (0, 0),
            )
        self._photo = ImageTk.PhotoImage(image)
        self._overlay = overlay
        self._pending_frame = True
        return RenderResult(True, time.perf_counter_ns() - started, "ok")

    def present(self) -> RenderResult:
        started = time.perf_counter_ns()
        if self._canvas is None:
            self._note_failure(
                "no_hwnd", "プレビュー面が未接続で提示できない stage=present"
            )
            return RenderResult(False, time.perf_counter_ns() - started, "no_hwnd")
        # 箱は有るので「未接続」とは言わない。compose が棄却した frame の
        # まま present を呼ぶと、原因が attach 側だと誤読される。
        if not self._pending_frame:
            return RenderResult(False, time.perf_counter_ns() - started, "no_frame")
        self._canvas.itemconfig(self._image_id, image=self._photo)
        self._pending_frame = False
        self._draw_overlay(self._overlay)
        return RenderResult(True, time.perf_counter_ns() - started, "ok")

    def release(self) -> None:
        canvas = self._canvas
        self._photo = None
        self._pending_frame = False
        self._rebuilding = False
        self._canvas = None
        self._image_id = None
        if canvas is not None:
            canvas.destroy()

    def client_size(self) -> tuple[int, int]:
        """受け皿の実寸。GDI 面の GetClientRect と同じ読み方をする。

        ``_size``（resize が Tk に要求した大きさ）ではない。Canvas は
        fill=BOTH, expand=True なので実寸は geometry manager が決める。
        """
        if self._canvas is None:
            return (0, 0)
        return (int(self._canvas.winfo_width()), int(self._canvas.winfo_height()))

    def self_test(self) -> SelfTestResult:
        """プレビューが画面に出ているかを検査する。形は GDI 面の自己検査と同じ。

        GDI 面は back buffer に sentinel を書いて child DC で読み戻すが、Canvas に
        DC は無い。代わりに Tk が答えられる値だけを読むので書き込みも後始末も要ら
        ず、呼ぶたびにその時の状態になる。``not_mapped`` は「壊れている」ではない
        （pack は mainloop が回るまでマップを予約するだけ）。

        ``visible`` は「オクルージョン無く表示されている」ことを意味しない。
        ``winfo_viewable()`` は先祖ウィンドウが最小化されていないかを見るが、
        他のウィンドウに隠れているかは測らない。オクルージョン検知はこの面の
        能力及び E2E の artifact でのみ行う。
        ``clip_box`` は名前に反してクリップ領域ではなくクライアントサイズを
        返す（GDI 面の同名フィールドと同じ誤称）。
        """
        if self._canvas is None:
            return SelfTestResult("not_run", None, (0, 0, 0, 0))
        width, height = self.client_size()
        if not self._canvas.winfo_viewable():
            return SelfTestResult("not_mapped", True, (0, 0, 0, 0))
        if width <= 1 or height <= 1:
            return SelfTestResult("empty_box", True, (0, 0, width, height))
        return SelfTestResult("visible", False, (0, 0, width, height))

    def _note_failure(self, detail: str, template: str, *args: Any) -> None:
        """同じ reason のログは 1 度だけ出す。回数は RenderResult の detail が持つ。

        compose / present は毎フレーム呼ばれるので、拒絶ごとに 1 行出すと
        1 分に数千行になって、必要な 1 行が埋もれる。GDI 面と同じ規則で、原因は
        detail で 1 度だけ言う（gdi_surface.py:888-893）。
        """
        if detail in self._logged_failures:
            return
        self._logged_failures.add(detail)
        logger.warning(template, *args)

    def _draw_overlay(self, overlay: OverlayState) -> None:
        # 項目は作り直さない。前回分を "overlay" タグでまとめて消す。
        self._canvas.delete("overlay")
        for stick, color in (
            (overlay.left_stick, "cyan"),
            (overlay.right_stick, "red"),
        ):
            if not stick.active:
                continue
            r = stick.radius
            k = r // 10
            self._canvas.create_oval(
                stick.center_x - r,
                stick.center_y - r,
                stick.center_x + r,
                stick.center_y + r,
                outline=color,
                tags="overlay",
            )
            self._canvas.create_oval(
                stick.knob_x - k,
                stick.knob_y - k,
                stick.knob_x + k,
                stick.knob_y + k,
                fill=color,
                tags="overlay",
            )
        guide = overlay.guide
        if guide.visible:
            self._canvas.create_rectangle(
                guide.x0,
                guide.y0,
                guide.x1,
                guide.y1,
                outline="red",
                dash=(4, 4),
                tags="overlay",
            )
        rect = overlay.img_rect
        if rect.visible:
            self._canvas.create_rectangle(
                rect.outer.x0,
                rect.outer.y0,
                rect.outer.x1,
                rect.outer.y1,
                width=4,
                outline="white",
                tags="overlay",
            )
            self._canvas.create_rectangle(
                rect.inner.x0,
                rect.inner.y0,
                rect.inner.x1,
                rect.inner.y1,
                width=2,
                outline=self._color_name(rect.color),
                tags="overlay",
            )

    @staticmethod
    def _color_name(colorref: int) -> str:
        """COLORREF を Tk の色名へ戻す（core の表を逆引きする）。"""
        return _NAME_BY_COLORREF.get(colorref, "white")
