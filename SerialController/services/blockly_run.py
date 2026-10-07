"""Blockly編集画面からの試し実行（保存せずに今の組み立てを走らせる）。

編集画面は各ブロックの手前に `_pokecon_step(self, '<ブロックID>')` を差した
コードを送ってくる。ここではそのコードを保存時と同じ検査に通し、目印の
呼び先を差し込んだ名前空間で読み込み、試し実行用のコマンドクラスを作る。

目印の呼び先は1回の実行の様子（TrialSession）へ「いまどのブロックか」を
書き、区切り（ブレークポイント）や「1つ進む」ではコマンド自身の一時停止を
使って止まる。一時停止を別に持たないのは、本体の一時停止・再開ボタンと
同じ状態を見せるため（どちらから再開しても続きが走る）。

tkinter には触らない。開始・停止は呼び出し側（ui）がGUIスレッドで行う。
"""

from __future__ import annotations

import ast
import builtins
import collections
import reprlib
import sys
import threading
from typing import Any

from core import blockly_validate
from core.CommandOperate import StopThread
from loguru import logger
from services import blockly_templates

#: 目印の関数名。編集画面（pokecon_editor.js の STATEMENT_PREFIX）と揃える。
TRACE_NAME = "_pokecon_step"

#: 試し実行の表示名の前置き。本体のログ・題名で通常の実行と見分ける。
TRIAL_PREFIX = "[試し] "

#: 拾っておくログの行数。編集画面は差分だけ取りに来るので古い分は捨てる。
MAX_LOG_LINES = 500

#: 読み込み時のモジュール名（トレースバックに出る）。
_MODULE_NAME = "pokecon_blockly_trial"

#: 変数欄に出す件数の上限（名前順で先頭から）。
_VARS_MAX = 50

#: 変数の表示値（repr）の上限文字数。超えたら末尾に … を付ける。
_VARS_REPR_MAX = 80

#: 変数欄に出せる値の型（命令・型・関数・モジュール等は出さない）。
_VARS_TYPES = (bool, int, float, str, list, tuple, dict)

#: 表示用の短い repr。上限（_VARS_REPR_MAX）より少し多めに作ってから切る。
_SHORT_REPR = reprlib.Repr()
# 件数で省略されるときは必ず上限を超える長さにし、切り方（末尾 …）を一本化する。
_SHORT_REPR.maxlist = _SHORT_REPR.maxtuple = _SHORT_REPR.maxdict = 40
_SHORT_REPR.maxstring = _SHORT_REPR.maxother = _VARS_REPR_MAX + 20
_SHORT_REPR.maxlevel = 3


