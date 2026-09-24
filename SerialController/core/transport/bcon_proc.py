#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bcon_proc.py - bcon送出路の別プロセス化（Phase 2）。

GUIプロセスのGIL・描画負荷から120Hz送出を切り離す。子が
シリアルポート・RXポンプ・live loop・会話opを所持し、親は
`BconProcTransport` 越しに同一操作面を使う。フレーム化・SEQ
（第2区画）は親に残し、送信ループ（第3区画）だけ移す。
起動は素のPopen＋TCPループバック集合（multiprocessing.spawnの
ハンドル複製が効かない環境でも動く）。

子入口はモジュール関数 `_worker_main`（Windows spawn安全のため
import時の副作用なし・tkinter非依存）。全メッセージはpicklable。
"""

from __future__ import annotations

import atexit
import base64
import logging
import os
import pickle
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
from typing import Any

from core.transport.base import BCON_STATE, Transport
from core.transport.bcon import BconTransport

PROC_PRESET_NAME = "switch-bcon-proc"
PROC_PRESET_DESCRIPTION = "Switch-bconを別プロセスで送出（GUI負荷切り離し）"
PROC_RPC_MARGIN_S = 5.0
PROC_ALIVE_POLL_S = 0.2

# 子へ送ってよい会話op（親の公開口と1:1）。増やすときは子実装と同時に。
_ALLOWED_CALLS = frozenset(
    {
        "is_open",
        "hello",
        "ping",
        "request_status",
        "request_color",
        "last_status",
        "last_player_info",
        "baud_hunt",
        "set_baud_index",
        "set_wired_mode",
        "set_emulate_mode",
        "send_beacon",
        "send_config",
        "start_live_loop",
        "stop_live_loop",
        "live_loop_running",
        "live_stats",
    }
)

_ZERO_STATUS = {
    "flags": 0,
    "last_seq": 0,
    "err_crc": 0,
    "err_drop": 0,
    "errcode": 0,
}

_ZERO_LIVE_STATS = {
    "sent": 0,
    "merged": 0,
    "dropped": 0,
    "seq_gap": 0,
    "err_crc": 0,
    "err_drop": 0,
    "rtt_ms": None,
}


def _picklable(value: Any) -> bool:
    """IPCに載せられる値か。"""
    try:
        pickle.dumps(value)
    except Exception:
        return False
    return True


class _PeerGone(EOFError):
    """相手が死んだ（ソケットEOF）。"""


class _SocketQueue:
    """TCP上のキュー代役。put/get(timeout)の口だけ持つ。

    1本の全二重ソケットを方向別に2つ包んで使う（受信バッファは
    包み毎に独立。送受信の混用はしないこと）。
    """

    def __init__(self, sock: socket.socket, name: str = "") -> None:
        self._sock = sock
        self._name = name
        self._buf = bytearray()
        self._send_lock = threading.Lock()
        try:
            sock.setblocking(True)
        except Exception:
            pass

    def put(self, obj: Any) -> None:
        try:
            raw = pickle.dumps(obj, protocol=4)
        except Exception:
            return
        blob = len(raw).to_bytes(4, "big") + raw
        with self._send_lock:
            try:
                self._sock.sendall(blob)
            except Exception:
                pass

    def get(self, timeout: float | None = None) -> Any:
        import select as _select

        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while True:
            while len(self._buf) >= 4:
                size = int.from_bytes(bytes(self._buf[:4]), "big")
                if len(self._buf) >= 4 + size:
                    raw = bytes(self._buf[4 : 4 + size])
                    del self._buf[: 4 + size]
                    try:
                        return pickle.loads(raw)
                    except Exception:
                        continue
                break
            if deadline is not None:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    raise queue.Empty()
            else:
                remain = None
            try:
                ready, _, _ = _select.select([self._sock], [], [], remain)
            except Exception:
                raise _PeerGone(f"{self._name} select failed")
            if not ready:
                raise queue.Empty()
            try:
                chunk = self._sock.recv(65536)
            except Exception:
                raise _PeerGone(f"{self._name} recv failed")
            if not chunk:
                raise _PeerGone(f"{self._name} EOF")
            self._buf.extend(chunk)

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass


class _QueueLogWriter:
    """子のprintを親へ運ぶ。LogPaneのリダイレクタと同型。"""

    def __init__(self, evt_q: Any) -> None:
        self._evt_q = evt_q

    def write(self, text: Any) -> int:
        try:
            chunk = str(text)
        except Exception:
            return 0
        try:
            for line in chunk.splitlines():
                if line.strip():
                    self._evt_q.put(("log", line))
        except Exception:
            pass
        return len(chunk)

    def flush(self) -> None:
        return None


def _precise_timer(on: bool) -> None:
    """子も待ち粒度1msで刻む。失敗は黙って coarse のままにする。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        if on:
            ctypes.windll.winmm.timeBeginPeriod(1)
        else:
            ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass


