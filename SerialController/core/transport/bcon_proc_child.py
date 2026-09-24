#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bcon_proc_child.py - 別プロセス送出の子側起動口。

`python -m core.transport.bcon_proc_child <port> <token> <cfg_b64>`
として起こす。multiprocessing.spawnのハンドル複製が効かない環境
でも動くよう、素のPopen＋TCPループバック集合で集合する。
"""

from __future__ import annotations

import base64
import pickle
import socket
import sys


def _child_main(port: int, token: str, cfg_b64: str) -> int:
    from core.transport.bcon_proc import _SocketQueue, _worker_main

    try:
        cfg = pickle.loads(base64.b64decode(cfg_b64.encode("ascii")))
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}
    try:
        sock = socket.create_connection(("127.0.0.1", int(port)), timeout=10.0)
    except Exception:
        return 2
    try:
        payload = ("hello-child", str(token))
        raw = pickle.dumps(payload, protocol=4)
        sock.sendall(len(raw).to_bytes(4, "big") + raw)
    except Exception:
        try:
            sock.close()
        except Exception:
            pass
        return 3
    cmd_q = _SocketQueue(sock, name="cmd")
    evt_q = _SocketQueue(sock, name="evt")
    try:
        _worker_main(cmd_q, evt_q, cfg)
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: bcon_proc_child <port> <token> <cfg_b64>")
        return 2
    try:
        port = int(argv[1])
    except (TypeError, ValueError):
        return 2
    return _child_main(port, argv[2], argv[3])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
