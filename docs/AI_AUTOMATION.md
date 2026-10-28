# 外部 AI でスクリプトを作る

## 利用モデル

利用者が自分のリポジトリ clone を Codex、Claude Code などの外部エージェントのワークスペースとして開き、既存の Blockly CLI を使う。
PokeCon への LLM 組込み、MCP、利用者ごとの skill の別途インストールは不要。
生成した作品は通常の Blockly エディタで開いて編集できる。

## 準備

通常のインストールは [README の Installation](../README.md#installation) を参照する。
追加で PATH 上の `node` と、リポジトリ指定の Python / uv 環境が必要。
リポジトリルートで次を順に実行する（仮想環境の activate は不要）。

```text
uv sync --frozen
node --version
uv run --frozen python tools/blockly_agent.py --help
```

全ファイルを一括で読む必要はない。
共通入口から、作業に必要な構文・作例・実装だけを読む。
リポジトリを読めたこと自体は、生成や実行の検証成功ではない。

## エージェントの入口

- Codex は [AGENTS.md](../AGENTS.md) から規約と作業手順を辿る（[公式ガイド](https://developers.openai.com/codex/guides/agents-md)）。
- Claude Code は互換用の [CLAUDE.md](../CLAUDE.md) の `@AGENTS.md` import から同じ規約へ進める。現行版の AGENTS.md 対応と import は [公式 memory 資料](https://code.claude.com/docs/en/memory#agents-md)・[追加ファイルの import](https://code.claude.com/docs/en/memory#import-additional-files) を参照する。
- OpenCode は AGENTS.md と薄い `pokecon-blockly` skill から、同じ [Blockly 手順](blockly-agent/README.md) を読む。

製品の版・設定によって自動読込みの条件は異なる。
自動読込みを使わないクライアントでは、次を依頼文の先頭に置く。
権限設定を勝手に変更せず、必要な権限がなければ不足を報告する。

```text
まず AGENTS.md と docs/AI_AUTOMATION.md を読み、Blockly 作業は docs/blockly-agent/README.md に従ってください。
```

## 依頼例

- 「A を 3 回押す作品を新規作成してください。実機への入力はせず、検査して保存してください。」
- 「既存の MyBlock.blockly.json の指定した待ちだけを 2.5 秒にしてください。ID・配置を保持し、dry-run と版検査を通して保存してください。」
- 「開いたエディタの指定位置へ待ちブロックを追加してください。対象 URL はこれです。ライブ編集を使い、保存は私が行います。」

ライブ編集・実画像取得・実機試行には、その clone の本体と対象プロファイルの Blockly エディタを利用者が開く。
エディタ起動ダイアログの「URLをコピー」で URL を取り出し、パスを除いた origin（`http://127.0.0.1:<port>` など）を `--server` に渡す。
固定ポートを仮定せず、他プロファイルを探索したり LAN に公開したりしない。
試行 preview は本体へ接続せず、実機への入力は別途、コード・対象 URL・上限秒数の明示許可が必要。

設定・lock・Discord 設定・私有の撮影画像を共有する必要はない。
同梱の `SerialController/Template/` と利用者が追加した画像を区別し、共有前に対象を確認する。
起動中の設定ファイル手編集は避ける（終了時に本体の値で上書きされる）。

## 用途別リンク

- [Blockly の作成・編集手順](blockly-agent/README.md)
- [コンパクト仕様](blockly-agent/references/spec.md) / [CLI 契約](blockly-agent/references/cli.md) / [作例](blockly-agent/examples/)
- [実画像からの認識準備と限定試行](blockly-agent/references/vision.md)
- [人向けエディタ操作](BLOCKLY_EDITOR.md) / [検証範囲](BLOCKLY_AGENT_VERIFICATION.md)
