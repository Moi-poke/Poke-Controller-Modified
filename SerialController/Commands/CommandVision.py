#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CommandVision.py - 画像認識 API（VisionMixin）.

画像認識部をまとめたファイルである。

なぜ Mixin にするか:
  画像認識は通信と無関係で、他の部分と混ぜる理由が無い。
  ただし ImageProcPythonCommand は PythonCommand を継承しており、待ち系
  （wait / _deadline / _runElapsed）と通知（Discord / Line）を親から使う。
  完全に独立した別クラスにはできないので、Mixin として重ねる形にする。

親クラスへの依存:
  self.wait / self._deadline / self._runElapsed … 待ち系（操作側）
   self.Discord … 通知側
  依存が少ないので Mixin として素直に分けられる。

互換:
  利用者の設定は from Commands.PythonCommandBase import ImageProcPythonCommand
  と書く。PythonCommandBase.py が引き続きこの名前を公開するので、既存の
  設定は変えずに使える。
"""

from __future__ import annotations

import functools
import os
import random
import re
import time
import traceback
from collections.abc import Callable
from os import path
from typing import Any

import cv2
import numpy as np
from loguru import logger

# テンプレート画像のキャッシュ件数。判定ループでは同じ画像を毎秒数十回
# 読み直すことになるため、読み込み結果を使い回す。
IMREAD_CACHE_SIZE = 128


# テンプレート画像の既定の置き場所。
#   __file__ の2つ上を基準にしている
#   （SerialController/Commands/ → SerialController/Template）。
#   このファイルも同じ Commands/ 配下にあるので、同じ式で同じ場所を指す。
#   別の階層へ置くと Template を見失うため、配置を変えるときはここを直す。
TEMPLATE_PATH = path.normpath(
    path.join(path.dirname(path.dirname(path.abspath(__file__))), "Template")
)


def _get_template_filespec(template_path: str) -> str:
    """
    テンプレート画像ファイルのパスを取得する。
    入力が絶対パスの場合は、`TEMPLATE_PATH`につなげずに返す。
    先頭の "./" や重なった区切りは正規化する（"./pokopia/x.png" と
    "pokopia/x.png" が同じ画像を指すようにするため）。
    Args:
        template_path (str): 画像パス
    Returns:
        str: 実際に読むファイルのパス
    """
    if path.isabs(template_path):
        return path.normpath(template_path)
    else:
        return path.normpath(path.join(TEMPLATE_PATH, template_path))


@functools.lru_cache(maxsize=IMREAD_CACHE_SIZE)
def _imread_or_raise(template_path: str, flags: int) -> np.ndarray:
    """テンプレート画像を読み込む。失敗したら理由の分かる例外にする。

    cv2.imread はファイルが無い・壊れている場合に例外ではなく None を返す。
    そのまま .shape を触ると TypeError になり、原因がパスだと分からない。

    判定ループでは毎秒数十回、同じファイルを読んでデコードすることになり、
    matchTemplate 本体より重くなることがある。引数は path と flags だけで
    副作用が無いため lru_cache で包める。

    規約: 返る配列はキャッシュで共有される。呼び出し側で書き換えない
    こと（書き換えると以降の判定すべてに影響する）。テンプレート画像を
    差し替えたときは clear_template_cache() を呼ぶ。
    """
    filespec = _get_template_filespec(template_path)
    # imread のスタブは ndarray 固定だが、実機では読めないと None が返る。
    # Any で受けて None を見る（isinstance では MatLike が ndarray の
    #   別名のため到達不能と見なされてしまう）。
    image: Any = cv2.imread(filespec, flags)
    if image is None:
        # ライトユーザーが最も詰まる箇所なので、理由と置き場所を
        # 具体的に出す。「無い」のか「壊れている（読めない）」のかを
        # 分けて書き、置き場所の既定（TEMPLATE_PATH）も添える。
        if not path.isfile(filespec):
            reason = "ファイルがありません"
            hint = (
                f"画像を {TEMPLATE_PATH} からの相対で置いてください "
                f"（例: {path.join(TEMPLATE_PATH, 'shiny_mark.png')}）"
            )
        else:
            reason = "ファイルはありますが画像として読めません（破損・形式違い）"
            hint = "別の画像ビューアで開けるか確かめ、開けない場合は作り直してください"
        raise FileNotFoundError(
            f"テンプレート画像を読み込めませんでした（{reason}）: {filespec}\n"
            f"{hint}\n"
            f"指定: {template_path!r} / 既定の置き場所: {TEMPLATE_PATH}"
        )
    image.flags.writeable = False  # 共有配列を誤って書き換えないための保険
    return image


def clear_template_cache() -> None:
    """テンプレート画像のキャッシュ（CPU 側）を捨てる。

    単独で呼ばないこと。GPU 側のキャッシュ（clearCudaCache）は別に
    持っているため、片方だけ捨てると CPU は新しい画像・GPU は古い画像
    で判定する。差し替え時は clearTemplateCaches() を使う。

    実行中にテンプレート画像を差し替えたときに呼ぶ。os.path.getmtime を
    キーへ含める案もあるが、判定のたびに stat が走るので採らなかった。
    """
    _imread_or_raise.cache_clear()


class VisionMixin:
    """画像認識の API をまとめた Mixin.

    単体では使わない。ImageProcPythonCommand が PythonCommand と一緒に
      継承することで、待ち系と通知が揃った状態になる。
    このクラス自身は self.camera / self.gsrc などを持たない。用意するのは
      継承側の __init__（_initVision）。
    """

    # 待ち系は継承側（OperateMixin 経由の PythonCommand）が用意する。
    # Mixin 単体には無いため、型だけ宣言する。
    wait: Callable[[float], None]
    _deadline: Callable[[float], Callable[[], bool]]
    _runElapsed: Callable[[], float]

    def _initVision(self, cam: Any, gui: Any = None) -> None:
        """画像認識に使う状態を用意する。継承側の __init__ から呼ぶ。

        cv2.cuda_GpuMat() は CUDA 無効ビルドでは AttributeError になる。
          ここで無条件に生成すると、GPU を使わない全コマンドまで起動不能に
          なるため、実際に GPU 版を呼んだときだけ確保する。
        """
        self.camera: Any = cam
        self.gui: Any = gui
        self.gsrc: Any = None
        self.gtmpl: Any = None
        self.gresult: Any = None
        # GPU 版のキャッシュ。matcher は (dtype, method)、テンプレートは
        # (path, use_gray) をキーにする
        self._cuda_matchers: dict[tuple[int, int], Any] = {}
        self._cuda_templates: dict[tuple[str, bool], Any] = {}

    def _ensure_cuda(self) -> bool:
        """CUDA が使えるかを判定し、使えれば GpuMat を用意する。"""
        if self.gsrc is not None:
            return True
        if not hasattr(cv2, "cuda_GpuMat") or not hasattr(cv2, "cuda"):
            return False
        try:
            if cv2.cuda.getCudaEnabledDeviceCount() < 1:
                return False
            self.gsrc = cv2.cuda_GpuMat()
            self.gtmpl = cv2.cuda_GpuMat()
            self.gresult = cv2.cuda_GpuMat()
        except Exception:
            logger.warning(f"CUDA is unavailable: {traceback.format_exc(limit=1)}")
            return False
        return True

    def _cudaMatcher(self, dtype: int, method: int) -> Any:
        """テンプレートマッチャを (dtype, method) 単位でキャッシュする。

        呼び出しのたびに createTemplateMatching すると、GPU 版の利点
        （生成と転送の削減）を自分で打ち消すことになる。
        """
        key = (dtype, method)
        matcher = self._cuda_matchers.get(key)
        if matcher is None:
            # CUDA 系は実行時にだけ存在する（スタブに無い）。
            matcher = cv2.cuda.createTemplateMatching(dtype, method)  # type: ignore[attr-defined]
            self._cuda_matchers[key] = matcher
        return matcher

    def _cudaTemplate(self, template_path: str, use_gray: bool) -> Any:
        """テンプレートを GpuMat にしてキャッシュする（転送を1度だけにする）。"""
        key = (template_path, bool(use_gray))
        gtmpl = self._cuda_templates.get(key)
        if gtmpl is None:
            template = _imread_or_raise(
                template_path,
                cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
            )
            gtmpl = cv2.cuda_GpuMat()  # type: ignore[attr-defined]
            gtmpl.upload(template)
            self._cuda_templates[key] = gtmpl
        return gtmpl

    def clearCudaCache(self) -> None:
        """GPU 側のキャッシュを捨てる。

        単独で呼ばないこと。CPU 側（clear_template_cache）と
        別管理のため、片方だけ捨てると両者で違う画像を使う。
        差し替え時は clearTemplateCaches() を使う。
        """
        self._cuda_matchers.clear()
        self._cuda_templates.clear()

    def clearTemplateCaches(self) -> None:
        """テンプレートのキャッシュを CPU・GPU まとめて捨てる。

        実行中に画像を差し替えたときは必ずこちらを呼ぶ。片方だけ
        捨てると、CPU 版は新しい画像・GPU 版は古い画像で判定し、
        「差し替えたのに直らない」形でしか症状が出ない。
        """
        clear_template_cache()
        self.clearCudaCache()

    # -- 入力の検証（画像認識の共通前処理） ---------------------------------

    def _readFrameOrRaise(self) -> np.ndarray:
        """現在のフレームを返す。取得できなければ RuntimeError にする。

        readFrame() は未接続・Disable 中・取得スレッド停止のいずれでも
        None を返す。そのまま crop すると TypeError: NoneType is not
        subscriptable、crop 無しなら cvtColor で cv2.error になり、
        どちらもメッセージから原因（カメラなのか crop 指定なのか）が
        読み取れない。ここで止めて言い切る。
        """
        camera = getattr(self, "camera", None)
        if camera is None:
            raise RuntimeError("カメラが割り当てられていません。")
        frame = camera.readFrame()
        if frame is None or getattr(frame, "size", 0) == 0:
            raise RuntimeError(
                "カメラから画像を取得できません"
                "（未接続 / Disable / 取得スレッド停止）。"
            )
        return frame

    @staticmethod
    def _cropOrRaise(src: np.ndarray, crop: Any) -> np.ndarray:
        """crop を検査してから切り出す。おかしければ ValueError。

        numpy のスライスは範囲外でも例外を出さず、黙って狭い配列を返す。
        crop の指定ミスが「別の場所を照合し続ける」形で表面化するため、
        閾値をいくら下げても直らない。これがこの一連で最も危ない。
        切り詰めて続行せず、その場で止めるのが要点。
        """
        if not crop:
            return src
        if len(crop) != 4:
            raise ValueError(f"crop は [x1, y1, x2, y2] の4要素です: {crop}")
        x1, y1, x2, y2 = (int(v) for v in crop)
        height, width = src.shape[0], src.shape[1]
        if x1 >= x2 or y1 >= y2:
            raise ValueError(f"crop の左右または上下が逆です: {crop}")
        if x1 < 0 or y1 < 0 or x2 > width or y2 > height:
            raise ValueError(f"crop が画面({width}x{height})の外を指しています: {crop}")
        return src[y1:y2, x1:x2]

    @staticmethod
    def _checkTemplate(src: np.ndarray, template: Any, mask: Any = None) -> None:
        """テンプレートとマスクの整合を見る。合わなければ ValueError。

        いずれも cv2.error になる条件だが、cv2 のメッセージは行列の
        次元しか語らないため、use_gray の指定漏れなのかテンプレートの
        取り違えなのかが分からない。
        """
        if template is None or getattr(template, "size", 0) == 0:
            raise ValueError("テンプレート画像が空です。")
        if template.ndim != src.ndim:
            raise ValueError(
                f"色の形式が違います（画面 ndim={src.ndim} /"
                f" テンプレート ndim={template.ndim}）。use_gray を揃えてください。"
            )
        if src.ndim == 3 and template.shape[2] != src.shape[2]:
            # ndim が同じでもチャンネル数は違いうる。アルファ付き PNG を
            # IMREAD_COLOR 以外で読むと 4ch になり、画面(3ch)と食い違う。
            raise ValueError(
                f"チャンネル数が違います（画面 {src.shape[2]}ch /"
                f" テンプレート {template.shape[2]}ch）。"
                "アルファ付きの画像は mask_path で渡してください。"
            )
        if template.shape[0] > src.shape[0] or template.shape[1] > src.shape[1]:
            raise ValueError(
                f"テンプレート({template.shape[1]}x{template.shape[0]})が"
                f"照合範囲({src.shape[1]}x{src.shape[0]})より大きいです。"
                "crop の指定を見直してください。"
            )
        if mask is not None and mask.shape[:2] != template.shape[:2]:
            raise ValueError(
                f"マスク({mask.shape[1]}x{mask.shape[0]})とテンプレート"
                f"({template.shape[1]}x{template.shape[0]})の大きさが違います。"
            )

    def _prepareSrc(self, crop: Any = None, use_gray: bool = True) -> np.ndarray:
        """readFrame → crop → 色変換 をまとめて行う（検証つき）。

        crop を先に切ってから色変換する。逆にすると使わない領域まで
        変換することになり、crop が全体の 1/9 でも 1280x720 の全面を
        変換してしまう（判定ループでは毎回この無駄が乗る）。
        """
        src = self._cropOrRaise(self._readFrameOrRaise(), crop)
        if use_gray:
            src = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
        return src

    # Judge if current screenshot contains an image using template matching
    # It's recommended that you use gray_scale option unless the template color wouldn't be cared for performace
    # 現在のスクリーンショットと指定した画像のテンプレートマッチングを行います
    # 色の違いを考慮しないのであればパフォーマンスの点からuse_grayをTrueにしてグレースケール画像を使うことを推奨します
    def isContainTemplate(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        show_value=False,
        show_position=True,
        show_only_true_rect=True,
        ms=2000,
        crop=None,
        mask_path=None,
    ):
        crop = crop or []
        src = self._prepareSrc(crop, use_gray)

        template = _imread_or_raise(
            template_path,
            cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
        )

        # mask用画像読み込み
        if mask_path is None:
            mask = None
            method = cv2.TM_CCOEFF_NORMED
        else:
            mask = _imread_or_raise(mask_path, 0)
            method = cv2.TM_CCORR_NORMED

        self._checkTemplate(src, template, mask)
        w, h = template.shape[1], template.shape[0]

        res = cv2.matchTemplate(src, template, method, mask)
        # マスク併用の TM_CCORR_NORMED は分母0の領域で NaN を返しうる。
        # NaN が混じると minMaxLoc の結果が不定になるため潰しておく。
        res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        if show_value:
            print(template_path + " ZNCC value: " + str(max_val))

        # crop したときは切り出した中の座標なので、画面全体の座標へ戻す。
        # 足さないと矩形が crop の左上ぶん左上へずれて描かれる。
        dx = crop[0] if len(crop) == 4 else 0
        dy = crop[1] if len(crop) == 4 else 0
        top_left = (max_loc[0] + dx, max_loc[1] + dy)
        bottom_right = (top_left[0] + w + 1, top_left[1] + h + 1)
        tag = str(time.perf_counter()) + str(random.random())
        if max_val >= threshold:
            if self.gui is not None and show_position:
                # self.gui.delete("ImageRecRect")
                self.gui.ImgRect(
                    *top_left, *bottom_right, outline="blue", tag=tag, ms=ms
                )
            return True
        else:
            if self.gui is not None and show_position and not show_only_true_rect:
                # self.gui.delete("ImageRecRect")
                self.gui.ImgRect(
                    *top_left, *bottom_right, outline="red", tag=tag, ms=ms
                )
            return False

    # 現在のスクリーンショットと指定した複数の画像のテンプレートマッチングを行います
    # 相関値が最も大きい値となった画像のインデックス、各画像のテンプレートマッチングの閾値、閾値判定結果を返します。
    # 色の違いを考慮しないのであればパフォーマンスの点からuse_grayをTrueにしてグレースケール画像を使うことを推奨します
    def isContainTemplate_max(
        self,
        template_path_list,
        threshold=0.7,
        use_gray=True,
        show_value=False,
        show_position=True,
        show_only_true_rect=True,
        ms=2000,
        crop=None,
        break_on_hit=False,
    ):
        """複数テンプレートのうち相関が最大のものを返す。

        break_on_hit=True にすると、閾値を超えたものを見つけた時点で
        残りを評価せずに返す。「どれか1つに一致したか」を見たいだけの
        用途では全件回す必要がないため。戻り値の互換のため既定は False
        （既定のままなら従来どおり全件を評価し、最大値を返す）。
        打ち切った場合、未評価のテンプレートの相関値は 0.0 で埋める。
        """
        if not template_path_list:
            raise ValueError("template_path_list が空です。")

        crop = crop or []
        src = self._prepareSrc(crop, use_gray)
        # crop したときは切り出した中の座標になるので、矩形を描く前に
        # 画面全体の座標へ戻す。足さないと crop の左上ぶんずれる。
        dx = crop[0] if len(crop) == 4 else 0
        dy = crop[1] if len(crop) == 4 else 0

        max_val_list = []
        judge_threshold_list = []
        for template_path in template_path_list:
            template = _imread_or_raise(
                template_path,
                cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
            )
            self._checkTemplate(src, template)
            w, h = template.shape[1], template.shape[0]

            method = cv2.TM_CCOEFF_NORMED
            res = cv2.matchTemplate(src, template, method)
            # 他の照合と同じく NaN を潰す。TM_CCOEFF_NORMED は分母に
            # 「テンプレートの平均からの偏差の二乗和」を持つため、真っ白・
            # 真っ黒など定数のテンプレートでは分母が 0 になり NaN が出る。
            # minMaxLoc の結果が不定になるうえ、np.argmax は NaN を最大と
            # 見なすので、1枚でも定数テンプレートが混ざるとそれが常に
            # 勝者になり、他がどれだけ一致していても無視される。
            res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)

            if show_value:
                print(template_path + " ZNCC value: " + str(max_val))

            top_left = (max_loc[0] + dx, max_loc[1] + dy)
            bottom_right = (top_left[0] + w + 1, top_left[1] + h + 1)
            tag = str(time.perf_counter()) + str(random.random())
            max_val_list.append(max_val)
            judge_threshold_list.append(max_val >= threshold)

            if max_val >= threshold:
                if self.gui is not None and show_position:
                    # self.gui.delete("ImageRecRect")
                    self.gui.ImgRect(
                        *top_left, *bottom_right, outline="blue", tag=tag, ms=ms
                    )
            else:
                if self.gui is not None and show_position and not show_only_true_rect:
                    # self.gui.delete("ImageRecRect")
                    self.gui.ImgRect(
                        *top_left, *bottom_right, outline="red", tag=tag, ms=ms
                    )

            if break_on_hit and judge_threshold_list[-1]:
                # 一致が1つ見つかれば十分な用途では、残りを評価しない。
                # 戻り値の形を保つため、未評価分は 0.0 / False で埋める。
                rest = len(template_path_list) - len(max_val_list)
                max_val_list.extend([0.0] * rest)
                judge_threshold_list.extend([False] * rest)
                break

        return np.argmax(max_val_list), max_val_list, judge_threshold_list

    def isContainTemplateGPU(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        show_value=False,
        not_show_false=True,
    ):
        """CUDA を使ったテンプレートマッチング（グレースケール専用）。

        マッチャを CV_8UC1 で作るため、カラー(3ch)の GpuMat を渡すと
        アサーション失敗になる。CUDA の TM_CCOEFF_NORMED は多チャンネル
        非対応で、dtype を変えるだけでは解決しない手法側の制約のため、
        use_gray=False はここで断る。黙って誤った結果を返すより、
        「この関数では出来ない」と言い切って CPU 版へ誘導する。

        not_show_false は本体で参照していない（互換のため残置）。
        """
        if not use_gray:
            raise ValueError(
                "isContainTemplateGPU はグレースケールのみ対応です。"
                "カラーで照合する場合は isContainTemplate() を"
                "使ってください。"
            )
        if not self._ensure_cuda():
            raise RuntimeError(
                "CUDA 対応の OpenCV が見つかりません。"
                "isContainTemplate() を使ってください。"
            )

        src = self._prepareSrc(None, use_gray)

        self.gsrc.upload(src)

        # CPU 版と同じ入力検証を通す。GPU 側だけ検証が無いと、
        # テンプレートが照合範囲より大きい場合に cv2 のアサーション
        # メッセージだけが出て、原因が crop なのか画像なのか分からない。
        # 検証には CPU 上のテンプレートが要るので、キャッシュと同じ
        # 読み方でもう一度読む（lru_cache が効くので実費は無い）。
        template = _imread_or_raise(
            template_path,
            cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
        )
        self._checkTemplate(src, template)
        gtmpl = self._cudaTemplate(template_path, use_gray)

        method = cv2.TM_CCOEFF_NORMED
        matcher = self._cudaMatcher(cv2.CV_8UC1, method)
        gresult = matcher.match(self.gsrc, gtmpl)
        resultg = gresult.download()
        # CPU 版と同じく NaN を潰す。定数テンプレート（真っ白・真っ黒）
        # では TM_CCOEFF_NORMED の分母が 0 になり NaN が出るため。
        resultg = np.nan_to_num(resultg, nan=0.0, posinf=0.0, neginf=0.0)
        _, max_val, _, max_loc = cv2.minMaxLoc(resultg)

        if show_value:
            print(template_path + " ZNCC value: " + str(max_val))

        return bool(max_val >= threshold)

    # Get interframe difference binarized image
    # フレーム間差分により2値化された画像を取得
    def getInterframeDiff(
        self,
        frame1: np.ndarray,
        frame2: np.ndarray,
        frame3: np.ndarray,
        threshold: float,
    ) -> np.ndarray:
        diff1 = cv2.absdiff(frame1, frame2)
        diff2 = cv2.absdiff(frame2, frame3)

        diff = cv2.bitwise_and(diff1, diff2)

        # binarize
        img_th = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)[1]

        # remove noise
        mask = cv2.medianBlur(img_th, 3)
        return mask

    # -- 待つ・探す（出現待ち / 停止待ち / 位置取得） -----------------------
    #
    # 期限は必ず _deadline() で測る。実時間の絶対期限にすると、一時停止
    # しているあいだも時計だけが進み、再開した直後に「時間切れ」と判定
    # されて1回も照合せずに False を返す。

    def _matchOnce(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        crop=None,
        mask_path=None,
        show_value=False,
    ):
        """1回だけ照合し、(判定, 相関値, 中心座標) をまとめて返す。

        readFrame → crop → 色変換 → matchTemplate までの手順は
        isContainTemplate と同じ。同じ前処理が方々へ散らばると、crop と
        色変換の順序を入れ替えたときのような修正が、その全部に要る。
        ここへ寄せて、公開メソッドはこれを呼ぶだけにする。

        中心座標は crop の左上を足して画面全体の座標へ直して返す。
        切り出した中の座標のまま返すと、呼び出し側が毎回 crop[0] を
        足すことになり、足し忘れがいつか必ず起きる。
        """
        crop = crop or []
        src = self._prepareSrc(crop, use_gray)

        template = _imread_or_raise(
            template_path,
            cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
        )
        if mask_path is None:
            mask = None
            method = cv2.TM_CCOEFF_NORMED
        else:
            mask = _imread_or_raise(mask_path, 0)
            method = cv2.TM_CCORR_NORMED

        self._checkTemplate(src, template, mask)
        h, w = template.shape[0], template.shape[1]
        res = cv2.matchTemplate(src, template, method, mask)
        res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        if show_value:
            print(f"{template_path} ZNCC value: {max_val}")

        dx = crop[0] if len(crop) == 4 else 0
        dy = crop[1] if len(crop) == 4 else 0
        center = (int(max_loc[0] + dx + w / 2), int(max_loc[1] + dy + h / 2))
        return bool(max_val >= threshold), float(max_val), center

    def waitTemplate(
        self,
        template_path,
        timeout=10.0,
        interval=0.2,
        threshold=0.7,
        use_gray=True,
        crop=None,
        mask_path=None,
        show_value=False,
    ) -> bool:
        """現れるまで待つ。見つかれば True、時間切れなら False。

        while not self.isContainTemplate(...): self.wait(0.5) と自前で
        書く形との違いは3つ。①必ず打ち切るので、想定外の画面へ入っても
        永久に回り続けない ②wait 経由なので停止要求で即座に抜ける
        ③時間切れを例外ではなく False で返すので、見つからなかった
        ときの分岐を呼び出し側で普通に書ける。
        """
        expired = self._deadline(timeout)
        while True:
            hit, _, _ = self._matchOnce(
                template_path, threshold, use_gray, crop, mask_path, show_value
            )
            if hit:
                return True
            if expired():
                logger.debug(f"waitTemplate timeout: {template_path}")
                return False
            self.wait(interval)

    def waitTemplateGone(
        self,
        template_path,
        timeout=10.0,
        interval=0.2,
        threshold=0.7,
        use_gray=True,
        crop=None,
        mask_path=None,
    ) -> bool:
        """消えるまで待つ。消えれば True、時間切れなら False。

        ロード中の表示やメッセージ枠が抜けきるのを待つ用途。出現待ちと
        対で用意しておかないと、消える側の while だけが各コマンドへ
        残ることになる。
        """
        expired = self._deadline(timeout)
        while True:
            hit, _, _ = self._matchOnce(
                template_path, threshold, use_gray, crop, mask_path
            )
            if not hit:
                return True
            if expired():
                logger.debug(f"waitTemplateGone timeout: {template_path}")
                return False
            self.wait(interval)

    def waitStable(
        self,
        quiet=0.5,
        timeout=10.0,
        threshold=20,
        interval=0.1,
        crop=None,
        ratio=0.001,
    ) -> bool:
        """画面の動きが止まるまで待つ。止まれば True、時間切れは False。

        テンプレート画像を1枚も用意せずに使えるのが利点。安全側に倒して
        self.wait(3.0) と固定で置いてある箇所を実際の停止検知へ替えると、
        1周あたりの待ちが実測ぶんまで縮む。

        getInterframeDiff は3枚から「動いた画素」だけを残すので、残った
        画素の割合が ratio 未満の状態が quiet 秒続いたら停止とみなす。
        1画素でも残ったら動きとみなす作りにすると、キャプチャのノイズで
        永久に止まらない。
        """
        crop = crop or []

        def gray() -> np.ndarray:
            """現在のフレームを crop してグレースケールで返す。"""
            return self._prepareSrc(crop, True)

        expired = self._deadline(timeout)
        f1, f2 = gray(), gray()
        quiet_from = None
        while True:
            self.wait(interval)
            f3 = gray()
            mask = self.getInterframeDiff(f1, f2, f3, threshold)
            moved = float(np.count_nonzero(mask)) / float(mask.size)
            if moved < ratio:
                if quiet_from is None:
                    # 静止し始めた時刻。実時間で持つと、一時停止していた
                    # あいだも「静止が続いた」ことになり、再開した瞬間に
                    # 停止とみなして早々に True を返す。
                    quiet_from = self._runElapsed()
                elif self._runElapsed() - quiet_from >= float(quiet):
                    return True
            else:
                quiet_from = None
            if expired():
                logger.debug(f"waitStable timeout: moved={moved:.4f}")
                return False
            f1, f2 = f2, f3

    def getTemplatePosition(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        crop=None,
        mask_path=None,
        show_value=False,
    ) -> tuple[int, int] | None:
        """一致位置の中心 (x, y) を画面全体の座標で返す。無ければ None。

        isContainTemplate も内部では max_loc を出しているが、GUI へ矩形を
        描くためだけに使って捨てている。戻り値の互換を壊さずに位置を
        取れるよう、別名のメソッドとして分ける。
        """
        hit, _, center = self._matchOnce(
            template_path, threshold, use_gray, crop, mask_path, show_value
        )
        return center if hit else None

    def preloadTemplates(
        self, template_paths: list[str], use_gray: bool = True
    ) -> list[str]:
        """先読みして、読めなかったパスの一覧を返す。

        _imread_or_raise が FileNotFoundError を投げるのは判定の瞬間なので、
        数時間走ったあとの分岐で初めてパスの打ち間違いが分かる。開始直後に
        読んでおけば、落ちるものは1秒で落ちる。lru_cache が効くため、
        そのまま暖機にもなる。
        """
        flags = cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR
        missing = []
        for template_path in template_paths:
            try:
                _imread_or_raise(template_path, flags)
            except FileNotFoundError:
                missing.append(template_path)
        if missing:
            logger.warning(f"読み込めないテンプレート: {missing}")
        return missing

    # -- 記録・複数検出・色 -------------------------------------------------

    def saveFrame(self, name: str = "frame", crop=None, folder: str = "Debug") -> str:
        """いまの画面を日時つきで保存し、保存先のパスを返す。

        show_value=True は print するだけなので、放置運用ではログが
        流れて消える。閾値を割ったときの画面が残っていれば、実機を
        止めずに机上で閾値を詰められる。

        名前には日時をミリ秒まで入れる。同じ判定が連続で外れたとき、
        秒までだと後の1枚が前の1枚を上書きしてしまう。
        """
        crop = crop or []
        frame = self._cropOrRaise(self._readFrameOrRaise(), crop)

        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        stamp += f"_{int((time.time() % 1) * 1000):03d}"
        safe = re.sub(r"[^0-9A-Za-z_.-]", "_", str(name))
        filespec = path.join(folder, f"{stamp}_{safe}.png")

        # cv2.imwrite は非 ASCII のパスで静かに False を返す。書けたかを
        # 戻り値で見ておかないと「保存したはずの画像が無い」になる。
        if not cv2.imwrite(filespec, frame):
            logger.warning(f"画面を保存できませんでした: {filespec}")
            return ""
        return filespec

    def isContainTemplateDump(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        crop=None,
        mask_path=None,
        folder: str = "Debug",
    ) -> bool:
        """判定し、外れたときだけ相関値つきで画面を保存する。

        isContainTemplate と戻り値も使い方も同じ。外れた回の画面が残る
        ので、閾値が渋いのか画面そのものが違うのかを後から切り分け
        られる。当たった回まで保存すると1周で数百枚になり使えない。
        """
        hit, val, _ = self._matchOnce(
            template_path, threshold, use_gray, crop, mask_path
        )
        if not hit:
            stem = path.splitext(path.basename(str(template_path)))[0]
            saved = self.saveFrame(f"{stem}_{val:.3f}", crop, folder)
            logger.debug(f"NG {template_path} val={val:.3f} -> {saved}")
        return hit

    def findAllTemplates(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        crop=None,
        max_count: int = 20,
        show_value=False,
    ) -> list[tuple[int, int]]:
        """閾値を超えた箇所すべての中心座標を、相関の高い順に返す。

        minMaxLoc は最大の1件しか返さないため、「並んでいる数」を数える
        用途には使えない。ここでは閾値を超えた点を全部拾う。

        ただし素直に拾うと、1つの対象に対して隣接する画素が数十件
        ヒットする。個数を数えるのが目的なら、これを間引かなければ
        答えが桁で狂う。テンプレートの幅・高さの半分より近い点は同じ
        対象とみなし、相関の高いほうだけを残す。
        """
        crop = crop or []
        src = self._prepareSrc(crop, use_gray)

        template = _imread_or_raise(
            template_path,
            cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
        )
        self._checkTemplate(src, template)
        h, w = template.shape[0], template.shape[1]
        res = cv2.matchTemplate(src, template, cv2.TM_CCOEFF_NORMED)
        res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)

        ys, xs = np.where(res >= threshold)
        if len(xs) == 0:
            return []

        order = np.argsort(res[ys, xs])[::-1]  # 相関の高い順に見る
        dx = crop[0] if len(crop) == 4 else 0
        dy = crop[1] if len(crop) == 4 else 0
        near_x, near_y = max(1, w // 2), max(1, h // 2)

        found: list[tuple[int, int]] = []
        for i in order:
            x, y = int(xs[i]), int(ys[i])
            if any(abs(x - px) < near_x and abs(y - py) < near_y for px, py in found):
                continue
            found.append((x, y))
            if len(found) >= max_count:
                break

        if show_value:
            print(f"{template_path} hits: {len(found)}")
        return [(x + dx + w // 2, y + dy + h // 2) for x, y in found]

    def countTemplate(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        crop=None,
        max_count: int = 20,
    ) -> int:
        """閾値を超えた箇所の個数を返す（findAllTemplates の件数）。"""
        return len(
            self.findAllTemplates(template_path, threshold, use_gray, crop, max_count)
        )

    def getColorRatio(self, crop, lower_hsv, upper_hsv) -> float:
        """指定領域で、その色が占める割合(0.0〜1.0)を返す。

        「画面が暗転した」「HPバーが赤い」「背景が白い（ロード中）」は、
        テンプレート画像を作るまでもない。HSV なら明るさの揺れに強く、
        グレースケールのテンプレートマッチより壊れにくい。

        色相は環状なので、赤のように 0 をまたぐ範囲は下限のほうが大きい
        値になる。その場合は2つに割って足す（そのまま inRange へ渡すと
        常に0件になり、「赤が無い」と誤判定する）。
        """
        crop = crop or []
        frame = self._cropOrRaise(self._readFrameOrRaise(), crop)

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        lo = np.array(lower_hsv, dtype=np.uint8)
        hi = np.array(upper_hsv, dtype=np.uint8)
        if int(lo[0]) <= int(hi[0]):
            mask = cv2.inRange(hsv, lo, hi)
        else:
            lo1 = np.array([lo[0], lo[1], lo[2]], dtype=np.uint8)
            hi1 = np.array([179, hi[1], hi[2]], dtype=np.uint8)
            lo2 = np.array([0, lo[1], lo[2]], dtype=np.uint8)
            hi2 = np.array([hi[0], hi[1], hi[2]], dtype=np.uint8)
            mask = cv2.bitwise_or(
                cv2.inRange(hsv, lo1, hi1), cv2.inRange(hsv, lo2, hi2)
            )
        total = float(mask.shape[0] * mask.shape[1])
        return float(np.count_nonzero(mask)) / total

    def isSimilarColor(self, crop, lower_hsv, upper_hsv, ratio: float = 0.6) -> bool:
        """指定領域が、おおむねその色で占められているかを返す。"""
        return self.getColorRatio(crop, lower_hsv, upper_hsv) >= float(ratio)
