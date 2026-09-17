#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bcon_jitter.py - 送信周期p50/p99とSTATUS差分の計測器。

bconの120Hz送出が実線で保たれているかを量る。使い方は
`python tools/bcon_jitter.py --port COM9 --baud 1000000 --secs 10`。
変換器のlatency timer設定（FTDI既定16msは120Hzの致命傷）を
--latency-noteに添えて前後比較する（例 `--latency-note "FTDI timer=1ms"`）。
TXをRXへ折返したloopback配線では自STATEがRXへ戻りSEQ連続性
（seq_gap）も見られる。Picoが居ないloopbackではHELLO応答が無いため
--no-helloで送出だけ量る。出力は1行
`p50=X.XXms p99=Y.YYms sent=N seq_gap=M err_crc=+0 err_drop=+0`＋CSV。
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path
from typing import Any


def percentile(samples: list[float], q: float) -> float:
    """qパーセンタイル（nearest-rank）。空は0.0。qは0-100へ丸める。"""
    if not samples:
        return 0.0
    try:
        rank_q = float(q)
    except (TypeError, ValueError):
        return 0.0
    rank_q = max(0.0, min(100.0, rank_q))
    ordered = sorted(samples)
    n = len(ordered)
    rank = int(-(-rank_q * n // 100))
    idx = max(0, min(n - 1, rank - 1))
    return float(ordered[idx])


def summarize(
    p50_ms: float,
    p99_ms: float,
    sent: int,
    seq_gap: int,
    d_crc: int,
    d_drop: int,
) -> str:
    """1行要約。err差分は符号つき（+0が正常）。"""
    return (
        f"p50={p50_ms:.2f}ms p99={p99_ms:.2f}ms sent={int(sent)} "
        f"seq_gap={int(seq_gap)} err_crc=+{int(d_crc)} err_drop=+{int(d_drop)}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """引数を読む。--portのみ必須、他はbcon既定（1Mbps・10秒）。"""
    parser = argparse.ArgumentParser(description="bconの120Hz送出を量る。")
    parser.add_argument("--port", required=True, help=" data-UARTの口（例 COM9）")
    parser.add_argument("--baud", type=int, default=1000000, help="速度（既定1M）")
    parser.add_argument("--secs", type=float, default=10.0, help="計測秒数（既定10）")
    parser.add_argument(
        "--latency-note",
        default="",
        help="変換器設定の覚書（例 'FTDI timer=1ms'。CSV先頭へ残る）",
    )
    parser.add_argument("--csv", default=None, help="CSVの出先（既定は自動名）")
    parser.add_argument(
        "--no-hello",
        action="store_true",
        help="HELLOを飛ばす（Picoの居ないloopback用）",
    )
    return parser.parse_args(argv)


def _csv_path(port: str, secs: float, explicit: str | None) -> Path:
    """CSVの出先。未指定は時刻つき自動名（cwd直下）。"""
    if explicit:
        return Path(explicit)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", port)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(f"bcon_jitter_{safe}_{stamp}.csv")


def _write_csv(
    path: Path,
    tx_times: list[float],
    meta: dict[str, Any],
    summary: str,
) -> None:
    """TX時刻列をCSVへ出す。先頭の#行は実行条件（pandasはcomment='#'で読む）。

    列はidx・t_s（開始起点秒）・delta_ms（前回からの間隔・先頭は0.0）。
    """
    t0 = tx_times[0] if tx_times else 0.0
    with open(path, "w", newline="", encoding="utf-8") as handle:
        for key, value in meta.items():
            handle.write(f"# {key}={value}\n")
        handle.write(f"# summary={summary}\n")
        writer = csv.writer(handle)
        writer.writerow(["idx", "t_s", "delta_ms"])
        prev: float | None = None
        for idx, moment in enumerate(tx_times):
            delta = 0.0 if prev is None else (moment - prev) * 1000.0
            writer.writerow([idx, f"{moment - t0:.6f}", f"{delta:.3f}"])
            prev = moment


def main(argv: list[str] | None = None) -> int:
    """開→（HELLO）→中立120Hz→要約＋CSV→閉じる。失敗は文言＋非ゼロ。"""
    args = parse_args(argv)
    try:
        secs = float(args.secs)
    except (TypeError, ValueError):
        print(f"秒数が不正です: {args.secs!r}", file=sys.stderr)
        return 2
    secs = max(1.0, min(secs, 120.0))
    root = Path(__file__).resolve().parent.parent / "SerialController"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from core.transport import create_transport
    except Exception as e:
        print(f"bconの読込に失敗しました: {e!r}", file=sys.stderr)
        return 2
    made = create_transport("bcon")
    try:
        opened = made.open(0, str(args.port), int(args.baud))
    except Exception as e:
        print(f"bconを開けませんでした: {e!r}", file=sys.stderr)
        return 1
    if not opened:
        print(
            f"bconを開けませんでした({args.port} {args.baud}bps)。"
            "配線・電源を確認してください。",
            file=sys.stderr,
        )
        return 1
    tx_times: list[float] = []

    def _on_begin(row: str, show: bool = True) -> None:
        _ = (row, show)
        tx_times.append(time.perf_counter())

    try:
        made.set_hooks(_on_begin, None)
    except Exception:
        pass
    try:
        made.start_rx_pump()
    except Exception:
        pass
    try:
        status0 = dict(made.last_status())
        stats0 = dict(made.live_stats())
    except Exception:
        status0 = {"err_crc": 0, "err_drop": 0}
        stats0 = {"sent": 0, "seq_gap": 0}
    if not bool(getattr(args, "no_hello", False)):
        try:
            if not made.hello(timeout=3.0):
                print("HELLOが通りません。送出だけ量ります。", file=sys.stderr)
        except Exception:
            print("HELLOで例外のため送出だけ量ります。", file=sys.stderr)
    else:
        print("HELLOを飛ばします（loopback想定）。", file=sys.stderr)
    try:
        made.send_row("end")
        made.start_live_loop()
    except Exception as e:
        print(f"送出開始に失敗しました: {e!r}", file=sys.stderr)
        made.close()
        return 1
    try:
        time.sleep(secs)
    except KeyboardInterrupt:
        print("中断しました。ここまでの分で集計します。", file=sys.stderr)
    try:
        made.stop_live_loop()
    except Exception:
        pass
    try:
        made.request_status(timeout=1.0)
    except Exception:
        pass
    try:
        status1 = dict(made.last_status())
        stats1 = dict(made.live_stats())
    except Exception:
        status1 = dict(status0)
        stats1 = dict(stats0)
    try:
        made.close()
    except Exception:
        pass
    deltas = [
        (after - before) * 1000.0 for before, after in zip(tx_times, tx_times[1:])
    ]
    p50 = percentile(deltas, 50)
    p99 = percentile(deltas, 99)
    try:
        sent = int(stats1.get("sent", 0)) - int(stats0.get("sent", 0))
        gap = int(stats1.get("seq_gap", 0)) - int(stats0.get("seq_gap", 0))
    except (TypeError, ValueError):
        sent, gap = len(tx_times), 0
    try:
        d_crc = int(status1.get("err_crc", 0)) - int(status0.get("err_crc", 0))
        d_drop = int(status1.get("err_drop", 0)) - int(status0.get("err_drop", 0))
    except (TypeError, ValueError):
        d_crc, d_drop = 0, 0
    line = summarize(p50, p99, sent, gap, d_crc, d_drop)
    print(line)
    meta = {
        "port": args.port,
        "baud": args.baud,
        "secs": secs,
        "latency_note": getattr(args, "latency_note", ""),
        "no_hello": bool(getattr(args, "no_hello", False)),
        "samples": len(deltas),
    }
    try:
        out = _csv_path(str(args.port), secs, getattr(args, "csv", None))
        _write_csv(out, tx_times, meta, line)
        print(f"csv={out}", file=sys.stderr)
    except Exception as e:
        print(f"CSV出力に失敗しました: {e!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
