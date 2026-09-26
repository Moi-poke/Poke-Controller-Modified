"""RED contracts for the present-path instrumentation; # noqa: SIZE_OK -
approved brief requires one module per design step group.

Design: ``docs/superpowers/specs/2026-09-26-gdi-preview-renderer-design.md``
section 10, "F. Instrumentation", including the subsection it calls the false-pass
trap.

``tests/preview_fps_support.py`` is the implementer's file, so every assertion
here reads it instead of editing it. Two tools are used, chosen per fact:

* **Source-level** (``ast`` over the harness) for anything that is a naming or
  wiring decision -- report keys, artifact names, dataclass field names, the pin
  list, the call sites that build the area. A rename is a source contract: there
  is no runtime behaviour to observe, only a name that must exist and a name that
  must not.
* **Behavioural** against the imported module for the two facts that are
  genuinely runtime: ``SyntheticFrameSource.readFrameWithTiming`` returning the
  4-tuple, and the report/artifact constants being a literal the manifest
  assertion is compared against.

The load-bearing assertion is
:func:`test_an_empty_present_stream_is_rejected_by_a_minimum_present_count`.
Today ``paste_entries`` feeds only ``paste_summary`` / ``paste_gaps`` /
``intervals.jsonl``. If the present path produced nothing at all those would be
silently zero, ``cadence_passed`` would be ``False``, and ``_report_passed``
would still return ``True`` because it reads only ``functional_accepted`` -- a
completely broken present path would report GREEN. The count therefore has to
participate in the *functional* decision, not only in the recorded performance
reference.
"""

from __future__ import annotations

import ast
import importlib
from typing import Any

import numpy as np
from gdi_source_readers import (
    HARNESS_SOURCE,
    annotated_field_names,
    class_method,
    class_node,
    dotted_name,
    enclosing_function,
    function_assignment_statement,
    module_tree,
    names_bound_to_call,
    names_in_line_span,
    referenced_names,
    statement_line_span,
    string_constants,
    string_tuple_value,
)

_PRESENT_SUMMARY_KEY = "present_summary"


def _harness_module() -> Any:
    """Import the harness at call time so an import error is a per-test RED."""
    return importlib.import_module("preview_fps_support")


def _harness_tree() -> ast.Module:
    return module_tree(HARNESS_SOURCE)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    defined = sorted(
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    )
    raise AssertionError(f"function {name!r} is not defined; defined: {defined}")


# ===========================================================================
# F1. The harness instruments the real present path
# ===========================================================================


def test_harness_never_touches_the_photo_image_any_more() -> None:
    # Given: the parsed harness.
    tree = _harness_tree()

    # When: every name and attribute in the module is collected.
    names = referenced_names(tree)

    # Then: _photo is gone in every form. PasteInstrumentation.__init__ reads
    # area._photo today and installs a wrapper on the PhotoImage's paste method;
    # both disappear once the renderer is the thing being instrumented.
    assert "_photo" not in names, "the harness still references area._photo"
    assert "paste" not in names, "the harness still names a paste path at all"

    # Then: and no string literal smuggles the name back as a patch target.
    assert not [value for value in string_constants(tree) if "_photo" in value]


def test_harness_instruments_compose_and_present() -> None:
    # Given: the parsed harness.
    names = referenced_names(_harness_tree())

    # Then: the instrumentation wraps the two renderer halves rather than a Tk
    # image method, so the t_compose / t_present split the plan requires is
    # measurable independently.
    assert "compose" in names
    assert "present" in names

    # Then: and there is one recorded present per tick plus an entry stream for
    # the cadence and gap summaries to be computed from.
    assert "PresentRecord" in names
    assert "present_entries" in names


def test_report_and_artifact_names_are_renamed_to_present() -> None:
    # Given: the string literals in the parsed harness.
    constants = string_constants(_harness_tree())

    # Then: the report and gap keys are renamed, so a report cannot be read as
    # evidence about a paste that no longer exists.
    assert "paste_summary" not in constants
    assert _PRESENT_SUMMARY_KEY in constants
    assert "paste_gaps" not in constants
    assert "present_gaps" in constants

    # Then: the interval artifact is renamed too. Compared as whole literals
    # rather than as substrings, because "intervals.jsonl" is a suffix of both
    # "present_intervals.jsonl" and "dispatch_intervals.jsonl".
    assert "intervals.jsonl" not in constants
    assert "present_intervals.jsonl" in constants
    assert "dispatch_intervals.jsonl" in constants, (
        "the dispatch stream keeps its own artifact; renaming the present stream "
        "must not collide with it"
    )


