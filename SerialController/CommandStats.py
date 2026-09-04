"""CommandStats.py - コマンドの使用履歴（実行回数と最終実行日時）.

Window.py から切り出した。ここが持つのは「どのコマンドを何回・いつ
使ったか」の記録だけで、画面の状態は一切見ない。

置き場所を settings.ini と分けているのには理由がある。settings.ini は
Settings.py が tk 変数へバインドしている設定用で、コマンドを起動する
たびに書き換わるデータを混ぜると、設定画面の読み書きと競合する。
用途が違うものは同じ入れ物に入れない。

保存は終了時にまとめて1回だけ行う。1回の実行ごとに書きに行くと、
短いコマンドを連続で回したときにファイル入出力が積み上がる。
代わりに、実行中の値はメモリ上の辞書で持つ。

記録が壊れていても、または読めなくても、コマンド一覧そのものは
動かなければならない。読み書きは失敗しても警告だけ出して続ける。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

from loguru import logger

STATS_JSON = "Commands/command_stats.json"

# 「最近使った」とみなす件数。多すぎると絞り込みの意味が無くなる。
RECENT_MAX = 10

# 「よく使う」とみなす最低回数。1回きりのものが混ざると目印にならない。
FREQUENT_MIN = 3

# 絞り込みへ足す仮想的なタグ。実体のタグではないので、保存も編集も
# されない。コマンド側に何も書かなくても効くのが利点。
TAG_RECENT = "最近使った"
TAG_FREQUENT = "よく使う"


def pathFor(profile: str = "") -> str:
    """プロファイル名から使用履歴ファイルのパスを作る。

    並列起動している台どうしで同じファイルを読み書きすると、後から
    終了した側の内容で丸ごと上書きされ、もう一方の記録が消える
    （書き込みが os.replace による全体差し替えのため、追記にならない）。

    分け方は settings ファイルと同じ規則にする。片方だけ別の規則に
    すると、プロファイルを増やしたときにどちらが対応しているのか
    分からなくなる。profile が空なら従来どおりのパスを返す。
    """
    if not profile:
        return STATS_JSON
    root, ext = os.path.splitext(STATS_JSON)
    return f"{root}.{profile}{ext}"


def load(profile: str = "") -> dict[str, dict]:
    """使用履歴を読む。無い・壊れている場合は空で返す。

    履歴が読めないことでコマンド一覧が止まるのは割に合わない。
    どんな失敗でも空を返し、警告だけ残して続ける。
    """
    path = pathFor(profile)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"使用履歴を読めませんでした: {e}")
        return {}
    if not isinstance(data, dict):
        logger.warning("使用履歴の形式が不正です。無視します。")
        return {}

    # 1件ずつ検査する。壊れた1件で全体を捨てると、他の記録まで失う。
    stats: dict[str, dict] = {}
    for name, rec in data.items():
        if not isinstance(name, str) or not isinstance(rec, dict):
            continue
        count = rec.get("count", 0)
        if not isinstance(count, int) or count < 0:
            count = 0
        last = rec.get("last", "")
        if not isinstance(last, str):
            last = ""
        stats[name] = {"count": count, "last": last}
    return stats


def save(stats: dict[str, dict], profile: str = "") -> bool:
    """使用履歴を書き出す。成否を返す。

    一時ファイルへ書いてから置換する。書いている途中で落ちても、
    元のファイルが半端な内容になって次回まるごと読めなくなる、
    という壊れ方を避けるため。
    """
    path = pathFor(profile)
    directory = os.path.dirname(path)
    try:
        if directory:
            os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory or ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(stats, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning(f"使用履歴を保存できませんでした: {e}")
        return False
    return True


def record(stats: dict[str, dict], name: str) -> None:
    """1回ぶんの実行を数える。書き出しはしない（終了時にまとめる）。"""
    if not name:
        return
    rec = stats.get(name)
    if rec is None:
        rec = {"count": 0, "last": ""}
        stats[name] = rec
    rec["count"] = int(rec.get("count", 0)) + 1
    rec["last"] = datetime.now().isoformat(timespec="seconds")


def usedCount(stats: dict[str, dict], name: str) -> int:
    """実行回数を返す。記録が無ければ 0。"""
    rec = stats.get(name)
    return int(rec.get("count", 0)) if isinstance(rec, dict) else 0


def lastUsed(stats: dict[str, dict], name: str) -> str:
    """最終実行日時（ISO 文字列）を返す。記録が無ければ空。"""
    rec = stats.get(name)
    return str(rec.get("last", "")) if isinstance(rec, dict) else ""


def formatLast(iso: str) -> str:
    """ISO 文字列を "2026/08/11 18:42" の形にする。

    記録は並べ替えのため ISO で持つが、そのままでは桁が多くて読みにくい。
    秒は実行の目印には要らないので落とす。解釈できない値は空を返し、
    表示側で「記録なし」と同じ扱いにする（例外で画面を止めない）。
    """
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%Y/%m/%d %H:%M")
    except ValueError:
        logger.debug(f"日時として読めない記録: {iso}")
        return ""


def summary(stats: dict[str, dict], name: str) -> str:
    """一覧へ添える "前回 2026/08/11 18:42 / 5回" を作る。

    一度も実行していないものには何も付けない。空欄で「まだ使って
    いない」ことが分かるし、全件に付けると名前が読みにくくなる。
    """
    count = usedCount(stats, name)
    if count <= 0:
        return ""
    when = formatLast(lastUsed(stats, name))
    if not when:
        return f"{count}回"
    return f"前回 {when} / {count}回"


def recentNames(stats: dict[str, dict], limit: int = RECENT_MAX) -> list[str]:
    """最近使った順の名前を返す。

    日時は ISO 文字列で持っているので、文字列のまま比較して正しく並ぶ
    （桁が揃っているため）。日時を持たない記録は対象にしない。
    """
    dated = [(v.get("last", ""), k) for k, v in stats.items() if v.get("last")]
    dated.sort(reverse=True)
    return [name for _last, name in dated[:limit]]


def frequentNames(stats: dict[str, dict], least: int = FREQUENT_MIN) -> list[str]:
    """よく使う名前を回数の多い順に返す。

    1回しか使っていないものまで含めると「よく使う」の意味が無くなる
    ので、下限を設ける。同数のときは名前順にして並びを安定させる。
    """
    hot = [(v.get("count", 0), k) for k, v in stats.items()
           if int(v.get("count", 0)) >= least]
    hot.sort(key=lambda x: (-x[0], x[1]))
    return [name for _count, name in hot]


def virtualTags(stats: dict[str, dict], name: str) -> list[str]:
    """その名前に付く仮想タグを返す。

    「最近使った」「よく使う」は保存されるタグではなく、履歴から
    毎回決まるもの。判定をここへ寄せておかないと、絞り込む側と
    表示する側で条件がずれる（片方だけ直す事故の温床になる）。
    """
    tags = []
    if name in recentNames(stats):
        tags.append(TAG_RECENT)
    if usedCount(stats, name) >= FREQUENT_MIN:
        tags.append(TAG_FREQUENT)
    return tags


def choices(stats: dict[str, dict]) -> list[str]:
    """絞り込みへ足す仮想タグの一覧を返す。該当が無ければ出さない。

    中身が空の選択肢を出すと、選んでも0件になるだけで意味がない。
    """
    result = []
    if recentNames(stats):
        result.append(TAG_RECENT)
    if frequentNames(stats):
        result.append(TAG_FREQUENT)
    return result
