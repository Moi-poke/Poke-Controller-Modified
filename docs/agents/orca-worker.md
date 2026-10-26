# Orca 作業役（worker）への追加規律

Orca の統括役から `docs/tasks/Txx.md` 指示書を受け取って動く場合だけ適用する（通常開発では無視してよい）。

- 指示書の「編集してよいファイル」以外は変更しない。迷ったら推測で実装せず、Orca の escalation（統括役への質問）で止める。
- 指示書を受け取ったら、まず `/goal` に完了条件を設定する（goal プラグインは `opencode.json` の `plugins` で有効化済み）。
- 完了条件のコマンド（`task ci` 等）がすべて成功するまで完了にしない。実機が必要で検証できない場合は `blocked` にして理由を書く。
- 完了したら変更をコミットし（履歴の書き換え・未コミット作業の破棄は禁止）、`worker_done` を必ず1回送る（`--outcome succeeded|failed`、`--files-modified`、`--report-path` 付き、要約3文＋`path:line` 証拠）。
- 既定エージェント（`build`）のまま作業し、`coordinator` 等へ切り替えない。worktree・ブランチの作成は Orca に任せ、自分では作らない。