class TrialSession:
    """1回の試し実行の様子。作業スレッドが書き、受け口のスレッドが読む。

    値は単純な代入だけで入れ替える（読む側は少し古い値を見ても困らない）。
    ログだけは錠で守る（番号の振り直しと読み出しが重ならないように）。
    """

    def __init__(self, run_id: int) -> None:
        self.run_id = int(run_id)
        #: いま（またはこれから）走るブロックのID。
        self.block_id = ""
        #: 区切り。このIDのブロックの手前で一時停止する。
        self.breakpoints: frozenset[str] = frozenset()
        #: 次のブロックの手前で一時停止する（「1つ進む」）。
        self.step_pending = False
        #: 編集画面から停止を頼んだか（記録用。結果の判定は finish_called で行う）。
        self.stop_requested = False
        #: 「正常終了する」（finish）を通ったか。停止（本体のボタンを含む）と
        #: finish はどちらも StopThread で抜けるため、これで見分ける。
        self.finish_called = False
        #: 結果。走っている間は空。完了・停止・エラー・開始できませんでした。
        self.result = ""
        self.error = ""
        self.error_block = ""
        #: 変数の今の値（表示用の文字列）。目印を通るたびに丸ごと差し替える。
        self.variables: dict[str, str] = {}
        #: 目印を通るたびに呼ぶ（検証用の差し口）。
        self.on_step: Any = None
        self._lock = threading.Lock()
        self._logs: collections.deque[tuple[int, str]] = collections.deque(
            maxlen=MAX_LOG_LINES
        )
        self._seq = 0

    @property
    def running(self) -> bool:
        """まだ結果が出ていないか。"""
        return not self.result

    def log(self, text: str) -> None:
        """実行中の出力（表示ブロック等）を1行ずつ積む。"""
        with self._lock:
            for line in str(text).splitlines() or [""]:
                self._seq += 1
                self._logs.append((self._seq, line))

    def logs_since(self, seq: int) -> list[tuple[int, str]]:
        """番号 seq より新しいログを返す。"""
        with self._lock:
            return [item for item in self._logs if item[0] > seq]

    def fail(self, message: str) -> None:
        """実行時の例外を、落ちたブロックと一緒に残す。"""
        if not self.error:
            self.error = str(message)
            self.error_block = self.block_id

    def end(self, result: str) -> None:
        """結果を確定する。最初の1回だけ効く（後から上書きしない）。"""
        if not self.result:
            self.result = str(result)

    def request_step(self, cmd: Any) -> None:
        """次のブロックの手前で止まるよう頼み、止まっていれば動かす。"""
        self.step_pending = True
        resume = getattr(cmd, "resume", None)
        if callable(resume):
            resume()

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        """編集画面へ返す様子。ログは since より新しい分だけ。"""
        return {
            "runId": self.run_id,
            "running": self.running,
            "result": self.result,
            "blockId": self.block_id,
            "error": self.error,
            "errorBlock": self.error_block,
            "logs": [list(item) for item in self.logs_since(int(since))],
            "vars": [[name, self.variables[name]] for name in sorted(self.variables)],
        }

    # -- 作業スレッドから呼ばれる ---------------------------------------------

    def step(self, cmd: Any, block_id: Any) -> None:
        """目印の本体。今のブロックを記録し、止まる指示があれば止まる。"""
        self.block_id = str(block_id)
        try:
            caller = sys._getframe(1)
        except ValueError:
            caller = None
        if caller is not None:
            try:
                self._refresh_variables(caller.f_globals, caller.f_locals)
            finally:
                del caller
        hook = self.on_step
        if hook is not None:
            hook(self.block_id)
        if self.step_pending or self.block_id in self.breakpoints:
            self.step_pending = False
            pause = getattr(cmd, "pause", None)
            if callable(pause):
                pause()
        # 停止・一時停止の関所。待ちを含まないループでも止められるよう、
        # ブロックごとに必ず通す。
        gate = getattr(cmd, "_gate", None)
        if callable(gate):
            gate()
        else:
            cmd.checkIfAlive()

    def _refresh_variables(
        self, f_globals: dict[Any, Any], f_locals: dict[Any, Any]
    ) -> None:
        """呼び出し元（do やサブルーチン）の変数を集めて丸ごと差し替える。

        読み手のスレッドは代入の前後どちらかを見るだけのため錠は要らない。
        集める処理で例外が出ても実行は止めない（前回の値のままにする）。
        """
        try:
            raw: dict[str, Any] = {}
            for source in (f_globals, f_locals):
                for name, value in source.items():
                    if not isinstance(name, str):
                        continue
                    if name.startswith("_") or name == "self":
                        continue
                    if value is None or isinstance(value, _VARS_TYPES):
                        raw[name] = value
            shown: dict[str, str] = {}
            for name in sorted(raw)[:_VARS_MAX]:
                try:
                    # 丸ごと repr すると大きなリスト・文字列でブロックごとに重くなる
                    # （10万件で1回数ms）。件数・文字数を先に絞った表示を作る。
                    text = _SHORT_REPR.repr(raw[name])
                except Exception:
                    continue
                if len(text) > _VARS_REPR_MAX:
                    text = text[:_VARS_REPR_MAX] + "…"
                shown[name] = text
            self.variables = shown
        except Exception:
            # 表示のための収集で実行を止めない（前回の値のまま）。
            pass


