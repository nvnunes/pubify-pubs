from __future__ import annotations

from collections.abc import Sequence
import os
from pathlib import Path
import shutil
import subprocess
import sys

import matplotlib as mpl
import numpy as np
import pytest

from conftest import FakePubifyBackend, FakeReadline, _hash_text, _strip_ansi, _write_external_paper, _write_table_paper
from pubify_pubs.cli import build_parser, main
import pubify_pubs.cli as core_cli
import pubify_pubs.commands.core as commands_core
import pubify_pubs.export as core_export
import pubify_pubs.runtime as core_runtime
import pubify_pubs.shell_incremental as core_shell_incremental
from pubify_pubs.data import load_publication_data_npz, publication_data_path, save_publication_data_npz
from pubify_pubs import TableResult
from pubify_data import data, external_data, figure, stat, table
from pubify_pubs.discovery import find_workspace_root, list_publication_ids, load_publication_definition
from pubify_pubs.runtime import (
    UserCodeExecutionError,
    build_publication,
    check_publication,
    init_publication,
    run_figures,
    run_stats,
    run_tables,
)
from pubify_pubs.config import load_workspace_config

PUBLICATIONS_AGENTS_TEMPLATE = "\n".join(
    [
        "# AGENTS.md",
        "",
        "## First Reads",
        "- Read the `pubify-pubs` agent surface before making publication-workflow decisions:",
        "  - `pubify-pubs/AGENTS.md`",
        "  - `pubify-pubs/README.md`",
        "",
    ]
)

def test_data_decorator_supports_single_path_form() -> None:
    @data("training.npy")
    def load_training(ctx, path):
        return path

    assert load_training.__pubs_loader__ == {
        "kind": "data",
        "style": "single",
        "paths": {"path": "training.npy"},
        "nocache": False,
    }

def test_data_decorator_supports_named_multi_path_form() -> None:
    @data(model="bundle.pt", meta="bundle.json", nocache=True)
    def load_bundle(ctx, model, meta):
        return model, meta

    assert load_bundle.__pubs_loader__ == {
        "kind": "data",
        "style": "named",
        "paths": {"model": "bundle.pt", "meta": "bundle.json"},
        "nocache": True,
    }

def test_data_decorator_rejects_empty_form() -> None:
    with pytest.raises(
        ValueError,
        match="@data requires exactly one positional path or one-or-more named paths",
    ):
        data()

def test_data_decorator_rejects_mixed_positional_and_named_paths() -> None:
    with pytest.raises(
        ValueError,
        match="@data accepts either one positional path or named paths, not both",
    ):
        data("training.npy", model="bundle.pt")

def test_data_decorator_rejects_absolute_paths() -> None:
    with pytest.raises(ValueError, match="@data paths must be relative, not absolute"):
        data("/abs/training.npy")

def test_data_decorator_rejects_parent_traversal() -> None:
    with pytest.raises(ValueError, match="@data paths must stay under their configured root"):
        data("../training.npy")

def test_save_publication_data_npz_saves_new_file_under_publication_data_root(repo: Path) -> None:
    saved_path = save_publication_data_npz("demo", "generated/sample.npz", values=np.array([1.0, 2.0]))

    assert saved_path == repo / "papers" / "demo" / "data" / "generated" / "sample.npz"
    assert saved_path.exists()
    with np.load(saved_path) as saved:
        assert np.array_equal(saved["values"], np.array([1.0, 2.0]))

def test_publication_data_path_resolves_under_publication_data_root(repo: Path) -> None:
    path = publication_data_path("demo", "generated/sample.pkl")

    assert path == repo / "papers" / "demo" / "data" / "generated" / "sample.pkl"

def test_publication_data_path_creates_parent_directories(repo: Path) -> None:
    path = publication_data_path("demo", "nested/deeper/sample.pkl")

    assert path.parent.exists()

def test_publication_data_path_rejects_absolute_paths(repo: Path) -> None:
    with pytest.raises(ValueError, match="must be relative, not absolute"):
        publication_data_path("demo", "/tmp/sample.pkl")

def test_publication_data_path_rejects_parent_traversal(repo: Path) -> None:
    with pytest.raises(ValueError, match="must stay under the publication data root"):
        publication_data_path("demo", "../sample.pkl")

def test_publication_data_path_ignores_legacy_workspace_data_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "pkg"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.yaml").write_text(
        "pubify-pubs:\n  publications_root: papers\n  data_root: output/papers\n",
        encoding="utf-8",
    )

    path = publication_data_path(
        "demo",
        "generated/sample.pkl",
        workspace_root=workspace_root,
    )

    assert path == workspace_root / "papers" / "demo" / "data" / "generated" / "sample.pkl"
    assert path.parent.exists()

def test_publication_data_path_uses_publication_local_data_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "pkg"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.yaml").write_text(
        "pubify-pubs:\n  publications_root: papers\n",
        encoding="utf-8",
    )

    path = publication_data_path(
        "demo",
        "generated/sample.pkl",
        workspace_root=workspace_root,
    )

    assert path == workspace_root / "papers" / "demo" / "data" / "generated" / "sample.pkl"
    assert path.parent.exists()

def test_save_publication_data_npz_creates_parent_directories(repo: Path) -> None:
    saved_path = save_publication_data_npz(
        "demo",
        "nested/deeper/sample.npz",
        values=np.array([3.0]),
    )

    assert saved_path.parent.exists()

def test_save_publication_data_npz_rejects_non_npz_paths(repo: Path) -> None:
    with pytest.raises(ValueError, match="must end with \\.npz"):
        save_publication_data_npz("demo", "generated/sample.npy", values=np.array([1.0]))

def test_save_publication_data_npz_rejects_absolute_paths(repo: Path) -> None:
    with pytest.raises(ValueError, match="must be relative, not absolute"):
        save_publication_data_npz("demo", "/tmp/sample.npz", values=np.array([1.0]))

def test_save_publication_data_npz_rejects_parent_traversal(repo: Path) -> None:
    with pytest.raises(ValueError, match="must stay under the publication data root"):
        save_publication_data_npz("demo", "../sample.npz", values=np.array([1.0]))

def test_save_publication_data_npz_rejects_existing_file_without_overwrite(repo: Path) -> None:
    target = repo / "papers" / "demo" / "data" / "generated" / "sample.npz"
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez(target, values=np.array([1.0]))

    with pytest.raises(FileExistsError, match="already exists"):
        save_publication_data_npz("demo", "generated/sample.npz", values=np.array([2.0]))

def test_save_publication_data_npz_overwrites_existing_file_when_requested(repo: Path) -> None:
    target = repo / "papers" / "demo" / "data" / "generated" / "sample.npz"
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez(target, values=np.array([1.0]))

    save_publication_data_npz(
        "demo",
        "generated/sample.npz",
        overwrite=True,
        values=np.array([2.0]),
    )

    with np.load(target) as saved:
        assert np.array_equal(saved["values"], np.array([2.0]))

def test_save_publication_data_npz_ignores_legacy_workspace_data_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "pkg"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.yaml").write_text(
        "pubify-pubs:\n  publications_root: papers\n  data_root: output/papers\n",
        encoding="utf-8",
    )

    saved_path = save_publication_data_npz(
        "demo",
        "generated/sample.npz",
        workspace_root=workspace_root,
        values=np.array([4.0]),
    )

    assert saved_path == workspace_root / "papers" / "demo" / "data" / "generated" / "sample.npz"
    with np.load(saved_path) as saved:
        assert np.array_equal(saved["values"], np.array([4.0]))

def test_save_publication_data_npz_uses_publication_local_data_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "pkg"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.yaml").write_text(
        "pubify-pubs:\n  publications_root: papers\n",
        encoding="utf-8",
    )

    saved_path = save_publication_data_npz(
        "demo",
        "generated/sample.npz",
        workspace_root=workspace_root,
        values=np.array([4.0]),
    )

    assert saved_path == workspace_root / "papers" / "demo" / "data" / "generated" / "sample.npz"
    with np.load(saved_path) as saved:
        assert np.array_equal(saved["values"], np.array([4.0]))

def test_workspace_config_defaults_preview_backends(repo: Path) -> None:
    workspace = load_workspace_config(repo)

    assert workspace.preview.publication == "preview"
    assert workspace.preview.figure == "preview"


def test_workspace_config_rejects_legacy_top_level_roots(tmp_path: Path) -> None:
    workspace_root = tmp_path / "pkg"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.yaml").write_text(
        "publications_root: papers\ndata_root: output/papers\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required pubify-pubs section"):
        load_workspace_config(workspace_root)