def test_the_record_and_dispatch_field_are_renamed_to_present() -> None:
    # Given: the parsed harness dataclasses.
    tree = _harness_tree()
    class_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }

    # Then: PasteRecord becomes PresentRecord.
    assert "PresentRecord" in class_names
    assert "PasteRecord" not in class_names

    # Then: and the per-dispatch flag that records "a present happened inside this
    # dispatch" is renamed with it, so no half-renamed field survives.
    dispatch_fields = annotated_field_names(tree, "DispatchRecord")
    assert "present_observed" in dispatch_fields
    assert "paste_observed" not in dispatch_fields


def test_required_evidence_files_keep_their_order_with_the_renamed_stream() -> None:
    # Given: the harness's declared evidence file order, read from its source.
    declared = string_tuple_value(_harness_tree(), "REQUIRED_EVIDENCE_FILES")

    # Then: the order is exactly the tuple literal, with the present stream in the
    # slot the paste stream occupied. Order is load-bearing because the manifest
    # assertion compares the manifest row list against this order.
    assert declared == (
        "report.json",
        "wakes.jsonl",
        "clock_pairs.jsonl",
        "present_intervals.jsonl",
        "dispatch_intervals.jsonl",
        "camera_reads.jsonl",
        "health.jsonl",
        "teardown.json",
        "thread_ids.json",
        "source_pins.json",
    )
    assert declared.index("present_intervals.jsonl") == 3, (
        "the renamed stream must keep position 3 so the artifact order is stable"
    )
    assert len(set(declared)) == len(declared), "the evidence file list has a duplicate"

    # Then: and the manifest writer still iterates that same declaration, which is
    # what makes the declared order the manifest order.
    writer = _function(_harness_tree(), "write_artifact_manifest")
    assert "REQUIRED_EVIDENCE_FILES" in ast.unparse(writer)


def test_required_report_fields_list_the_present_names() -> None:
    # Given: the harness's declared report fields, read from the live module so the
    # frozenset the parent actually compares a child report against is the one
    # under test.
    fields = set(_harness_module().REQUIRED_REPORT_FIELDS)

    # Then: the renamed keys are required and the old ones are not, so a report
    # still carrying paste_* keys is rejected as incomplete.
    assert _PRESENT_SUMMARY_KEY in fields
    assert "present_gaps" in fields
    assert "paste_summary" not in fields
    assert "paste_gaps" not in fields

    # Then: and the fields the reference/gate separation depends on stay.
    for name in ("functional_accepted", "performance_reference", "target_mean_min_hz"):
        assert name in fields, name


def test_source_pins_cover_the_camera_the_present_path_reads_through() -> None:
    # Given: the harness's source pin list.
    pins = string_tuple_value(_harness_tree(), "SOURCE_PIN_PATHS")

    # Then: core/Camera.py is pinned, because Task 2 changed it and the present
    # path's inputs -- the frame, the sequence and both timestamps -- come from
    # it. A pin list that omits it lets the present path's own input change
    # silently while the artifacts still look identical.
    assert "SerialController/core/Camera.py" in pins

    # Then: and the files this harness and the area it measures stay pinned.
    for path in (
        "SerialController/GuiAssets.py",
        "SerialController/Window.py",
        "tests/preview_fps_support.py",
    ):
        assert path in pins, path
    assert len(set(pins)) == len(pins), "the source pin list has a duplicate"


def test_synthetic_source_exposes_a_four_tuple_read_frame_with_timing() -> None:
    # Given: a synthetic frame source that has not been started, so no producer
    # thread exists and nothing races the assertions.
    source = _harness_module().SyntheticFrameSource()

    # When: the present path reads a frame with timing.
    result = source.readFrameWithTiming()

    # Then: it returns the same 4-tuple core.Camera.readFrameWithTiming returns:
    # (frame, seq, t_capture_ns, t_ready_ns).
    assert isinstance(result, tuple)
    assert len(result) == 4
    frame, sequence, t_capture_ns, t_ready_ns = result
    assert isinstance(frame, np.ndarray)
    assert frame.shape == (720, 1280, 3)
    assert isinstance(sequence, int)
    assert isinstance(t_capture_ns, int)
    assert isinstance(t_ready_ns, int)
    assert t_capture_ns >= 0
    assert t_ready_ns >= t_capture_ns


