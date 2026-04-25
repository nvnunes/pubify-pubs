from __future__ import annotations

import ast
from importlib import resources
from pathlib import Path
import re


FIGURES_TEMPLATE_ASSET = "figures.py"
STUB_MARKERS = {
    "data": ("# pubs:data-stub:start", "# pubs:data-stub:end"),
    "figure": ("# pubs:figure-stub:start", "# pubs:figure-stub:end"),
    "stat": ("# pubs:stat-stub:start", "# pubs:stat-stub:end"),
    "table": ("# pubs:table-stub:start", "# pubs:table-stub:end"),
}
STUB_PLACEHOLDERS = {
    "data": "<data-id>",
    "figure": "<figure-id>",
    "stat": "<stat-id>",
    "table": "<table-id>",
}
FUNCTION_PREFIXES = {
    "data": "load_",
    "figure": "plot_",
    "stat": "compute_",
    "table": "tabulate_",
}
VALID_STUB_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def render_init_figures_module() -> str:
    text = _load_figures_asset_text()
    replacements = {
        "data": "example_data",
        "figure": "example",
        "stat": "example",
        "table": "example",
    }
    for kind, placeholder in STUB_PLACEHOLDERS.items():
        text = text.replace(placeholder, replacements[kind])
    return _strip_marker_lines(text)


def build_figures_stub(kind: str, stub_id: str) -> str:
    if kind not in STUB_MARKERS:
        raise ValueError(f"Unsupported stub kind '{kind}'")
    text = _extract_marked_block(_load_figures_asset_text(), *STUB_MARKERS[kind])
    return text.replace(STUB_PLACEHOLDERS[kind], stub_id).strip() + "\n"


def generated_stub_function_name(kind: str, stub_id: str) -> str:
    return f"{FUNCTION_PREFIXES[kind]}{stub_id}"


def validate_stub_id(stub_id: str) -> None:
    if not VALID_STUB_ID_RE.fullmatch(stub_id):
        raise ValueError(
            f"Invalid id '{stub_id}': ids must be snake_case and start with a letter"
        )


def module_function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def add_stub_to_figures_module(path: Path, *, kind: str, stub_id: str) -> None:
    validate_stub_id(stub_id)
    text = path.read_text(encoding="utf-8")
    text = _ensure_required_imports(text, kind)
    stub_text = build_figures_stub(kind, stub_id)
    if kind == "data":
        updated = _insert_after_last_loader(text, stub_text)
    else:
        updated = _append_stub(text, stub_text)
    path.write_text(updated, encoding="utf-8")


def _load_figures_asset_text() -> str:
    asset = resources.files("pubify_pubs.assets.init").joinpath(FIGURES_TEMPLATE_ASSET)
    return asset.read_text(encoding="utf-8")


def _strip_marker_lines(text: str) -> str:
    lines = [line for line in text.splitlines() if not line.startswith("# pubs:")]
    return "\n".join(lines) + "\n"


def _extract_marked_block(text: str, start_marker: str, end_marker: str) -> str:
    lines = text.splitlines()
    start_index = lines.index(start_marker) + 1
    end_index = lines.index(end_marker)
    return "\n".join(lines[start_index:end_index]).strip("\n")


def _ensure_required_imports(text: str, kind: str) -> str:
    lines = text.splitlines()
    if kind in {"data", "stat", "table"}:
        lines = _ensure_plain_import(lines, "import numpy as np")
    if kind == "figure":
        lines = _ensure_plain_import(lines, "import matplotlib.pyplot as plt")
        lines = _ensure_from_import(lines, "pubify_pubs", "FigureResult")
    if kind == "stat":
        lines = _ensure_from_import(lines, "pubify_pubs", "StatResult")
    if kind == "table":
        lines = _ensure_from_import(lines, "pubify_pubs", "TableResult")
    lines = _ensure_from_import(lines, "pubify_data", kind)
    return "\n".join(lines) + ("\n" if lines else "")


def _ensure_plain_import(lines: list[str], import_line: str) -> list[str]:
    if any(line.strip() == import_line for line in lines):
        return lines
    insert_at = _import_insert_index(lines)
    lines[insert_at:insert_at] = [import_line]
    return lines


def _ensure_from_import(lines: list[str], module_name: str, symbol: str) -> list[str]:
    target = f"from {module_name} import "
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(target):
            continue
        existing = [item.strip() for item in stripped[len(target) :].split(",")]
        if symbol in existing:
            return lines
        existing.append(symbol)
        lines[index] = f"{target}{', '.join(sorted(existing))}"
        return lines
    insert_at = _import_insert_index(lines)
    lines[insert_at:insert_at] = [f"{target}{symbol}"]
    return lines


def _import_insert_index(lines: list[str]) -> int:
    index = 0
    if lines and lines[0].startswith('"""'):
        index = 1
        while index < len(lines):
            if lines[index].startswith('"""'):
                index += 1
                break
            index += 1
    while index < len(lines) and (lines[index].startswith("import ") or lines[index].startswith("from ") or not lines[index].strip()):
        index += 1
    return index


def _insert_after_last_loader(text: str, stub_text: str) -> str:
    tree = ast.parse(text)
    loader_end_lineno: int | None = None
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name.startswith("load_"):
            loader_end_lineno = max(loader_end_lineno or 0, node.end_lineno or node.lineno)
    if loader_end_lineno is None:
        return _append_stub(text, stub_text)
    lines = text.splitlines()
    insertion = ["", "", *stub_text.rstrip("\n").splitlines()]
    lines[loader_end_lineno:loader_end_lineno] = insertion
    return "\n".join(lines).rstrip() + "\n"


def _append_stub(text: str, stub_text: str) -> str:
    base = text.rstrip()
    if not base:
        return stub_text
    return base + "\n\n\n" + stub_text.rstrip("\n") + "\n"