def test_workspace_config_ignores_legacy_data_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "pkg"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.yaml").write_text(
        "\n".join(
            [
                "pubify-pubs:",
                "  publications_root: papers",
                '  data_root: ""',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    workspace = load_workspace_config(workspace_root)

    assert workspace.publications_root == workspace_root / "papers"

def test_load_publication_definition_uses_publication_local_data_root(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "pkg"
    publication_root = workspace_root / "papers" / "demo"
    publication_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.yaml").write_text(
        "pubify-pubs:\n  publications_root: papers\n",
        encoding="utf-8",
    )
    (publication_root / "pub.yaml").write_text(
        "\n".join(
            [
                'mirror_root: ""',
                "main_tex: main.tex",
                "pubify-mpl-template:",
                "  textwidth_in: 6.0",
                "  textheight_in: 8.0",
                "  base_fontsize_pt: 10.0",
                "  baseline_skip_pt: 12.0",
                "  caption_fontsize_pt: 9.0",
                "  caption_lineheight_pt: 10.0",
                "pubify-mpl-defaults:",
                "  layout: one",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (publication_root / "figures.py").write_text("", encoding="utf-8")

    publication = load_publication_definition(workspace_root, "demo")

    assert publication.paths.data_root == workspace_root / "papers" / "demo" / "data"

def test_workspace_config_parses_nested_preview_backends(tmp_path: Path) -> None:
    workspace_root = tmp_path / "pkg"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.yaml").write_text(
        "\n".join(
            [
                "pubify-pubs:",
                "  publications_root: papers",
                "  data_root: output/papers",
                "  preview:",
                "    publication: vscode",
                "    figure: preview",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    workspace = load_workspace_config(workspace_root)

    assert workspace.preview.publication == "vscode"
    assert workspace.preview.figure == "preview"

def test_workspace_config_rejects_invalid_preview_backend(tmp_path: Path) -> None:
    workspace_root = tmp_path / "pkg"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.yaml").write_text(
        "\n".join(
            [
                "pubify-pubs:",
                "  publications_root: papers",
                "  data_root: output/papers",
                "  preview:",
                "    publication: finder",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="preview.publication must be one of: preview, vscode"):
        load_workspace_config(workspace_root)

def test_load_publication_data_npz_returns_plain_dict_of_arrays(repo: Path) -> None:
    source = repo / "papers" / "demo" / "data" / "generated" / "sample.npz"
    source.parent.mkdir(parents=True, exist_ok=True)
    np.savez(source, alpha=np.array([1.0, 2.0]), beta=np.array([3.0]))

    loaded = load_publication_data_npz(source)

    assert isinstance(loaded, dict)
    assert set(loaded) == {"alpha", "beta"}
    assert np.array_equal(loaded["alpha"], np.array([1.0, 2.0]))
    assert np.array_equal(loaded["beta"], np.array([3.0]))

def test_load_publication_data_npz_missing_path_fails_clearly(repo: Path) -> None:
    missing = repo / "papers" / "demo" / "data" / "generated" / "missing.npz"

    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_publication_data_npz(missing)

def test_load_publication_data_npz_non_file_path_fails_clearly(repo: Path) -> None:
    directory = repo / "papers" / "demo" / "data" / "generated"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "folder.npz"
    path.mkdir()

    with pytest.raises(ValueError, match="must be a file"):
        load_publication_data_npz(path)

def test_load_publication_data_npz_non_npz_path_fails_clearly(repo: Path) -> None:
    path = repo / "papers" / "demo" / "data" / "generated" / "sample.npy"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="must end with \\.npz"):
        load_publication_data_npz(path)

def test_load_publication_data_npz_materializes_arrays_before_returning(repo: Path) -> None:
    source = repo / "papers" / "demo" / "data" / "generated" / "sample.npz"
    source.parent.mkdir(parents=True, exist_ok=True)
    np.savez(source, alpha=np.array([1.0, 2.0]))

    loaded = load_publication_data_npz(source)
    np.savez(source, alpha=np.array([9.0, 9.0]))

    assert np.array_equal(loaded["alpha"], np.array([1.0, 2.0]))

def test_pubs_helpers_exports_load_paper_data_npz() -> None:
    assert callable(load_publication_data_npz)

def test_pubs_helpers_exports_paper_data_path() -> None:
    assert callable(publication_data_path)

def test_external_data_decorator_supports_single_path_form() -> None:
    @external_data("scratch", "training.npy")
    def load_training(ctx, path):
        return path

    assert load_training.__pubs_loader__ == {
        "kind": "external_data",
        "root_name": "scratch",
        "style": "single",
        "paths": {"path": "training.npy"},
        "nocache": False,
    }

def test_external_data_decorator_supports_named_multi_path_form() -> None:
    @external_data("shared", model="bundle.pt", meta="bundle.json", nocache=True)
    def load_bundle(ctx, model, meta):
        return model, meta

    assert load_bundle.__pubs_loader__ == {
        "kind": "external_data",
        "root_name": "shared",
        "style": "named",
        "paths": {"model": "bundle.pt", "meta": "bundle.json"},
        "nocache": True,
    }

def test_external_data_decorator_rejects_absolute_paths() -> None:
    with pytest.raises(ValueError, match="@external_data paths must be relative, not absolute"):
        external_data("scratch", "/abs/training.npy")

def test_external_data_decorator_rejects_parent_traversal() -> None:
    with pytest.raises(
        ValueError,
        match="@external_data paths must stay under their configured root",
    ):
        external_data("scratch", "../training.npy")

def test_stat_decorator_marks_callable() -> None:
    @stat
    def compute_sample_count(ctx):
        return "42"

    assert getattr(compute_sample_count, "__pubs_stat__", False) is True

def test_parser_supports_documented_surface() -> None:
    parser = build_parser()

    assert parser.parse_args(["list"]).subject == "list"
    init_args = parser.parse_args(["init", "demo"])
    assert init_args.subject == "init"
    assert init_args.arg2 == "demo"
    shell_args = parser.parse_args(["demo", "shell"])
    assert shell_args.subject == "demo"
    assert shell_args.arg2 == "shell"
    args = parser.parse_args(["demo", "figure", "compare", "update"])
    assert args.subject == "demo"
    assert args.arg2 == "figure"
    assert args.arg3 == "compare"
    assert args.arg4 == "update"
    data_args = parser.parse_args(["demo", "data", "list"])
    assert data_args.subject == "demo"
    assert data_args.arg2 == "data"
    data_add_args = parser.parse_args(["demo", "data", "add", "example_data"])
    assert data_add_args.arg3 == "add"
    assert data_add_args.arg4 == "example_data"
    stat_args = parser.parse_args(["demo", "stat", "training_summary", "update"])
    assert stat_args.subject == "demo"
    assert stat_args.arg2 == "stat"
    assert stat_args.arg3 == "training_summary"
    assert stat_args.arg4 == "update"
    stat_add_args = parser.parse_args(["demo", "stat", "add", "sample_stat"])
    assert stat_add_args.arg3 == "add"
    assert stat_add_args.arg4 == "sample_stat"
    update_args = parser.parse_args(["demo", "update"])
    assert update_args.subject == "demo"
    assert update_args.arg2 == "update"
    assert update_args.arg3 is None
    assert data_args.arg3 == "list"
    data_alias_args = parser.parse_args(["demo", "data"])
    assert data_alias_args.subject == "demo"
    assert data_alias_args.arg2 == "data"
    assert data_alias_args.arg3 is None
    figure_args = parser.parse_args(["demo", "figure"])
    assert figure_args.subject == "demo"
    assert figure_args.arg2 == "figure"
    assert figure_args.arg3 is None
    figure_list_args = parser.parse_args(["demo", "figure", "list"])
    assert figure_list_args.subject == "demo"
    assert figure_list_args.arg2 == "figure"
    assert figure_list_args.arg3 == "list"
    figure_add_args = parser.parse_args(["demo", "figure", "add", "sample_plot"])
    assert figure_add_args.arg3 == "add"
    assert figure_add_args.arg4 == "sample_plot"
    figure_preview_args = parser.parse_args(["demo", "figure", "compare", "preview", "2"])
    assert figure_preview_args.subject == "demo"
    assert figure_preview_args.arg2 == "figure"
    assert figure_preview_args.arg3 == "compare"
    assert figure_preview_args.arg4 == "preview"
    assert figure_preview_args.arg5 == "2"
    figure_latex_args = parser.parse_args(["demo", "figure", "compare", "latex", "subcaption"])
    assert figure_latex_args.arg3 == "compare"
    assert figure_latex_args.arg4 == "latex"
    assert figure_latex_args.arg5 == "subcaption"
    stat_latex_args = parser.parse_args(["demo", "stat", "training_summary", "tex"])
    assert stat_latex_args.arg3 == "training_summary"
    assert stat_latex_args.arg4 == "tex"
    table_latex_args = parser.parse_args(["demo", "table", "summary", "latex"])
    assert table_latex_args.arg3 == "summary"
    assert table_latex_args.arg4 == "latex"

def test_find_workspace_root_and_list_publications(repo: Path) -> None:
    assert find_workspace_root(repo) == repo
    assert list_publication_ids(repo) == ["demo"]

def test_find_workspace_root_works_from_nested_directory(repo: Path) -> None:
    nested = repo / "papers" / "demo" / "tex" / "sections"
    assert find_workspace_root(nested) == repo

def test_missing_figures_entrypoint_fails_clearly(repo: Path) -> None:
    (repo / "papers" / "demo" / "figures.py").unlink()
    with pytest.raises(FileNotFoundError, match="Missing figures entrypoint:"):
        load_publication_definition(repo, "demo")

def test_named_path_loader_requires_named_parameters_after_ctx(repo: Path) -> None:
    (repo / "papers" / "demo" / "figures.py").write_text(
        "\n".join(
            [
                "from pubify_data import data",
                "",
                "@data(model='bundle/model.txt', meta='bundle/meta.txt')",
                "def load_bundle(ctx, paths):",
                "    return paths",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must accept named path parameters"):
        load_publication_definition(repo, "demo")

def test_check_validates_discovery_and_data_paths(repo: Path, fake_pubify_mpl: FakePubifyBackend) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    check_publication(paper)
    assert sorted(paper.figures) == ["compare", "single"]
    assert sorted(paper.loaders) == ["bundle", "training"]
    assert paper.config.pubify_mpl.template["textwidth_in"] == 6.75
    assert paper.config.pubify_mpl.default_layout == "twowide"

def test_export_uses_documented_output_names_and_cache(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")

    compare_paths = run_figures(paper, "compare")
    assert [path.name for path in compare_paths] == ["compare_1.pdf", "compare_2.pdf"]
    assert compare_paths[0].read_text(encoding="utf-8") == "compare:training"
    assert compare_paths[1].read_text(encoding="utf-8") == "bundle:meta|model"

    export_path = run_figures(paper, "single")[0]
    assert export_path.name == "single.pdf"
    assert export_path.read_text(encoding="utf-8") == "single:training"
    assert paper.module.CALLS["training"] == 2
    assert paper.module.CALLS["bundle"] == 1

def test_check_fails_when_stat_depends_on_unknown_loader(repo: Path) -> None:
    _write_external_paper(
        repo,
        publication_id="badstats",
        external_root_lines=["  project: output"],
        figure_lines=[
            "from pubify_data import stat",
            "",
            "@stat",
            "def compute_missing_dep(ctx, missing_loader):",
            "    return 'x'",
        ],
    )

    publication = load_publication_definition(repo, "badstats")

    with pytest.raises(ValueError, match="Stat 'missing_dep' depends on unknown loader 'missing_loader'"):
        check_publication(publication)

def test_load_publication_definition_rejects_duplicate_stat_ids(repo: Path) -> None:
    _write_external_paper(
        repo,
        publication_id="dupes",
        external_root_lines=["  project: output"],
        figure_lines=[
            "from pubify_data import stat",
            "",
            "@stat",
            "def compute_same(ctx):",
            "    return 'a'",
            "",
            "@stat",
            "def same(ctx):",
            "    return 'b'",
        ],
    )

    with pytest.raises(ValueError, match="Duplicate stat id 'same'"):
        load_publication_definition(repo, "dupes")

def test_run_figures_exposes_publication_rc_context(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
) -> None:
    original_usetex = mpl.rcParams["text.usetex"]

    (repo / "papers" / "demo" / "figures.py").write_text(
        "\n".join(
            [
                "import matplotlib as mpl",
                "import matplotlib.pyplot as plt",
                "from pubify_data import figure",
                "",
                "@figure",
                "def single(ctx):",
                "    with ctx.rc:",
                "        fig, _ax = plt.subplots()",
                "        fig._observed = {",
                "            'text.usetex': mpl.rcParams['text.usetex'],",
                "            'font.family': list(mpl.rcParams['font.family']),",
                "            'font.size': mpl.rcParams['font.size'],",
                "        }",
                "    fig._pubs_name = 'single'",
                "    return fig",
                "",
            ]
        ),
        encoding="utf-8",
    )

    paper = load_publication_definition(repo, "demo")
    run_figures(paper, "single")

    observed = getattr(fake_pubify_mpl.save_calls[0][0], "_observed")
    assert observed["text.usetex"] == original_usetex
    assert observed["font.family"] == ["serif"]
    assert observed["font.size"] == 10

def test_run_stats_resolves_loader_dependencies(repo: Path) -> None:
    publication = load_publication_definition(repo, "demo")

    computed = run_stats(publication)

    assert [item.stat_id for item in computed] == ["training_summary"]
    assert [value.macro_name for value in computed[0].values] == [
        "StatTrainingSummaryValue",
        "StatTrainingSummaryBundle",
    ]
    assert [value.display for value in computed[0].values] == ["training", "meta|model"]
    assert [value.tex for value in computed[0].values] == ["training", r"\texttt{meta|model}"]

def test_full_export_clears_stale_outputs_before_writing(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    paper.paths.autofigures_root.mkdir(parents=True, exist_ok=True)
    (paper.paths.autofigures_root / "stale.pdf").write_text("old", encoding="utf-8")

    export_paths = run_figures(paper)

    assert not (paper.paths.autofigures_root / "stale.pdf").exists()
    assert sorted(path.name for path in export_paths) == ["compare_1.pdf", "compare_2.pdf", "single.pdf"]

def test_targeted_export_does_not_remove_unrelated_outputs(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    paper.paths.autofigures_root.mkdir(parents=True, exist_ok=True)
    (paper.paths.autofigures_root / "keep.pdf").write_text("keep", encoding="utf-8")

    run_figures(paper, "single")

    assert (paper.paths.autofigures_root / "keep.pdf").read_text(encoding="utf-8") == "keep"
    assert (paper.paths.autofigures_root / "single.pdf").exists()

def test_cli_figure_update_prints_paper_relative_paths(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    autofigures_root = paper.paths.autofigures_root
    (autofigures_root / "compare_1.pdf").write_text("compare 1", encoding="utf-8")
    (autofigures_root / "compare_2.pdf").write_text("compare 2", encoding="utf-8")
    (autofigures_root / "keep.pdf").write_text("keep", encoding="utf-8")

    assert main(["demo", "figure", "single", "update"]) == 0
    assert [line for line in capsys.readouterr().out.strip().splitlines() if line] == [
        "Data",
        "- training: loaded",
        "Figures",
        "- single: updated",
    ]
    assert (autofigures_root / "compare_1.pdf").read_text(encoding="utf-8") == "compare 1"
    assert (autofigures_root / "compare_2.pdf").read_text(encoding="utf-8") == "compare 2"
    assert (autofigures_root / "keep.pdf").read_text(encoding="utf-8") == "keep"

def test_cli_data_without_subcommand_aliases_to_data_list(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "data"]) == 0
    output = capsys.readouterr().out
    assert "pinned   training" in output
    assert "training.npy" in output

def test_cli_figure_and_figure_list_print_figure_inventory(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "figure"]) == 0
    output = capsys.readouterr().out.splitlines()
    assert output == [
        "figure   compare   training, bundle",
        "figure   single    training",
    ]

def test_cli_figures_alias_prints_figure_inventory_without_help_exposure(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "figures"]) == 0
    output = capsys.readouterr().out.splitlines()
    assert output == [
        "figure   compare   training, bundle",
        "figure   single    training",
    ]

    assert main(["demo", "figure", "list"]) == 0
    output = capsys.readouterr().out.splitlines()
    assert output == [
        "figure   compare   training, bundle",
        "figure   single    training",
    ]

def test_cli_preview_opens_built_pdf(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[list[Path], str]] = []
    pdf_path = repo / "papers" / "demo" / "tex" / "build" / "main.pdf"
    pdf_path.write_text("pdf", encoding="utf-8")

    monkeypatch.setattr(
        commands_core,
        "open_publication_previews",
        lambda paths, *, backend: opened.append((list(paths), backend)),
    )

    assert main(["demo", "preview"]) == 0
    captured = capsys.readouterr()
    assert opened == [([pdf_path], "preview")]
    assert captured.out.strip() == str(pdf_path)

def test_cli_preview_requires_existing_built_pdf(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "preview"]) == 1
    captured = capsys.readouterr()
    assert "Built publication PDF does not exist:" in captured.err

def test_cli_figure_preview_opens_exported_single_pdf(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[list[Path], str]] = []
    figure_path = repo / "papers" / "demo" / "tex" / "autofigures" / "single.pdf"
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    figure_path.write_text("pdf", encoding="utf-8")

    monkeypatch.setattr(
        commands_core,
        "open_publication_previews",
        lambda paths, *, backend: opened.append((list(paths), backend)),
    )

    assert main(["demo", "figure", "single", "preview"]) == 0
    captured = capsys.readouterr()
    assert opened == [([figure_path], "preview")]
    assert captured.out.strip() == "tex/autofigures/single.pdf"

def test_cli_figure_preview_opens_all_exported_multipanel_pdfs(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[list[Path], str]] = []
    root = repo / "papers" / "demo" / "tex" / "autofigures"
    root.mkdir(parents=True, exist_ok=True)
    left = root / "compare_1.pdf"
    right = root / "compare_2.pdf"
    left.write_text("pdf", encoding="utf-8")
    right.write_text("pdf", encoding="utf-8")

    monkeypatch.setattr(
        commands_core,
        "open_publication_previews",
        lambda paths, *, backend: opened.append((list(paths), backend)),
    )

    assert main(["demo", "figure", "compare", "preview"]) == 0
    captured = capsys.readouterr()
    assert opened == [([left, right], "preview")]
    assert captured.out.splitlines() == [
        "tex/autofigures/compare_1.pdf",
        "tex/autofigures/compare_2.pdf",
    ]

def test_cli_figure_preview_opens_requested_subfigure_pdf(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[list[Path], str]] = []
    root = repo / "papers" / "demo" / "tex" / "autofigures"
    root.mkdir(parents=True, exist_ok=True)
    left = root / "compare_1.pdf"
    right = root / "compare_2.pdf"
    left.write_text("pdf", encoding="utf-8")
    right.write_text("pdf", encoding="utf-8")

    monkeypatch.setattr(
        commands_core,
        "open_publication_previews",
        lambda paths, *, backend: opened.append((list(paths), backend)),
    )

    assert main(["demo", "figure", "compare", "preview", "2"]) == 0
    captured = capsys.readouterr()
    assert opened == [([right], "preview")]
    assert captured.out.strip() == "tex/autofigures/compare_2.pdf"

def test_cli_figure_preview_rejects_out_of_range_subfigure_index(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = repo / "papers" / "demo" / "tex" / "autofigures"
    root.mkdir(parents=True, exist_ok=True)
    (root / "compare_1.pdf").write_text("pdf", encoding="utf-8")
    (root / "compare_2.pdf").write_text("pdf", encoding="utf-8")

    assert main(["demo", "figure", "compare", "preview", "3"]) == 1
    captured = capsys.readouterr()
    assert "requested subfigure 3" in captured.err

def test_cli_figure_preview_requires_exported_pdf(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "figure", "single", "preview"]) == 1
    captured = capsys.readouterr()
    assert "Exported figure PDF does not exist for 'single'." in captured.err

def test_cli_figure_latex_emits_padded_figfloat_block(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "figure", "single", "latex"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("\n\\usepackage{pubify}\n\\figfloat\n")
    assert captured.out.endswith("\n\n")
    assert r"\figone" in captured.out
    assert r"{autofigures/single}" in captured.out
    assert "[Example caption.]" in captured.out
    assert "[fig:single]" in captured.out

def test_cli_figure_latex_subcaption_emits_wrapped_panels(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "figure", "compare", "tex", "subcaption"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("\n\\usepackage{pubify}\n\\figfloat\n")
    assert r"\figtwowide" in captured.out
    assert r"\fig{autofigures/compare_1}[Example subcaption][fig:compare:a]" in captured.out
    assert r"\fig{autofigures/compare_2}[Example subcaption][fig:compare:b]" in captured.out
    assert "[fig:compare]" in captured.out

def test_cli_figure_latex_subcaption_rejects_single_panel(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "figure", "single", "latex", "subcaption"]) == 1

    captured = capsys.readouterr()
    assert "latex subcaption mode is only supported for multi-panel figures" in captured.err

def test_cli_figure_latex_skips_existing_pubify_package_with_options(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (repo / "papers" / "demo" / "tex" / "main.tex").write_text(
        "\n".join(
            [
                r"\documentclass{article}",
                r"\usepackage[demo]{pubify}",
                r"\begin{document}",
                r"Demo",
                r"\end{document}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["demo", "figure", "single", "latex"]) == 0

    captured = capsys.readouterr()
    assert not captured.out.startswith("\n\\usepackage{pubify}")

def test_cli_preview_uses_vscode_backend_when_configured(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[list[Path], str]] = []
    pdf_path = repo / "papers" / "demo" / "tex" / "build" / "main.pdf"
    pdf_path.write_text("pdf", encoding="utf-8")
    (repo / "pubify.yaml").write_text(
        "\n".join(
            [
                "pubify-pubs:",
                "  publications_root: papers",
                "  data_root: output/papers",
                "  preview:",
                "    publication: vscode",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        commands_core,
        "open_publication_previews",
        lambda paths, *, backend: opened.append((list(paths), backend)),
    )

    assert main(["demo", "preview"]) == 0
    assert opened == [([pdf_path], "vscode")]

def test_cli_figure_preview_uses_vscode_backend_when_configured(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[list[Path], str]] = []
    figure_path = repo / "papers" / "demo" / "tex" / "autofigures" / "single.pdf"
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    figure_path.write_text("pdf", encoding="utf-8")
    (repo / "pubify.yaml").write_text(
        "\n".join(
            [
                "pubify-pubs:",
                "  publications_root: papers",
                "  data_root: output/papers",
                "  preview:",
                "    figure: vscode",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        commands_core,
        "open_publication_previews",
        lambda paths, *, backend: opened.append((list(paths), backend)),
    )

    assert main(["demo", "figure", "single", "preview"]) == 0
    assert opened == [([figure_path], "vscode")]

def test_open_publication_previews_uses_preview_backend_on_macos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_text("pdf", encoding="utf-8")

    monkeypatch.setattr(sys, "platform", "darwin")

    def fake_run(command: list[str], **kwargs: object) -> object:
        commands.append(command)
        return object()

    monkeypatch.setattr(subprocess, "run", fake_run)

    core_cli._open_publication_previews([pdf_path], backend="preview")

    assert commands == [["open", "-a", "Preview", str(pdf_path.resolve())]]

def test_open_publication_previews_uses_vscode_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_text("pdf", encoding="utf-8")

    def fake_run(command: list[str], **kwargs: object) -> object:
        commands.append(command)
        return object()

    monkeypatch.setattr(subprocess, "run", fake_run)

    core_cli._open_publication_previews([pdf_path], backend="vscode")

    assert commands == [["code", "-n", str(pdf_path.resolve())]]

def test_open_publication_previews_rejects_preview_backend_off_macos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_text("pdf", encoding="utf-8")
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(RuntimeError, match="supported only on macOS"):
        core_cli._open_publication_previews([pdf_path], backend="preview")

def test_open_publication_previews_reports_missing_vscode_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_text("pdf", encoding="utf-8")

    def missing_code(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing_code)

    with pytest.raises(RuntimeError, match="Could not find the VS Code `code` command on PATH"):
        core_cli._open_publication_previews([pdf_path], backend="vscode")

def test_cli_shell_runs_paper_scoped_commands(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_publication(load_publication_definition(repo, "demo"))
    prompts: list[str] = []
    commands = iter(["data list", "figure single update", "figure single preview 1", "update", "build", "preview", "quit"])

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(commands)

    def fake_build(paper_definition: object) -> object:
        build_path = repo / "papers" / "demo" / "tex" / "build" / "main.pdf"
        build_path.parent.mkdir(parents=True, exist_ok=True)
        build_path.write_text("pdf", encoding="utf-8")
        return None

    previewed: list[tuple[list[Path], str]] = []

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)
    monkeypatch.setattr(commands_core, "build_publication", fake_build)
    monkeypatch.setattr(
        commands_core,
        "open_publication_previews",
        lambda paths, *, backend: previewed.append((list(paths), backend)),
    )

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert prompts == ["demo> "] * 7
    assert "pinned   training   training.npy" in captured.out
    assert "tex/autofigures/single.pdf" in captured.out
    assert "pinned   " in captured.out
    assert "training_summary" in captured.out
    assert str(repo / "papers" / "demo" / "tex" / "build" / "main.pdf") in captured.out
    assert previewed == [
        ([repo / "papers" / "demo" / "tex" / "autofigures" / "single.pdf"], "preview"),
        ([repo / "papers" / "demo" / "tex" / "build" / "main.pdf"], "preview"),
    ]

def test_cli_shell_help_and_rejects_top_level_commands(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = iter(["help", "list", "init demo", "quit"])

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert "Shell commands for demo:" in captured.out
    assert "  data [list|add <data-id>]" in captured.out
    assert "  figure [list|add <figure-id>|update|<figure-id> update|<figure-id> preview [<subfig-idx>]|<figure-id> latex [subcaption]]" in captured.out
    assert "  stat [list|add <stat-id>|update|<stat-id> update|<stat-id> latex]" in captured.out
    assert "  table [list|add <table-id>|update|<table-id> update|<table-id> latex]" in captured.out
    assert "  update" in captured.out
    assert "  preview" in captured.out
    assert "Error: unsupported shell command 'list'" in captured.err
    assert "Error: unsupported shell command 'init'" in captured.err

def test_cli_shell_stat_commands(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = iter(["stat list", "stat training_summary update", "quit"])

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert "training_summary" in captured.out
    assert "Stats" in captured.out
    assert r"  \StatTrainingSummaryValue = training" in captured.out
    assert r"  \StatTrainingSummaryBundle = meta|model" in captured.out

def test_cli_shell_figure_latex_command_emits_padded_snippet(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = iter(["figure compare latex subcaption", "quit"])

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert "\n\\figfloat\n" in captured.out
    assert r"\fig{autofigures/compare_1}[Example subcaption][fig:compare:a]" in captured.out

def test_cli_data_add_inserts_stub_after_last_loader(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "data", "add", "sample_data"]) == 0

    captured = capsys.readouterr()
    figures_path = repo / "papers" / "demo" / "figures.py"
    figures_text = figures_path.read_text(encoding="utf-8")

    assert "Data" in captured.out
    assert "- sample_data: added" in _strip_ansi(captured.out)
    assert "import numpy as np" in figures_text
    assert "def load_sample_data(ctx, file_path):" in figures_text
    assert '"x": np.array([1, 2, 3]),' in figures_text
    assert '"y": np.array([1, 2, 3]),' in figures_text
    assert figures_text.index("def load_bundle") < figures_text.index("def load_sample_data")
    assert figures_text.index("def load_sample_data") < figures_text.index("def plot_single")

def test_cli_figure_add_appends_stub_and_adds_missing_imports(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    figures_path = repo / "papers" / "demo" / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "from pubify_data import data",
                "",
                "@data('training.npy')",
                "def load_training(ctx, path):",
                "    return path.read_text(encoding='utf-8')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["demo", "figure", "add", "sample_plot"]) == 0

    captured = capsys.readouterr()
    figures_text = figures_path.read_text(encoding="utf-8")
    assert "Figures" in captured.out
    assert "- sample_plot: added" in _strip_ansi(captured.out)
    assert "import matplotlib.pyplot as plt" in figures_text
    assert "from pubify_pubs import FigureExport" in figures_text
    assert "from pubify_data import data, figure" in figures_text
    assert figures_text.rstrip().endswith(
        "\n".join(
            [
                "@figure",
                "def plot_sample_plot(ctx, example_data):",
                "    fig, ax = plt.subplots()",
                '    ax.scatter(example_data["x"], example_data["y"])',
                "    return FigureExport(",
                "        fig,",
                '        layout="one",',
                "    )",
            ]
        )
    )

def test_cli_stat_add_appends_stub_and_adds_missing_imports(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    figures_path = repo / "papers" / "demo" / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "from pubify_data import data",
                "",
                "@data('training.npy')",
                "def load_training(ctx, path):",
                "    return path.read_text(encoding='utf-8')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["demo", "stat", "add", "sample_stat"]) == 0

    captured = capsys.readouterr()
    figures_text = figures_path.read_text(encoding="utf-8")
    assert "Stats" in captured.out
    assert "- sample_stat: added" in _strip_ansi(captured.out)
    assert "import numpy as np" in figures_text
    assert "from pubify_data import data, stat" in figures_text
    assert figures_text.rstrip().endswith(
        "\n".join(
            [
                "@stat",
                "def compute_sample_stat(ctx, example_data):",
                "    return {",
                '        "Count": str(example_data["x"].size),',
                '        "Mean": str(example_data["y"].mean()),',
                "    }",
            ]
        )
    )

def test_cli_table_add_appends_stub_and_adds_missing_imports(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    figures_path = repo / "papers" / "demo" / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "from pubify_data import data",
                "",
                "@data('training.npy')",
                "def load_training(ctx, path):",
                "    return path.read_text(encoding='utf-8')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["demo", "table", "add", "sample_table"]) == 0

    captured = capsys.readouterr()
    figures_text = figures_path.read_text(encoding="utf-8")
    assert "Tables" in captured.out
    assert "- sample_table: added" in _strip_ansi(captured.out)
    assert "import numpy as np" in figures_text
    assert "from pubify_pubs import TableResult" in figures_text
    assert "from pubify_data import data, table" in figures_text
    assert figures_text.rstrip().endswith(
        "\n".join(
            [
                "@table",
                "def tabulate_sample_table(ctx, example_data):",
                "    return TableResult(",
                '        np.column_stack((example_data["x"], example_data["y"])),',
                '        formats=["{}", "{}"],',
                "    )",
            ]
        )
    )


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["demo", "data", "add", "training"], "Loader 'training' already exists"),
        (["demo", "figure", "add", "single"], "Figure 'single' already exists"),
        (["demo", "stat", "add", "training_summary"], "Stat 'training_summary' already exists"),
        (
            ["demo", "table", "add", "Bad-Id"],
            "Invalid id 'Bad-Id': ids must be snake_case and start with a letter",
        ),
        (
            ["demo", "data", "add", "Bad-Id"],
            "Invalid id 'Bad-Id': ids must be snake_case and start with a letter",
        ),
    ],
)

def test_cli_add_rejects_duplicate_or_invalid_ids(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    message: str,
) -> None:
    assert main(argv) == 1
    captured = capsys.readouterr()
    assert message in captured.err

def test_cli_add_rejects_function_name_collision(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    figures_path = repo / "papers" / "demo" / "figures.py"
    figures_path.write_text(
        figures_path.read_text(encoding="utf-8")
        + "\n\ndef plot_extra(ctx):\n    return None\n",
        encoding="utf-8",
    )

    assert main(["demo", "figure", "add", "extra"]) == 1

    captured = capsys.readouterr()
    assert f"Function 'plot_extra' already exists in {figures_path}" in captured.err

def test_cli_shell_add_commands_mutate_figures_module(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = iter(
        [
            "data add sample_data",
            "figure add sample_plot",
            "stat add sample_stat",
            "table add sample_table",
            "quit",
        ]
    )

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0

    figures_text = (repo / "papers" / "demo" / "figures.py").read_text(encoding="utf-8")
    assert "import numpy as np" in figures_text
    assert "def load_sample_data(ctx, file_path):" in figures_text
    assert '"x": np.array([1, 2, 3]),' in figures_text
    assert '"y": np.array([1, 2, 3]),' in figures_text
    assert "def plot_sample_plot(ctx, example_data):" in figures_text
    assert "def compute_sample_stat(ctx, example_data):" in figures_text
    assert "def tabulate_sample_table(ctx, example_data):" in figures_text

def test_cli_shell_update_forces_publication_refresh(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = iter(["update", "quit"])
    load_count = 0
    original_load = core_cli.load_publication_definition

    def wrapped_load(repo_root: Path, publication_id: str) -> object:
        nonlocal load_count
        load_count += 1
        return original_load(repo_root, publication_id)

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)
    monkeypatch.setattr(core_cli, "load_publication_definition", wrapped_load)

    assert main(["demo", "shell"]) == 0
    capsys.readouterr()
    assert load_count == 2

def test_cli_shell_auto_reloads_when_figures_py_changes(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_publication(load_publication_definition(repo, "demo"))
    load_count = 0
    original_load = core_cli.load_publication_definition
    figures_path = repo / "papers" / "demo" / "figures.py"
    step = 0

    def wrapped_load(repo_root: Path, publication_id: str) -> object:
        nonlocal load_count
        load_count += 1
        return original_load(repo_root, publication_id)

    def fake_input(prompt: str) -> str:
        nonlocal step
        step += 1
        if step == 1:
            return "data list"
        if step == 2:
            figures_path.write_text(figures_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            return "data list"
        return "quit"

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)
    monkeypatch.setattr(core_cli, "load_publication_definition", wrapped_load)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert captured.out.count("pinned   training   training.npy") == 2
    assert load_count == 2

def test_cli_shell_auto_reloads_when_pub_yaml_changes(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_publication(load_publication_definition(repo, "demo"))
    load_count = 0
    original_load = core_cli.load_publication_definition
    config_path = repo / "papers" / "demo" / "pub.yaml"
    step = 0

    def wrapped_load(repo_root: Path, publication_id: str) -> object:
        nonlocal load_count
        load_count += 1
        return original_load(repo_root, publication_id)

    def fake_input(prompt: str) -> str:
        nonlocal step
        step += 1
        if step == 1:
            return "data list"
        if step == 2:
            config_path.write_text(config_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            return "data list"
        return "quit"

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)
    monkeypatch.setattr(core_cli, "load_publication_definition", wrapped_load)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert captured.out.count("pinned   training   training.npy") == 2
    assert load_count == 2

def test_reload_session_publication_keeps_unchanged_imports_on_build_style_reload(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication_root = repo / "papers" / "demo"
    helper_path = publication_root / "support_plotting.py"
    figures_path = publication_root / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "from pubify_data import figure",
                "from support_plotting import add_title",
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "",
                "@figure",
                "def plot_single(ctx):",
                "    fig, ax = plt.subplots()",
                "    add_title(ax)",
                "    return fig",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    helper_path.write_text(
        "\n".join(
            [
                "def add_title(ax):",
                "    ax.set_title('demo')",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    publication = load_publication_definition(repo, "demo")
    loader_cache, pending_data_output = core_cli._preload_shell_loader_cache(publication)
    method_state = core_shell_incremental.collect_shell_method_state(publication)
    session = core_cli.PublicationShellSession(
        workspace_root=repo,
        publication_id="demo",
        publication=publication,
        fingerprints=core_cli._collect_reload_fingerprints(publication.paths, method_state.imported_module_paths),
        loader_cache=loader_cache,
        pending_data_output=pending_data_output,
        method_state=method_state,
        last_success_method_state=method_state,
        cached_figure_output_names={},
        cached_stats={},
        cached_tables={},
    )

    purged: list[Path] = []

    def fake_purge(paths: object) -> None:
        purged.extend(Path(path) for path in paths)

    monkeypatch.setattr(core_cli, "purge_modules_by_paths", fake_purge)
    figures_path.write_text(figures_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = core_cli._reload_session_publication(session, purge_all_imported_modules=False)

    assert result.reloaded is True
    assert result.imported_modules_changed is False
    assert purged == []

def test_cli_shell_update_reloads_publication_local_helpers_after_loader_rename(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication_root = repo / "papers" / "demo"
    figures_path = publication_root / "figures.py"
    helpers_path = publication_root / "helpers.py"
    figures_path.write_text(
        "\n".join(
            [
                "from pubify_data import data",
                "from helpers import compute_summary, plot_single",
                "",
                "@data('training.npy')",
                "def load_training(ctx, path):",
                "    return 'training'",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    helpers_path.write_text(
        "\n".join(
            [
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "from pubify_data import figure, stat",
                "",
                "@figure",
                "def plot_single(ctx, training):",
                "    fig, _ax = plt.subplots()",
                "    return fig",
                "",
                "@stat",
                "def compute_summary(ctx, training):",
                "    return training",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    init_publication(load_publication_definition(repo, "demo"))
    data_root = repo / "papers" / "demo" / "data"
    (data_root / "training.npy").write_bytes(b"training")
    (data_root / "dataset.npy").write_bytes(b"dataset")
    step = 0

    def fake_input(prompt: str) -> str:
        nonlocal step
        step += 1
        if step == 1:
            return "data list"
        if step == 2:
            figures_path.write_text(
                "\n".join(
                    [
                        "from pubify_data import data",
                        "from helpers import compute_summary, plot_single",
                        "",
                        "@data('dataset.npy')",
                        "def load_dataset(ctx, path):",
                        "    return 'dataset'",
                        "",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            helpers_path.write_text(
                "\n".join(
                    [
                        "import matplotlib",
                        "matplotlib.use('Agg')",
                        "import matplotlib.pyplot as plt",
                        "from pubify_data import figure, stat",
                        "",
                        "@figure",
                        "def plot_single(ctx, dataset):",
                        "    fig, _ax = plt.subplots()",
                        "    return fig",
                        "",
                        "@stat",
                        "def compute_summary(ctx, dataset):",
                        "    return dataset",
                        "",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            return "update"
        if step == 3:
            return "data list"
        return "quit"

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert "pinned   training   training.npy" in captured.out
    assert "pinned   dataset   dataset.npy" in captured.out
    assert "Figures" in captured.out
    assert "depends on unknown loader" not in captured.err

def test_cli_shell_reload_failure_keeps_session_alive(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_publication(load_publication_definition(repo, "demo"))
    figures_path = repo / "papers" / "demo" / "figures.py"
    original_text = figures_path.read_text(encoding="utf-8")
    step = 0

    def fake_input(prompt: str) -> str:
        nonlocal step
        step += 1
        if step == 1:
            return "data list"
        if step == 2:
            figures_path.write_text("def broken(:\n", encoding="utf-8")
            return "data list"
        if step == 3:
            figures_path.write_text(original_text, encoding="utf-8")
            return "data list"
        return "quit"

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert captured.out.count("pinned   training   training.npy") == 2
    assert "Error:" in captured.err

def test_cli_shell_user_code_exception_keeps_session_alive(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication_root = repo / "papers" / "demo"
    figures_path = publication_root / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "from pubify_data import figure",
                "",
                "@figure",
                "def plot_single(ctx):",
                "    print('about to explode')",
                "    raise ZeroDivisionError('bad math')",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    init_publication(load_publication_definition(repo, "demo"))
    commands = iter(["figure single update", "data list", "quit"])

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert "  about to explode" in captured.out
    assert "  ZeroDivisionError: bad math" in captured.out
    assert "demo: no declared data" in captured.out

def test_cli_figure_update_fails_cleanly_for_tuple_returning_loader(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    figures_path = repo / "papers" / "demo" / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "from pubify_data import data, figure",
                "",
                "@data('training.npy')",
                "def load_training(ctx, path):",
                "    return path, path",
                "",
                "@figure",
                "def plot_single(ctx, training):",
                "    fig, ax = plt.subplots()",
                "    ax.plot([0, 1], [0, 1])",
                "    return fig",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["demo", "figure", "update"]) == 1

    captured = capsys.readouterr()
    assert "Loader 'training' returned a tuple." in captured.out
    assert "wrap multiple values in a dict, dataclass, or other single container." in captured.out

def test_cli_figure_update_fails_cleanly_for_none_returning_loader(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    figures_path = repo / "papers" / "demo" / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "from pubify_data import data, figure",
                "",
                "@data('training.npy')",
                "def load_training(ctx, path):",
                "    return None",
                "",
                "@figure",
                "def plot_single(ctx, training):",
                "    fig, ax = plt.subplots()",
                "    ax.plot([0, 1], [0, 1])",
                "    return fig",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["demo", "figure", "update"]) == 1

    captured = capsys.readouterr()
    assert "Loader 'training' returned None. Loaders must return one object." in captured.out

def test_cli_shell_loader_tuple_return_keeps_session_alive(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    figures_path = repo / "papers" / "demo" / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "from pubify_data import data, figure",
                "",
                "@data('training.npy', nocache=True)",
                "def load_training(ctx, path):",
                "    return path, path",
                "",
                "@figure",
                "def plot_single(ctx, training):",
                "    fig, ax = plt.subplots()",
                "    ax.plot([0, 1], [0, 1])",
                "    return fig",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    commands = iter(["figure update", "data list", "quit"])

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0

    captured = capsys.readouterr()
    assert "Loader 'training' returned a tuple." in captured.out
    assert "wrap multiple values in a dict, dataclass, or other single container." in captured.out
    assert "pinned   training   training.npy" in captured.out

def test_cli_shell_loader_none_return_keeps_session_alive(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    figures_path = repo / "papers" / "demo" / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "from pubify_data import data, figure",
                "",
                "@data('training.npy', nocache=True)",
                "def load_training(ctx, path):",
                "    return None",
                "",
                "@figure",
                "def plot_single(ctx, training):",
                "    fig, ax = plt.subplots()",
                "    ax.plot([0, 1], [0, 1])",
                "    return fig",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    commands = iter(["figure update", "data list", "quit"])

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0

    captured = capsys.readouterr()
    assert "Loader 'training' returned None. Loaders must return one object." in captured.out
    assert "pinned" in captured.out

def test_cli_shell_update_recomputes_loader_data(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication_root = repo / "papers" / "demo"
    figures_path = publication_root / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "from pubify_data import data, stat",
                "",
                "@data('training.txt')",
                "def load_training(ctx, path):",
                "    return Path(path).read_text(encoding='utf-8').strip()",
                "",
                "@stat",
                "def compute_training_value(ctx, training):",
                "    return training",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    training_path = repo / "papers" / "demo" / "data" / "training.txt"
    training_path.write_text("first\n", encoding="utf-8")
    init_publication(load_publication_definition(repo, "demo"))
    commands = iter(["stat training_value update", "update", "quit"])

    def fake_input(prompt: str) -> str:
        command = next(commands)
        if command == "update":
            training_path.write_text("second\n", encoding="utf-8")
        return command

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert r"  \StatTrainingValue = first" in captured.out
    assert r"  \StatTrainingValue = second" in captured.out

def test_cli_shell_preloads_normal_loaders_once_and_reuses_them_across_commands(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication_root = repo / "papers" / "demo"
    figures_path = publication_root / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "from pubify_data import data, stat",
                "",
                "@data('training.txt')",
                "def load_training(ctx, path):",
                "    value = Path(path).read_text(encoding='utf-8').strip()",
                "    print(f'loading training {value}')",
                "    return value",
                "",
                "@stat",
                "def compute_training_value(ctx, training):",
                "    return training",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    training_path = repo / "papers" / "demo" / "data" / "training.txt"
    training_path.write_text("alpha\n", encoding="utf-8")
    init_publication(load_publication_definition(repo, "demo"))
    commands = iter(["stat training_value update", "stat training_value update", "quit"])

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert captured.out.count("loading training alpha") == 1
    assert captured.out.index("loading training alpha") < captured.out.index("training_value")
    assert captured.out.count(r"  \StatTrainingValue = alpha") == 2

def test_cli_shell_nocache_loader_runs_once_per_command(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication_root = repo / "papers" / "demo"
    figures_path = publication_root / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "from pubify_data import data, stat",
                "",
                "@data('training.txt', nocache=True)",
                "def load_training(ctx, path):",
                "    value = Path(path).read_text(encoding='utf-8').strip()",
                "    print(f'loading training {value}')",
                "    return value",
                "",
                "@stat",
                "def compute_training_value(ctx, training):",
                "    return training",
                "",
                "@stat",
                "def compute_training_again(ctx, training):",
                "    return training",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    training_path = repo / "papers" / "demo" / "data" / "training.txt"
    training_path.write_text("beta\n", encoding="utf-8")
    init_publication(load_publication_definition(repo, "demo"))
    commands = iter(["stat update", "stat update", "quit"])

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert captured.out.count("loading training beta") == 2
    assert captured.out.count(r"  \StatTrainingValue = beta") == 2
    assert captured.out.count(r"  \StatTrainingAgain = beta") == 2

def test_cli_shell_persists_history_in_publication_root(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_publication(load_publication_definition(repo, "demo"))
    history_path = repo / "papers" / "demo" / ".pubs-history"
    history_path.write_text("data list\n", encoding="utf-8")
    fake_readline = FakeReadline()
    commands = iter(["figure single update", "quit"])

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: fake_readline)

    assert main(["demo", "shell"]) == 0
    assert fake_readline.read_paths == [str(history_path)]
    assert history_path.read_text(encoding="utf-8").splitlines() == ["data list", "figure single update", "quit"]

def test_cli_shell_history_is_trimmed_to_recent_entries(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_publication(load_publication_definition(repo, "demo"))
    history_path = repo / "papers" / "demo" / ".pubs-history"
    fake_readline = FakeReadline()
    history_path.write_text(
        "\n".join(f"cmd-{idx}" for idx in range(core_cli.SHELL_HISTORY_LIMIT + 25)) + "\n",
        encoding="utf-8",
    )
    commands = iter(["quit"])

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: fake_readline)

    assert main(["demo", "shell"]) == 0
    lines = history_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == core_cli.SHELL_HISTORY_LIMIT
    assert lines[0] == "cmd-26"
    assert lines[-2] == f"cmd-{core_cli.SHELL_HISTORY_LIMIT + 24}"
    assert lines[-1] == "quit"

def test_cli_shell_persists_history_when_readline_already_contains_latest_entry(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_publication(load_publication_definition(repo, "demo"))
    history_path = repo / "papers" / "demo" / ".pubs-history"
    history_path.write_text("data list\n", encoding="utf-8")
    fake_readline = FakeReadline()
    commands = iter(["figure single update", "quit"])

    def fake_input(prompt: str) -> str:
        line = next(commands)
        fake_readline.history.append(line)
        return line

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: fake_readline)

    assert main(["demo", "shell"]) == 0
    assert history_path.read_text(encoding="utf-8").splitlines() == ["data list", "figure single update", "quit"]

def test_configure_shell_readline_binds_arrow_keys_for_gnu_and_libedit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gnu_readline = FakeReadline()
    gnu_readline.__doc__ = "GNU readline interface"
    monkeypatch.setitem(sys.modules, "readline", gnu_readline)
    assert core_cli._configure_shell_readline() is gnu_readline
    assert '"\\e[C": forward-char' in gnu_readline.bindings
    assert '"\\e[D": backward-char' in gnu_readline.bindings
    assert '"\\eOC": forward-char' in gnu_readline.bindings
    assert '"\\eOD": backward-char' in gnu_readline.bindings
    assert gnu_readline.auto_history_values == [False]

    libedit_readline = FakeReadline()
    libedit_readline.__doc__ = "libedit readline compatibility"
    monkeypatch.setitem(sys.modules, "readline", libedit_readline)
    assert core_cli._configure_shell_readline() is libedit_readline
    assert "bind ^[[C ed-next-char" in libedit_readline.bindings
    assert "bind ^[[D ed-prev-char" in libedit_readline.bindings
    assert "bind ^[OC ed-next-char" in libedit_readline.bindings
    assert "bind ^[OD ed-prev-char" in libedit_readline.bindings
    assert libedit_readline.auto_history_values == [False]

def test_subfigure_index_is_one_based(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")

    output = run_figures(paper, "compare", 2)
    assert [path.name for path in output] == ["compare_2.pdf"]

    with pytest.raises(UserCodeExecutionError):
        run_figures(paper, "compare", 3)

def test_build_runs_from_tex_into_tex_build(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    calls: list[tuple[list[str], Path]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, "", "")

    build_publication(paper, runner=runner)
    assert calls == [
        (
            [
                "latexmk",
                "-pdf",
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-outdir={paper.paths.build_root}",
                "main.tex",
            ],
            paper.paths.tex_root,
        )
    ]

def test_build_retries_stale_latexmk_failure_with_force(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    calls: list[tuple[list[str], Path]] = []

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append((command, cwd))
        if len(calls) == 1:
            raise subprocess.CalledProcessError(
                12,
                command,
                "Latexmk: Nothing to do for 'main.tex'.\n"
                "Collected error summary (may duplicate other messages):\n"
                "  pdflatex: gave an error in previous invocation of latexmk.\n",
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    build_publication(paper, runner=runner)

    assert calls == [
        (
            [
                "latexmk",
                "-pdf",
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-outdir={paper.paths.build_root}",
                "main.tex",
            ],
            paper.paths.tex_root,
        ),
        (
            [
                "latexmk",
                "-g",
                "-pdf",
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-outdir={paper.paths.build_root}",
                "main.tex",
            ],
            paper.paths.tex_root,
        ),
    ]

def test_build_failure_reports_latex_diagnostic_from_log(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    log_path = paper.paths.build_root / "main.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                "(/tmp/demo.tex",
                "! LaTeX Error: File `missing.sty' not found.",
                "demo.tex:12: Undefined control sequence",
                "l.12 \\usepackage{missing}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(12, command, "", "")

    try:
        build_publication(paper, runner=runner)
    except ValueError as exc:
        text = str(exc)
    else:
        raise AssertionError("build_publication should have raised ValueError")
    assert "latexmk exit 12" in text
    assert f"Log file: {log_path}" in text
    assert "LaTeX error: LaTeX Error: File `missing.sty' not found." in text
    assert "Source: demo.tex:12: Undefined control sequence" in text
    assert r"Context: l.12 \usepackage{missing}" in text

def test_build_failure_reports_partial_signal_when_available(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    log_path = paper.paths.build_root / "main.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "Runaway argument?\nmore text\n",
        encoding="utf-8",
    )

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, command, "", "")

    with pytest.raises(ValueError) as exc_info:
        build_publication(paper, runner=runner)

    text = str(exc_info.value)
    assert "latexmk exit 1" in text
    assert f"Log file: {log_path}" in text
    assert "LaTeX error: Runaway argument?" in text

def test_build_failure_reports_cant_find_file_over_fatal_error(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    log_path = paper.paths.build_root / "main.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                "! I can't find file `pst-tools.tex'.",
                "l.22 \\ifx\\PSTtoolsloaded\\endinput\\else\\input pstricks-add.tex\\fi",
                "! Emergency stop.",
                "!  ==> Fatal error occurred, no output PDF file produced!",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, command, "", "")

    with pytest.raises(ValueError) as exc_info:
        build_publication(paper, runner=runner)

    text = str(exc_info.value)
    assert "LaTeX error: I can't find file `pst-tools.tex'." in text
    assert "Fatal error occurred" not in text

def test_build_failure_reports_missing_log_fallback(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(2, command, "", "")

    with pytest.raises(ValueError) as exc_info:
        build_publication(paper, runner=runner)

    text = str(exc_info.value)
    assert "latexmk exit 2" in text
    assert f"Log file: {paper.paths.build_root / 'main.log'}" in text
    assert "no LaTeX diagnostic could be extracted" in text

def test_cli_build_reports_expected_error_without_traceback(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    log_path = paper.paths.build_root / "main.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("! Undefined control sequence.\n", encoding="utf-8")

    def fail_build(paper_definition: object) -> object:
        raise ValueError(
            "\n".join(
                [
                    "LaTeX build failed for 'demo' (latexmk exit 1).",
                    f"Log file: {log_path}",
                    "LaTeX error: Undefined control sequence.",
                ]
            )
        )

    monkeypatch.setattr(commands_core, "build_publication", fail_build)

    assert main(["demo", "build"]) == 1
    captured = capsys.readouterr()
    assert "Error: LaTeX build failed for 'demo' (latexmk exit 1)." in captured.err
    assert "Traceback" not in captured.err

def test_cli_build_prints_pdf_output_path(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def succeed_build(paper_definition: object) -> object:
        return None

    monkeypatch.setattr(commands_core, "build_publication", succeed_build)

    assert main(["demo", "build"]) == 0
    captured = capsys.readouterr()
    lines = [line for line in captured.out.strip().splitlines() if line]
    assert lines[-2] == "PDF"
    assert lines[-1] == f"- {repo / 'papers' / 'demo' / 'tex' / 'build' / 'main.pdf'}: updated"

def test_cli_build_does_not_refresh_generated_inputs(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_run_figures(*args: object, **kwargs: object) -> list[Path]:
        calls.append("export")
        return [repo / "papers" / "demo" / "tex" / "autofigures" / "single.pdf"]

    def fake_run_stat_updates(*args: object, **kwargs: object) -> tuple[Path, tuple[object, ...]]:
        calls.append("stats")
        return (repo / "papers" / "demo" / "tex" / "autostats.tex", ())

    def fake_build(paper_definition: object) -> object:
        calls.append("build")
        return None

    monkeypatch.setattr(commands_core, "run_figures", fake_run_figures)
    monkeypatch.setattr(commands_core, "run_stat_updates", fake_run_stat_updates)
    monkeypatch.setattr(commands_core, "build_publication", fake_build)
    init_publication(load_publication_definition(repo, "demo"))

    assert main(["demo", "build"]) == 0
    captured = capsys.readouterr()
    assert calls == ["build"]
    assert captured.out.strip().endswith("/tex/build/main.pdf: updated")

def test_cli_build_does_not_refresh_when_outputs_are_stale_or_missing(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_build(paper_definition: object) -> object:
        calls.append("build")
        return None

    def fake_run_figures(*args: object, **kwargs: object) -> list[Path]:
        calls.append("export")
        return [repo / "papers" / "demo" / "tex" / "autofigures" / "single.pdf"]

    def fake_run_stat_updates(*args: object, **kwargs: object) -> tuple[Path, tuple[object, ...]]:
        calls.append("stats")
        return (repo / "papers" / "demo" / "tex" / "autostats.tex", ())

    monkeypatch.setattr(commands_core, "build_publication", fake_build)
    monkeypatch.setattr(commands_core, "run_figures", fake_run_figures)
    monkeypatch.setattr(commands_core, "run_stat_updates", fake_run_stat_updates)

    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    if paper.paths.autofigures_root.exists():
        shutil.rmtree(paper.paths.autofigures_root)
    paper.paths.autostats_path.unlink(missing_ok=True)

    assert main(["demo", "build"]) == 0
    captured = capsys.readouterr()
    assert calls == ["build"]
    assert captured.out.strip().endswith("/tex/build/main.pdf: updated")

def test_cli_shell_build_does_not_refresh_generated_outputs(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    commands = iter(["build", "build", "quit"])
    publication = load_publication_definition(repo, "demo")
    init_publication(publication)
    publication.paths.autofigures_root.mkdir(parents=True, exist_ok=True)
    (publication.paths.autofigures_root / "compare_1.pdf").write_text("compare-1", encoding="utf-8")
    (publication.paths.autofigures_root / "compare_2.pdf").write_text("compare-2", encoding="utf-8")
    (publication.paths.autofigures_root / "single.pdf").write_text("single", encoding="utf-8")
    publication.paths.autostats_path.write_text("% stats\n", encoding="utf-8")
    publication.paths.autotables_path.write_text("% tables\n", encoding="utf-8")

    def fake_run_figures(*args: object, **kwargs: object) -> list[Path]:
        figure_id = args[1]
        calls.append("export")
        autofigures_root = repo / "papers" / "demo" / "tex" / "autofigures"
        autofigures_root.mkdir(parents=True, exist_ok=True)
        if figure_id == "compare":
            first = autofigures_root / "compare_1.pdf"
            second = autofigures_root / "compare_2.pdf"
            first.write_text("compare-1", encoding="utf-8")
            second.write_text("compare-2", encoding="utf-8")
            return [first, second]
        output = autofigures_root / "single.pdf"
        output.write_text("single", encoding="utf-8")
        return [output]

    def fake_run_stat_updates(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append("stats")
        publication.paths.autostats_path.write_text("% stats\n", encoding="utf-8")
        return {}

    def fake_build(paper_definition: object) -> object:
        calls.append("build")
        return None

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)
    monkeypatch.setattr(commands_core, "build_publication", fake_build)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert calls == ["build", "build"]
    assert "Figures" not in captured.out

def test_cli_shell_build_after_update_still_only_builds(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    commands = iter(["update", "build", "quit"])

    def fake_run_figures(*args: object, **kwargs: object) -> list[Path]:
        figure_id = args[1]
        calls.append("export")
        autofigures_root = repo / "papers" / "demo" / "tex" / "autofigures"
        autofigures_root.mkdir(parents=True, exist_ok=True)
        if figure_id == "compare":
            first = autofigures_root / "compare_1.pdf"
            second = autofigures_root / "compare_2.pdf"
            first.write_text("compare-1", encoding="utf-8")
            second.write_text("compare-2", encoding="utf-8")
            return [first, second]
        output = autofigures_root / "single.pdf"
        output.write_text("single", encoding="utf-8")
        return [output]

    def fake_run_stat_updates(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append("stats")
        (repo / "papers" / "demo" / "tex" / "autostats.tex").write_text("% stats\n", encoding="utf-8")
        return {}

    def fake_run_table_updates(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append("tables")
        (repo / "papers" / "demo" / "tex" / "autotables.tex").write_text("% tables\n", encoding="utf-8")
        return {}

    def fake_build(paper_definition: object) -> object:
        calls.append("build")
        return None

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)
    monkeypatch.setattr(commands_core, "build_publication", fake_build)

    assert main(["demo", "shell"]) == 0
    capsys.readouterr()
    assert calls == ["build"]

def test_cli_shell_build_reloads_publication_when_imported_module_changes(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication_root = repo / "papers" / "demo"
    figures_path = publication_root / "figures.py"
    helper_path = publication_root / "support_plotting.py"
    figures_path.write_text(
        "\n".join(
            [
                "from pubify_data import figure",
                "from support_plotting import add_title",
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "",
                "@figure",
                "def plot_single(ctx):",
                "    fig, ax = plt.subplots()",
                "    add_title(ax)",
                "    return fig",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    helper_path.write_text(
        "\n".join(
            [
                "def add_title(ax):",
                "    ax.set_title('v1')",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    load_publication_definition(repo, "demo")
    commands = iter(["build", "build", "quit"])
    step = 0

    def fake_input(prompt: str) -> str:
        nonlocal step
        step += 1
        if step == 2:
            helper_path.write_text(
                "\n".join(
                    [
                        "def add_title(ax):",
                        "    ax.set_title('v2')",
                        "",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
        return next(commands)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)
    monkeypatch.setattr(commands_core, "build_publication", lambda publication: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""

def test_collect_shell_method_state_tracks_figures_py_helpers_only_for_dependent_nodes(
    repo: Path,
) -> None:
    publication_root = repo / "papers" / "demo"
    figures_path = publication_root / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "from pubify_data import figure",
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "",
                "def helper_used():",
                "    return 'a'",
                "",
                "def helper_unused():",
                "    return 'b'",
                "",
                "@figure",
                "def plot_compare(ctx):",
                "    fig, ax = plt.subplots()",
                "    ax.set_title(helper_used())",
                "    return fig",
                "",
                "@figure",
                "def plot_single(ctx):",
                "    fig, _ax = plt.subplots()",
                "    return fig",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    publication = load_publication_definition(repo, "demo")
    publication.paths.autofigures_root.mkdir(parents=True, exist_ok=True)
    (publication.paths.autofigures_root / "compare.pdf").write_text("compare", encoding="utf-8")
    (publication.paths.autofigures_root / "single.pdf").write_text("single", encoding="utf-8")
    original_state = core_shell_incremental.collect_shell_method_state(publication)

    figures_path.write_text(
        figures_path.read_text(encoding="utf-8").replace("return 'a'", "return 'changed'"),
        encoding="utf-8",
    )
    updated_publication = load_publication_definition(repo, "demo")
    updated_state = core_shell_incremental.collect_shell_method_state(updated_publication)
    plan = core_shell_incremental.plan_incremental_shell_build(
        updated_publication,
        updated_state,
        original_state,
        cached_figure_output_names={"compare": ("compare.pdf",), "single": ("single.pdf",)},
        cached_stats_complete=True,
        cached_tables_complete=True,
    )

    assert plan.full_refresh is False
    assert plan.figure_ids == ("compare",)
    assert plan.changed_loader_ids == ()
    assert plan.stat_ids == ()
    assert plan.table_ids == ()

def test_collect_shell_method_state_tracks_figures_py_constants_only_for_dependent_nodes(
    repo: Path,
) -> None:
    publication_root = repo / "papers" / "demo"
    figures_path = publication_root / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "from pubify_data import figure",
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "",
                "PLOT_TITLE = 'a'",
                "OTHER_TITLE = 'b'",
                "",
                "@figure",
                "def plot_compare(ctx):",
                "    fig, ax = plt.subplots()",
                "    ax.set_title(PLOT_TITLE)",
                "    return fig",
                "",
                "@figure",
                "def plot_single(ctx):",
                "    fig, _ax = plt.subplots()",
                "    return fig",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    publication = load_publication_definition(repo, "demo")
    publication.paths.autofigures_root.mkdir(parents=True, exist_ok=True)
    (publication.paths.autofigures_root / "compare.pdf").write_text("compare", encoding="utf-8")
    (publication.paths.autofigures_root / "single.pdf").write_text("single", encoding="utf-8")
    original_state = core_shell_incremental.collect_shell_method_state(publication)

    figures_path.write_text(
        figures_path.read_text(encoding="utf-8").replace("PLOT_TITLE = 'a'", "PLOT_TITLE = 'changed'"),
        encoding="utf-8",
    )
    updated_publication = load_publication_definition(repo, "demo")
    updated_state = core_shell_incremental.collect_shell_method_state(updated_publication)
    plan = core_shell_incremental.plan_incremental_shell_build(
        updated_publication,
        updated_state,
        original_state,
        cached_figure_output_names={"compare": ("compare.pdf",), "single": ("single.pdf",)},
        cached_stats_complete=True,
        cached_tables_complete=True,
    )

    assert plan.full_refresh is False
    assert plan.figure_ids == ("compare",)
    assert plan.changed_loader_ids == ()
    assert plan.stat_ids == ()
    assert plan.table_ids == ()

def test_collect_shell_method_state_tracks_external_editable_submodules(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_root = repo.parent / "external_tools_repo"
    package_root = external_root / "external_tools"
    package_root.mkdir(parents=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    helper_path = package_root / "helper.py"
    helper_path.write_text(
        "\n".join(
            [
                "def value():",
                "    return 1",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(external_root))

    publication_root = repo / "papers" / "demo"
    figures_path = publication_root / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "from external_tools import helper",
                "from pubify_data import figure",
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "",
                "@figure",
                "def plot_single(ctx):",
                "    fig, ax = plt.subplots()",
                "    ax.set_title(str(helper.value()))",
                "    return fig",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    publication = load_publication_definition(repo, "demo")
    state = core_shell_incremental.collect_shell_method_state(publication)

    assert helper_path.resolve() in state.imported_module_paths

def test_plan_incremental_shell_build_marks_figure_stale_when_output_names_change_with_same_count(
    repo: Path,
) -> None:
    publication = load_publication_definition(repo, "demo")
    publication.paths.autofigures_root.mkdir(parents=True, exist_ok=True)
    (publication.paths.autofigures_root / "compare_1.pdf").write_text("compare-1", encoding="utf-8")
    (publication.paths.autofigures_root / "compare_2.pdf").write_text("compare-2", encoding="utf-8")
    (publication.paths.autofigures_root / "single.pdf").write_text("single", encoding="utf-8")
    state = core_shell_incremental.collect_shell_method_state(publication)

    plan = core_shell_incremental.plan_incremental_shell_build(
        publication,
        state,
        state,
        cached_figure_output_names={"compare": ("compare.pdf", "compare_2.pdf"), "single": ("single.pdf",)},
        cached_stats_complete=True,
        cached_tables_complete=True,
    )

    assert plan.full_refresh is False
    assert plan.figure_ids == ("compare",)
    assert plan.changed_loader_ids == ()
    assert plan.stat_ids == ()
    assert plan.table_ids == ()

def test_figure_output_matching_does_not_cross_match_shared_suffix_names(
    repo: Path,
) -> None:
    publication_root = repo / "papers" / "demo"
    figures_path = publication_root / "figures.py"
    figures_path.write_text(
        "\n".join(
            [
                "from pubify_pubs import FigureExport",
                "from pubify_data import figure",
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "",
                "@figure",
                "def plot_field_ee_maps(ctx):",
                "    fig1, _ax1 = plt.subplots()",
                "    fig2, _ax2 = plt.subplots()",
                "    return FigureExport([fig1, fig2], layout='twowide')",
                "",
                "@figure",
                "def plot_field_ee_maps_shared(ctx):",
                "    fig, _ax = plt.subplots()",
                "    return FigureExport(fig, layout='onewide')",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    publication = load_publication_definition(repo, "demo")
    root = publication.paths.autofigures_root
    root.mkdir(parents=True, exist_ok=True)
    (root / "field_ee_maps_1.pdf").write_text("1", encoding="utf-8")
    (root / "field_ee_maps_2.pdf").write_text("2", encoding="utf-8")
    (root / "field_ee_maps_shared.pdf").write_text("shared", encoding="utf-8")

    assert core_shell_incremental._current_figure_output_names(publication, "field_ee_maps") == (
        "field_ee_maps_1.pdf",
        "field_ee_maps_2.pdf",
    )
    assert core_shell_incremental._current_figure_output_names(publication, "field_ee_maps_shared") == (
        "field_ee_maps_shared.pdf",
    )

def test_collect_shell_method_state_skips_pubify_package_modules(
    repo: Path,
) -> None:
    publication = load_publication_definition(repo, "demo")
    state = core_shell_incremental.collect_shell_method_state(publication)

    assert all("pubify_pubs" not in str(path) for path in state.imported_module_paths)
    assert all("pubify-mpl" not in str(path) for path in state.imported_module_paths)

def test_cli_build_rejects_update_flag(repo: Path) -> None:
    with pytest.raises(SystemExit):
        main(["demo", "build", "--update"])

def test_cli_update_runs_figure_and_stat_refresh(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_run_figures(*args: object, **kwargs: object) -> list[Path]:
        calls.append("figure")
        return [repo / "papers" / "demo" / "tex" / "autofigures" / "single.pdf"]

    def fake_run_stat_updates(*args: object, **kwargs: object) -> tuple[Path, tuple[object, ...]]:
        calls.append("stat")
        return (repo / "papers" / "demo" / "tex" / "autostats.tex", ())

    monkeypatch.setattr(commands_core, "run_figures", fake_run_figures)
    monkeypatch.setattr(commands_core, "run_stat_updates", fake_run_stat_updates)

    assert main(["demo", "update"]) == 0
    captured = capsys.readouterr()
    assert calls == ["figure", "figure", "stat"]
    lines = [line for line in captured.out.strip().splitlines() if line]
    assert lines == [
        "Data",
        "- bundle: loaded",
        "- training: loaded",
        "Publication Files",
        "- tex/pubify.sty: updated",
        "- tex/pubify-template.tex: updated",
        "Figures",
        "- compare: updated",
        "- single: updated",
    ]
    paper = load_publication_definition(repo, "demo")
    assert fake_pubify_mpl.prepare_calls == [(paper.paths.tex_root, paper.config.pubify_mpl.template)]

def test_cli_build_clear_removes_existing_build_outputs(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_build(paper_definition: object) -> object:
        calls.append("build")
        return None

    monkeypatch.setattr(commands_core, "build_publication", fake_build)

    paper = load_publication_definition(repo, "demo")
    paper.paths.build_root.mkdir(parents=True, exist_ok=True)
    stale_pdf = paper.paths.build_root / "main.pdf"
    stale_aux = paper.paths.build_root / "main.aux"
    nested_dir = paper.paths.build_root / "cache"
    stale_pdf.write_text("old pdf\n", encoding="utf-8")
    stale_aux.write_text("old aux\n", encoding="utf-8")
    nested_dir.mkdir()
    (nested_dir / "temp.txt").write_text("nested\n", encoding="utf-8")

    assert main(["demo", "build", "--clear"]) == 0

    captured = capsys.readouterr()
    assert calls == ["build"]
    assert captured.out.strip().endswith("/tex/build/main.pdf: updated")
    assert paper.paths.build_root.exists()
    assert not stale_pdf.exists()
    assert not stale_aux.exists()
    assert not nested_dir.exists()

def test_cli_build_still_rejects_force(repo: Path) -> None:
    with pytest.raises(SystemExit):
        main(["demo", "build", "--force"])

def test_cli_list_rejects_clear_flag(repo: Path) -> None:
    with pytest.raises(SystemExit):
        main(["list", "--clear"])

def test_init_creates_tex_tree_and_runs_prepare(repo: Path, fake_pubify_mpl: FakePubifyBackend) -> None:
    paper = load_publication_definition(repo, "demo")
    for path in (paper.paths.autofigures_root, paper.paths.build_root):
        if path.exists():
            if path.is_dir():
                for child in sorted(path.rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                path.rmdir()

    init_publication(paper)

    assert paper.paths.tex_root.exists()
    assert paper.paths.autofigures_root.exists()
    assert paper.paths.build_root.exists()
    assert fake_pubify_mpl.prepare_calls == [(paper.paths.tex_root, paper.config.pubify_mpl.template)]
    assert (paper.paths.tex_root / "pubify.sty").exists()
    assert (paper.paths.tex_root / "pubify-template.tex").exists()

def test_cli_stat_list_outputs_discovered_stats(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "stat", "list"]) == 0
    assert capsys.readouterr().out.strip() == "training_summary"

def test_cli_stats_alias_works_for_list(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "stats", "list"]) == 0
    assert capsys.readouterr().out.strip() == "training_summary"

def test_cli_stat_update_writes_autostats_and_prints_all_stats(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    publication = load_publication_definition(repo, "demo")

    assert main(["demo", "stat", "update"]) == 0

    captured = capsys.readouterr()
    assert "Data" in captured.out
    assert "- bundle: loaded" in captured.out
    assert "- training: loaded" in captured.out
    assert "Stats" in captured.out
    assert "- training_summary" in captured.out
    assert r"  \StatTrainingSummaryValue = training" in captured.out
    assert r"  \StatTrainingSummaryBundle = meta|model" in captured.out
    assert publication.paths.autostats_path.read_text(encoding="utf-8") == "\n".join(
        [
            r"\newcommand{\StatTrainingSummaryValue}{training}",
            r"\newcommand{\StatTrainingSummaryBundle}{\texttt{meta|model}}",
            "",
        ]
    )

def test_cli_stat_update_selected_stat_prints_only_selected_block(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "stat", "training_summary", "update"]) == 0

    captured = capsys.readouterr()
    assert "Stats" in captured.out
    assert "- training_summary" in captured.out
    assert r"  \StatTrainingSummaryValue = training" in captured.out
    assert r"  \StatTrainingSummaryBundle = meta|model" in captured.out

def test_cli_stat_latex_emits_macro_lines_with_padding(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "stat", "training_summary", "latex"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("\n\\input{autostats.tex}\n")
    assert captured.out.endswith("\n\n")
    assert r"\StatTrainingSummaryValue{}" in captured.out
    assert r"\StatTrainingSummaryBundle{}" in captured.out

def test_cli_stat_latex_skips_existing_autostats_input(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (repo / "papers" / "demo" / "tex" / "main.tex").write_text(
        "\n".join(
            [
                r"\documentclass{article}",
                r"\input{autostats.tex}",
                r"\begin{document}",
                r"Demo",
                r"\end{document}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["demo", "stat", "training_summary", "latex"]) == 0

    captured = capsys.readouterr()
    assert not captured.out.startswith("\n\\input{autostats.tex}")

def test_cli_table_update_writes_autotables_and_prints_table_block(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_table_paper(
        repo,
        publication_id="tablesdemo",
        figures_lines=[
            "from pubify_pubs import TableResult",
            "from pubify_data import table",
            "",
            "@table",
            "def tabulate_summary(ctx):",
            "    return TableResult([['Metric', 'Value'], ['Count', 3]], formats=['{}', '{}'])",
        ],
        main_tex_lines=[
            r"\documentclass{article}",
            r"\usepackage{pubify}",
            r"\begin{document}",
            r"\input{autotables.tex}",
            r"\begin{tabular}{ll}",
            r"\TableSummary",
            r"\end{tabular}",
            r"\end{document}",
        ],
    )

    assert main(["tablesdemo", "table", "update"]) == 0

    captured = capsys.readouterr()
    assert "Tables" in captured.out
    assert "- summary: updated" in _strip_ansi(captured.out)
    assert (repo / "papers" / "tablesdemo" / "tex" / "autotables.tex").read_text(encoding="utf-8")

def test_cli_table_latex_emits_single_body_scaffold(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_table_paper(
        repo,
        publication_id="tablelatex",
        figures_lines=[
            "from pubify_pubs import TableResult",
            "from pubify_data import table",
            "",
            "@table",
            "def tabulate_summary(ctx):",
            "    return TableResult([['Metric', 'Value'], ['Count', 3]], formats=['{}', '{}'])",
        ],
        main_tex_lines=[
            r"\documentclass{article}",
            r"\usepackage{pubify}",
            r"\begin{document}",
            r"\input{autotables.tex}",
            r"\end{document}",
        ],
    )

    assert main(["tablelatex", "table", "summary", "latex"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("\n\\begin{table}[t]\n")
    assert captured.out.endswith("\n\n")
    assert r"\begin{tabular}{ll}" in captured.out
    assert r"Column 1 & Column 2 \\" in captured.out
    assert r"\TableSummary" in captured.out
    assert r"\label{tab:summary}" in captured.out
    assert r"\input{autotables.tex}" not in captured.out

def test_cli_table_latex_emits_grouped_multi_body_scaffold(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_table_paper(
        repo,
        publication_id="tablelatexmulti",
        figures_lines=[
            "from pubify_pubs import TableResult",
            "from pubify_data import table",
            "",
            "@table",
            "def tabulate_summary(ctx):",
            "    return TableResult([[['A', 'B']], [['C', 'D']]], formats=['{}', '{}'])",
        ],
        main_tex_lines=[
            r"\documentclass{article}",
            r"\usepackage{pubify}",
            r"\begin{document}",
            r"\input{autotables.tex}",
            r"\end{document}",
        ],
    )

    assert main(["tablelatexmulti", "table", "summary", "tex"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert r"\multicolumn{2}{l}{Body 1} \\" in captured.out
    assert r"\TableSummary{1}" in captured.out
    assert r"\multicolumn{2}{l}{Body 2} \\" in captured.out
    assert r"\TableSummary{2}" in captured.out

def test_cli_table_latex_emits_missing_autotables_input_when_needed(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_table_paper(
        repo,
        publication_id="tablelatexinput",
        figures_lines=[
            "from pubify_pubs import TableResult",
            "from pubify_data import table",
            "",
            "@table",
            "def tabulate_summary(ctx):",
            "    return TableResult([['Metric', 'Value']], formats=['{}', '{}'])",
        ],
        main_tex_lines=[
            r"\documentclass{article}",
            r"\begin{document}",
            r"\end{document}",
        ],
    )

    assert main(["tablelatexinput", "table", "summary", "latex"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("\n\\input{autotables.tex}\n\\begin{table}[t]\n")

def test_cli_update_validates_tables(repo: Path) -> None:
    _write_table_paper(
        repo,
        publication_id="tablecheck",
        figures_lines=[
            "from pubify_pubs import TableResult",
            "from pubify_data import table",
            "",
            "@table",
            "def tabulate_summary(ctx):",
            "    return TableResult([['Metric', 'Value'], ['Count', 3]], formats=['{}', '{}'])",
        ],
        main_tex_lines=[
            r"\documentclass{article}",
            r"\usepackage{pubify}",
            r"\begin{document}",
            r"\input{autotables.tex}",
            r"\begin{tabular}{ll}",
            r"\TableSummary",
            r"\end{tabular}",
            r"\end{document}",
        ],
    )

    init_publication(load_publication_definition(repo, "tablecheck"))

    assert main(["tablecheck", "update"]) == 0

def test_cli_tables_alias_maps_to_table(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_table_paper(
        repo,
        publication_id="tablesalias",
        figures_lines=[
            "from pubify_pubs import TableResult",
            "from pubify_data import table",
            "",
            "@table",
            "def tabulate_summary(ctx):",
            "    return TableResult([['Metric', 'Value']], formats=['{}', '{}'])",
        ],
        main_tex_lines=[
            r"\documentclass{article}",
            r"\usepackage{pubify}",
            r"\begin{document}",
            r"\input{autotables.tex}",
            r"\begin{tabular}{ll}",
            r"\TableSummary",
            r"\end{tabular}",
            r"\end{document}",
        ],
    )

    assert main(["tablesalias", "tables", "list"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "summary"

def test_init_bootstraps_missing_publication_root_and_skeleton_yaml(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["init", "fresh"])

    assert rc == 0
    output = capsys.readouterr().out.strip()
    assert output.endswith("/papers/fresh")
    fresh_root = repo / "papers" / "fresh"
    fresh_data_root = repo / "papers" / "fresh" / "data"
    assert fresh_root.exists()
    assert fresh_data_root.exists()
    config_path = fresh_root / "pub.yaml"
    assert config_path.exists()
    config_text = config_path.read_text(encoding="utf-8")
    assert "publication_id:" not in config_text
    assert 'mirror_root: ""' in config_text
    assert "external_data_roots:" in config_text
    assert "  project: output" in config_text
    assert "pubify-mpl-template:" in config_text
    assert "pubify-mpl-defaults:" in config_text
    assert "layout: one" in config_text
    assert "dpi:" not in config_text
    assert (fresh_root / "tex").exists()
    assert (fresh_root / "figures.py").exists()
    figures_py = (fresh_root / "figures.py").read_text(encoding="utf-8")
    assert '"""Figures entrypoint for publication figures."""' in figures_py
    assert "import matplotlib.pyplot as plt" in figures_py
    assert "import numpy as np" in figures_py
    assert "from pubify_pubs import FigureExport, TableResult" in figures_py
    assert "from pubify_data import data, figure, stat, table" in figures_py
    assert "# Data" in figures_py
    assert "# Figures, Stats & Tables" in figures_py
    assert "def load_example_data(ctx, file_path):" in figures_py
    assert '"x": np.array([' in figures_py
    assert '"y": np.array([' in figures_py
    assert "def plot_example(ctx, example_data):" in figures_py
    assert 'ax.scatter(example_data["x"], example_data["y"])' in figures_py
    assert 'layout="one"' in figures_py
    assert "def compute_example(ctx, example_data):" in figures_py
    assert '"Count": str(example_data["x"].size)' in figures_py
    assert '"Mean": str(example_data["y"].mean())' in figures_py
    assert "def tabulate_example(ctx, example_data):" in figures_py
    assert 'np.column_stack((example_data["x"], example_data["y"]))' in figures_py
    assert "# pubs:" not in figures_py
    main_tex = fresh_root / "tex" / "main.tex"
    assert main_tex.exists()
    main_tex_text = main_tex.read_text(encoding="utf-8")
    assert r"\usepackage{pubify}" in main_tex_text
    assert r"\section*{Overview}" in main_tex_text
    assert r"\input{autostats.tex}" not in main_tex_text
    assert r"\input{autotables.tex}" not in main_tex_text
    assert r"\figfloat" not in main_tex_text
    assert r"\figone{autofigures/example}" not in main_tex_text
    assert r"\StatExampleCount{}" not in main_tex_text
    assert r"\StatExampleMean{}" not in main_tex_text
    assert r"\TableExample" not in main_tex_text
    assert r"\graphicspath{{figures/}}" not in main_tex_text
    assert (fresh_root / "tex" / "autofigures").exists()
    assert (fresh_root / "tex" / "autofigures").is_symlink()
    assert os.readlink(fresh_root / "tex" / "autofigures") == "../data/tex-artifacts/autofigures"
    assert os.readlink(fresh_root / "tex" / "autostats.tex") == "../data/tex-artifacts/autostats.tex"
    assert os.readlink(fresh_root / "tex" / "autotables.tex") == "../data/tex-artifacts/autotables.tex"
    assert (fresh_root / "data" / "tex-artifacts" / "autofigures").is_dir()
    assert (fresh_root / "data" / "tex-artifacts" / "autostats.tex").exists()
    assert (fresh_root / "data" / "tex-artifacts" / "autotables.tex").exists()
    assert (fresh_root / "tex" / "build").exists()
    assert not (fresh_root / "preview").exists()
    assert (fresh_root / "tex" / "pubify.sty").exists()
    assert (fresh_root / "tex" / "pubify-template.tex").exists()
    assert fake_pubify_mpl.prepare_calls[0][0] == fresh_root / "tex"
    assert fake_pubify_mpl.prepare_calls[0][1]["textwidth_in"] == 5.39643
    assert fake_pubify_mpl.prepare_calls[0][1]["textheight_in"] == 7.5896
    assert fake_pubify_mpl.prepare_calls[0][1]["base_fontsize_pt"] == 12.0
    assert fake_pubify_mpl.prepare_calls[0][1]["caption_lineheight_pt"] == 13.6

def test_force_init_migrates_legacy_direct_tex_artifacts(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    legacy_autofigures = repo / "papers" / "demo" / "tex" / "autofigures"
    legacy_autofigures.mkdir(parents=True, exist_ok=True)
    (legacy_autofigures / "single.pdf").write_text("legacy figure", encoding="utf-8")
    (repo / "papers" / "demo" / "tex" / "autostats.tex").write_text("% legacy stats\n", encoding="utf-8")
    (repo / "papers" / "demo" / "tex" / "autotables.tex").write_text("% legacy tables\n", encoding="utf-8")

    assert main(["--force", "init", "demo"]) == 0
    capsys.readouterr()

    publication = load_publication_definition(repo, "demo")
    assert publication.paths.tex_autofigures_root.is_symlink()
    assert publication.paths.tex_autostats_path.is_symlink()
    assert publication.paths.tex_autotables_path.is_symlink()
    assert (publication.paths.autofigures_root / "single.pdf").read_text(encoding="utf-8") == "legacy figure"
    assert publication.paths.autostats_path.read_text(encoding="utf-8") == "% legacy stats\n"
    assert publication.paths.autotables_path.read_text(encoding="utf-8") == "% legacy tables\n"

def test_init_without_publication_id_initializes_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    monkeypatch.chdir(workspace_root)

    assert main(["init"]) == 0

    output = capsys.readouterr().out.strip()
    assert output == str(workspace_root)
    assert (workspace_root / "papers").exists()
    assert (workspace_root / "papers" / "AGENTS.md").read_text(encoding="utf-8") == PUBLICATIONS_AGENTS_TEMPLATE
    assert not (workspace_root / "output" / "papers").exists()
    assert (workspace_root / "pubify.yaml").read_text(encoding="utf-8") == "\n".join(
        [
            "pubify-pubs:",
            "  publications_root: papers",
            "  preview:",
            "    publication: preview",
            "    figure: preview",
            "",
        ]
    )

def test_workspace_init_rejects_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_root)

    with pytest.raises(SystemExit):
        main(["--force", "init"])

    assert "workspace init does not accept --force" in capsys.readouterr().err

def test_init_without_publication_id_is_idempotent_and_preserves_existing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.yaml").write_text(
        "\n".join(
            [
                "pubify-pubs:",
                "  publications_root: manuscripts",
                "  data_root: shared-data",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace_root)

    assert main(["init"]) == 0
    capsys.readouterr()
    publications_root = workspace_root / "manuscripts"
    agents_path = publications_root / "AGENTS.md"
    agents_path.unlink()
    publications_root.rmdir()

    assert main(["init"]) == 0
    capsys.readouterr()

    assert (workspace_root / "pubify.yaml").read_text(encoding="utf-8") == "\n".join(
        [
            "pubify-pubs:",
            "  publications_root: manuscripts",
            "  data_root: shared-data",
            "",
        ]
    )
    assert publications_root.exists()
    assert (publications_root / "AGENTS.md").read_text(encoding="utf-8") == PUBLICATIONS_AGENTS_TEMPLATE

def test_init_without_publication_id_creates_agents_in_configured_publications_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.yaml").write_text(
        "\n".join(
            [
                "pubify-pubs:",
                "  publications_root: manuscripts",
                '  data_root: ""',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace_root)

    assert main(["init"]) == 0
    capsys.readouterr()

    assert (workspace_root / "manuscripts" / "AGENTS.md").read_text(encoding="utf-8") == PUBLICATIONS_AGENTS_TEMPLATE
    assert not (workspace_root / "papers" / "AGENTS.md").exists()

def test_check_after_init_passes_when_mirror_root_is_blank(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
) -> None:
    main(["init", "fresh"])
    fresh = load_publication_definition(repo, "fresh")
    check_publication(fresh)

def test_init_is_idempotent_and_recreates_missing_bootstrap_files(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["init", "fresh"]) == 0
    capsys.readouterr()
    fresh_root = repo / "papers" / "fresh"
    (fresh_root / "figures.py").unlink()
    (fresh_root / "tex" / "main.tex").unlink()

    assert main(["init", "fresh"]) == 0
    capsys.readouterr()

    assert (fresh_root / "figures.py").exists()
    assert (fresh_root / "tex" / "main.tex").exists()
    assert len(fake_pubify_mpl.prepare_calls) == 2
    assert fake_pubify_mpl.prepare_calls[0][0] == fresh_root / "tex"
    assert fake_pubify_mpl.prepare_calls[1][0] == fresh_root / "tex"

def test_init_publication_backfills_publications_agents_file_when_missing(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    agents_path = repo / "papers" / "AGENTS.md"
    agents_path.unlink(missing_ok=True)

    assert main(["init", "fresh"]) == 0
    capsys.readouterr()

    assert agents_path.read_text(encoding="utf-8") == PUBLICATIONS_AGENTS_TEMPLATE

def test_init_publication_does_not_overwrite_existing_publications_agents_file(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    agents_path = repo / "papers" / "AGENTS.md"
    agents_path.write_text("# custom\n", encoding="utf-8")

    assert main(["init", "fresh"]) == 0
    capsys.readouterr()

    assert agents_path.read_text(encoding="utf-8") == "# custom\n"

def test_init_respects_existing_configured_main_tex_path(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    publication_root = repo / "papers" / "configured"
    publication_root.mkdir(parents=True, exist_ok=True)
    (publication_root / "pub.yaml").write_text(
        "\n".join(
                [
                    'mirror_root: ""',
                    "main_tex: paper.tex",
                "pubify-mpl-template:",
                "  textwidth_in: 5.39643",
                "  textheight_in: 7.5896",
                "  base_fontsize_pt: 12.0",
                "  caption_lineheight_pt: 13.6",
                "  subcaption_lineheight_pt: 13.6",
                "  row_skip_in: 0.11",
                "  caption_skip_in: 0.11",
                "  subcaption_skip_in: 0.08",
                "  subcaption_allowance_in: 0.08",
                "  caption_allowance_in: 0.08",
                "pubify-mpl-defaults:",
                "  layout: one",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["init", "configured"]) == 0
    capsys.readouterr()

    assert (publication_root / "figures.py").exists()
    assert (publication_root / "tex" / "paper.tex").exists()
    assert not (publication_root / "tex" / "main.tex").exists()
    assert r"\usepackage{pubify}" in (publication_root / "tex" / "paper.tex").read_text(encoding="utf-8")

def test_export_does_not_call_prepare(repo: Path, fake_pubify_mpl: FakePubifyBackend) -> None:
    paper = load_publication_definition(repo, "demo")
    run_figures(paper, "single")
    assert fake_pubify_mpl.prepare_calls == []

def test_check_fails_when_pubify_support_files_are_missing(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    with pytest.raises(ValueError, match="Missing pubify support file:"):
        check_publication(paper)

def test_check_fails_when_mirror_root_is_missing(repo: Path, fake_pubify_mpl: FakePubifyBackend) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    mirror_root = repo / "mirror" / "demo"
    mirror_root.rmdir()

    with pytest.raises(ValueError, match="Mirror does not exist:"):
        check_publication(paper)

def test_check_fails_when_declared_data_path_is_missing(repo: Path) -> None:
    (repo / "papers" / "demo" / "data" / "training.npy").unlink()
    paper = load_publication_definition(repo, "demo")

    with pytest.raises(ValueError, match="Missing data path for loader 'training'"):
        check_publication(paper)

def test_data_list_shows_one_row_per_declared_pinned_path(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(core_cli.sys.stdout, "isatty", lambda: False)

    assert main(["demo", "data", "list"]) == 0
    output = capsys.readouterr().out.strip().splitlines()

    assert output == [
        "pinned   training   training.npy",
        "pinned   bundle     bundle/model.txt",
        "pinned   bundle     bundle/meta.txt",
    ]

def test_data_list_shows_external_rows_and_does_not_validate_paths(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch_root = repo / "missing-scratch-inventory"
    _write_external_paper(
        repo,
        publication_id="datalist",
        external_root_lines=[f"  scratch: {scratch_root}"],
        figure_lines=[
            "from pubify_data import data, external_data, figure",
            "",
            "@data('training.npy')",
            "def load_training(ctx, path):",
            "    return path",
            "",
            "@data(model='bundle/model.txt', meta='bundle/meta.txt')",
            "def load_bundle(ctx, model, meta):",
            "    return model, meta",
            "",
            "@external_data('scratch', 'training.npy')",
            "def load_external_training(ctx, path):",
            "    return path",
            "",
            "@external_data('scratch', model='bundle/model.txt', meta='bundle/meta.txt')",
            "def load_external_bundle(ctx, model, meta):",
            "    return model, meta",
            "",
            "@figure",
            "def plot_unused(ctx):",
            "    return None",
        ],
    )
    monkeypatch.setattr(core_cli.sys.stdout, "isatty", lambda: False)

    assert main(["datalist", "data", "list"]) == 0
    output = capsys.readouterr().out.strip().splitlines()

    assert output == [
        "pinned   training            training.npy",
        "pinned   bundle              bundle/model.txt",
        "pinned   bundle              bundle/meta.txt",
        "external   external_training   scratch:training.npy",
        "external   external_bundle     scratch:bundle/model.txt",
        "external   external_bundle     scratch:bundle/meta.txt",
    ]

def test_data_list_repeats_loader_id_for_multi_path_rows_and_keeps_per_loader_rows(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch_root = repo / "shared-external-data"
    _write_external_paper(
        repo,
        publication_id="datadedupe",
        external_root_lines=[f"  scratch: {scratch_root}"],
        figure_lines=[
            "from pubify_data import external_data, figure",
            "",
            "@external_data('scratch', model_dir='models', tiptop_dir='tiptop')",
            "def load_one(ctx, model_dir, tiptop_dir):",
            "    return model_dir, tiptop_dir",
            "",
            "@external_data('scratch', model_dir='models', tiptop_dir='tiptop')",
            "def load_two(ctx, model_dir, tiptop_dir):",
            "    return model_dir, tiptop_dir",
            "",
            "@external_data('scratch', tiptop_dir='tiptop')",
            "def load_three(ctx, tiptop_dir):",
            "    return tiptop_dir",
            "",
            "@figure",
            "def plot_unused(ctx):",
            "    return None",
        ],
    )
    monkeypatch.setattr(core_cli.sys.stdout, "isatty", lambda: False)

    assert main(["datadedupe", "data", "list"]) == 0
    output = capsys.readouterr().out.strip().splitlines()

    assert output == [
        "external   one     scratch:models",
        "external   one     scratch:tiptop",
        "external   two     scratch:models",
        "external   two     scratch:tiptop",
        "external   three   scratch:tiptop",
    ]

def test_data_list_colors_status_only_on_tty(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch_root = repo / "missing-scratch-color"
    _write_external_paper(
        repo,
        publication_id="datacolor",
        external_root_lines=[f"  scratch: {scratch_root}"],
        figure_lines=[
            "from pubify_data import data, external_data, figure",
            "",
            "@data('training.npy')",
            "def load_training(ctx, path):",
            "    return path",
            "",
            "@external_data('scratch', 'training.npy')",
            "def load_external_training(ctx, path):",
            "    return path",
            "",
            "@figure",
            "def plot_unused(ctx):",
            "    return None",
        ],
    )
    monkeypatch.setattr(core_cli.sys.stdout, "isatty", lambda: True)

    assert main(["datacolor", "data", "list"]) == 0
    output = capsys.readouterr().out

    assert "\033[32m" in output
    assert "\033[31m" in output
    assert "\033[32mpinned\033[0m   training            training.npy" in output
    assert "\033[31mexternal\033[0m   external_training   scratch:training.npy" in output
    assert _strip_ansi(output).splitlines() == [
        "pinned   training            training.npy",
        "external   external_training   scratch:training.npy",
    ]

def test_data_list_empty_state_is_clear(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_external_paper(
        repo,
        publication_id="nodata",
        external_root_lines=[],
        figure_lines=[
            "from pubify_data import figure",
            "",
            "@figure",
            "def plot_unused(ctx):",
            "    return None",
        ],
    )
    monkeypatch.setattr(core_cli.sys.stdout, "isatty", lambda: False)

    assert main(["nodata", "data", "list"]) == 0
    assert capsys.readouterr().out.strip() == "nodata: no declared data"

def test_external_data_single_path_resolves_from_configured_root(repo: Path) -> None:
    scratch_root = repo / "scratch-data"
    scratch_root.mkdir(parents=True, exist_ok=True)
    (scratch_root / "training.npy").write_text("external-training", encoding="utf-8")
    _write_external_paper(
        repo,
        publication_id="externalsingle",
        external_root_lines=[f"  scratch: {scratch_root}"],
        figure_lines=[
            "import matplotlib",
            "matplotlib.use('Agg')",
            "import matplotlib.pyplot as plt",
            "from pubify_data import external_data, figure",
            "",
            "@external_data('scratch', 'training.npy')",
            "def load_training(ctx, path):",
            "    return path.read_text(encoding='utf-8')",
            "",
            "@figure",
            "def plot_single(ctx, training):",
            "    fig, ax = plt.subplots()",
            "    fig._pubs_name = training",
            "    ax.plot([0, 1], [0, 1])",
            "    return fig",
        ],
    )

    paper = load_publication_definition(repo, "externalsingle")
    assert paper.loaders["training"].root_name == "scratch"
    outputs = run_figures(paper, "single")

    assert outputs[0].read_text(encoding="utf-8") == "external-training"

def test_external_data_multi_path_resolves_named_paths_from_configured_root(repo: Path) -> None:
    shared_root = repo / "shared-data" / "bundle"
    shared_root.mkdir(parents=True, exist_ok=True)
    (shared_root / "model.txt").write_text("model", encoding="utf-8")
    (shared_root / "meta.txt").write_text("meta", encoding="utf-8")
    _write_external_paper(
        repo,
        publication_id="externalmulti",
        external_root_lines=[f"  shared: {repo / 'shared-data'}"],
        figure_lines=[
            "import matplotlib",
            "matplotlib.use('Agg')",
            "import matplotlib.pyplot as plt",
            "from pubify_data import external_data, figure",
            "",
            "@external_data('shared', model='bundle/model.txt', meta='bundle/meta.txt')",
            "def load_bundle(ctx, model, meta):",
            "    return '|'.join(path.read_text(encoding='utf-8') for path in (meta, model))",
            "",
            "@figure",
            "def plot_single(ctx, bundle):",
            "    fig, ax = plt.subplots()",
            "    fig._pubs_name = bundle",
            "    ax.plot([0, 1], [0, 1])",
            "    return fig",
        ],
    )

    paper = load_publication_definition(repo, "externalmulti")
    outputs = run_figures(paper, "single")

    assert outputs[0].read_text(encoding="utf-8") == "meta|model"

def test_check_fails_when_external_root_config_is_missing(repo: Path) -> None:
    _write_external_paper(
        repo,
        publication_id="missingrootconfig",
        external_root_lines=[],
        figure_lines=[
            "from pubify_data import external_data",
            "",
            "@external_data('scratch', 'training.npy')",
            "def load_training(ctx, path):",
            "    return path",
        ],
    )

    paper = load_publication_definition(repo, "missingrootconfig")

    with pytest.raises(
        ValueError,
        match="Missing external data root config for loader 'training': scratch",
    ):
        check_publication(paper)

def test_check_fails_when_external_root_path_is_missing(repo: Path) -> None:
    missing_root = repo / "missing-scratch"
    _write_external_paper(
        repo,
        publication_id="missingrootpath",
        external_root_lines=[f"  scratch: {missing_root}"],
        figure_lines=[
            "from pubify_data import external_data",
            "",
            "@external_data('scratch', 'training.npy')",
            "def load_training(ctx, path):",
            "    return path",
        ],
    )

    paper = load_publication_definition(repo, "missingrootpath")

    with pytest.raises(
        ValueError,
        match=f"Missing external data root path for loader 'training': {missing_root}",
    ):
        check_publication(paper)

def test_check_fails_when_external_data_path_is_missing(repo: Path) -> None:
    scratch_root = repo / "scratch-data-missing-file"
    scratch_root.mkdir(parents=True, exist_ok=True)
    _write_external_paper(
        repo,
        publication_id="missingexternalfile",
        external_root_lines=[f"  scratch: {scratch_root}"],
        figure_lines=[
            "from pubify_data import external_data",
            "",
            "@external_data('scratch', 'training.npy')",
            "def load_training(ctx, path):",
            "    return path",
        ],
    )

    paper = load_publication_definition(repo, "missingexternalfile")

    with pytest.raises(
        ValueError,
        match=f"Missing external data path for loader 'training': {scratch_root / 'training.npy'}",
    ):
        check_publication(paper)

def test_external_data_relative_root_is_resolved_from_workspace_root(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_root = repo / "shared-data"
    shared_root.mkdir(parents=True, exist_ok=True)
    (shared_root / "training.npy").write_text("stable-relative-root", encoding="utf-8")
    _write_external_paper(
        repo,
        publication_id="externalrelative",
        external_root_lines=["  scratch: shared-data"],
        figure_lines=[
            "import matplotlib",
            "matplotlib.use('Agg')",
            "import matplotlib.pyplot as plt",
            "from pubify_data import external_data, figure",
            "",
            "@external_data('scratch', 'training.npy')",
            "def load_training(ctx, path):",
            "    return path.read_text(encoding='utf-8')",
            "",
            "@figure",
            "def plot_single(ctx, training):",
            "    fig, ax = plt.subplots()",
            "    fig._pubs_name = training",
            "    ax.plot([0, 1], [0, 1])",
            "    return fig",
        ],
    )

    monkeypatch.chdir(repo / "papers")
    paper = load_publication_definition(repo, "externalrelative")

    assert Path(paper.config.external_data_roots["scratch"]) == shared_root.resolve()
    outputs = run_figures(paper, "single")
    assert outputs[0].read_text(encoding="utf-8") == "stable-relative-root"

def test_external_data_absolute_root_is_kept_unchanged(repo: Path) -> None:
    shared_root = (repo / "absolute-shared").resolve()
    shared_root.mkdir(parents=True, exist_ok=True)
    (shared_root / "training.npy").write_text("absolute-root", encoding="utf-8")
    _write_external_paper(
        repo,
        publication_id="externalabsolute",
        external_root_lines=[f"  scratch: {shared_root}"],
        figure_lines=[
            "import matplotlib",
            "matplotlib.use('Agg')",
            "import matplotlib.pyplot as plt",
            "from pubify_data import external_data, figure",
            "",
            "@external_data('scratch', 'training.npy')",
            "def load_training(ctx, path):",
            "    return path.read_text(encoding='utf-8')",
            "",
            "@figure",
            "def plot_single(ctx, training):",
            "    fig, ax = plt.subplots()",
            "    fig._pubs_name = training",
            "    ax.plot([0, 1], [0, 1])",
            "    return fig",
        ],
    )

    paper = load_publication_definition(repo, "externalabsolute")

    assert Path(paper.config.external_data_roots["scratch"]) == shared_root
    outputs = run_figures(paper, "single")
    assert outputs[0].read_text(encoding="utf-8") == "absolute-root"

def test_build_fails_with_init_message_when_pubify_support_files_are_missing(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    with pytest.raises(ValueError, match=r"Run `pubs init demo`"):
        build_publication(paper)

def test_cli_normalizes_documented_short_form(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["init", "demo"]) == 0
    init_output = capsys.readouterr().out.strip()
    assert init_output.endswith("/papers/demo")
    rc = main(["demo", "data", "list"])
    assert rc == 0
    assert "training.npy" in capsys.readouterr().out

def test_old_init_syntax_is_rejected() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        main(["demo", "init"])

def test_init_publication_requires_initialized_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    monkeypatch.chdir(workspace_root)

    assert main(["init", "demo"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Run `pubs init` in your workspace root and try again." in captured.err

def test_list_requires_initialized_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    monkeypatch.chdir(workspace_root)

    assert main(["list"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Run `pubs init` in your workspace root and try again." in captured.err

def test_prepare_is_unsupported(repo: Path) -> None:
    with pytest.raises(SystemExit):
        main(["demo", "prepare"])

def test_no_arg_invocation_prints_multiline_help_block(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main([])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage: pubs [--force] [--clear] <command>" in captured.err
    assert "Commands:" in captured.err
    assert "  pubs list" in captured.err
    assert "  pubs init" in captured.err
    assert "  pubs init <publication-id>" in captured.err
    assert "  pubs <publication-id> shell" in captured.err
    assert "  pubs <publication-id> data [list|add <data-id>]" in captured.err
    assert "  pubs <publication-id> figure [list|add <figure-id>|update|<figure-id> update|<figure-id> preview [<subfig-idx>]|<figure-id> latex [subcaption]]" in captured.err
    assert "  pubs <publication-id> stat [list|add <stat-id>|update|<stat-id> update|<stat-id> latex]" in captured.err
    assert "  pubs <publication-id> table [list|add <table-id>|update|<table-id> update|<table-id> latex]" in captured.err
    assert "  pubs <publication-id> update" in captured.err
    assert "  pubs <publication-id> build [--clear]" in captured.err
    assert "  pubs <publication-id> preview" in captured.err
    assert "positional arguments:" not in captured.err
    assert "subject" not in captured.err
    assert "arg2" not in captured.err
    assert (
        "expected 'list', 'init', 'init <publication-id>', or '<publication-id> <command>'" in captured.err
    )

def test_build_reports_validation_failure_without_traceback(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mirror_root = repo / "mirror" / "demo"
    mirror_root.rmdir()

    rc = main(["demo", "build"])

    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error: Publication 'demo' failed validation:" in captured.err
    assert "Mirror does not exist:" in captured.err

def test_build_failure_ignores_package_info_and_reports_real_error(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    log_path = paper.paths.build_root / "main.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                "Package microtype Info: Loading configuration file microtype.cfg.",
                "! LaTeX Error: File `missing.sty' not found.",
                "! Emergency stop.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, command, "", "")

    with pytest.raises(ValueError) as exc_info:
        build_publication(paper, runner=runner)

    text = str(exc_info.value)
    assert "LaTeX error: LaTeX Error: File `missing.sty' not found." in text
    assert "microtype Info" not in text