def test_synthetic_source_read_frame_with_timing_stamps_in_the_perf_counter_domain() -> (
    None
):
    # Given: the parsed SyntheticFrameSource class.
    definition = class_node(_harness_tree(), "SyntheticFrameSource")

    # When: its readFrameWithTiming is read.
    method = class_method(definition, "readFrameWithTiming")
    source = ast.unparse(method)

    # Then: the stamps come from perf_counter_ns, the same domain the clock, the
    # report and the gap rows already use. A time.time_ns stamp would make the
    # present intervals meaningless rather than obviously wrong.
    assert "perf_counter_ns" in source

    # Then: and the method is declared directly on the class with the same
    # readFrameWithSeq-compatible signature, so the present path can prefer the
    # timed read without changing the frozen two-value one.
    assert method in list(definition.body)
    assert [parameter.arg for parameter in method.args.args] == ["self", "copy"]
    assert method.args.kwonlyargs == []


def test_both_harness_call_sites_build_the_area_with_ser_none_positionally() -> None:
    # Given: the two places the harness builds a real CaptureArea.
    call_sites = _capture_area_call_sites(_harness_tree())

    # Then: both exist, and both pass ser=None as the fourth positional argument
    # with everything else by keyword, which is what freezes the constructor.
    assert set(call_sites) == {"start", "_run_teardown_probe"}
    for owner, call in call_sites.items():
        assert len(call["args"]) == 4, f"{owner} passes {len(call['args'])} positionals"
        assert call["args"][3] == "None", f"{owner} must pass ser=None positionally"
        assert set(call["keywords"]) == {
            "master",
            "show_width",
            "show_height",
            "take_stick_log",
        }, f"{owner} passes {sorted(call['keywords'])}"


# ===========================================================================
# F2. The false-pass gate is closed, and the reference stays reference-only
# ===========================================================================


def test_an_empty_present_stream_is_rejected_by_a_minimum_present_count() -> None:
    # Given: the parsed harness.
    tree = _harness_tree()

    # When: every `x >= 2` comparison in the module is collected with its host.
    floors = _minimum_two_comparisons(tree)
    assert floors, "the harness contains no `>= 2` floor at all"

    # Then: at least one of those floors tests a *present* quantity, so there is
    # an explicit "the present path produced something" requirement. Without it an
    # empty present stream is silently zero and the run reports GREEN.
    present_floors = [
        host for operand, host in floors if host is not None and "present" in operand
    ]
    assert present_floors, (
        "the `>= 2` floor is never applied to the present stream; it guards cadence "
        "only, so an empty present stream still reports GREEN: "
        f"{[(operand, host.name if host else None) for operand, host in floors]}"
    )

    # Then: and the floor lives in a run-class-aware function, so it is the
    # native_production run that is rejected rather than every run at once.
    for host in present_floors:
        assert "NATIVE_PRODUCTION" in ast.unparse(host), (
            f"the present floor in {host.name!r} is not conditioned on the run class"
        )


def test_the_present_cadence_feeds_the_functional_decision() -> None:
    # Given: the report builder.
    tree = _harness_tree()
    builder = _function(tree, "_build_report")
    present_local = _present_cadence_local(builder)

    # When: the statements that decide functional_ok and evidence_ok are located.
    decision_names: set[str] = set()
    for name in ("functional_ok", "evidence_ok"):
        decision_names |= names_in_line_span(
            tree,
            *statement_line_span(function_assignment_statement(builder, name)),
        )

    # Then: the present cadence result is read while that decision is being made,
    # so an empty present stream can make the run fail functionally. Counting
    # presents only inside the recorded performance reference would leave the
    # false pass wide open.
    assert present_local in decision_names, (
        f"{present_local!r} builds the present summary but is never read while "
        "functional_ok / evidence_ok are decided, so the present count only "
        "reaches the performance reference"
    )


def test_report_passed_still_gates_on_functional_accepted_for_native_production() -> (
    None
):
    # Given: the native-production branch of the harness's terminal gate.
    reporter = _function(_harness_tree(), "_report_passed")
    branch = _match_case_body(reporter, "NATIVE_PRODUCTION")
    returned = [
        node.value
        for statement in branch
        for node in ast.walk(statement)
        if isinstance(node, ast.Return) and node.value is not None
    ]
    assert returned, "the native_production case returns nothing"

    # Then: it is decided by functional_accepted, and the new present floor must
    # not turn cadence into a pass/fail gate. The report keys are string literals
    # (report.get("...") or report["..."]), so literals count as reads here.
    for value in returned:
        names = referenced_names(value) | string_constants(value)
        assert "functional_accepted" in names, (
            f"the native_production return reads {sorted(names)} instead of "
            "functional_accepted"
        )
        assert "performance_reference" not in names, (
            "_report_passed started reading the performance reference, which would "
            "make cadence a gate and reject a sub-target but sound run"
        )
        assert _PRESENT_SUMMARY_KEY not in names, (
            "_report_passed re-implements the present floor; the floor belongs in "
            "the functional decision, not duplicated in the terminal gate"
        )


