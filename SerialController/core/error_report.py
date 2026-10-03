"""error_report.py - 不具合報告の文面組み立て.

GUI から切り離した純粋寄りの手順。ここが持つのは「貼り付け用の
テキストを作る」だけで、画面（tkinter）も設定の中身も見ない。
設定ファイルや webhook のような秘密は引数に取らず、読みにも行かない。
ログは最新ファイルの末尾だけに絞る（全文だと重く、個人情報も増える）。
"""

from __future__ import annotations

import datetime as dt
import glob
import os

from loguru import logger

# ログ置き場の名前。PokeConLogger が作る ../log と合わせる。
LOG_DIRNAME = "log"
# 報告に載せる末尾の上限。全文は重いのでここで打ち切る。
TAIL_LINES = 200
TAIL_MAX_BYTES = 50 * 1024
# 統計行を省く前に読む量。実ログは 6 割超が 5 秒ごとの映像統計なので、
# 省いた後に TAIL_LINES 行が残るよう多めに読む。
SCAN_LINES = 2000
SCAN_MAX_BYTES = 400 * 1024
# 定期的に出る統計行の目印（ログの「モジュール 関数」の部分）。最新の 1 行
# だけ残せば、カメラが映らない不具合も「取込 0fps」で分かる。
VIDEO_STATS_MARKER = "_log_video_stats"


def resolve_log_dir(app_dir: str | None = None) -> str:
    """ログ置き場の絶対パスを返す。cwd は見ない。

    app_dir は SerialController/ の絶対のはず（WindowUtils.APP_DIR と
    同じ物）。渡されなければこのファイルの位置から求める。ログは
    その1つ上（リポジトリ直下）の log/ にある。
    """
    if app_dir is None:
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = os.path.abspath(app_dir)
    return os.path.normpath(os.path.join(os.path.dirname(base), LOG_DIRNAME))


def latest_log_file(log_dir: str | None = None) -> str | None:
    """最新の log_*.log を返す。無ければ None。落ちない。"""
    if log_dir is None:
        log_dir = resolve_log_dir()
    try:
        if not os.path.isdir(log_dir):
            return None
        candidates = glob.glob(os.path.join(log_dir, "log_*.log"))
        if not candidates:
            return None
        return max(candidates, key=lambda p: os.path.getmtime(p))
    except OSError as e:
        logger.warning(f"ログ一覧を取れませんでした: {e}")
        return None


def read_tail_lines(
    path: str, max_lines: int = TAIL_LINES, max_bytes: int = TAIL_MAX_BYTES
) -> list[str]:
    """末尾だけを読む。無い・読めない物は空で返す。

    大きな1行を溜め込まないよう、後ろの max_bytes だけ読んでから
    行に割り、さらに後ろの max_lines だけ残す。
    """
    try:
        with open(path, "rb") as f:
            try:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - max_bytes), os.SEEK_SET)
                raw = f.read(max_bytes + 1)
            except OSError:
                raw = f.read()
        text = raw.decode("utf-8", errors="replace")
    except OSError as e:
        logger.debug(f"ログ末尾を読めませんでした: {e}")
        return []
    lines = text.splitlines()
    lines = lines[-max_lines:]
    # バイト上限を超えていたら前から落とす。
    while len(lines) > 1 and len("\n".join(lines).encode("utf-8")) > max_bytes:
        lines = lines[1:]
    return lines


def condense_tail(lines: list[str], max_lines: int = TAIL_LINES) -> list[str]:
    """定期的な映像統計を最新の 1 行に畳み、末尾 max_lines 行に切る。

    統計に押し出されて、肝心の例外や操作の行が報告から消えるのを防ぐ。
    省いた行数は 1 行書き添える（隠したことが読み手に分かるように）。
    """
    stats = [i for i, line in enumerate(lines) if VIDEO_STATS_MARKER in line]
    if len(stats) <= 1:
        return lines[-max_lines:]
    latest = stats[-1]
    dropped = set(stats[:-1])
    kept = [line for i, line in enumerate(lines) if i not in dropped]
    note = f"(映像の統計 {len(dropped)} 行を省略。最新の 1 行だけ残しています)"
    kept = kept[-(max_lines - 1) :]
    # 最新の統計行は位置ごと残っている。省略の注記はその直前に置く。
    position = kept.index(lines[latest]) if lines[latest] in kept else 0
    return kept[:position] + [note] + kept[position:]


def build_report(
    app_version: str,
    os_name: str,
    python_version: str,
    profile: str,
    transport: str,
    error_text: str,
    log_path: str | None,
    log_tail: list[str] | str,
) -> str:
    """貼り付け用の報告文を作る。秘密は混ぜない。

    settings.ini や webhook は読まない・受け取らない。載せるのは
    環境の要点＋例外文＋ログ末尾だけにする。
    """
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(log_tail, str):
        tail_text = log_tail
    else:
        tail_text = "\n".join(log_tail) if log_tail else "(ログ末尾なし)"
    log_name = log_path if log_path else "(ログファイルなし)"
    lines = [
        "## 不具合報告",
        "",
        f"- 日時: {now}",
        f"- バージョン: {app_version}",
        f"- OS: {os_name}",
        f"- Python: {python_version}",
        f"- プロファイル: {profile if profile else '(既定)'}",
        f"- 通信方式: {transport if transport else '(未設定)'}",
        f"- ログファイル: {log_name}",
        "",
        "### 再現手順",
        "1. ",
        "2. ",
        "",
        "### エラー内容",
        "```",
        error_text.strip() if error_text.strip() else "(エラー文なし)",
        "```",
        "",
        "### ログ末尾",
        "```",
        tail_text,
        "```",
    ]
    return "\n".join(lines) + "\n"
