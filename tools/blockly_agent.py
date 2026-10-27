#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blockly の authoring CLI（catalog / check / build / render）。

エージェントがコンパクト仕様から `.blockly.json` / `.py` の対を作るための
入り口。既定のアプリディレクトリは `SerialController/` で、`--app-dir` に
より差し替えられる（テスト用）。終了コードは 0=成功、1=検査失敗、
2=使い方の誤り / node 不在。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_DIR = ROOT / "SerialController"
if str(CONTROLLER_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROLLER_DIR))

from core import blockly_headless  # noqa: E402
from services import blockly_author, blockly_templates  # noqa: E402

EXIT_OK = 0
EXIT_CHECK = 1
EXIT_USAGE = 2


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--app-dir",
        default=None,
        help="アプリのディレクトリ（既定: リポジトリの SerialController/）",
    )
    parser = argparse.ArgumentParser(
        prog="blockly_agent.py",
        description="Blockly のコンパクト仕様から保存物を作る",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    catalog = sub.add_parser(
        "catalog",
        parents=[common],
        help="ブロックの fields / inputs 一覧を出す",
    )
    catalog.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="出力形式（既定: json）",
    )
    catalog.add_argument(
        "--output",
        default=None,
        help="書き出すパス（省略時は標準出力）",
    )

    check = sub.add_parser(
        "check", parents=[common], help="仕様を組み立てて保存前検査を通す"
    )
    check.add_argument("spec", help="コンパクト仕様の JSON ファイル")
    check.add_argument("--json", action="store_true", help="結果を JSON で出す")

    build = sub.add_parser(
        "build", parents=[common], help="仕様から workspace JSON を作る"
    )
    build.add_argument("spec", help="コンパクト仕様の JSON ファイル")
    build.add_argument(
        "--write",
        action="store_true",
        help="検査後に .py / .blockly.json の対を保存する",
    )
    build.add_argument("--stem", default=None, help="保存名（仕様の stem を上書き）")
    build.add_argument("--json", action="store_true", help="結果を JSON で出す")

    render = sub.add_parser(
        "render", parents=[common], help="workspace JSON から Python を生成する"
    )
    render.add_argument("workspace", help="Blockly の workspace JSON ファイル")
    render.add_argument("--json", action="store_true", help="結果を JSON で出す")

    return parser


def _app_dir(args: argparse.Namespace) -> Path:
    value = getattr(args, "app_dir", None)
    return Path(value).resolve() if value else CONTROLLER_DIR


def _read_json(path: str) -> tuple[Any | None, str | None]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except OSError as e:
        return None, f"ファイルを読めません: {path}（{e}）"
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, f"JSON を読めません: {path}（{e}）"


def _print_json(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _print_errors(errors: list[str], warnings: list[str]) -> None:
    for warning in warnings:
        sys.stderr.write(f"注意: {warning}\n")
    for error in errors:
        sys.stderr.write(f"- {error}\n")


def _node_missing(errors: list[str]) -> bool:
    return any(blockly_headless.NODE_MISSING_MESSAGE in error for error in errors)


def _failure_exit(errors: list[str]) -> int:
    return EXIT_USAGE if _node_missing(errors) else EXIT_CHECK


def _cmd_catalog(args: argparse.Namespace) -> int:
    app_dir = _app_dir(args)
    result = blockly_headless.catalog(app_dir)
    if not result.ok or result.data is None:
        _print_errors(result.errors or ["カタログを取得できませんでした"], [])
        return _failure_exit(result.errors)
    if args.format == "markdown":
        templates = blockly_templates.list_image_templates(app_dir)
        text = blockly_author.catalog_markdown(result.data, templates)
    else:
        text = json.dumps(result.data, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target = Path(args.output)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", newline="\n") as fp:
                fp.write(text)
        except OSError as e:
            sys.stderr.write(f"書き出せません: {target}（{e}）\n")
            return EXIT_CHECK
        sys.stderr.write(f"書き出しました: {target}\n")
    else:
        sys.stdout.write(text)
    return EXIT_OK


def _cmd_check(args: argparse.Namespace) -> int:
    spec, error = _read_json(args.spec)
    if error is not None:
        sys.stderr.write(error + "\n")
        return EXIT_USAGE
    result = blockly_author.check_spec(spec, _app_dir(args))
    if args.json:
        _print_json(
            {
                "ok": result.ok,
                "errors": result.errors,
                "warnings": result.warnings,
                "stem": result.stem,
                "workspace": result.workspace,
                "code": result.code,
            }
        )
    elif result.ok:
        sys.stdout.write(
            "検査に通りました" + (f": {result.stem}" if result.stem else "") + "\n"
        )
        for warning in result.warnings:
            sys.stderr.write(f"注意: {warning}\n")
    else:
        _print_errors(result.errors, result.warnings)
    return EXIT_OK if result.ok else _failure_exit(result.errors)


def _cmd_build(args: argparse.Namespace) -> int:
    spec, error = _read_json(args.spec)
    if error is not None:
        sys.stderr.write(error + "\n")
        return EXIT_USAGE
    app_dir = _app_dir(args)
    if args.write:
        stem = args.stem
        if (
            stem is None
            and isinstance(spec, dict)
            and isinstance(spec.get("stem"), str)
        ):
            stem = spec["stem"]
        if not isinstance(stem, str) or not stem.strip():
            sys.stderr.write(
                "保存名（stem）を書いてください（仕様の stem か --stem）\n"
            )
            return EXIT_USAGE
        result = blockly_author.save_spec(spec, app_dir, stem)
        if args.json:
            _print_json(
                {
                    "ok": result.status == "saved",
                    "status": result.status,
                    "message": result.message,
                    "py": result.py_rel,
                    "json": result.json_rel,
                    "errors": result.errors,
                    "warnings": result.warnings,
                }
            )
        else:
            stream = sys.stdout if result.status == "saved" else sys.stderr
            stream.write(result.message + "\n")
        return EXIT_OK if result.status == "saved" else _failure_exit(result.errors)

    catalog = blockly_headless.catalog(app_dir)
    if not catalog.ok or catalog.data is None:
        _print_errors(catalog.errors or ["カタログを取得できませんでした"], [])
        return _failure_exit(catalog.errors)
    built = blockly_author.build_workspace(spec, catalog.data)
    if not built.ok or built.workspace is None:
        _print_errors(built.errors, [*catalog.warnings, *built.warnings])
        return EXIT_CHECK
    if args.json:
        _print_json(
            {
                "ok": True,
                "workspace": built.workspace,
                "warnings": [*catalog.warnings, *built.warnings],
            }
        )
    else:
        sys.stdout.write(
            json.dumps(built.workspace, ensure_ascii=False, indent=2) + "\n"
        )
    return EXIT_OK


def _cmd_render(args: argparse.Namespace) -> int:
    workspace, error = _read_json(args.workspace)
    if error is not None:
        sys.stderr.write(error + "\n")
        return EXIT_USAGE
    result = blockly_author.render_workspace(workspace, _app_dir(args))
    if args.json:
        _print_json(
            {
                "ok": result.ok,
                "code": result.code,
                "errors": result.errors,
                "warnings": result.warnings,
            }
        )
    elif result.ok:
        sys.stdout.write(result.code)
    else:
        _print_errors(result.errors, result.warnings)
    return EXIT_OK if result.ok else _failure_exit(result.errors)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "catalog":
        return _cmd_catalog(args)
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "build":
        return _cmd_build(args)
    if args.command == "render":
        return _cmd_render(args)
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
