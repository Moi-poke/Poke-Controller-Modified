"""CommandTags.py - コマンドのタグ管理.

Window.py から切り出した。切り出せたのは、この一連の処理が GUI の状態
（self.*）を一切参照していなかったため。入力（クラス・名前・上書き表）を
与えると出力（タグの一覧）が決まるだけの純粋な処理で、画面と混ぜておく
理由が無い。

タグの出どころは3つあり、優先順位は次のとおり。

    1. Commands/tags.json   … 画面や手編集で付け替える。最優先
    2. クラス属性 TAGS      … コマンドの作者が意図を書く
    3. 置き場所のフォルダ名 … 既存コマンドを書き換えずに分類する

JSON を最優先にしたのは、コードを触らずその場で直せる手段をいちばん
強くしておかないと、付け替えのたびにコード編集と再読み込みの往復が
要るため。
"""

from __future__ import annotations

import json
import os

from core import Utility as util
from loguru import logger

TAGS_JSON = "Commands/tags.json"
TAG_UNCLASSIFIED = "未分類"  # タグが1つも無いものへ必ず付ける
TAG_ALL = "すべて"  # 絞り込みの初期状態。これを既定にしないと既存分が消えて見える

# 置き場所からタグを拾うとき、分類として意味を持たない階層は捨てる。
SKIP_FOLDERS = frozenset({"Commands", "PythonCommands", "McuCommands"})


def commandName(cmd_class: type) -> str:
    """表示に使う名前。NAME が無いものはクラス名で代用する。"""
    return str(getattr(cmd_class, "NAME", "") or cmd_class.__name__)


def loadOverrides() -> dict[str, list[str]]:
    """Commands/tags.json があれば読む。無くても既定の動作を変えない。

    壊れた JSON でコマンド一覧そのものが出せなくなるのは割に合わないので、
    読めなければ警告だけ出して空で続ける。
    """
    path = util.ospath(TAGS_JSON)
    if not os.path.isfile(path):
        return {}

    try:
        with open(path, encoding="utf-8") as fp:
            raw = json.load(fp)
    except (OSError, ValueError) as exc:
        message = f"Failed to read {TAGS_JSON}: {exc}"
        print(message)
        logger.warning(message)
        return {}

    if not isinstance(raw, dict):
        message = f"{TAGS_JSON} must be an object of name -> tags."
        print(message)
        logger.warning(message)
        return {}

    overrides: dict[str, list[str]] = {}
    for name, tags in raw.items():
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, (list, tuple)):
            continue
        cleaned = [str(t).strip() for t in tags if str(t).strip()]
        if cleaned:
            overrides[str(name)] = cleaned
    return overrides


def saveOverrides(overrides: dict[str, list[str]]) -> bool:
    """Commands/tags.json へ書き出す。成否を返す。

    一時ファイルへ書いてから置き換える。途中で落ちても、元のファイルが
    半端な内容で残らない（settings.ini と同じ作法）。
    """
    path = util.ospath(TAGS_JSON)
    tmp = f"{path}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(overrides, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
        os.replace(tmp, path)
    except OSError as exc:
        message = f"Failed to write {TAGS_JSON}: {exc}"
        print(message)
        logger.error(message)
        return False

    message = f"Saved {TAGS_JSON} ({len(overrides)} entries)."
    print(message)
    logger.info(message)
    return True


def folderTags(cmd_class: type) -> list[str]:
    """置き場所のフォルダ名をタグとして拾う。

    既存のコマンドを1行も書き換えずにタグを付けられるのが利点。
    Commands/PythonCommands/剣盾/孵化.py なら「剣盾」を拾う。
    直下に置いたものは階層が無いので、ここでは何も返さない。
    """
    module = str(getattr(cmd_class, "__module__", ""))
    parts = [p for p in module.split(".") if p]
    # 末尾はモジュール名そのもの。その手前までが置き場所を表す。
    folders = parts[:-1]
    return [p for p in folders if p not in SKIP_FOLDERS]


def collectTags(
    cmd_class: type, name: str, overrides: dict[str, list[str]]
) -> list[str]:
    """3つの出どころを合成する。優先順位は JSON > クラス属性 > フォルダ。

    1つも無いものへ必ず「未分類」を付ける。ここを省くと、タグ機能を入れた
    瞬間に既存のコマンドが絞り込みから消えて「見つからない」になる。
    消えたことは画面に出ないので、原因を探しにくい。
    """
    if name in overrides:
        return list(overrides[name])

    tags: list[str] = []
    declared = getattr(cmd_class, "TAGS", ())
    if isinstance(declared, str):
        declared = (declared,)
    for tag in declared:
        text = str(tag).strip()
        if text and text not in tags:
            tags.append(text)

    for tag in folderTags(cmd_class):
        if tag not in tags:
            tags.append(tag)

    return tags or [TAG_UNCLASSIFIED]


def displayName(name: str, tags: list[str]) -> str:
    """タグを前置した表示名。並べ替えるとタグごとにまとまる。"""
    if not tags or tags == [TAG_UNCLASSIFIED]:
        return name
    return f"[{tags[0]}] {name}"


def findDuplicateNames(classes: list[type]) -> dict[str, list[type]]:
    """重複NAMEの診断。表示用の注意喚起であり、読み込み自体は変えない。

    `buildCommandMap` が見分け名で両方載せるのに対し、こちらは重複の
    有無だけを返す。loader/check 時の警告用。空なら重複なし。
    """
    by_name: dict[str, list[type]] = {}
    for cmd_class in classes:
        name = commandName(cmd_class)
        by_name.setdefault(name, []).append(cmd_class)
    return {k: v for k, v in by_name.items() if len(v) > 1}


def buildCommandMap(classes: list[type]) -> dict[str, type]:
    """NAME を鍵にした対応表を作る。同名は見分けを付けて両方載せる。

    同名を弾くと一覧から消える。消えたことは画面に出ないため「あるはず
    のものが無い」という探しにくい形になる。落とさずに全部載せる。

    見分けには所属モジュール名を添える。同名がある分だけ両方に添えるのが
    要点で、片方だけ素の名前のままにすると、どちらが素になるかが読み込み
    順で変わり、表示名が回ごとに入れ替わる。
    """
    names = [commandName(c) for c in classes]
    duplicated = {n for n in names if names.count(n) > 1}

    mapping: dict[str, type] = {}
    for cmd_class, name in zip(classes, names):
        if name in duplicated:
            module = str(cmd_class.__module__).rsplit(".", 1)[-1]
            name = f"{name} [{module}]"

        # 所属まで同じなら連番で分ける。ここで弾くと、同じファイルに
        # 同名クラスを置いた場合に片方が黙って消える。
        base_name = name
        serial = 2
        while name in mapping:
            name = f"{base_name} ({serial})"
            serial += 1

        mapping[name] = cmd_class
    return mapping


def sortTagChoices(tables: list[dict[str, list[str]]]) -> list[str]:
    """絞り込みに出すタグの並びを作る。先頭は必ず「すべて」。

    「未分類」は数が多くなりがちなので末尾へ回す。
    """
    tags: list[str] = []
    has_unclassified = False
    for table in tables:
        for names in table.values():
            for tag in names:
                if tag == TAG_UNCLASSIFIED:
                    has_unclassified = True
                elif tag not in tags:
                    tags.append(tag)

    tags.sort()
    if has_unclassified:
        tags.append(TAG_UNCLASSIFIED)
    return [TAG_ALL, *tags]