def test_the_mean_target_is_still_the_configured_fps_as_a_float() -> None:
    # Given: the cadence contract builder.
    target = _keyword_value(
        _function(_harness_tree(), "cadence_contract"), "target_mean_min_hz"
    )

    # Then: the reference target is exactly the configured fps widened to a float,
    # so a run at the configured rate is inside the reference by construction.
    assert ast.unparse(target) == "float(configured_fps)"


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------
def _minimum_two_comparisons(
    tree: ast.Module,
) -> list[tuple[str, ast.FunctionDef | None]]:
    """``[(left-hand dotted name, enclosing function)]`` for every ``x >= 2``."""
    found: list[tuple[str, ast.FunctionDef | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, ast.GtE) for op in node.ops):
            continue
        if not any(
            isinstance(comparator, ast.Constant) and comparator.value == 2
            for comparator in node.comparators
        ):
            continue
        for operand in (node.left, *node.comparators):
            if isinstance(operand, ast.Constant):
                continue
            found.append((dotted_name(operand), enclosing_function(tree, node)))
    return found


def _keyword_value(function: ast.FunctionDef, keyword_name: str) -> ast.AST:
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == keyword_name:
                return keyword.value
    raise AssertionError(f"no {keyword_name!r} keyword in {function.name!r}")


def _match_case_body(function: ast.FunctionDef, case_name: str) -> list[ast.stmt]:
    """The statements of the ``case <enum>.<case_name>`` inside a match statement."""
    for node in ast.walk(function):
        if not isinstance(node, ast.match_case):
            continue
        pattern = node.pattern
        if not isinstance(pattern, ast.MatchValue):
            continue
        if not isinstance(pattern.value, ast.Attribute):
            continue
        if pattern.value.attr == case_name:
            return node.body
    cases = sorted(
        pattern.value.attr
        for node in ast.walk(function)
        if isinstance(node, ast.match_case)
        for pattern in [node.pattern]
        if isinstance(pattern, ast.MatchValue)
        and isinstance(pattern.value, ast.Attribute)
    )
    raise AssertionError(f"no {case_name!r} case in {function.name!r}; cases: {cases}")


def _keyed_value(function: ast.FunctionDef, key: str) -> ast.AST | None:
    """The value bound to ``key`` in a dict literal or a subscript assignment."""
    for node in ast.walk(function):
        if isinstance(node, ast.Dict):
            for literal, value in zip(node.keys, node.values, strict=True):
                if isinstance(literal, ast.Constant) and literal.value == key:
                    return value
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == key
                ):
                    return node.value
    return None


def _present_cadence_local(builder: ast.FunctionDef) -> str:
    """The local name the present summary is built from.

    Located through the report key rather than through the local's own name, so
    the contract survives whatever the local is called.
    """
    value = _keyed_value(builder, _PRESENT_SUMMARY_KEY)
    assert value is not None, (
        f"_build_report never sets {_PRESENT_SUMMARY_KEY!r}; the present cadence is "
        "not in the report at all"
    )
    cadence_locals = names_bound_to_call(builder, "summarize_cadence")
    candidates = {
        node.id
        for node in ast.walk(value)
        if isinstance(node, ast.Name) and node.id in cadence_locals
    }
    assert candidates, (
        f"{_PRESENT_SUMMARY_KEY!r} is not built from a summarize_cadence result; "
        f"cadence locals are {sorted(cadence_locals)}"
    )
    assert len(candidates) == 1, (
        f"{_PRESENT_SUMMARY_KEY!r} draws on more than one cadence result: "
        f"{sorted(candidates)}"
    )
    return next(iter(candidates))


def _capture_area_call_sites(tree: ast.Module) -> dict[str, dict[str, Any]]:
    """``{enclosing function: {"args": [...], "keywords": [...]}}`` for each call."""
    sites: dict[str, dict[str, Any]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "CaptureArea":
            continue
        owner = enclosing_function(tree, node)
        assert owner is not None, "a CaptureArea call is not inside a function"
        sites[owner.name] = {
            "args": [ast.unparse(argument) for argument in node.args],
            "keywords": [
                keyword.arg for keyword in node.keywords if keyword.arg is not None
            ],
        }
    return sites
