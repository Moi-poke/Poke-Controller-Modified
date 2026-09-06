#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自作スクリプト配布のCLI（梱包・検証・導入・削除・一覧）。

使い方（リポジトリ直下で）:
  uv run --frozen python SerialController/ScriptPackTool.py pack <配置DIR> <出すZIP>
  uv run --frozen python SerialController/ScriptPackTool.py check <ZIP>
  uv run --frozen python SerialController/ScriptPackTool.py install <ZIP> [--app-dir DIR] [--yes]
  uv run --frozen python SerialController/ScriptPackTool.py uninstall <名前> [--app-dir DIR] [--yes]
  uv run --frozen python SerialController/ScriptPackTool.py list [--app-dir DIR]

--app-dir を省いたら本ファイルの場所を使う。os.chdir はしない。
導入・削除の実行中ガードは GUI 側が担うため、CLI では注意文を出す。
"""

from __future__ import annotations

import argparse
import os
import sys

APP_DIR_DEFAULT = os.path.dirname(os.path.abspath(__file__))

if __name__ != "__main__":
    _cli_dir = os.path.dirname(os.path.abspath(__file__))
    if _cli_dir not in sys.path:
        sys.path.insert(0, _cli_dir)


def _cmd_pack(args: argparse.Namespace) -> int:
    from core import pack_zip

    try:
        names = pack_zip.create_pack(args.src_dir, args.out_zip)
    except (pack_zip.PackError, OSError) as e:
        print(f"梱包できません: {e}")
        return 1
    print(f"梱包しました: {args.out_zip}（{len(names)}件： manifestを含む）")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    import tempfile
    import zipfile
    from pathlib import Path

    from core import pack_zip

    with tempfile.TemporaryDirectory(prefix="pokecon_check_") as tmp:
        staged = Path(tmp) / "staged"
        staged.mkdir()
        try:
            pack_zip.safe_extract(args.zip, staged)
        except (pack_zip.PackError, OSError, zipfile.BadZipFile) as e:
            print(f"zip を開けません: {e}")
            return 1
        report = pack_zip.validate_staged(staged)
    if not report.ok or report.manifest is None:
        print("配布物が不正です:\n- " + "\n- ".join(report.errors))
        return 1
    print(f"OK: {report.manifest.name} 版{report.manifest.version}")
    for warning in report.warnings:
        print(f"注意: {warning}")
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    from services import script_pack

    print("注意: アプリが起動中・実行中なら先に止めてください（CLI では見張れません）")
    res = script_pack.install_zip(args.app_dir, args.zip, allow_overwrite=args.yes)
    if res.status == "confirm-overwrite":
        print(res.message)
        print("上書きするには --yes を付けてください")
        return 1
    print(res.message)
    for warning in res.warnings:
        print(f"注意: {warning}")
    return 0 if res.status == "installed" else 1


def _cmd_uninstall(args: argparse.Namespace) -> int:
    from services import script_pack

    if not args.yes:
        print(f"{args.name} を削除するには --yes を付けてください")
        return 1
    print("注意: アプリが起動中・実行中なら先に止めてください（CLI では見張れません）")
    res = script_pack.uninstall_package(args.app_dir, args.name)
    print(res.message)
    return 0 if res.status == "removed" else 1


def _cmd_list(args: argparse.Namespace) -> int:
    from services import script_pack

    records = script_pack.list_installed(args.app_dir)
    if not records:
        print("導入済みパッケージはありません")
        return 0
    for rec in records:
        print(f"{rec.name} 版{rec.version}（{rec.installed_at}）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="自作スクリプト配布の梱包・導入")
    sub = parser.add_subparsers(dest="command", required=True)
    pack = sub.add_parser("pack", help="配置フォルダからzipを作る")
    pack.add_argument("src_dir")
    pack.add_argument("out_zip")
    pack.set_defaults(func=_cmd_pack)
    check = sub.add_parser("check", help="zipを検証する")
    check.add_argument("zip")
    check.set_defaults(func=_cmd_check)
    install = sub.add_parser("install", help="zipを導入する")
    install.add_argument("zip")
    install.add_argument("--app-dir", default=APP_DIR_DEFAULT)
    install.add_argument("--yes", action="store_true")
    install.set_defaults(func=_cmd_install)
    uninstall = sub.add_parser("uninstall", help="導入済みを削除する")
    uninstall.add_argument("name")
    uninstall.add_argument("--app-dir", default=APP_DIR_DEFAULT)
    uninstall.add_argument("--yes", action="store_true")
    uninstall.set_defaults(func=_cmd_uninstall)
    listing = sub.add_parser("list", help="導入済みを列挙する")
    listing.add_argument("--app-dir", default=APP_DIR_DEFAULT)
    listing.set_defaults(func=_cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    _main_dir = os.path.dirname(os.path.abspath(__file__))
    if _main_dir not in sys.path:
        sys.path.insert(0, _main_dir)
    raise SystemExit(main())
