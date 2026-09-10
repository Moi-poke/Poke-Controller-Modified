# 配布書式 v0（pokecon.json。`format`欄は将来の判別用に予約、現行は無くてもv0扱い）

zip内の配置：

- `pokecon.json`（本書式）
- `Commands/PythonCommands/<entry>`（自作スクリプト本体。実ツリーと同じ形）
- `Template/<name>/...`（テンプレ画像。素の名前は新規禁止）

`pokecon.json` の項目は `core/pack_manifest.py` の検査が正とする。
`entry`・`templates` は相対パスのみ（絶対・`..`・`:` 禁止）。
`entry` のフォルダ名・ファイル名（拡張子除く）は英字・数字・`_`のみ（例: `my_pack/MyPack.py`、`my-pack/`は不可）。
画像拡張子は `.png`/`.jpg`/`.jpeg`/`.bmp`。
entryのimport走査は相対・公開面外を異常、未知トップレベルを注意に留める（正本は`core/user_api_allowlist.py`）。

検証： `uv run --frozen pytest tests/test_pack_manifest.py -q`

## zipの配置

- `pokecon.json`
- `Commands/PythonCommands/<entry>`（単一 `.py`。区切りは英字・数字・`_`のみ）
- `Template/<name>/...`

- 記録は `<APP_DIR>/InstalledPacks/<name>.json`、上書き前の写しは `<APP_DIR>/InstalledPacks/.backup/<name>_<日時>/`。
- 同版の再導入は失敗、異版は確認のうえ上書き。実行中はGUIが断る。
- CLI: `uv run --frozen python SerialController/ScriptPackTool.py <pack|check|install|uninstall|list> ...`（`--help` で詳細）。
