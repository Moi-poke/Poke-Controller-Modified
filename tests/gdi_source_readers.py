"""Static source readers for the GDI present-path contracts; # noqa: SIZE_OK.

Design: ``docs/superpowers/specs/2026-09-26-gdi-preview-renderer-design.md``
sections 8, 10 and 11.

Every class-level and source-level fact about ``CaptureArea``, ``Window`` and the
FPS harness is asserted against the *parsed* file rather than a live object: a
real ``tk.Frame`` cannot be constructed without a display, and the harness builds
a real Tk root that cannot run headless. Reading the file also turns a structural
regression into a readable failure -- a resurrected ``coords()`` call is reported
by name instead of surfacing as a TclError from deep inside Tk.

These readers are pure functions over an ``ast`` tree. They are split from the
runtime doubles in ``tests/gdi_present_doubles.py`` on purpose: one tracks the
production file's syntax and the other tracks the production object's behaviour,
and the two change for different reasons.

The file is a helper module, not a test module: its name deliberately does not
start with ``test_`` so pytest never collects it.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
SERIAL_CONTROLLER = _REPO_ROOT / "SerialController"
GUI_ASSETS_SOURCE = SERIAL_CONTROLLER / "GuiAssets.py"
WINDOW_SOURCE = SERIAL_CONTROLLER / "Window.py"
HARNESS_SOURCE = _REPO_ROOT / "tests" / "preview_fps_support.py"


def module_tree(path: Path) -> ast.Module:
    """Parse a production or harness file, reporting absence as a missing file."""
    assert path.is_file(), f"source file is missing: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    """The ``ClassDef`` for ``name``, with a readable message when absent."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    defined = sorted(
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    )
    raise AssertionError(f"class {name!r} is not defined; defined classes: {defined}")


def class_method(node: ast.ClassDef, name: str) -> ast.FunctionDef:
    """The ``FunctionDef`` for a method of ``node`` only.

    Scoped to the class body so a same-named module function cannot satisfy the
    lookup, which matters because every contract here is about the class body.
    """
    for child in node.body:
        if isinstance(child, ast.FunctionDef) and child.name == name:
            return child
    defined = sorted(
        child.name for child in node.body if isinstance(child, ast.FunctionDef)
    )
    raise AssertionError(
        f"method {name!r} is not defined on the class; defined: {defined}"
    )


def referenced_names(node: ast.AST) -> set[str]:
    """Every ``Name`` id and ``Attribute`` attr reachable from ``node``."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def dotted_name(node: ast.AST) -> str:
    """A readable ``a.b.c`` rendering of a Name/Attribute chain."""
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def string_constants(node: ast.AST) -> set[str]:
    """Every string literal reachable from ``node``."""
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def call_attr_names(node: ast.AST) -> set[str]:
    """The attribute names of every ``x.y(...)`` call reachable from ``node``."""
    return {
        child.func.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    }


def attribute_attr_names(node: ast.AST) -> set[str]:
    """Every ``.attr`` read or write reachable from ``node``."""
    return {child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)}


def assigned_attribute_names(node: ast.AST) -> set[str]:
    """The attribute names assigned anywhere in ``node`` (instance state)."""
    names: set[str] = set()
    for child in ast.walk(node):
        targets: tuple[ast.AST, ...] = ()
        if isinstance(child, ast.Assign):
            targets = tuple(child.targets)
        elif isinstance(child, ast.AnnAssign):
            targets = (child.target,)
        for target in targets:
            if isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def string_tuple_value(node: ast.AST, name: str) -> tuple[str, ...]:
    """The string constants of a module-level tuple assignment, in order.

    Handles both ``NAME = (...)`` and ``NAME: Final[...] = (...)``, because the
    harness declares every pin and evidence list as an annotated assignment.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Assign):
            targets: tuple[ast.AST, ...] = tuple(child.targets)
            value: ast.AST | None = child.value
        elif isinstance(child, ast.AnnAssign):
            targets = (child.target,)
            value = child.value
        else:
            continue
        if value is None:
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            continue
        if not isinstance(value, ast.Tuple | ast.List | ast.Set):
            continue
        return tuple(
            element.value
            for element in value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        )
    raise AssertionError(f"no string collection assignment named {name!r} was found")


def annotated_field_names(node: ast.Module, class_name: str) -> list[str]:
    """The annotated field names of a dataclass, in declaration order."""
    definition = class_node(node, class_name)
    return [
        child.target.id
        for child in definition.body
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
    ]


def names_bound_to_call(node: ast.AST, callee: str) -> set[str]:
    """Local names bound to a call of ``callee``, e.g. ``summarize_cadence``."""
    bound: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Assign):
            continue
        value = child.value
        if not isinstance(value, ast.Call):
            continue
        func = value.func
        called = func.attr if isinstance(func, ast.Attribute) else None
        if isinstance(func, ast.Name):
            called = func.id
        if called != callee:
            continue
        for target in child.targets:
            if isinstance(target, ast.Name):
                bound.add(target.id)
    return bound


def enclosing_function(node: ast.AST, target: ast.AST) -> ast.FunctionDef | None:
    """The innermost ``FunctionDef`` that contains ``target``."""
    for child in ast.walk(node):
        if isinstance(child, ast.FunctionDef) and any(
            grandchild is target for grandchild in ast.walk(child)
        ):
            return child
    return None


def statement_line_span(node: ast.AST) -> tuple[int, int]:
    """The 1-based inclusive line span of a statement node."""
    start = getattr(node, "lineno", 0)
    return start, getattr(node, "end_lineno", start)


def names_in_line_span(tree: ast.Module, start: int, end: int) -> set[str]:
    """Every ``Name`` id occurring on lines ``start..end`` inclusive."""
    return {
        child.id
        for child in ast.walk(tree)
        if isinstance(child, ast.Name) and start <= getattr(child, "lineno", -1) <= end
    }


def function_assignment_statement(function: ast.FunctionDef, name: str) -> ast.stmt:
    """The assignment statement binding ``name`` inside ``function``."""
    for child in ast.walk(function):
        if not isinstance(child, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == name
            for target in child.targets
        ):
            return child
    raise AssertionError(f"{name!r} is not assigned inside {function.name!r}")