def build_trial_class(
    code: str, session: TrialSession
) -> tuple[type | None, list[str]]:
    """試し実行用のコマンドクラスを作る。失敗したら (None, 理由の一覧)。

    検査は保存と同じ（import の公開面・NAME と do・サブルーチン・テンプレ参照）。
    検査を通らないコードは読み込まない（読み込み＝実行のため）。
    """
    errors = blockly_validate.validate_generated_code(code)
    if not errors:
        errors = blockly_templates.validate_template_refs(code)
    if not errors:
        errors = _check_trace_calls(code)
    if errors:
        return None, errors

    def trial_print(*args: Any, **kwargs: Any) -> None:
        # 本体のログ欄にも従来どおり出し、編集画面向けにも拾う。
        builtins.print(*args, **kwargs)
        sep = kwargs.get("sep", " ")
        session.log((" " if sep is None else str(sep)).join(str(a) for a in args))

    namespace: dict[str, Any] = {
        "__name__": _MODULE_NAME,
        "__builtins__": builtins,
        TRACE_NAME: session.step,
        "print": trial_print,
    }
    try:
        exec(compile(code, "<blockly-trial>", "exec"), namespace)
    except Exception as e:
        logger.warning(f"試し実行のコードを読み込めません: {e}")
        return None, [f"コードを読み込めません: {type(e).__name__}: {e}"]

    # 読み込んだコードのクラス。属性（NAME・do 等）は実行時に決まるため Any で持つ。
    bases: list[Any] = [
        value
        for value in namespace.values()
        if isinstance(value, type)
        and value.__module__ == _MODULE_NAME
        and isinstance(getattr(value, "NAME", None), str)
        and callable(getattr(value, "do", None))
    ]
    if len(bases) != 1:
        return None, ["実行するプログラムが見つかりません"]
    base = bases[0]

    def do(self: Any) -> None:
        try:
            base.do(self)
        except StopThread:
            raise
        except Exception as e:
            session.fail(f"{type(e).__name__}: {e}")
            session.end("エラー")
            raise
        session.end("完了")

    def finish(self: Any) -> None:
        session.finish_called = True
        base.finish(self)

    def cleanup(self: Any, ser: Any = None) -> None:
        # 結果は後始末の手前で確定する。後始末は完了の合図（postProcess）を
        # 出すため、後から決めると「終わったのに結果が無い」瞬間ができる。
        # 停止は本体のボタン・Esc・編集画面のどれでもここを通る。
        if getattr(self, "_history_failed", False):
            session.end("エラー")
        session.end("完了" if session.finish_called else "停止")
        base._cleanup(self, ser)

    def do_safe(self: Any, ser: Any) -> None:
        try:
            base.do_safe(self, ser)
        finally:
            # 後始末を通らない経路の保険（編集画面が待ち続けないように）。
            session.end("エラー")

    def print2(self: Any, *args: Any, sep: str = " ", end: str = "\n") -> None:
        session.log(sep.join(str(a) for a in args))
        base.print2(self, *args, sep=sep, end=end)

    trial = type(
        base.__name__,
        (base,),
        {
            "NAME": TRIAL_PREFIX + str(base.NAME),
            # 使用履歴・実行履歴に試し実行を混ぜない目印（CommandRunner が見る）。
            "POKECON_TRIAL": True,
            "__module__": _MODULE_NAME,
            "do": do,
            "do_safe": do_safe,
            "finish": finish,
            "_cleanup": cleanup,
            "print2": print2,
        },
    )
    return trial, []


def _check_trace_calls(code: str) -> list[str]:
    """目印の呼出が `_pokecon_step(self, '<ID>')` の形だけかを確かめる。

    ブロックIDはそのまま引用符で囲んでコードへ入る。手で書き換えた保存物の
    IDに引用符が入っていると、コードへ別の式を差し込めてしまうため、
    引数が素の文字列でないものは走らせない。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"生成コードの文法が壊れています（{e}）"]
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == TRACE_NAME
        ):
            continue
        args = node.args
        if (
            len(args) != 2
            or node.keywords
            or not (isinstance(args[0], ast.Name) and args[0].id == "self")
            or not (
                isinstance(args[1], ast.Constant) and isinstance(args[1].value, str)
            )
        ):
            return [
                "目印（ブロックID）の形が正しくありません（保存物のIDを確認してください）"
            ]
    return []
