#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blockly の authoring CLI（catalog / check / build / render と画像補助）。

`frame` / `template` / `match` / `color` は編集器の目視確認向けの補助で、
`/frame` の取得・テンプレ切出し・照合・色割合を GUI 無しで行う。判定は
`services/` 側の実装（照合と色割合）にそのまま委ね、ここでは入出力だけを
面倒みる。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_DIR = ROOT / "SerialController"
if str(CONTROLLER_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROLLER_DIR))

from core import blockly_headless  # noqa: E402
from services import blockly_author, blockly_save, blockly_templates  # noqa: E402

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
    check.add_argument(
        "spec", help="コンパクト仕様、または --workspace 時の workspace JSON"
    )
    check.add_argument(
        "--workspace",
        action="store_true",
        help="位置引数を保存済み workspace JSON として検査する（仕様へ逆変換しない）",
    )
    check.add_argument("--json", action="store_true", help="結果を JSON で出す")

    build = sub.add_parser(
        "build", parents=[common], help="仕様から workspace JSON を作る"
    )
    build.add_argument(
        "spec", help="コンパクト仕様、または --workspace 時の workspace JSON"
    )
    build.add_argument(
        "--workspace",
        action="store_true",
        help="位置引数を保存済み workspace JSON として扱う（ID・配置を保持する）",
    )
    build.add_argument(
        "--write",
        action="store_true",
        help="検査後に .py / .blockly.json の対を保存する",
    )
    build.add_argument("--stem", default=None, help="保存名（仕様の stem を上書き）")
    build.add_argument(
        "--overwrite-external",
        action="store_true",
        help="手編集を検出した .py を上書きする（ユーザーの明示許可時だけ）",
    )
    build.add_argument(
        "--expect-current-sha256",
        default=None,
        help="保存済みworkspaceの要約値。違えば上書きしない（同時編集の検出）",
    )
    build.add_argument("--json", action="store_true", help="結果を JSON で出す")

    patch = sub.add_parser(
        "patch",
        parents=[common],
        help="既存workspaceを検証つきで部分編集する",
    )
    patch.add_argument("workspace", help="Blockly の workspace JSON ファイル")
    patch.add_argument(
        "--ops", required=True, help="操作の JSON ファイル（operations 配列）"
    )
    patch.add_argument(
        "--output", default=None, help="編集後workspaceの書き出し先（新規のみ）"
    )
    patch.add_argument(
        "--write",
        action="store_true",
        help="検査後に .py / .blockly.json の対を保存する",
    )
    patch.add_argument("--stem", default=None, help="保存名（--write では必須）")
    patch.add_argument(
        "--expect-current-sha256",
        default=None,
        help="保存済みworkspaceの要約値。違えば上書きしない（同時編集の検出）",
    )
    patch.add_argument(
        "--dry-run", action="store_true", help="書き込まずに結果だけ返す"
    )
    patch.add_argument(
        "--overwrite-external",
        action="store_true",
        help="手編集を検出した .py を上書きする（ユーザーの明示許可時だけ）",
    )
    patch.add_argument("--json", action="store_true", help="結果を JSON で出す")

    live = sub.add_parser(
        "live", help="開いているエディタとライブ編集する（保存はしない）"
    )
    live_sub = live.add_subparsers(dest="live_command", required=True)
    live_status = live_sub.add_parser("status", help="いまのworkspaceと版を読む")
    live_status.add_argument(
        "--server", required=True, help="エディタの接続先（http://127.0.0.1:<port>）"
    )
    live_status.add_argument(
        "--output", default=None, help="workspace をファイルへ書き出す"
    )
    live_status.add_argument("--json", action="store_true", help="結果を JSON で出す")
    live_patch = live_sub.add_parser(
        "patch", help="版を指定してパッチを渡す（保存はしない）"
    )
    live_patch.add_argument(
        "--server", required=True, help="エディタの接続先（http://127.0.0.1:<port>）"
    )
    live_patch.add_argument(
        "--ops", required=True, help="操作の JSON ファイル（operations 配列）"
    )
    live_patch.add_argument(
        "--base-revision", required=True, help="読んだときの revision"
    )
    live_patch.add_argument("--json", action="store_true", help="結果を JSON で出す")

    render = sub.add_parser(
        "render", parents=[common], help="workspace JSON から Python を生成する"
    )
    render.add_argument("workspace", help="Blockly の workspace JSON ファイル")
    render.add_argument("--json", action="store_true", help="結果を JSON で出す")

    trial = sub.add_parser(
        "trial",
        parents=[common],
        help="時間上限つきの試し実行（既定はプレビューだけ）",
    )
    trial.add_argument(
        "spec", help="コンパクト仕様、または --workspace 時の workspace JSON"
    )
    trial.add_argument(
        "--workspace",
        action="store_true",
        help="位置引数を保存済み workspace JSON として扱う",
    )
    trial.add_argument(
        "--server",
        required=True,
        help="エディタの接続先（http://127.0.0.1:<port>）",
    )
    trial.add_argument(
        "--max-seconds",
        type=float,
        required=True,
        help="試し実行の上限秒数（正の有限値）",
    )
    trial.add_argument(
        "--allow-input",
        action="store_true",
        help="ユーザーの許可がある場合だけ本体へ入力送信する",
    )
    trial.add_argument(
        "--expected-sha256",
        default=None,
        help="許可したコードの digest（preview の codeSha256）",
    )
    trial.add_argument("--json", action="store_true", help="結果を JSON で出す")

    frame = sub.add_parser(
        "frame",
        parents=[common],
        help="エディタの /frame 画像を1枚保存する",
    )
    frame.add_argument(
        "--server",
        required=True,
        help="エディタの接続先（http://127.0.0.1:<port>）",
    )
    frame.add_argument("--output", required=True, help="保存先 PNG パス")
    frame.add_argument("--json", action="store_true", help="結果を JSON で出す")

    template = sub.add_parser(
        "template",
        parents=[common],
        help="画像からテンプレートを切り出して Template/blockly に保存する",
    )
    template.add_argument("--image", required=True, help="元画像（PNG など）")
    template.add_argument(
        "--name",
        required=True,
        help="保存名（Template/blockly/<名>.png。重複時は _2 を付ける）",
    )
    template.add_argument(
        "--rect",
        nargs=4,
        type=int,
        required=True,
        metavar=("X1", "Y1", "X2", "Y2"),
        help="切出し矩形（実画素。X1<X2 / Y1<Y2）",
    )
    template.add_argument("--json", action="store_true", help="結果を JSON で出す")

    match = sub.add_parser(
        "match",
        parents=[common],
        help="画像内でテンプレートを照合する",
    )
    match.add_argument("--image", required=True, help="照合する画像")
    match.add_argument(
        "--template",
        required=True,
        help="Template/ からの相対名（例: blockly/x.png）",
    )
    match.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="一致のしきい値（既定: 0.7）",
    )
    match.add_argument(
        "--gray",
        dest="use_gray",
        action="store_true",
        default=True,
        help="グレースケールで照合する（既定）",
    )
    match.add_argument(
        "--no-gray",
        dest="use_gray",
        action="store_false",
        help="色のまま照合する",
    )
    match.add_argument(
        "--crop",
        nargs=4,
        type=int,
        default=None,
        metavar=("X1", "Y1", "X2", "Y2"),
        help="検索範囲（実画素。省略時は全体）",
    )
    match.add_argument("--preview", default=None, help="確認用 PNG の書き出し先")
    match.add_argument("--json", action="store_true", help="結果を JSON で出す")

    color = sub.add_parser(
        "color",
        parents=[common],
        help="画像内の色の割合を測る",
    )
    color.add_argument("--image", required=True, help="測る画像")
    color.add_argument(
        "--lower",
        nargs=3,
        type=int,
        required=True,
        metavar=("H", "S", "V"),
        help="HSV の下限（H は 0〜179）",
    )
    color.add_argument(
        "--upper",
        nargs=3,
        type=int,
        required=True,
        metavar=("H", "S", "V"),
        help="HSV の上限（H は 0〜179）",
    )
    color.add_argument(
        "--ratio",
        type=float,
        default=0.6,
        help="一致とみなす割合（既定: 0.6）",
    )
    color.add_argument(
        "--crop",
        nargs=4,
        type=int,
        default=None,
        metavar=("X1", "Y1", "X2", "Y2"),
        help="測る範囲（実画素。省略時は全体）",
    )
    color.add_argument("--preview", default=None, help="確認用 PNG の書き出し先")
    color.add_argument("--json", action="store_true", help="結果を JSON で出す")

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
    app_dir = _app_dir(args)
    if args.workspace:
        result = blockly_author.check_workspace(spec, app_dir)
    else:
        result = blockly_author.check_spec(spec, app_dir)
    if args.json:
        _print_json(
            {
                "ok": result.ok,
                "errors": result.errors,
                "warnings": result.warnings,
                "stem": result.stem,
                "workspace": result.workspace,
                "workspaceSha256": (
                    blockly_author.workspace_digest(result.workspace)
                    if args.workspace and result.workspace is not None
                    else None
                ),
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
    if not args.workspace and args.expect_current_sha256 is not None:
        sys.stderr.write(
            "--expect-current-sha256 は --workspace の保存でだけ使えます\n"
        )
        return EXIT_USAGE
    if args.workspace:
        return _build_workspace_command(args, spec, app_dir)
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


def _build_workspace_command(
    args: argparse.Namespace, workspace_json: Any, app_dir: Path
) -> int:
    """既存 workspace を検査し、必要ならそのまま保存する（構造を保つ）。"""
    checked = blockly_author.check_workspace(workspace_json, app_dir)
    if not checked.ok or checked.workspace is None:
        _print_errors(checked.errors, checked.warnings)
        return _failure_exit(checked.errors)
    if not args.write:
        if args.json:
            _print_json(
                {
                    "ok": True,
                    "workspace": checked.workspace,
                    "workspaceSha256": blockly_author.workspace_digest(
                        checked.workspace
                    ),
                    "code": checked.code,
                    "warnings": checked.warnings,
                }
            )
        else:
            sys.stdout.write(
                json.dumps(checked.workspace, ensure_ascii=False, indent=2) + "\n"
            )
        return EXIT_OK
    stem = args.stem
    if not isinstance(stem, str) or not stem.strip():
        sys.stderr.write(
            "保存名（--stem）を書いてください（workspace には stem がありません）\n"
        )
        return EXIT_USAGE
    revision = _revision_problem(args, app_dir, stem, require_existing=False)
    if revision is not None:
        code, message = revision
        return _fail(args, message, code=code)
    if not args.overwrite_external:
        existing = blockly_save.load_blockly(app_dir, stem)
        if existing.status == "ok" and existing.external_edit:
            message = (
                f"{stem}.py は保存時の内容から変わっています（手編集の可能性）。"
                "取り込み方をユーザーへ確認し、明示の許可があるときだけ "
                "--overwrite-external を付けてください。"
            )
            if args.json:
                _print_json(
                    {
                        "ok": False,
                        "status": "failed",
                        "message": message,
                        "errors": [message],
                    }
                )
            else:
                sys.stderr.write(message + "\n")
            return EXIT_CHECK
    result = blockly_author.save_workspace(checked.workspace, app_dir, stem)
    if args.json:
        _print_json(
            {
                "ok": result.status == "saved",
                "status": result.status,
                "message": result.message,
                "py": result.py_rel,
                "json": result.json_rel,
                "workspaceSha256": blockly_author.workspace_digest(checked.workspace),
                "errors": result.errors,
                "warnings": result.warnings,
            }
        )
    else:
        stream = sys.stdout if result.status == "saved" else sys.stderr
        stream.write(result.message + "\n")
    return EXIT_OK if result.status == "saved" else _failure_exit(result.errors)


def _revision_problem(
    args: argparse.Namespace,
    app_dir: Path,
    stem: str,
    *,
    require_existing: bool,
) -> tuple[int, str] | None:
    """保存済みworkspaceの版が期待と違えば (終了コード, 理由) を返す。

    `--expect-current-sha256` があるときだけ見る。`require_existing` は
    patch のように「既存の続き」を前提にする場合に使う。
    """
    expected = getattr(args, "expect_current_sha256", None)
    if expected is None:
        return None
    value = str(expected).strip().lower()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        return (EXIT_USAGE, "--expect-current-sha256 は64桁の16進で書いてください")
    current = blockly_save.load_blockly(app_dir, stem)
    if current.status != "ok":
        if require_existing:
            return (
                EXIT_CHECK,
                f"{stem} の保存物がありません（新規保存するなら "
                "--expect-current-sha256 を外してください）",
            )
        return (
            EXIT_CHECK,
            f"{stem} の保存物がありません（--expect-current-sha256 を外すか、"
            "既存名を確認してください）",
        )
    try:
        workspace = json.loads(current.workspace_json)
        actual = blockly_author.workspace_digest(workspace)
    except (json.JSONDecodeError, ValueError) as e:
        return (EXIT_CHECK, f"保存物のworkspaceを読めません: {e}")
    if actual != value:
        return (
            EXIT_CHECK,
            f"{stem} の保存物が変わっています（保存済み {actual} / 期待 {value}）。"
            "最新のworkspaceを読み直してからやり直してください。",
        )
    return None


def _cmd_patch(args: argparse.Namespace) -> int:
    """既存workspaceを検証つきで部分編集し、必要なら保存する。"""
    if args.dry_run and (args.write or args.output):
        return _fail(
            args,
            "--dry-run は --write / --output と一緒に使えません",
            code=EXIT_USAGE,
        )
    if args.write and args.output:
        return _fail(args, "--write と --output は同時に使えません", code=EXIT_USAGE)
    if args.write and not (isinstance(args.stem, str) and args.stem.strip()):
        return _fail(
            args,
            "保存名（--stem）を書いてください",
            code=EXIT_USAGE,
        )
    if args.expect_current_sha256 is not None and not args.write:
        return _fail(
            args,
            "--expect-current-sha256 は --write と一緒に使ってください",
            code=EXIT_USAGE,
        )
    workspace, error = _read_json(args.workspace)
    if error is not None:
        sys.stderr.write(error + "\n")
        return EXIT_USAGE
    operations, error = _read_json(args.ops)
    if error is not None:
        sys.stderr.write(error + "\n")
        return EXIT_USAGE
    app_dir = _app_dir(args)
    catalog = blockly_headless.catalog(app_dir)
    if not catalog.ok or catalog.data is None:
        errors = catalog.errors or ["カタログを取得できませんでした"]
        return _fail_list(args, errors, code=_failure_exit(errors))
    patched = blockly_author.patch_workspace(workspace, operations, catalog.data)
    if not patched.ok or patched.workspace is None:
        return _fail_list(args, patched.errors or ["操作を適用できませんでした"])
    checked = blockly_author.check_workspace(patched.workspace, app_dir)
    if not checked.ok or checked.workspace is None:
        return _fail_list(
            args,
            checked.errors or ["検査に通りませんでした"],
            checked.warnings,
            code=(EXIT_USAGE if _node_missing(checked.errors) else EXIT_CHECK),
        )
    digest = blockly_author.workspace_digest(checked.workspace)
    payload: dict[str, Any] = {
        "ok": True,
        "status": "patched",
        "message": f"{len(patched.changes)} 件の操作を適用しました",
        "workspace": checked.workspace,
        "workspaceSha256": digest,
        "code": checked.code,
        "changes": patched.changes,
        "warnings": [*catalog.warnings, *checked.warnings],
    }
    if args.write:
        revision = _revision_problem(args, app_dir, args.stem, require_existing=True)
        if revision is not None:
            code, message = revision
            return _fail(args, message, code=code)
        if not args.overwrite_external:
            existing = blockly_save.load_blockly(app_dir, args.stem)
            if existing.status == "ok" and existing.external_edit:
                return _fail(
                    args,
                    f"{args.stem}.py は保存時の内容から変わっています（手編集の可能性）。"
                    "取り込み方をユーザーへ確認し、明示の許可があるときだけ "
                    "--overwrite-external を付けてください。",
                )
        result = blockly_author.save_workspace(checked.workspace, app_dir, args.stem)
        payload.update(
            {
                "ok": result.status == "saved",
                "status": result.status,
                "message": result.message,
                "py": result.py_rel,
                "json": result.json_rel,
                "errors": result.errors,
            }
        )
        if args.json:
            _print_json(payload)
        else:
            stream = sys.stdout if result.status == "saved" else sys.stderr
            stream.write(result.message + "\n")
        return EXIT_OK if result.status == "saved" else _failure_exit(result.errors)
    if args.output:
        text = json.dumps(checked.workspace, ensure_ascii=False, indent=2) + "\n"
        write_error = _write_new(args.output, text.encode("utf-8"))
        if write_error is not None:
            return _fail(args, write_error)
        payload.update(
            {"status": "written", "message": f"書き出しました: {args.output}"}
        )
        if args.json:
            _print_json(payload)
        else:
            sys.stdout.write(payload["message"] + "\n")
        return EXIT_OK
    if args.dry_run or args.json:
        if args.dry_run:
            payload["status"] = "dry_run"
            payload["message"] = (
                f"dry-run: {len(patched.changes)} 件の操作を適用できます"
                "（書き込みません）"
            )
        _print_json(payload)
        return EXIT_OK
    sys.stdout.write(json.dumps(checked.workspace, ensure_ascii=False, indent=2) + "\n")
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


def _editor_client(origin: str) -> Any:
    """エディタへ繋ぐ薄いクライアントを作る。重い import をここへ閉じ込める。"""
    from services.blockly_agent_client import EditorClient

    return EditorClient(origin)


def _fail(args: argparse.Namespace, message: str, *, code: int = EXIT_CHECK) -> int:
    """失敗を出して終了コードを返す。--json なら整形 JSON、無ければ stderr。"""
    if getattr(args, "json", False):
        _print_json({"ok": False, "message": message, "errors": [message]})
    else:
        sys.stderr.write(message + "\n")
    return code


def _fail_list(
    args: argparse.Namespace,
    errors: list[str],
    warnings: list[str] | None = None,
    *,
    code: int = EXIT_CHECK,
) -> int:
    """一覧の失敗を、--json なら機械可読で、無ければ stderr で返す。"""
    if getattr(args, "json", False):
        _print_json(
            {
                "ok": False,
                "message": errors[0] if errors else "失敗しました",
                "errors": errors,
            }
        )
    else:
        _print_errors(errors, warnings or [])
    return code


def _read_image(path: str, match_mod: Any) -> tuple[bytes | None, str | None]:
    """画像ファイルを読む（サイズ上限つき）。失敗時は日本語の理由を返す。"""
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return None, f"画像を読めません: {path}（{e}）"
    if len(raw) > match_mod.MAX_UPLOAD_BYTES:
        limit = match_mod.MAX_UPLOAD_BYTES // (1024 * 1024)
        return None, f"画像が大きすぎます（{limit}MBまで）: {path}"
    return raw, None


def _decode(raw: bytes, match_mod: Any) -> tuple[Any, str | None]:
    """PNG 等のバイト列を BGR 画像にする。壊れていれば理由を返す。"""
    try:
        return match_mod.decode_frame_png(raw), None
    except ValueError as e:
        return None, str(e)


def _write_new(path: str, data: bytes) -> str | None:
    """既存を上書きせずに書き出す。書けなければ日本語の理由を返す。"""
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as fp:
            fp.write(data)
    except FileExistsError:
        return f"既にファイルがあります（上書きしません）: {path}"
    except OSError as e:
        return f"書き出せません: {path}（{e}）"
    return None


def _parse_ratio(value: object) -> float:
    """一致率（0〜1）を検査する。おかしければ ValueError。"""
    try:
        ratio = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("一致率（--ratio）は0〜1の数値で書いてください") from None
    if not math.isfinite(ratio) or not 0 <= ratio <= 1:
        raise ValueError("一致率（--ratio）は0〜1の数値で書いてください")
    return ratio


def _preview_best_match(img: Any, result: Any, crop: list[int] | None) -> Any:
    """元画像の複写に検索範囲と最良一致の矩形を描く（座標系は元のまま）。"""
    import cv2

    canvas = img.copy()
    if crop is not None:
        x1, y1, x2, y2 = crop
        cv2.rectangle(canvas, (x1, y1), (x2 - 1, y2 - 1), (255, 255, 0), 2)
    color = (0, 255, 0) if result.matched else (0, 0, 255)
    x = int(result.rect.get("x", 0))
    y = int(result.rect.get("y", 0))
    w = max(1, int(result.rect.get("width", 1)))
    h = max(1, int(result.rect.get("height", 1)))
    cv2.rectangle(canvas, (x, y), (x + w - 1, y + h - 1), color, 2)
    return canvas


def _png_of(img: Any) -> bytes | None:
    """BGR 画像を PNG バイト列にする。符号化できなければ None。"""
    import cv2

    ok, buf = cv2.imencode(".png", img)
    return bytes(buf) if ok else None


def _cmd_frame(args: argparse.Namespace) -> int:
    if Path(args.output).exists():
        return _fail(args, f"既にファイルがあります（上書きしません）: {args.output}")
    from services import blockly_match

    try:
        client = _editor_client(args.server)
    except ValueError as e:
        return _fail(args, str(e), code=EXIT_USAGE)
    try:
        png = client.frame()
    except ValueError as e:
        return _fail(args, str(e))
    img, error = _decode(png, blockly_match)
    if error is not None:
        return _fail(args, error)
    write_error = _write_new(args.output, png)
    if write_error is not None:
        return _fail(args, write_error)
    height, width = int(img.shape[0]), int(img.shape[1])
    message = f"フレームを保存しました: {args.output}"
    if args.json:
        _print_json(
            {
                "ok": True,
                "message": message,
                "image": args.output,
                "width": width,
                "height": height,
            }
        )
    else:
        sys.stdout.write(message + "\n")
    return EXIT_OK


def _cmd_template(args: argparse.Namespace) -> int:
    from services import blockly_capture, blockly_match

    raw, error = _read_image(args.image, blockly_match)
    if error is not None:
        return _fail(args, error)
    assert raw is not None
    img, error = _decode(raw, blockly_match)
    if error is not None:
        return _fail(args, error)
    height, width = int(img.shape[0]), int(img.shape[1])
    try:
        rect = blockly_match.parse_crop(list(args.rect), width, height)
    except ValueError as e:
        return _fail(args, f"切出し範囲が不正です: {e}")
    if rect is None:  # --rect は必須なので届かない
        return _fail(args, "切出し範囲が不正です: 範囲がありません")
    x1, y1, x2, y2 = rect
    normalized = {
        "x": x1 / width,
        "y": y1 / height,
        "width": (x2 - x1) / width,
        "height": (y2 - y1) / height,
    }
    result = blockly_capture.save_template(_app_dir(args), args.name, raw, normalized)
    if result.status != "saved":
        return _fail(args, result.message)
    saved_path = _app_dir(args) / "Template" / result.rel
    saved_raw, error = _read_image(str(saved_path), blockly_match)
    if error is not None:
        return _fail(args, error)
    assert saved_raw is not None
    saved_img, error = _decode(saved_raw, blockly_match)
    if error is not None:
        return _fail(args, error)
    saved_height, saved_width = int(saved_img.shape[0]), int(saved_img.shape[1])
    message = f"保存しました: Template/{result.rel}"
    if args.json:
        _print_json(
            {
                "ok": True,
                "message": message,
                "template": result.rel,
                "width": saved_width,
                "height": saved_height,
                "rect": [x1, y1, x2, y2],
            }
        )
    else:
        sys.stdout.write(message + "\n")
    return EXIT_OK


def _cmd_match(args: argparse.Namespace) -> int:
    from services import blockly_match

    try:
        threshold = blockly_match.parse_threshold(args.threshold)
    except ValueError as e:
        return _fail(args, str(e), code=EXIT_USAGE)
    raw, error = _read_image(args.image, blockly_match)
    if error is not None:
        return _fail(args, error)
    assert raw is not None
    img, error = _decode(raw, blockly_match)
    if error is not None:
        return _fail(args, error)
    height, width = int(img.shape[0]), int(img.shape[1])
    crop: list[int] | None = None
    if args.crop is not None:
        try:
            crop = blockly_match.parse_crop(list(args.crop), width, height)
        except ValueError as e:
            return _fail(args, f"検索範囲が不正です: {e}")
    if args.preview and Path(args.preview).exists():
        return _fail(args, f"既にファイルがあります（上書きしません）: {args.preview}")
    # 復号は1回だけ済ませ、ndarray のまま照合へ渡す。
    result = blockly_match.match_template(
        _app_dir(args),
        img,
        args.template,
        threshold,
        args.use_gray,
        crop,
    )
    if result.status != "ok":
        return _fail(args, result.message)
    preview_path: str | None = None
    if args.preview:
        canvas = _preview_best_match(img, result, crop)
        data = _png_of(canvas)
        if data is None:
            return _fail(args, "プレビュー画像を書き出せませんでした")
        write_error = _write_new(args.preview, data)
        if write_error is not None:
            return _fail(args, write_error)
        preview_path = args.preview
    if args.json:
        payload: dict[str, Any] = {
            "ok": True,
            "message": result.message,
            "score": float(result.score),
            "matched": bool(result.matched),
            "count": int(result.count),
            "rect": dict(result.rect),
            "width": width,
            "height": height,
            "template": args.template,
            "threshold": threshold,
            "use_gray": bool(args.use_gray),
            "crop": crop,
        }
        if preview_path is not None:
            payload["preview"] = preview_path
        _print_json(payload)
    else:
        sys.stdout.write(result.message + "\n")
    return EXIT_OK


def _cmd_color(args: argparse.Namespace) -> int:
    from services import blockly_color, blockly_match

    try:
        ratio_limit = _parse_ratio(args.ratio)
    except ValueError as e:
        return _fail(args, str(e), code=EXIT_USAGE)
    try:
        lower, upper = blockly_color.parse_hsv_pair(list(args.lower), list(args.upper))
    except ValueError as e:
        return _fail(args, f"HSV が不正です: {e}", code=EXIT_USAGE)
    raw, error = _read_image(args.image, blockly_match)
    if error is not None:
        return _fail(args, error)
    assert raw is not None
    img, error = _decode(raw, blockly_match)
    if error is not None:
        return _fail(args, error)
    height, width = int(img.shape[0]), int(img.shape[1])
    crop: list[int] | None = list(args.crop) if args.crop is not None else None
    if args.preview and Path(args.preview).exists():
        return _fail(args, f"既にファイルがあります（上書きしません）: {args.preview}")
    measured, error = blockly_color.ratio_on_png(img, lower, upper, crop)
    if error is not None:
        return _fail(args, f"色の割合を測れません: {error}")
    matched = measured >= ratio_limit
    preview_path: str | None = None
    if args.preview:
        # crop を渡すとプレビューは ROI だけの画像になる（既存仕様のまま）。
        data, _, error = blockly_color.filter_png(img, lower, upper, "mask", crop)
        if error is not None or data is None:
            return _fail(args, f"プレビュー画像を作れません: {error or '不明な理由'}")
        write_error = _write_new(args.preview, data)
        if write_error is not None:
            return _fail(args, write_error)
        preview_path = args.preview
    message = (
        f"割合 {measured * 100:.1f}%（しきい値 {ratio_limit * 100:.1f}%）→"
        f" {'一致' if matched else '不一致'}"
    )
    if args.json:
        payload: dict[str, Any] = {
            "ok": True,
            "message": message,
            "ratio": float(measured),
            "matched": matched,
            "width": width,
            "height": height,
            "lower": lower,
            "upper": upper,
            "crop": crop,
        }
        if preview_path is not None:
            payload["preview"] = preview_path
        _print_json(payload)
    else:
        sys.stdout.write(message + "\n")
    return EXIT_OK


TRIAL_POLL_SECONDS = 0.2
#: サーバ側の上限に加える待ちの余裕。これを超えたら自分の runId に stop を1回送る。
TRIAL_GRACE_SECONDS = 5.0


def _positive_seconds(value: object, label: str) -> float:
    """正の有限秒数を検査する。おかしければ ValueError。"""
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{label} は正の秒数で書いてください") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{label} は正の秒数で書いてください")
    return seconds


def _code_digest(code: str) -> str:
    """比較用の要約値。改行はLFへ寄せる（保存層の pySha256 と同じ寄せ方）。"""
    return hashlib.sha256(code.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _merge_logs(logs: list[str], seq: int, raw: object) -> tuple[list[str], int]:
    """状態の logs（[番号, 文] の配列）を差分で取り込む。"""
    if not isinstance(raw, list):
        return logs, seq
    for item in raw:
        if (
            isinstance(item, (list, tuple))
            and len(item) == 2
            and isinstance(item[0], int)
            and isinstance(item[1], str)
        ):
            number, text = item
            if number > seq:
                logs.append(text)
                seq = number
    return logs, seq


def _trial_status(state: dict[str, Any]) -> str:
    """状態を CLI の status へ写す。時間切れは停止より先に見る。"""
    if state.get("timedOut") is True:
        return "timed_out"
    result = state.get("result")
    if result == "完了":
        return "completed"
    if result == "停止":
        return "stopped"
    return "failed"


def _stop_once(client: Any, run_id: int) -> None:
    """自分の runId にだけ停止を1回送る。失敗しても投げない。"""
    try:
        client.control(run_id, "stop")
    except ValueError:
        pass


def _cmd_trial(args: argparse.Namespace) -> int:
    try:
        seconds = _positive_seconds(args.max_seconds, "--max-seconds")
    except ValueError as e:
        return _fail(args, str(e), code=EXIT_USAGE)
    if args.allow_input and not args.expected_sha256:
        return _fail(
            args,
            "--allow-input には --expected-sha256（preview の codeSha256）が必要です",
            code=EXIT_USAGE,
        )
    spec, error = _read_json(args.spec)
    if error is not None:
        sys.stderr.write(error + "\n")
        return EXIT_USAGE
    app_dir = _app_dir(args)
    if args.workspace:
        checked = blockly_author.check_workspace(spec, app_dir)
    else:
        checked = blockly_author.check_spec(spec, app_dir)
    if not checked.ok or checked.workspace is None:
        _print_errors(checked.errors, checked.warnings)
        return _failure_exit(checked.errors)
    generated = blockly_headless.generate_trial(checked.workspace, app_dir)
    if not generated.ok or generated.data is None:
        _print_errors(
            generated.errors or ["試し実行のコードを生成できませんでした"],
            generated.warnings,
        )
        return _failure_exit(generated.errors)
    trial_code = generated.data.get("code")
    if not isinstance(trial_code, str) or not trial_code.strip():
        return _fail(args, "試し実行のコードを生成できませんでした")
    digest = _code_digest(checked.code)
    preview = {
        "status": "preview",
        "codeSha256": digest,
        "code": trial_code,
        "workspace": checked.workspace,
        "server": args.server,
        "maxSeconds": seconds,
    }
    if not args.allow_input:
        if args.json:
            _print_json(preview)
        else:
            sys.stdout.write(trial_code)
            sys.stdout.write(
                f"\n# これはプレビューです（入力は送りません）\n"
                f"# codeSha256: {digest}\n"
                f"# 上限: {seconds:g} 秒 / サーバ: {args.server}\n"
            )
        return EXIT_OK
    expected = str(args.expected_sha256).strip().lower()
    if expected != digest:
        return _fail(
            args,
            "preview の codeSha256 と現在の生成コードが違います。"
            "preview をやり直し、許可したコードを確認してください。",
        )
    return _run_trial(args, trial_code, seconds, digest)


def _run_trial(
    args: argparse.Namespace, trial_code: str, seconds: float, digest: str
) -> int:
    try:
        client = _editor_client(args.server)
    except ValueError as e:
        return _fail(args, str(e), code=EXIT_USAGE)
    try:
        state = client.state()
    except ValueError as e:
        return _fail(args, str(e))
    capabilities = state.get("capabilities")
    required = {"boundedTrial", "runIdControl"}
    if not isinstance(capabilities, list) or not required <= set(capabilities):
        return _fail(
            args,
            "このエディタは時間上限つき試し実行に対応していません"
            "（本体を更新してから開き直してください）",
        )
    if state.get("available") is not True:
        return _fail(
            args,
            "本体と繋がっていません（保存して本体で実行してください）",
        )
    if state.get("busy") or state.get("otherBusy"):
        return _fail(
            args, "本体で別のコマンドが実行中です（終わってから試してください）"
        )
    try:
        started = client.start_trial(trial_code, max_seconds=seconds)
    except ValueError as e:
        return _fail(args, f"試し実行を開始できません: {e}")
    run_id = started.get("runId")
    if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
        return _fail(
            args,
            "試し実行を開始できません（runId を受け取れませんでした）。"
            "本体側の状態を確認してください。",
        )
    logs: list[str] = []
    seq = 0
    last: dict[str, Any] = {}
    status = "failed"
    deadline = time.monotonic() + seconds + TRIAL_GRACE_SECONDS
    try:
        while True:
            try:
                state = client.state(since=seq)
            except ValueError as e:
                _stop_once(client, run_id)
                return _fail(args, f"試し実行の状態を読めません: {e}")
            if state.get("runId") != run_id:
                status = "superseded"
                break
            logs, seq = _merge_logs(logs, seq, state.get("logs"))
            last = state
            if not state.get("running"):
                status = _trial_status(state)
                break
            if time.monotonic() >= deadline:
                # クライアント側の待ち上限。自分の runId だけを止める。
                _stop_once(client, run_id)
                try:
                    state = client.state(since=seq)
                except ValueError as e:
                    return _fail(args, f"試し実行の状態を読めません: {e}")
                logs, seq = _merge_logs(logs, seq, state.get("logs"))
                last = state
                status = _trial_status(state) if not state.get("running") else "failed"
                break
            time.sleep(TRIAL_POLL_SECONDS)
    except KeyboardInterrupt:
        _stop_once(client, run_id)
        return _fail(args, "試し実行を中断しました（停止を要求しました）")
    payload: dict[str, Any] = {
        "ok": status == "completed",
        "status": status,
        "runId": run_id,
        "result": str(last.get("result", "")),
        "logs": logs,
        "blockId": str(last.get("blockId", "")),
        "error": str(last.get("error", "")),
        "errorBlock": str(last.get("errorBlock", "")),
        "timedOut": bool(last.get("timedOut")),
        "codeSha256": digest,
    }
    if args.json:
        _print_json(payload)
    else:
        lines = [f"試し実行: {status}（runId {run_id}）"]
        if payload["result"]:
            lines.append(f"結果: {payload['result']}")
        if payload["error"]:
            lines.append(f"エラー: {payload['error']}")
        if payload["logs"]:
            lines.append("ログ:")
            lines.extend(f"  {text}" for text in payload["logs"])
        stream = sys.stdout if status == "completed" else sys.stderr
        stream.write("\n".join(lines) + "\n")
    return EXIT_OK if status == "completed" else EXIT_CHECK


def _cmd_live(args: argparse.Namespace) -> int:
    """開いているエディタの版を読み、版を指定してパッチを渡す。"""
    try:
        client = _editor_client(args.server)
    except ValueError as e:
        return _fail(args, str(e), code=EXIT_USAGE)
    if args.live_command == "status":
        try:
            payload = client.live_state()
        except ValueError as e:
            return _fail(args, str(e))
        if payload.get("ok") is not True:
            return _fail(args, str(payload.get("message") or "状態を読めません"))
        workspace = payload.get("workspace")
        if args.output and isinstance(workspace, dict):
            text = json.dumps(workspace, ensure_ascii=False, indent=2) + "\n"
            write_error = _write_new(args.output, text.encode("utf-8"))
            if write_error is not None:
                return _fail(args, write_error)
        if args.json:
            _print_json(
                {
                    "ok": True,
                    "revision": payload.get("revision", ""),
                    "dirty": bool(payload.get("dirty")),
                    "workspace": workspace,
                    "output": args.output,
                }
            )
        else:
            sys.stdout.write(
                f"revision: {payload.get('revision', '')}"
                f" / 未保存: {'あり' if payload.get('dirty') else 'なし'}\n"
            )
        return EXIT_OK

    operations, error = _read_json(args.ops)
    if error is not None:
        sys.stderr.write(error + "\n")
        return EXIT_USAGE
    try:
        payload = client.live_patch(operations, args.base_revision)
    except ValueError as e:
        return _fail(args, str(e))
    if payload.get("ok") is True:
        if args.json:
            _print_json(
                {
                    "ok": True,
                    "status": payload.get("status", "applied"),
                    "message": payload.get("message", ""),
                    "revision": payload.get("revision", ""),
                    "workspace": payload.get("workspace"),
                    "changes": payload.get("changes", []),
                    "code": payload.get("code", ""),
                }
            )
        else:
            sys.stdout.write(
                "適用しました（保存はまだです）。"
                f"revision: {payload.get('revision', '')}\n"
            )
        return EXIT_OK
    message = str(payload.get("message") or "適用できませんでした")
    if args.json:
        _print_json(
            {
                "ok": False,
                "status": payload.get("status", "failed"),
                "message": message,
                "revision": payload.get("revision", ""),
                "workspace": payload.get("workspace"),
                "errors": [message],
            }
        )
    else:
        sys.stderr.write(message + "\n")
    return EXIT_CHECK


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
    if args.command == "patch":
        return _cmd_patch(args)
    if args.command == "live":
        return _cmd_live(args)
    if args.command == "trial":
        return _cmd_trial(args)
    if args.command == "frame":
        return _cmd_frame(args)
    if args.command == "template":
        return _cmd_template(args)
    if args.command == "match":
        return _cmd_match(args)
    if args.command == "color":
        return _cmd_color(args)
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