def _worker_main(cmd_q: Any, evt_q: Any, cfg: dict) -> None:
    """子プロセス入口。例外では落ちず、止め指示でのみ抜ける。

    親が死ぬとcmd口がEOFになるため、そこで線を閉じて抜ける
    （TerminateProcess殺しではdaemon指定が効かないための対策）。
    """
    sys.stdout = _QueueLogWriter(evt_q)  # type: ignore[assignment]
    _precise_timer(True)
    try:
        import logging

        logging.getLogger().addHandler(logging.NullHandler())
    except Exception:
        pass
    try:
        transport = BconTransport(
            logger=None, use_len12=bool((cfg or {}).get("use_len12", False))
        )
    except Exception:
        traceback.print_exc()
        return

    def _forward_frame(frame: Any) -> None:
        # 全転送を残す。選別は親の購読側で行い、ここで削らない。
        # 他の購読者がlive/状態確認で全ftype（STATUS等）を要するため、
        # 絞ると壊れる。T1数え（ftype別rx率・qsize）はBconSetup側で賄え、
        # 配達の帰属付けは要らない旨の史料として残す。
        try:
            ftype = int(frame[0]) & 0xFF
            payload = bytes(frame[1])
            seq = int(frame[2]) & 0xFF
        except (TypeError, ValueError, IndexError):
            return
        try:
            evt_q.put(("rx", ftype, payload, seq))
        except Exception:
            pass

    def _hook_begin(row: Any, show: Any = True) -> None:
        try:
            evt_q.put(("hook", "begin", str(row), bool(show)))
        except Exception:
            pass

    def _hook_end(row: Any, show: Any = True) -> None:
        try:
            evt_q.put(("hook", "end", str(row), bool(show)))
        except Exception:
            pass

    try:
        transport.set_hooks(_hook_begin, _hook_end)
    except Exception:
        pass
    try:
        transport.subscribe_rx(_forward_frame)
    except Exception:
        pass

    def _reply(call_id: Any, ok: bool, result: Any) -> None:
        try:
            evt_q.put(("reply", call_id, bool(ok), result))
        except Exception:
            pass

    while True:
        try:
            msg = cmd_q.get()
        except _PeerGone:
            # 親が死んだ（ソケットEOF）。線を閉じて静かに抜ける。
            break
        except Exception:
            break
        try:
            op = msg[0] if isinstance(msg, tuple) and msg else ""
        except Exception:
            continue
        try:
            if op == "stop":
                break
            if op == "close":
                _, call_id = msg[:2]
                try:
                    transport.close()
                except Exception:
                    traceback.print_exc()
                _reply(call_id, True, True)
                continue
            if op == "open":
                _, call_id, port_num, port_name, baudrate = msg[:5]
                try:
                    ok = bool(transport.open(port_num, port_name, baudrate))
                except Exception:
                    traceback.print_exc()
                    ok = False
                _reply(call_id, True, ok)
            elif op == "stage":
                _, row, measure = msg
                try:
                    transport.send_row(
                        row if isinstance(row, str) else "",
                        bool(measure),
                    )
                except Exception:
                    traceback.print_exc()
            elif op == "poke":
                try:
                    transport.flush_pending()
                except Exception:
                    pass
            elif op == "call":
                _, call_id, method, args = msg
                if method not in _ALLOWED_CALLS:
                    _reply(call_id, False, {"error": f"unknown op: {method!r}"})
                    continue
                try:
                    func = getattr(transport, method)
                    result = func(*args) if isinstance(args, tuple) else func()
                    _reply(call_id, True, result)
                except Exception:
                    traceback.print_exc()
                    _reply(call_id, False, None)
            # 未知opは無視する（新旧の版ずれで落とさない）。
        except Exception:
            traceback.print_exc()
            continue
    try:
        transport.close()
    except Exception:
        pass
    finally:
        _precise_timer(False)


