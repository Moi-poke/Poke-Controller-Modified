#!/usr/bin/env python3
"""指示書・設定の参照切れを機械的に検査する。

共通エージェント文書の相対リンク、Claude の import、`task <name>` の参照、
opencode.json の agent prompt が実在するかを確認する。
壊れた参照はエージェントに誤ったコンテキストを与えるため、task ci で落とす。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
MD_FILES = (
    "AGENTS.md",
    "README.md",
    "CLAUDE.md",
    "docs/AI_AUTOMATION.md",
    "docs/blockly-agent/README.md",
    "docs/blockly-agent/references/spec.md",
    "docs/blockly-agent/references/cli.md",
    "docs/blockly-agent/references/vision.md",
    "docs/blockly-agent/references/blocks.md",
    ".opencode/skills/pokecon-blockly/SKILL.md",
    "docs/BLOCKLY_EDITOR.md",
    "docs/BLOCKLY_AGENT_VERIFICATION.md",
    "docs/design-blockly-agent.md",
)
REQUIRED = (
    *MD_FILES,
    "opencode.json",
    "taskfile.yml",
    ".github/workflows/ci.yml",
    "docs/blockly-agent/examples/basic.json",
    "docs/blockly-agent/examples/flow.json",
    "docs/blockly-agent/examples/vision.json",
    "docs/blockly-agent/examples/subroutine.json",
    "docs/blockly-agent/examples/audio.json",
    "docs/blockly-agent/examples/dialog.json",
)
TASK_DOC_FILES = (
    "AGENTS.md",
    "docs/ARCHITECTURE.md",
    "docs/AI_AUTOMATION.md",
    "docs/blockly-agent/README.md",
    "docs/BLOCKLY_AGENT_VERIFICATION.md",
)
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
TASK_REF = re.compile(r"`task ([a-z][a-z0-9_-]*)")
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
PROMPT_REF = re.compile(r"^\{file:(.+)\}$")
INSTRUCTION_IMPORT = re.compile(r"^@([^\s]+)\s*$", re.MULTILINE)


def strip_fences(text: str) -> str:
    return FENCE.sub("", text)


def task_names(taskfile: Path) -> set[str]:
    names: set[str] = set()
    in_tasks = False
    for line in taskfile.read_text(encoding="utf-8").splitlines():
        if line.rstrip() == "tasks:":
            in_tasks = True
            continue
        if not in_tasks:
            continue
        if line and not line.startswith(" "):
            break
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match:
            names.add(match.group(1))
    return names


def check_links(root: Path, errors: list[str]) -> int:
    checked = 0
    for name in MD_FILES:
        path = root / name
        if not path.is_file():
            continue
        text = strip_fences(path.read_text(encoding="utf-8"))
        for raw in LINK.findall(text):
            target = unquote(raw.split("#", 1)[0])
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            checked += 1
            if not (path.parent / target).resolve().exists():
                errors.append(f"{name}: リンク先が無い: {raw}")
    return checked


def check_instruction_imports(root: Path, errors: list[str]) -> int:
    path = root / "CLAUDE.md"
    if not path.is_file():
        return 0
    text = strip_fences(path.read_text(encoding="utf-8"))
    targets = INSTRUCTION_IMPORT.findall(text)
    for target in targets:
        if not (path.parent / target).resolve().is_file():
            errors.append(f"CLAUDE.md: import先が無い: {target}")
    return len(targets)


def check_tasks(root: Path, errors: list[str]) -> int:
    available = task_names(root / "taskfile.yml")
    if not available:
        errors.append("taskfile.yml: tasks: からタスク名を1つも読めない")
        return 0
    checked = 0
    for name in TASK_DOC_FILES:
        path = root / name
        if not path.is_file():
            continue
        text = strip_fences(path.read_text(encoding="utf-8"))
        for task in TASK_REF.findall(text):
            checked += 1
            if task not in available:
                errors.append(f"{name}: 未定義のタスク: task {task}")
    return checked


def check_opencode(root: Path, errors: list[str]) -> int:
    path = root / "opencode.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"opencode.json: JSONとして読めない: {exc}")
        return 0
    checked = 0
    agents = config.get("agent")
    if not isinstance(agents, dict):
        return 0
    for name, agent in agents.items():
        if not isinstance(agent, dict):
            errors.append(f"opencode.json: agent.{name} がオブジェクトでない")
            continue
        prompt = agent.get("prompt")
        if not isinstance(prompt, str):
            continue
        match = PROMPT_REF.match(prompt.strip())
        if not match:
            continue
        checked += 1
        target = match.group(1)
        if not (root / target).is_file():
            errors.append(
                f"opencode.json: agent.{name}.prompt の参照先が無い: {target}"
            )
    return checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=ROOT, help="検査するリポジトリのルート"
    )
    args = parser.parse_args()
    root = args.root.resolve()

    errors: list[str] = []
    for name in REQUIRED:
        if not (root / name).is_file():
            errors.append(f"{name}: 必須ファイルが無い")
    if errors:
        for error in errors:
            print(error)
        return 1

    checked = check_links(root, errors)
    checked += check_tasks(root, errors)
    checked += check_opencode(root, errors)
    checked += check_instruction_imports(root, errors)

    if errors:
        for error in errors:
            print(error)
        return 1

    lines = (root / "AGENTS.md").read_text(encoding="utf-8").count("\n") + 1
    print(f"OK: 参照 {checked} 件、AGENTS.md {lines} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
