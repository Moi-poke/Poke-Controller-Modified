#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_journal.py - コマンド走行の記録文面を作る純粋部品。

走行の開始・終了をファイルログへ残すときの文面と差分計算だけを持つ。
時計も線も持たない（呼び出し側が時刻・統計辞書を渡す）。tkinter・画面
部品・アプリ層は import しない（services 境界適合）。
"""

from __future__ import annotations

import re
import time
from typing import Any

# live統計の差分を取るキー。dropped（溢れ捨て）がPC側滞留の証拠になる。
_LIVE_KEYS = ("put", "sent", "dropped", "keepalive", "replaced", "priority")


def safe_name(name: Any) -> str:
    """ファイル名に使える表示名へ直す。空なら unnamed。"""
    text = str(name) if name is not None else ""
    cleaned = "".join(ch if ch not in '/\\:*?"<>|' else "_" for ch in text).strip()
    return cleaned or "unnamed"


def run_csv_name(stamp: str, profile: Any, cmd_name: Any) -> str:
    """走行ごとのワイヤ記録CSV名。stamp は YYYYMMDD-HHMMSS 形。"""
    return f"run_{stamp}_{safe_name(profile)}_{safe_name(cmd_name)}.csv"


def live_delta(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> dict[str, int]:
    """live統計の前後差分。未知キーは無視する（将来の追加に強い）。"""
    before = before or {}
    after = after or {}
    result: dict[str, int] = {}
    for key in _LIVE_KEYS:
        try:
            old = int(before.get(key, 0))
        except (TypeError, ValueError):
            old = 0
        try:
            new = int(after.get(key, 0))
        except (TypeError, ValueError):
            new = 0
        if key in before or key in after:
            result[key] = new - old
    return result


def format_live_delta(delta: dict[str, int]) -> str:
    """差分を1行文面へ。dropped>0 は滞留ありと明示する。"""
    parts = [f"{key}=+{delta[key]}" for key in _LIVE_KEYS if key in delta]
    text = " ".join(parts) if parts else "統計なし"
    if delta.get("dropped", 0) > 0:
        text += "（滞留あり：申告が送出を上回った）"
    return text


def format_m_delta(base: dict[str, Any] | None, now: dict[str, Any] | None) -> str:
    """Mモニタ press/ok/ng の差分を1行文面へ。"""
    if base is None:
        return "M基準なし（次走から差分が出る）"
    if now is None:
        return "M取得失敗（混線・未接続の可能性）"
    try:
        dp = int(now.get("press", 0)) - int(base.get("press", 0))
        do = int(now.get("ok", 0)) - int(base.get("ok", 0))
        dn = int(now.get("ng", 0)) - int(base.get("ng", 0))
    except (TypeError, ValueError):
        return "M取得失敗（混線・未接続の可能性）"
    text = f"press=+{dp} ok=+{do} ng=+{dn}"
    if dn > 0:
        text += "（Picoが行を拒否した）"
    return text


def format_run_start(
    cmd_name: Any,
    profile: Any,
    transport: Any,
    dwell_ms: Any,
    repeat_ms: Any,
) -> str:
    """走行開始の1行。何がどの条件で走ったかを残す。"""
    return (
        f"走行開始: {cmd_name} profile={profile} transport={transport} "
        f"dwell={dwell_ms}ms repeat={repeat_ms}ms"
    )


def format_run_end(
    cmd_name: Any,
    reason: Any,
    seconds: float,
    live_text: str,
    m_text: str,
    csv_path: str,
    rows: int,
) -> str:
    """走行終了の1行。PC無罪か滞留かをここで読み取れる。"""
    return (
        f"走行終了: {cmd_name} 理由={reason} {seconds:.1f}秒 "
        f"live[{live_text}] M[{m_text}] wire={csv_path}({rows}行)"
    )


def _mon_state(lines: list[str]) -> bool | None:
    """直近の monitor on/off 応答。無ければNone。"""
    for line in reversed(lines):
        text = str(line).strip()
        if text == "monitor on":
            return True
        if text == "monitor off":
            return False
    return None


def _parse_mon_counts(lines: list[str]) -> dict[str, int]:
    """Mモニタ行の press/ok/ng を読む。無ければ空辞書（直近行を採る）。"""
    found: dict[str, int] = {}
    for line in lines:
        m = re.search(r"press=(\d+)\s+ok=(\d+)\s+ng=(\d+)", str(line))
        if m:
            found = {
                "press": int(m.group(1)),
                "ok": int(m.group(2)),
                "ng": int(m.group(3)),
            }
    return found


def read_m_counts(
    transport: Any,
    tries: int = 4,
    poll_s: float = 0.7,
    settle_s: float = 1.5,
) -> dict[str, int]:
    """Mを確実にONにし、heartbeatのmon行を読む。失敗時は空辞書。

    購読経由で読む（直読みはRXポンプと競合し空振りする）。M自体の
    即時応答（monitor on/off）で開閉を確定する。読み終えたらMを消す。
    serを持たない方式では空辞書を返す（壊さない）。
    """
    collected: list[str] = []
    try:
        subscribe = getattr(transport, "subscribe_rx", None)
        ser = getattr(transport, "ser", None)
        if not callable(subscribe) or ser is None:
            return {}
        unsub = subscribe(collected.append)
    except Exception:
        return {}
    turned_on = False
    try:
        for _ in range(max(1, int(tries))):
            try:
                ser.write(b"M\n")
            except Exception:
                return {}
            if poll_s > 0:
                time.sleep(poll_s)
            if _mon_state(collected) is True:
                turned_on = True
                break
        if settle_s > 0:
            time.sleep(settle_s)
        return _parse_mon_counts(collected)
    finally:
        try:
            unsub()
        except Exception:
            pass
        if turned_on:
            try:
                ser.write(b"M\n")
            except Exception:
                pass
