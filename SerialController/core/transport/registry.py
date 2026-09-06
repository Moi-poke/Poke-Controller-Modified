#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""registry.py - 通信方式のプリセット登録簿。

なぜ登録簿を置くか:
  「運び方を差し替えられる」形だけでは、差し替えるのに呼び出し側が
  実装クラスを import して自分で組み立てる必要があった。
  利用者から見ると「選ぶ」ことができない。設定画面や起動引数から
    指定できるようにするには、名前と作り方の対応表が要る。

ここが持つのは「名前 → 作り方」だけ。実装そのものは持たない。
  利用者が自分で書いた Transport も register_transport で足せる
    （利用者定義）。本体側は中身を知らない。
"""

from __future__ import annotations

import os
import traceback
from collections.abc import Callable
from logging import getLogger
from typing import Any

from core.transport.base import LEGACY_ROW, VALID_CAPABILITIES, Transport
from core.transport.text_serial import PicoUartTransport, TextSerialTransport

# 既定のプリセット名。設定が無い・読めない・知らない名前のときはここへ戻す
DEFAULT_TRANSPORT = "legacy_text"

# 名前 → {"factory": 呼ぶと Transport を返すもの, "description": 説明,
#         "capability": 許可済み能力, "builtin": 最初から入っているか}
_REGISTRY: dict[str, dict[str, Any]] = {}


def register_transport(
    name: str,
    factory: Callable[..., Transport],
    description: str = "",
    builtin: bool = False,
    replace: bool = False,
) -> bool:
    """プリセットを登録する。登録できた場合は True を返す。

    name は設定画面や --transport で指定する鍵。factory は「呼ぶと
    Transport を返すもの」で、クラスそのものでよい。

    同じ名前が既にある場合、既定では上書きせず False を返す。利用者が自作を
    追加したつもりで本体側の実装を置き換えてしまう事故を防ぐためである。
    意図的に置き換える場合のみ replace=True を指定する。

    capability は未知値でも受け入れる。将来の接続方式（HID/BLE/TCP 等）が
    独自名を持てるようにするため。未知値は Sender が legacy 等価として扱い
    （同期 send_row、live worker なし）、worker が要る方式だけが
    LIVE_WORKER_CAPABILITIES へ名を連ねる。登録時の注記はファイル側
    （logger）のみ。利用者の次の行動が変わる失敗（resolve/create 時）は
    呼び出し側が GUI へ出す。
    """
    reg_log = getLogger(__name__)
    key = str(name).strip()
    if not key:
        reg_log.warning("プリセット名が空です。登録しませんでした。")
        return False
    # factory は注釈上 Callable だが、利用者プラグインの誤登録を実行時に
    #   弾く。注釈のまま callable() を書くと到達不能と見なされるため、
    #   Any を経由して判定する。検査自体は外さない。
    factory_maybe: Any = factory
    if not callable(factory_maybe):
        reg_log.warning(f"プリセット '{key}' の作り方が呼び出せません。")
        return False
    if key in _REGISTRY and not replace:
        reg_log.warning(
            f"プリセット '{key}' は既にあります（置き換えるなら replace=True）。"
        )
        return False
    capability = getattr(factory, "capability", LEGACY_ROW)
    if capability not in VALID_CAPABILITIES:
        # 未知 capability は弾かず受け入れる（将来の接続方式の余白）。
        # Sender は legacy 等価で動く。worker が要る方式は
        # LIVE_WORKER_CAPABILITIES へ加えること。
        reg_log.warning(
            f"プリセット '{key}' の capability '{capability}' は未知値ですが、"
            "legacy 等価として登録します。"
        )

    _REGISTRY[key] = {
        "factory": factory,
        "description": str(description),
        "capability": capability,
        "builtin": bool(builtin),
    }
    return True


def unregister_transport(name: str) -> bool:
    """プリセットを外す。組み込みは外せない（戻せなくなるため）。"""
    info = _REGISTRY.get(str(name))
    if info is None:
        return False
    if info.get("builtin"):
        getLogger(__name__).warning(f"'{name}' は組み込みなので外せません。")
        return False
    del _REGISTRY[str(name)]
    return True


def list_transports() -> list[str]:
    """登録されているプリセット名の一覧（設定画面の候補に使う）。"""
    return list(_REGISTRY.keys())


def describe_transports() -> list[tuple[str, str]]:
    """(名前, 説明) の一覧。画面へ出すときの並びはこの順。"""
    return [(k, v.get("description", "")) for k, v in _REGISTRY.items()]


def get_transport_info(name: str) -> dict[str, Any] | None:
    """1件ぶんの登録内容。無ければ None。"""
    info = _REGISTRY.get(str(name))
    return dict(info) if info is not None else None


def resolve_transport_name(name: str | None) -> str:
    """指定された名前を、実際に使える名前へ直す。

    知らない名前は黙って既定へ落とさない。理由を出してから落とす。
      設定ファイルの綴りを間違えたまま「なぜか従来どおり動く」と、
      指定が効いていないことに気づけない（静かに壊れる型）。
    利用者の次の行動が変わるため GUI＋ファイルの両方へ出す。
    """
    key = str(name).strip() if name is not None else ""
    if not key:
        return DEFAULT_TRANSPORT
    if key in _REGISTRY:
        return key
    msg = (
        f"通信方式 '{key}' は登録されていません。"
        f"既定の '{DEFAULT_TRANSPORT}' を使います。"
    )
    print(msg)
    getLogger(__name__).warning(msg)
    sub = "  使えるもの: " + ", ".join(list_transports())
    print(sub)
    getLogger(__name__).info(sub)
    return DEFAULT_TRANSPORT


def create_transport(name: str | None = None, logger: Any = None) -> Transport:
    """名前から運び方を1つ作る。設定画面・起動引数はここを通る。

    作れなかった場合も None を返さず、既定の実装を返す。ここで
      None が返ると呼び出し側が全部 None 検査を書く羽目になり、
      しかも「線が無い」状態はどこかで必ず落ちる。
    失敗は利用者の次の行動が変わるため GUI へ1行＋詳細はファイル側へ。
    """
    log = logger if logger is not None else getLogger(__name__)
    key = resolve_transport_name(name)
    info = _REGISTRY.get(key)
    if info is None:
        # 既定すら登録されていない（想定外）。最後の砦として直に作る
        return TextSerialTransport(logger=logger)
    try:
        try:
            return info["factory"](logger=logger)
        except TypeError:
            # logger を受け取らない作り方もある（利用者定義など）
            return info["factory"]()
    except Exception:
        msg = (
            f"通信方式 '{key}' を用意できませんでした。"
            f"既定の '{DEFAULT_TRANSPORT}' へ戻します。"
        )
        print(msg)
        try:
            log.error(msg + "\n" + traceback.format_exc())
        except Exception:
            getLogger(__name__).error(msg)
        if key != DEFAULT_TRANSPORT:
            return create_transport(DEFAULT_TRANSPORT, logger=logger)
        return TextSerialTransport(logger=logger)


def load_transport_plugins(dir_path: str) -> list[str]:
    """フォルダの .py を読み、利用者定義のプリセットを取り込む。

    各ファイルはモジュール直下に register(register_transport) を
      持つこと。呼ばれた側はその関数で自分の Transport を登録する。
      本体は中身を知らないまま、名前だけで選べるようになる。

    1本が壊れていても他は読む。読めなかった理由はファイル側へ出す。
      黙って飛ばすと「置いたのに出てこない」理由が分からない。
      GUI には出さない。起動時のプラグイン不備で利用者の次の行動は
      変わらない（組み込みで動く）ため。
    """
    added: list[str] = []
    if not dir_path or not os.path.isdir(dir_path):
        return added
    import importlib.util

    plug_log = getLogger(__name__)
    for filename in sorted(os.listdir(dir_path)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        full = os.path.join(dir_path, filename)
        try:
            spec = importlib.util.spec_from_file_location(
                "transport_plugin_" + filename[:-3], full
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            hook = getattr(module, "register", None)
            if not callable(hook):
                plug_log.warning(f"{filename} に register() がありません。")
                continue
            before = set(_REGISTRY.keys())
            hook(register_transport)
            added.extend(sorted(set(_REGISTRY.keys()) - before))
        except Exception:
            plug_log.error(
                f"{filename} を読み込めませんでした。\n{traceback.format_exc()}"
            )
    return added


# 組み込みプリセット。
register_transport(
    TextSerialTransport.name,
    TextSerialTransport,
    description="従来と同じテキスト行を pyserial で送る（本家 Leonardo 用）",
    builtin=True,
)

register_transport(
    PicoUartTransport.name,
    PicoUartTransport,
    description="Picoへfull-state S行を送る（有線=USBシリアル変換器 / 無線=PicoのUSB直結）",
    builtin=True,
)
