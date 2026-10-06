"""CommandHistory.py - コマンドの実行履歴（1 回の実行 = 1 行）.

CommandStats（回数 + 最終日時）とは別物で、こちらは実行のたびに
1 行ずつ残る一覧。履歴タブに新しい順で出し、選び直して再実行できる。

CommandStats と同じくプロファイル別 JSON に持ち、保存は終了時に
まとめて 1 回だけ行う。読み書きに失敗しても実行そのものは止めない。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

HISTORY_JSON = "Commands/command_history.json"

# 履歴の上限。古い方から捨てる。begin と load の両方で守る。
MAX_ENTRIES = 500

# 種別。runner が kind_of で決める。表示名は kind_label が付ける。
KIND_PYTHON = "python"
KIND_MCU = "mcu"

# 結果。実行中だけが特別（次回起動時に中断へ直す）で、あとは任意の str。
RESULT_RUNNING = "実行中"
RESULT_DONE = "完了"
RESULT_STOPPED = "手動停止"
RESULT_ERROR = "エラー"
RESULT_ABORTED = "中断"


@dataclass(slots=True)
class HistoryEntry:
    """履歴の 1 行。started はローカル時刻の "YYYY-MM-DD HH:MM:SS"。"""

    id: int
    kind: str
    name: str
    started: str
    seconds: float | None = None
    result: str = RESULT_RUNNING

    def to_dict(self) -> dict[str, Any]:
        """保存用の辞書へ変える。"""
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "started": self.started,
            "seconds": self.seconds,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, d: Any) -> HistoryEntry | None:
        """辞書から戻す。形が壊れていれば None（その 1 件だけ捨てる）。"""
        if not isinstance(d, dict):
            return None
        raw_id = d.get("id")
        raw_kind = d.get("kind")
        raw_name = d.get("name")
        raw_started = d.get("started")
        raw_result = d.get("result")
        # bool は int の派生だが id としては受けない。True が 1 になる
        # 混同を避けるため、型は厳密に見る。
        if type(raw_id) is not int:
            return None
        if not isinstance(raw_kind, str):
            return None
        if not isinstance(raw_name, str):
            return None
        if not isinstance(raw_started, str):
            return None
        if not isinstance(raw_result, str):
            return None
        raw_seconds = d.get("seconds", None)
        if raw_seconds is not None and (
            isinstance(raw_seconds, bool) or not isinstance(raw_seconds, (int, float))
        ):
            return None
        seconds = float(raw_seconds) if raw_seconds is not None else None
        return cls(
            id=raw_id,
            kind=raw_kind,
            name=raw_name,
            started=raw_started,
            seconds=seconds,
            result=raw_result,
        )


def from_dict(d: Any) -> HistoryEntry | None:
    """辞書から履歴の 1 行を戻す。形が壊れていれば None。

    HistoryEntry.from_dict への薄い入口。検証はそちらが持つ。
    """
    return HistoryEntry.from_dict(d)


def path_for(profile: str = "") -> str:
    """プロファイル名から履歴ファイルのパスを作る。

    CommandStats.pathFor と同じ規則にする。片方だけ別の規則にすると、
    プロファイルを増やしたときにどちらが対応しているのか分からなくなる。
    """
    if not profile:
        return HISTORY_JSON
    root, ext = os.path.splitext(HISTORY_JSON)
    return f"{root}.{profile}{ext}"


def load(profile: str = "") -> list[HistoryEntry]:
    """履歴を読む。無い・壊れている場合は空で返す。

    1 件ずつ検査し、不正な件だけ捨てる。実行中のまま残った件は、前回
    アプリが実行中に終わったものなので中断へ直す。id が重複していれば
    読み込み順に振り直す（選び直しの鍵がぶれないように）。
    """
    path = path_for(profile)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"実行履歴を読めませんでした: {e}")
        return []
    if not isinstance(data, list):
        logger.warning("実行履歴の形式が不正です。無視します。")
        return []

    entries: list[HistoryEntry] = []
    for raw in data:
        entry = HistoryEntry.from_dict(raw)
        if entry is None:
            continue
        if entry.result == RESULT_RUNNING:
            entry.result = RESULT_ABORTED
        entries.append(entry)
    entries = entries[:MAX_ENTRIES]
    # 重複があれば読み込み順に振り直す。並びは変えない。
    seen: set[int] = set()
    duplicated = False
    for entry in entries:
        if entry.id in seen:
            duplicated = True
            break
        seen.add(entry.id)
    if duplicated:
        for i, entry in enumerate(entries):
            entry.id = i + 1
    return entries


def save(entries: list[HistoryEntry], profile: str = "") -> bool:
    """履歴を書き出す。成否を返す。

    CommandStats.save と同じく一時ファイル + os.replace の原子書きに
    する。書いている途中で落ちても元のファイルが半端にならないため。
    """
    path = path_for(profile)
    directory = os.path.dirname(path)
    try:
        if directory:
            os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory or ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(
                [entry.to_dict() for entry in entries],
                fp,
                ensure_ascii=False,
                indent=2,
            )
        os.replace(tmp, path)
    except OSError as e:
        logger.warning(f"実行履歴を保存できませんでした: {e}")
        return False
    return True


def begin(
    entries: list[HistoryEntry], kind: str, name: str, now: datetime
) -> HistoryEntry:
    """実行の開始を先頭へ積む。作った件を返す。

    id は既存の最大 + 1（空なら 1）。消した分とぶつからないよう、
    件数ではなく最大値から決める。上限を超えた分は古い方から捨てる。
    """
    top = 0
    for entry in entries:
        if entry.id > top:
            top = entry.id
    entry = HistoryEntry(
        id=top + 1,
        kind=kind,
        name=name,
        started=now.strftime("%Y-%m-%d %H:%M:%S"),
    )
    entries.insert(0, entry)
    del entries[MAX_ENTRIES:]
    return entry


def finish(entry: HistoryEntry, result: str, seconds: float | None) -> None:
    """実行の終了を件へ書き込む。負の秒は 0.0 に直す。"""
    entry.result = result
    if seconds is None:
        entry.seconds = None
    elif seconds < 0:
        entry.seconds = 0.0
    else:
        entry.seconds = seconds


def remove(entries: list[HistoryEntry], ids: Iterable[int]) -> int:
    """該当 id を取り除き、消した件数を返す（その場で変更する）。"""
    wanted = set(ids)
    before = len(entries)
    entries[:] = [entry for entry in entries if entry.id not in wanted]
    return before - len(entries)


def clear(entries: list[HistoryEntry], keep: HistoryEntry | None = None) -> None:
    """全消去する（その場で変更する）。

    keep が渡され entries に含まれていれば、その 1 件だけ残す。
    実行中の行を消さないために使う。
    """
    if keep is not None and any(entry is keep for entry in entries):
        entries[:] = [keep]
    else:
        entries.clear()


def kind_label(kind: str) -> str:
    """種別の表示名。知らない種別はそのまま出す。"""
    if kind == KIND_PYTHON:
        return "Python"
    if kind == KIND_MCU:
        return "MCU"
    return kind


def matches(entry: HistoryEntry, query: str) -> bool:
    """絞り込みに当たるか。語は空白区切りで AND、大文字小文字は問わない。

    見るのは名前・結果・種別の表示名・開始日時の 4 つ。
    """
    words = query.split()
    if not words:
        return True
    haystacks = (
        entry.name.casefold(),
        entry.result.casefold(),
        kind_label(entry.kind).casefold(),
        entry.started.casefold(),
    )
    for word in words:
        folded = word.casefold()
        if not any(folded in hay for hay in haystacks):
            return False
    return True


def filtered(entries: list[HistoryEntry], query: str) -> list[HistoryEntry]:
    """並びを保ったまま絞り込む。"""
    return [entry for entry in entries if matches(entry, query)]


def format_duration(seconds: float | None) -> str:
    """所要時間の表示。None は空、切り捨てた整数秒で m:ss / h:mm:ss。"""
    if seconds is None:
        return ""
    total = max(0, int(seconds))
    if total < 3600:
        return f"{total // 60}:{total % 60:02d}"
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def sort_key(entry: HistoryEntry, column: str) -> tuple[Any, int]:
    """一覧の並べ替え鍵。第 2 要素は id（同値時の安定化）。

    未知の列は started 扱いにする。seconds が無い件は -1.0 として
    数値より前に出す。
    """
    if column == "kind":
        return (entry.kind.casefold(), entry.id)
    if column == "name":
        return (entry.name.casefold(), entry.id)
    if column == "result":
        return (entry.result.casefold(), entry.id)
    if column == "seconds":
        value = float(entry.seconds) if entry.seconds is not None else -1.0
        return (value, entry.id)
    return (entry.started, entry.id)
