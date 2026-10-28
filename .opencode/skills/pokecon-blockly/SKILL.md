---
name: pokecon-blockly
description: Poke-Controller Modified の Blockly プログラムを、コンパクトな JSON 仕様または既存 workspace からヘッドレスに組み立て、画像認識の準備・照合・限定試行まで行って `.blockly.json` と `.py` の対として保存する手順。新しい Blockly マクロの作成、既存 Blockly 保存物の修正、画像認識・音声・サブルーチンを含む自動化の組み立てに使う。
---

# Poke-Controller Blockly

新しい Blockly マクロの作成、保存済み `.blockly.json` の部分編集、画像認識・音声・サブルーチンを含む自動化の組み立てに使う。

作業前に共通の [Blockly 作成・編集手順](../../../docs/blockly-agent/README.md) を読み、その権限境界・保存・検証手順に従う。
導入は [外部 AI の利用手順](../../../docs/AI_AUTOMATION.md)、構文・CLI・画像認識・作例は共通手順から必要なものだけ読む。
この skill に製品別の手順・作例は複製しない。

実画面の操作確認が必要な場合だけ、OpenCode 固有の任意補助として [gui-probe](../gui-probe/SKILL.md) を使える。