class _PopenHandle:
    """subprocess.Popenの最小handle（is_alive/terminate/join）。"""

    def __init__(self, proc: Any) -> None:
        self._proc = proc

    def is_alive(self) -> bool:
        try:
            return self._proc.poll() is None
        except Exception:
            return False

    def terminate(self) -> None:
        try:
            self._proc.terminate()
        except Exception:
            pass

    def join(self, timeout: float | None = None) -> None:
        try:
            self._proc.wait(timeout=timeout)
        except Exception:
            pass


def _default_spawn(entry: Any, cfg: dict) -> tuple[Any, Any, Any]:
    """本物の起動。素のPopen＋TCPループバック集合で集合する。

    multiprocessing.spawnのハンドル複製が効かない環境でも動く。
    子は `-m core.transport.bcon_proc_child` で起動する。
    """
    _ = entry
    transport_dir = os.path.dirname(os.path.abspath(__file__))
    controller_dir = os.path.dirname(os.path.dirname(transport_dir))
    child_mod = os.path.join(transport_dir, "bcon_proc_child.py")
    if not os.path.isfile(child_mod):
        raise RuntimeError("bcon_proc_child module not found")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(15.0)
        port = int(listener.getsockname()[1])
        token = secrets.token_hex(16)
        try:
            cfg_raw = pickle.dumps(dict(cfg or {}), protocol=4)
        except Exception:
            raise RuntimeError("proc cfg not picklable")
        cfg_b64 = base64.b64encode(cfg_raw).decode("ascii")
        env = dict(os.environ)
        path_key = "PYTHONPATH"
        env[path_key] = controller_dir + os.pathsep + env.get(path_key, "")
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "core.transport.bcon_proc_child",
                str(port),
                token,
                cfg_b64,
            ],
            cwd=controller_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            ),
        )
        try:
            conn, _addr = listener.accept()
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
            raise RuntimeError("proc child did not connect")
        try:
            conn.settimeout(15.0)
            hello = _recv_framed(conn)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            raise RuntimeError("proc child hello failed")
        finally:
            try:
                listener.close()
            except Exception:
                pass
        if (
            not isinstance(hello, tuple)
            or len(hello) != 2
            or hello[0] != "hello-child"
            or hello[1] != token
        ):
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            raise RuntimeError("proc child token mismatch")
        try:
            conn.settimeout(None)
        except Exception:
            pass
    except Exception:
        try:
            listener.close()
        except Exception:
            pass
        raise
    cmd_q = _SocketQueue(conn, name="cmd")
    evt_q = _SocketQueue(conn, name="evt")
    return _PopenHandle(proc), cmd_q, evt_q


def _recv_framed(sock: socket.socket) -> Any:
    """1フレーム受信（集合時のhello用）。"""
    need = 4
    buf = bytearray()
    while len(buf) < need:
        chunk = sock.recv(need - len(buf))
        if not chunk:
            raise _PeerGone("hello EOF")
        buf.extend(chunk)
    size = int.from_bytes(bytes(buf[:4]), "big")
    while len(buf) < 4 + size:
        chunk = sock.recv(4 + size - len(buf))
        if not chunk:
            raise _PeerGone("hello EOF")
        buf.extend(chunk)
    return pickle.loads(bytes(buf[4 : 4 + size]))


