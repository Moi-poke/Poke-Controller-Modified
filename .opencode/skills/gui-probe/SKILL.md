---
name: gui-probe
description: Poke-Controller Modified の tkinter GUI を、実機シリアル/カメラを掴まずに起動してスクリーンショットとウィジェット geometry を .tmp/gui-probe/ に保存する手順。レイアウト、タブ表示、ラベル、ツールチップ、スクロールバーの実画面確認に使う。Windows のデスクトップセッション専用。
---

# GUI probe

「実画面を確認したい」「スクショで見せて」と言われたとき、または UI のレイアウト・表示条件を変えたときに使う。ヘッドレスでは確認できない見た目だけを対象にする。

## 手順

1. probe 用の設定を作る。既存の `SerialController/settings.ini` か `settings.<profile>.ini` をコピーして `SerialController/settings.probe.ini` にする。次のキーを変える。
   - `com_port = 99`
   - `com_port_name = COM99`
   - `is_show_realtime = False`
   - `camera_id = 99`（カメラが開く場合がある。開いても数秒で終了する）
   `settings*.ini` は終了時に上書きされるので、実プロファイルは使わない。
2. `.tmp/gui-probe/probe.py` を作る（下の雛形）。出力先は `.tmp/gui-probe/<時刻>/`。`%TEMP%` は venv の Python から書けない環境があるので使わない。
3. 実行する。Windows の Git Bash なら:
   ```bash
   timeout 15 .venv/Scripts/python.exe .tmp/gui-probe/probe.py || true
   ```
   `timeout` が 124 を返しても、`report.json` と `window.png` ができていれば成功。GUI プロセスが残って待たされることがあるため、終了コードだけでは判断しない。
4. 最新の `.tmp/gui-probe/*/` を開き、`report.json` の `geometry` / `title` と `window.png` を確認する。崩れ（切れ、重なり、不要なスクロールバー、英語のままの表示、ツールチップの実装メモ）を、画像の座標とともに報告する。
5. `SerialController/settings.probe.ini` を消す。`.tmp/gui-probe/` はコミットしない。

## 雛形

```python
import ctypes
import json
import os
import sys
import time
from pathlib import Path

ctypes.windll.user32.SetProcessDPIAware()
BASE = Path(r"C:\PokeCon\Poke-Controller-Modified")
SC = BASE / "SerialController"
sys.path.insert(0, str(SC))
os.chdir(SC)

import WindowUtils

WindowUtils.listComPorts = lambda: []  # 実ポートへフォールバックさせない

from PIL import ImageGrab
from Window import PokeControllerApp

outdir = BASE / ".tmp" / "gui-probe" / time.strftime("%Y%m%d-%H%M%S")
outdir.mkdir(parents=True, exist_ok=True)

app = PokeControllerApp(profile="probe")
root = app.root
for _ in range(20):
    root.update_idletasks()
    root.update()
    time.sleep(0.1)
time.sleep(1.0)
root.update()
x, y, w, h = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
shot = outdir / "window.png"
ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(shot)
(outdir / "report.json").write_text(
    json.dumps({"shot": str(shot), "geometry": [x, y, w, h], "title": root.title()}),
    encoding="utf-8",
)
os._exit(0)
```

`print` は GUI のログ欄へリダイレクトされて消えるので、結果は `report.json` に書く。

## 落とし穴

- `app.exit()` は確認ダイアログを出すので probe では呼ばない。`root.destroy()` はカメラ/音声スレッドでぶら下がることがある。撮影後は `os._exit(0)` で終える。
- それでも `timeout` が 124 を返すことがある（GUI 側のプロセスが残る）。成果物の有無で判断する。
- `WindowUtils.listComPorts` を空にしないと、COM99 が一覧に無いため先頭の実ポート（COM1 など）へフォールバックして接続する。probe の先頭で `listComPorts = lambda: []` にする。
- カメラは `is_show_realtime=False` でも開くことがある。実機のキャプチャボードを掴んでいる間は実行しない。
- 別インスタンスが動いていると `pokecon*.lock` で落ちる。probe は専用プロファイルで起動する。
- 画像だけで判断できないもの（ホバー中のツールチップ、キーボードフォーカス）は「未確認」と書く。
