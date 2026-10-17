"""switch-bcon-proc が本家 bcon と同じ操作面を保つことを固定する。"""

from __future__ import annotations

import inspect

from core.transport.bcon import BconTransport
from core.transport.bcon_proc import _ALLOWED_CALLS, BconProcTransport

#: proc 側が自前で持つ（本家へ転送しない）公開メンバー。
#: 転送するのは会話系（hello/config/last_* など）だけで、フレーム送出と
#: 受信ポンプは proc 側が自分のループで回す。ここを取り違えると
#: 「子プロセスが実行できない名前を許可リストへ入れる」ことになる。
LOCAL_ONLY = {
    "isOpened",
    "proc_alive",
    "rpc_stats",
    "close",
    "get_raw_serial",
    "open",
    "send_row",
    "flush_pending",
    "add_listener",
    "remove_listener",
    "set_hooks",
    "start_rx_pump",
    "stop_rx_pump",
    "rx_pump_running",
    "subscribe_rx",
    "wait_rx",  # 引数が本家と違う（別途揃えるか要判断）
    "acquire_write_lock",
}


def _public(cls: type) -> set[str]:
    return {n for n, _ in inspect.getmembers(cls) if not n.startswith("_")}


def test_proc_exposes_every_public_member_of_the_direct_transport() -> None:
    """Given: 本家と proc の 2 方式 / When: 公開メンバーを比べる / Then: 欠けが無いこと。"""
    missing = _public(BconTransport) - _public(BconProcTransport)
    # last_rumble の欠落で振動表示が必ず「（なし）」になった実例がある。
    assert not missing, f"proc に無い公開メンバー: {sorted(missing)}"


def test_forwarded_members_are_allowed_by_the_child_rpc() -> None:
    """Given: 本家だけが持つ公開メソッド / When: 子プロセスの許可リストと比べる / Then: すべて許可されていること。"""
    forwarded = {
        name
        for name in _public(BconTransport) - LOCAL_ONLY
        if callable(getattr(BconTransport, name))
        and not isinstance(getattr(BconTransport, name), property)
    }
    missing = sorted(forwarded - set(_ALLOWED_CALLS))
    assert not missing, f"許可リストに無いので子プロセスが実行できない: {missing}"


def test_signatures_of_forwarded_members_match() -> None:
    """Given: 子プロセスへ転送する公開メソッド / When: 引数を比べる / Then: 一致すること。"""
    mismatched: list[str] = []
    forwarded = sorted(
        set(_ALLOWED_CALLS) & _public(BconTransport) & _public(BconProcTransport)
    )
    for name in forwarded:
        direct = getattr(BconTransport, name)
        proc = getattr(BconProcTransport, name)
        if not (callable(direct) and callable(proc)):
            continue
        if isinstance(direct, property) or isinstance(proc, property):
            continue
        if list(inspect.signature(direct).parameters) != list(
            inspect.signature(proc).parameters
        ):
            mismatched.append(name)
    assert not mismatched, f"引数が食い違う: {mismatched}"


def test_local_only_names_have_an_implementation_on_the_proc_transport() -> None:
    """Given: proc 側が自前で持つと宣言した名前 / When: 実体を引く / Then: すべて実在すること。"""
    missing = sorted(
        name for name in LOCAL_ONLY if not hasattr(BconProcTransport, name)
    )
    assert not missing, f"宣言だけで実装が無い: {missing}"


def test_binary_transports_expose_no_raw_serial() -> None:
    """Given: バイナリ方式の 2 実装 / When: 生シリアルを問う / Then: どちらも None を返すこと。"""
    # ASCII の M をバイナリ列へ混ぜない契約（run_journal.raw_serial_for_m が使う）。
    assert BconTransport.get_raw_serial(object.__new__(BconTransport)) is None
    assert BconProcTransport.get_raw_serial(object.__new__(BconProcTransport)) is None
