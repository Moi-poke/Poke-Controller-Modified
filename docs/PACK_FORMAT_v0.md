# 配布書式 v0（pokecon.json）

zip内の配置：

- `pokecon.json`（本書式）
- `Commands/PythonCommands/<entry>`（自作スクリプト本体。実ツリーと同じ形）
- `Template/<name>/...`（テンプレ画像。素の名前は新規禁止）

`pokecon.json` の項目は `core/pack_manifest.py` の検査が正とする。
`entry`・`templates` は相対パスのみ（絶対・`..`・`:` 禁止）。
画像拡張子は `.png`/`.jpg`/`.jpeg`/`.bmp`。

検証： `uv run --frozen pytest tests/test_pack_manifest.py -q`

## zipの配置

- `pokecon.json`
- `Commands/PythonCommands/<entry>`（単一 `.py`。区切りは英字・数字・`_`のみ）
- `Template/<name>/...`

- 記録は `<APP_DIR>/InstalledPacks/<name>.json`、上書き前の写しは `<APP_DIR>/InstalledPacks/.backup/<name>_<日時>/`。
- 同版の再導入は失敗、異版は確認のうえ上書き。実行中はGUIが断る。
- CLI: `uv run --frozen python SerialController/ScriptPackTool.py <pack|check|install|uninstall|list> ...`（`--help` で詳細）。
