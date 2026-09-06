"""services - GUI 非依存の利用者層（core の使い方を束ねる）。

core/ が純粋ロジック、services/ がその組み立てと手順、
ui 側（Window.py）が表示と tk 変数の読み書きを受け持つ。
services/ は tkinter を import しない（tools/check_core.py で検査）。
"""