class BconProcTransport(Transport):
    """別プロセスbconの親側プロキシ。操作面は本家と同一にする。

    Sender・BconSetup・スクリプトは本家と同じ呼び方で使える。
    子が死んだ呼出しは安全既定値（False/None/空）で返し、落とさない。
    """

    name = "switch-bcon-proc"
    capability = BCON_STATE

    def __init__(
        self,
        logger: Any = None,
        use_len12: bool = False,
        _spawn: Any = None,
    ) -> None:
        self._logger = logger
        self._use_len12 = bool(use_len12)
        self._spawn = _spawn or _default_spawn
        self._worker: Any = None
        self._cmd_q: Any = None
        self._evt_q: Any = None
        self._call_seq = 0
        self._pending: dict[Any, tuple[threading.Event, dict]] = {}
        self._pending_lock = threading.Lock()
        self._subs: list[Any] = []
        self._subs_lock = threading.Lock()
        self._hook_begin: Any = None
        self._hook_end: Any = None
        self._dispatch_thread: Any = None
        self._dispatch_stop = threading.Event()
        self._opened = False
        self._dead = False
        # 常時計測の素朴な数え（H1切り分け用・永置）。
        # int/float/perf_counterだけで、per-frameのI/Oはしない。
        self._rpc_count = 0
        self._rpc_last_ms = 0.0
        self._rpc_max_ms = 0.0
        self._rpc_last_method = ""
        self._rpc_log_t = 0.0
        self._rx_fanout_total = 0
        self._rx_fanout_last = 0
        try:
            atexit.register(self._terminate_worker)
        except Exception:
            pass

    # -- 寿命 ----------------------------------------------------------

    def _worker_alive(self) -> bool:
        try:
            return (
                self._worker is not None
                and not self._dead
                and bool(self._worker.is_alive())
            )
        except Exception:
            return False

    def _mark_dead(self) -> None:
        self._dead = True
        self._opened = False
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for event, _box in pending:
            try:
                event.set()
            except Exception:
                pass

    def _warn(self, msg: str) -> None:
        """親側の失敗理由を残す。振る舞いは変えない（落とさない）。"""
        try:
            if self._logger is not None:
                self._logger.warning(msg)
        except Exception:
            pass
        try:
            logging.getLogger(__name__).warning(msg)
        except Exception:
            pass

    def _terminate_worker_for_test(self) -> None:
        """検査用：残骸処理を bypass して子だけ殺す（crash再現）。"""
        worker = self._worker
        try:
            if worker is not None:
                worker.terminate()
        except Exception:
            pass

    def _terminate_worker(self) -> None:
        worker, cmd_q, evt_q = self._worker, self._cmd_q, self._evt_q
        self._worker = None
        self._cmd_q = None
        self._evt_q = None
        try:
            if worker is not None:
                try:
                    if cmd_q is not None:
                        cmd_q.put(("stop",))
                except Exception:
                    pass
                try:
                    if bool(worker.is_alive()):
                        worker.join(timeout=2.0)
                except Exception:
                    pass
                try:
                    if bool(worker.is_alive()):
                        worker.terminate()
                        worker.join(timeout=2.0)
                except Exception:
                    pass
        except Exception:
            pass
        for closer in (cmd_q, evt_q):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        try:
            self._dispatch_stop.set()
        except Exception:
            pass

    def _spawn_worker(self) -> bool:
        """子を起こす。起動済みなら何もしない。"""
        if self._worker_alive():
            self._dead = False
            return True
        self._terminate_worker()
        self._dispatch_stop.clear()
        try:
            handle, cmd_q, evt_q = self._spawn(
                _worker_main, {"use_len12": self._use_len12}
            )
        except Exception as e:
            self._warn(f"proc送出子の起動に失敗しました: {e!r}")
            return False
        self._worker = handle
        self._cmd_q = cmd_q
        self._evt_q = evt_q
        self._dead = False
        try:
            start = getattr(handle, "start", None)
            if callable(start):
                start()
        except Exception as e:
            self._warn(f"proc送出子の開始に失敗しました: {e!r}")
            self._mark_dead()
            return False
        if not self._worker_alive():
            self._warn(
                "proc送出子が起動しませんでした（子stdoutはDEVNULLのため詳細は起動例外を参照）"
            )
            self._mark_dead()
            return False
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, name="BconProcDispatch", daemon=True
        )
        self._dispatch_thread.start()
        return True

    def proc_alive(self) -> bool:
        """子が応答するか（起動確認用）。"""
        if not self._worker_alive():
            return False
        ok, _result = self._call("live_loop_running", (), timeout=3.0)
        return bool(ok)

    def _dispatch_loop(self) -> None:
        """子evtの配達。購読・print・hook・replyを捌く。

        rxは全購読者へ扇状配達する。帰属付けの選別はしない。
        他の購読者がlive/状態確認で全ftypeを要するため、削ると壊れる。
        後片付けは購読側（BconSetupの取込後始末）の責務であり、配達側で
        勝手に外さない。
        """
        evt_q = self._evt_q
        while not self._dispatch_stop.is_set():
            try:
                evt = evt_q.get(timeout=PROC_ALIVE_POLL_S)
            except queue.Empty:
                continue
            except _PeerGone:
                self._mark_dead()
                break
            except Exception:
                break
            try:
                kind = evt[0] if isinstance(evt, tuple) and evt else ""
            except Exception:
                continue
            try:
                if kind == "rx":
                    _, ftype, payload, seq = evt
                    with self._subs_lock:
                        subs = list(self._subs)
                    # 配達数の数え（H3切り分け用・intだけ・I/Oなし）。
                    try:
                        self._rx_fanout_total += 1
                        self._rx_fanout_last = len(subs)
                    except Exception:
                        pass
                    for func in subs:
                        try:
                            func((int(ftype), bytes(payload), int(seq)))
                        except Exception:
                            pass
                elif kind == "log":
                    print(evt[1] if len(evt) > 1 else "")
                elif kind == "hook":
                    _, which, row, show = evt
                    hook = self._hook_begin if which == "begin" else self._hook_end
                    if hook is not None:
                        try:
                            hook(str(row), bool(show))
                        except Exception:
                            pass
                elif kind == "reply":
                    _, call_id, ok, result = evt
                    with self._pending_lock:
                        entry = self._pending.pop(call_id, None)
                    if entry is not None:
                        event, box = entry
                        box["ok"] = bool(ok)
                        box["result"] = result
                        try:
                            event.set()
                        except Exception:
                            pass
                # 未知evtは無視する。
            except Exception:
                continue

    def _note_rpc(self, method: str, ms: float) -> None:
        # RPC往復の写し（H1切り分け用・int/floatだけ・I/Oなし）。
        # 遅い往復だけdebugへ間引きで出す（既定では出ない）。
        try:
            value = float(ms)
            self._rpc_count += 1
            self._rpc_last_ms = value
            self._rpc_last_method = str(method)
            if value > float(self._rpc_max_ms):
                self._rpc_max_ms = value
            if value >= 100.0:
                now = time.perf_counter()
                if now - float(self._rpc_log_t) >= 5.0:
                    self._rpc_log_t = now
                    logging.getLogger(__name__).debug(
                        "bcon-proc RPC遅延 %s=%.1fms", method, value
                    )
        except Exception:
            pass

    def rpc_stats(self) -> dict[str, Any]:
        # 直近の往復写し。呼ぶだけで線に触れない。
        try:
            return {
                "count": int(self._rpc_count),
                "last_ms": float(self._rpc_last_ms),
                "max_ms": float(self._rpc_max_ms),
                "last_method": str(self._rpc_last_method),
                "rx_fanout_total": int(self._rx_fanout_total),
                "rx_fanout_last": int(self._rx_fanout_last),
            }
        except Exception:
            return {
                "count": 0,
                "last_ms": 0.0,
                "max_ms": 0.0,
                "last_method": "",
                "rx_fanout_total": 0,
                "rx_fanout_last": 0,
            }

    def _call(
        self, method: str, args: tuple = (), timeout: float | None = None
    ) -> tuple[bool, Any]:
        """子へ会話opを送り応答を待つ。(成否, 結果)を返す。"""
        if not self._worker_alive():
            return False, None
        if not _picklable(args):
            return False, None
        started = time.perf_counter()
        with self._pending_lock:
            self._call_seq += 1
            call_id = self._call_seq
            event = threading.Event()
            box: dict = {}
            self._pending[call_id] = (event, box)
        try:
            self._cmd_q.put(("call", call_id, method, tuple(args)))
        except Exception:
            with self._pending_lock:
                self._pending.pop(call_id, None)
            self._mark_dead()
            return False, None
        limit = float(timeout) if timeout else 10.0
        if not event.wait(limit + PROC_RPC_MARGIN_S):
            with self._pending_lock:
                self._pending.pop(call_id, None)
            if not self._worker_alive():
                self._mark_dead()
            self._note_rpc(method, (time.perf_counter() - started) * 1000.0)
            return False, None
        self._note_rpc(method, (time.perf_counter() - started) * 1000.0)
        return bool(box.get("ok", False)), box.get("result")

    # -- Transport操作面 -------------------------------------------------

    def open(
        self,
        portNum: int,
        portName: str = "",
        baudrate: int = 1000000,
        **extra: Any,
    ) -> bool:
        """子を起こして線を開く。成否を返す。"""
        try:
            num = int(portNum)
        except (TypeError, ValueError):
            num = 0
        try:
            rate = int(baudrate)
        except (TypeError, ValueError):
            self._warn(f"bcon-procのbaud指定が不正です: {baudrate!r}")
            return False
        name = portName.strip() if isinstance(portName, str) else ""
        _ = extra
        label = f"{name or num} {rate}bps"
        if not self._spawn_worker():
            self._warn(f"bcon-procの子を起こせませんでした({label})")
            return False
        with self._pending_lock:
            self._call_seq += 1
            call_id = self._call_seq
            event = threading.Event()
            box: dict = {}
            self._pending[call_id] = (event, box)
        try:
            self._cmd_q.put(("open", call_id, num, name, rate))
        except Exception as e:
            with self._pending_lock:
                self._pending.pop(call_id, None)
            self._mark_dead()
            self._warn(f"bcon-procのopen指示を子へ送れませんでした({label}): {e!r}")
            return False
        limit = 10.0 + PROC_RPC_MARGIN_S
        if not event.wait(limit):
            with self._pending_lock:
                self._pending.pop(call_id, None)
            self._mark_dead()
            self._warn(
                f"bcon-procのopen応答待ちがtimeoutしました({label} {limit}s待機): "
                "子の起動クラッシュの可能性（子stdoutはDEVNULL）"
            )
            return False
        ok = bool(box.get("result", False))
        self._opened = ok
        if not ok:
            self._warn(f"bcon-procのopenが子で失敗しました({label}): {box!r}")
            self._terminate_worker()
        return ok

    def close(self) -> None:
        """線を閉じて子を畳む。二重安全。例外は投げない。"""
        try:
            if self._worker_alive():
                with self._pending_lock:
                    self._call_seq += 1
                    call_id = self._call_seq
                    event = threading.Event()
                    box: dict = {}
                    self._pending[call_id] = (event, box)
                try:
                    self._cmd_q.put(("close", call_id))
                    event.wait(5.0)
                except Exception:
                    pass
                finally:
                    with self._pending_lock:
                        self._pending.pop(call_id, None)
        except Exception:
            pass
        finally:
            self._opened = False
            self._terminate_worker()

    def is_open(self) -> bool:
        """開いているか。子の応答で確かめる。"""
        if not self._worker_alive() or not self._opened:
            return False
        ok, result = self._call("is_open", (), timeout=3.0)
        if not ok:
            return False
        return bool(result)

    def isOpened(self) -> bool:
        """後方互換の別名。"""
        try:
            return bool(self.is_open())
        except Exception:
            return False

    def send_row(self, row: str, measure_perf: bool = True) -> None:
        """姿勢行を子へ積む（fire-and-forget）。例外は投げない。"""
        try:
            if not self._worker_alive():
                return
            text = row if isinstance(row, str) else ""
            self._cmd_q.put(("stage", text, bool(measure_perf)))
        except Exception:
            try:
                self._mark_dead()
            except Exception:
                pass

    def flush_pending(self) -> None:
        """子のloopを起こして即送させる。"""
        try:
            if not self._worker_alive():
                return
            self._cmd_q.put(("poke",))
        except Exception:
            pass

    def add_listener(self, func: Any) -> bool:
        """bcon本家と同様に繋げない（False）。"""
        _ = func
        return False

    def remove_listener(self, func: Any) -> None:
        """繋いでいないため何もしない。"""
        _ = func
        return None

    def set_hooks(self, on_write_begin: Any = None, on_write_end: Any = None) -> None:
        """親側で保持し、子からのhook転送で呼ぶ。"""
        self._hook_begin = on_write_begin
        self._hook_end = on_write_end

    def get_raw_serial(self) -> None:
        """bcon本家と同様に渡さない（None）。"""
        return None

    def subscribe_rx(self, func: Any) -> Any:
        """受信フレームごとの購読。子が全frameを転送する。"""
        if callable(func):
            with self._subs_lock:
                if func not in self._subs:
                    self._subs.append(func)

        def _unsub() -> None:
            try:
                with self._subs_lock:
                    if func in self._subs:
                        self._subs.remove(func)
            except Exception:
                pass

        return _unsub

    # -- 会話op（子へRPC。引数・戻りは本家と同一形） -----------------------

    def _rpc_bool(self, method: str, args: tuple, timeout: float) -> bool:
        ok, result = self._call(method, args, timeout=timeout)
        return bool(ok and result)

    def hello(self, timeout: float = 3.0, quiet: bool = False) -> bool:
        return self._rpc_bool("hello", (timeout, quiet), timeout)

    def ping(self, timeout: float = 1.0, quiet: bool = False) -> float | None:
        ok, result = self._call("ping", (timeout, quiet), timeout=timeout)
        if not ok or result is None:
            return None
        try:
            return float(result)
        except (TypeError, ValueError):
            return None

    def live_loop_running(self) -> bool:
        ok, result = self._call("live_loop_running", (), timeout=3.0)
        return bool(ok and result)

    def start_live_loop(self, interval_s: float = 1.0 / 120.0) -> bool:
        return self._rpc_bool("start_live_loop", (interval_s,), 5.0)

    def stop_live_loop(self) -> bool:
        return self._rpc_bool("stop_live_loop", (), 5.0)

    def live_stats(self) -> dict[str, Any]:
        ok, result = self._call("live_stats", (), timeout=3.0)
        if not ok or not isinstance(result, dict):
            return dict(_ZERO_LIVE_STATS)
        merged = dict(_ZERO_LIVE_STATS)
        for key in merged:
            if key in result:
                merged[key] = result[key]
        return merged

    def last_status(self) -> dict[str, int]:
        ok, result = self._call("last_status", (), timeout=3.0)
        if not ok or not isinstance(result, dict):
            return dict(_ZERO_STATUS)
        merged = dict(_ZERO_STATUS)
        for key in merged:
            if key in result:
                try:
                    merged[key] = int(result[key])
                except (TypeError, ValueError):
                    pass
        return merged

    def last_player_info(self) -> bytes:
        ok, result = self._call("last_player_info", (), timeout=3.0)
        if not ok or result is None:
            return b""
        try:
            return bytes(result)
        except (TypeError, ValueError):
            return b""

    def request_status(
        self, timeout: float = 1.0, quiet: bool = False
    ) -> dict[str, int] | None:
        ok, result = self._call("request_status", (timeout, quiet), timeout=timeout)
        if not ok or not isinstance(result, dict):
            return None
        return {str(k): v for k, v in result.items()}

    def baud_hunt(self, candidates: Any = None, timeout_per: float = 0.5) -> int | None:
        ok, result = self._call("baud_hunt", (candidates, timeout_per), timeout=30.0)
        if not ok or result is None:
            return None
        try:
            return int(result)
        except (TypeError, ValueError):
            return None

    def set_baud_index(self, index: int, timeout: float = 2.0) -> bool:
        return self._rpc_bool("set_baud_index", (index, timeout), timeout)

    def set_wired_mode(self, enable: bool, timeout: float = 1.0) -> bool:
        return self._rpc_bool("set_wired_mode", (enable, timeout), timeout)

    def set_emulate_mode(self, role: int, timeout: float = 1.0) -> bool:
        return self._rpc_bool("set_emulate_mode", (role, timeout), timeout)

    def send_beacon(self, timeout: float = 2.0) -> dict[str, int] | None:
        ok, result = self._call("send_beacon", (timeout,), timeout=timeout)
        if not ok or not isinstance(result, dict):
            return None
        return {str(k): v for k, v in result.items()}

    def send_config(self, type_: int, payload: bytes) -> bool:
        try:
            args = (int(type_) & 0xFF, bytes(payload))
        except (TypeError, ValueError):
            return False
        return self._rpc_bool("send_config", args, 5.0)

    def request_color(self, timeout: float = 1.0) -> bytes | None:
        try:
            limit = float(timeout)
        except (TypeError, ValueError):
            limit = 1.0
        ok, result = self._call("request_color", (limit,), timeout=limit)
        if not ok or result is None:
            return None
        try:
            payload = bytes(result)
        except (TypeError, ValueError):
            return None
        if len(payload) != 12:
            return None
        return payload
