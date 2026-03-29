from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

from pubify_pubs.cli import build_parser, main
import pubify_pubs.cli as core_cli
import pubify_pubs.export as core_export
import pubify_pubs.mirror as core_mirror
import pubify_pubs.pinning as core_pinning
import pubify_pubs.runtime as core_runtime
from pubify_pubs.data import load_publication_data_npz, publication_data_path, save_publication_data_npz
from pubify_pubs.decorators import data, external_data, figure
from pubify_pubs.discovery import find_workspace_root, list_publication_ids, load_publication_definition
from pubify_pubs.mirror import diff_publication, pull_publication, push_publication
from pubify_pubs.runtime import build_publication, check_publication, init_publication, run_figures
from pubify_pubs.config import load_workspace_config


class FakePubifyBackend:
    def __init__(self) -> None:
        self.prepare_calls: list[tuple[Path, dict[str, object]]] = []
        self.save_calls: list[tuple[object, str, Path, dict[str, object], dict[str, object]]] = []

    def prepare(self, destination: Path, template: dict[str, object]) -> tuple[Path, Path]:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        style_path = destination / "pubify.sty"
        template_path = destination / "pubify-template.tex"
        style_path.write_text("% pubify\n", encoding="utf-8")
        template_path.write_text(str(dict(template)), encoding="utf-8")
        self.prepare_calls.append((destination, dict(template)))
        return style_path, template_path

    def save_fig(
        self,
        fig_or_ax: object,
        layout: str,
        filename: Path,
        *,
        template: dict[str, object] | None = None,
        **kwargs: object,
    ) -> None:
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        label = getattr(fig_or_ax, "_pubs_name", None)
        if label is None and hasattr(fig_or_ax, "figure"):
            label = getattr(fig_or_ax.figure, "_pubs_name", None)
        path.write_text(label or "panel", encoding="utf-8")
        self.save_calls.append((fig_or_ax, layout, path, dict(template or {}), dict(kwargs)))


class FakeReadline:
    def __init__(self) -> None:
        self.bindings: list[str] = []
        self.history: list[str] = []
        self.read_paths: list[str] = []
        self.write_paths: list[str] = []
        self.__doc__ = ""

    def parse_and_bind(self, binding: str) -> None:
        self.bindings.append(binding)

    def read_history_file(self, path: str) -> None:
        self.read_paths.append(path)
        history_path = Path(path)
        if history_path.exists():
            self.history = history_path.read_text(encoding="utf-8").splitlines()

    def write_history_file(self, path: str) -> None:
        self.write_paths.append(path)
        Path(path).write_text("\n".join(self.history) + ("\n" if self.history else ""), encoding="utf-8")

    def get_current_history_length(self) -> int:
        return len(self.history)

    def get_history_item(self, index: int) -> str | None:
        if 1 <= index <= len(self.history):
            return self.history[index - 1]
        return None

    def add_history(self, line: str) -> None:
        self.history.append(line)


@pytest.fixture(autouse=True)
def fake_pubify(monkeypatch: pytest.MonkeyPatch) -> FakePubifyBackend:
    backend = FakePubifyBackend()
    monkeypatch.setattr(core_export, "pubify_mpl", backend)
    monkeypatch.setattr(core_runtime, "pubify_mpl", backend)
    return backend


@pytest.fixture()
def fake_pubify_mpl(fake_pubify: FakePubifyBackend) -> FakePubifyBackend:
    return fake_pubify


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (repo_root / "pubify.conf").write_text(
        "publications_root: papers\ndata_root: output/papers\n",
        encoding="utf-8",
    )
    (repo_root / "mirror" / "demo").mkdir(parents=True)
    (repo_root / "papers" / "demo" / "tex" / "sections").mkdir(parents=True)
    (repo_root / "output" / "papers" / "demo" / "bundle").mkdir(parents=True)
    (repo_root / "papers" / "demo" / "pub.yaml").write_text(
        "\n".join(
            [
                "publication_id: demo",
                "main_tex: main.tex",
                f"mirror_root: {repo_root / 'mirror' / 'demo'}",
                "pubify-mpl-template:",
                "  textwidth_in: 6.75",
                "  textheight_in: 9.7",
                "  base_fontsize_pt: 10",
                "pubify-mpl-defaults:",
                "  layout: twowide",
                "  dpi: 144",
                "  hide_labels: true",
                "sync_excludes:",
                "  - drafts/*",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / "papers" / "demo" / "figures.py").write_text(
        "\n".join(
            [
                "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "from pubify_pubs.decorators import data, figure",
                "",
                "CALLS = {'training': 0, 'bundle': 0}",
                "",
                "@data('training.npy')",
                "def load_training(ctx, path):",
                "    CALLS['training'] += 1",
                "    return path.read_text(encoding='utf-8')",
                "",
                "@data(model='bundle/model.txt', meta='bundle/meta.txt')",
                "def load_bundle(ctx, paths):",
                "    CALLS['bundle'] += 1",
                "    return '|'.join(paths[name].read_text(encoding='utf-8') for name in sorted(paths))",
                "",
                "@figure",
                "def plot_single(ctx, training):",
                "    fig, ax = plt.subplots()",
                "    fig._pubs_name = f'single:{training}'",
                "    ax.plot([0, 1], [0, 1])",
                "    return fig",
                "",
                "@figure",
                "def plot_compare(ctx, training, bundle):",
                "    fig1, ax1 = plt.subplots()",
                "    fig1._pubs_name = f'compare:{training}'",
                "    ax1.plot([0, 1], [0, 1])",
                "    fig2, ax2 = plt.subplots()",
                "    fig2._pubs_name = f'bundle:{bundle}'",
                "    ax2.plot([0, 1], [1, 0])",
                "    return [fig1, fig2]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / "papers" / "demo" / "tex" / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nDemo\n\\end{document}\n",
        encoding="utf-8",
    )
    (repo_root / "papers" / "demo" / "tex" / "sections" / "intro.tex").write_text(
        "intro\n",
        encoding="utf-8",
    )
    (repo_root / "papers" / "demo" / "tex" / "build").mkdir(parents=True)
    (repo_root / "papers" / "demo" / "tex" / "build" / "ignored.aux").write_text(
        "ignore\n",
        encoding="utf-8",
    )
    (repo_root / "papers" / "demo" / "tex" / "drafts").mkdir(parents=True)
    (repo_root / "papers" / "demo" / "tex" / "drafts" / "note.txt").write_text(
        "skip\n",
        encoding="utf-8",
    )
    (repo_root / "output" / "papers" / "demo" / "training.npy").write_text("training", encoding="utf-8")
    (repo_root / "output" / "papers" / "demo" / "bundle" / "model.txt").write_text(
        "model",
        encoding="utf-8",
    )
    (repo_root / "output" / "papers" / "demo" / "bundle" / "meta.txt").write_text(
        "meta",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo_root)
    return repo_root

def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _strip_ansi(value: str) -> str:
    for code in ("\033[31m", "\033[33m", "\033[36m", "\033[32m", "\033[2m", "\033[0m"):
        value = value.replace(code, "")
    return value


def _write_external_paper(
    repo: Path,
    *,
    publication_id: str,
    external_root_lines: list[str],
    figure_lines: list[str],
) -> Path:
    publication_root = repo / "papers" / publication_id
    (publication_root / "tex").mkdir(parents=True, exist_ok=True)
    (repo / "output" / "papers" / publication_id).mkdir(parents=True, exist_ok=True)
    lines = [
        'mirror_root: ""',
        "main_tex: main.tex",
        "external_data_roots:",
        *external_root_lines,
        "pubify-mpl-template:",
        "  textwidth_in: 6.75",
        "  textheight_in: 9.7",
        "  base_fontsize_pt: 10",
        "pubify-mpl-defaults:",
        "  layout: one",
    ]
    (publication_root / "pub.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (publication_root / "figures.py").write_text("\n".join(figure_lines) + "\n", encoding="utf-8")
    (publication_root / "tex" / "main.tex").write_text(
        "\\documentclass{article}\n\\usepackage{pubify}\n\\begin{document}\nX\n\\end{document}\n",
        encoding="utf-8",
    )
    return publication_root


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
    def load_bundle(ctx, paths):
        return paths

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


def test_save_publication_data_npz_saves_new_file_under_paper_output(repo: Path) -> None:
    saved_path = save_publication_data_npz("demo", "generated/sample.npz", values=np.array([1.0, 2.0]))

    assert saved_path == repo / "output" / "papers" / "demo" / "generated" / "sample.npz"
    assert saved_path.exists()
    with np.load(saved_path) as saved:
        assert np.array_equal(saved["values"], np.array([1.0, 2.0]))


def test_publication_data_path_resolves_under_paper_output(repo: Path) -> None:
    path = publication_data_path("demo", "generated/sample.pkl")

    assert path == repo / "output" / "papers" / "demo" / "generated" / "sample.pkl"


def test_publication_data_path_creates_parent_directories(repo: Path) -> None:
    path = publication_data_path("demo", "nested/deeper/sample.pkl")

    assert path.parent.exists()


def test_publication_data_path_rejects_absolute_paths(repo: Path) -> None:
    with pytest.raises(ValueError, match="must be relative, not absolute"):
        publication_data_path("demo", "/tmp/sample.pkl")


def test_publication_data_path_rejects_parent_traversal(repo: Path) -> None:
    with pytest.raises(ValueError, match="must stay under the publication data root"):
        publication_data_path("demo", "../sample.pkl")


def test_publication_data_path_supports_workspace_root_override(tmp_path: Path) -> None:
    workspace_root = tmp_path / "pkg"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.conf").write_text(
        "publications_root: papers\ndata_root: output/papers\n",
        encoding="utf-8",
    )

    path = publication_data_path(
        "demo",
        "generated/sample.pkl",
        workspace_root=workspace_root,
    )

    assert path == workspace_root / "output" / "papers" / "demo" / "generated" / "sample.pkl"
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
    target = repo / "output" / "papers" / "demo" / "generated" / "sample.npz"
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez(target, values=np.array([1.0]))

    with pytest.raises(FileExistsError, match="already exists"):
        save_publication_data_npz("demo", "generated/sample.npz", values=np.array([2.0]))


def test_save_publication_data_npz_overwrites_existing_file_when_requested(repo: Path) -> None:
    target = repo / "output" / "papers" / "demo" / "generated" / "sample.npz"
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


def test_save_publication_data_npz_supports_workspace_root_override(tmp_path: Path) -> None:
    workspace_root = tmp_path / "pkg"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.conf").write_text(
        "publications_root: papers\ndata_root: output/papers\n",
        encoding="utf-8",
    )

    saved_path = save_publication_data_npz(
        "demo",
        "generated/sample.npz",
        workspace_root=workspace_root,
        values=np.array([4.0]),
    )

    assert saved_path == workspace_root / "output" / "papers" / "demo" / "generated" / "sample.npz"
    with np.load(saved_path) as saved:
        assert np.array_equal(saved["values"], np.array([4.0]))


def test_workspace_config_defaults_preview_backends(repo: Path) -> None:
    workspace = load_workspace_config(repo)

    assert workspace.preview.publication == "preview"
    assert workspace.preview.figure == "preview"


def test_workspace_config_parses_nested_preview_backends(tmp_path: Path) -> None:
    workspace_root = tmp_path / "pkg"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (workspace_root / "pubify.conf").write_text(
        "\n".join(
            [
                "publications_root: papers",
                "data_root: output/papers",
                "preview:",
                "  publication: vscode",
                "  figure: preview",
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
    (workspace_root / "pubify.conf").write_text(
        "\n".join(
            [
                "publications_root: papers",
                "data_root: output/papers",
                "preview:",
                "  publication: finder",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="preview.publication must be one of: preview, vscode"):
        load_workspace_config(workspace_root)


def test_load_publication_data_npz_returns_plain_dict_of_arrays(repo: Path) -> None:
    source = repo / "output" / "papers" / "demo" / "generated" / "sample.npz"
    source.parent.mkdir(parents=True, exist_ok=True)
    np.savez(source, alpha=np.array([1.0, 2.0]), beta=np.array([3.0]))

    loaded = load_publication_data_npz(source)

    assert isinstance(loaded, dict)
    assert set(loaded) == {"alpha", "beta"}
    assert np.array_equal(loaded["alpha"], np.array([1.0, 2.0]))
    assert np.array_equal(loaded["beta"], np.array([3.0]))


def test_load_publication_data_npz_missing_path_fails_clearly(repo: Path) -> None:
    missing = repo / "output" / "papers" / "demo" / "generated" / "missing.npz"

    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_publication_data_npz(missing)


def test_load_publication_data_npz_non_file_path_fails_clearly(repo: Path) -> None:
    directory = repo / "output" / "papers" / "demo" / "generated"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "folder.npz"
    path.mkdir()

    with pytest.raises(ValueError, match="must be a file"):
        load_publication_data_npz(path)


def test_load_publication_data_npz_non_npz_path_fails_clearly(repo: Path) -> None:
    path = repo / "output" / "papers" / "demo" / "generated" / "sample.npy"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="must end with \\.npz"):
        load_publication_data_npz(path)


def test_load_publication_data_npz_materializes_arrays_before_returning(repo: Path) -> None:
    source = repo / "output" / "papers" / "demo" / "generated" / "sample.npz"
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
    def load_bundle(ctx, paths):
        return paths

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


def test_parser_supports_documented_surface() -> None:
    parser = build_parser()

    assert parser.parse_args(["list"]).subject == "list"
    init_args = parser.parse_args(["init", "demo"])
    assert init_args.subject == "init"
    assert init_args.arg2 == "demo"
    shell_args = parser.parse_args(["demo", "shell"])
    assert shell_args.subject == "demo"
    assert shell_args.arg2 == "shell"
    args = parser.parse_args(["demo", "export", "compare", "2"])
    assert args.subject == "demo"
    assert args.arg2 == "export"
    assert args.arg3 == "compare"
    assert args.arg4 == "2"
    ignore_args = parser.parse_args(["demo", "ignore", "sections/intro.tex"])
    assert ignore_args.subject == "demo"
    assert ignore_args.arg2 == "ignore"
    assert ignore_args.arg3 == "sections/intro.tex"
    data_args = parser.parse_args(["demo", "data", "list"])
    assert data_args.subject == "demo"
    assert data_args.arg2 == "data"
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
    figure_preview_args = parser.parse_args(["demo", "figure", "compare", "preview", "2"])
    assert figure_preview_args.subject == "demo"
    assert figure_preview_args.arg2 == "figure"
    assert figure_preview_args.arg3 == "compare"
    assert figure_preview_args.arg4 == "preview"
    assert figure_preview_args.arg5 == "2"
    pin_args = parser.parse_args(["demo", "data", "training", "pin"])
    assert pin_args.subject == "demo"
    assert pin_args.arg2 == "data"
    assert pin_args.arg3 == "training"
    assert pin_args.arg4 == "pin"


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


def test_cli_export_prints_paper_relative_paths(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "export", "single"]) == 0
    assert capsys.readouterr().out.strip() == "tex/autofigures/single.pdf"


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
        core_cli,
        "_open_publication_previews",
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
        core_cli,
        "_open_publication_previews",
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
        core_cli,
        "_open_publication_previews",
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
        core_cli,
        "_open_publication_previews",
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


def test_cli_preview_uses_vscode_backend_when_configured(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[list[Path], str]] = []
    pdf_path = repo / "papers" / "demo" / "tex" / "build" / "main.pdf"
    pdf_path.write_text("pdf", encoding="utf-8")
    (repo / "pubify.conf").write_text(
        "\n".join(
            [
                "publications_root: papers",
                "data_root: output/papers",
                "preview:",
                "  publication: vscode",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        core_cli,
        "_open_publication_previews",
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
    (repo / "pubify.conf").write_text(
        "\n".join(
            [
                "publications_root: papers",
                "data_root: output/papers",
                "preview:",
                "  figure: vscode",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        core_cli,
        "_open_publication_previews",
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
    commands = iter(["check", "export single", "figure single preview 1", "data list", "diff list", "build", "preview", "quit"])

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
    monkeypatch.setattr(core_cli, "build_publication", fake_build)
    monkeypatch.setattr(
        core_cli,
        "_open_publication_previews",
        lambda paths, *, backend: previewed.append((list(paths), backend)),
    )

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert prompts == ["demo> "] * 8
    assert "demo: ok" in captured.out
    assert "tex/autofigures/single.pdf" in captured.out
    assert "pinned   " in captured.out
    assert "main.tex" in captured.out
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
    assert "  figure [list|<figure-id> preview [<subfig-idx>]]" in captured.out
    assert "  preview" in captured.out
    assert "  reload" in captured.out
    assert "Error: unsupported shell command 'list'" in captured.err
    assert "Error: unsupported shell command 'init'" in captured.err


def test_cli_shell_reload_command_reloads_paper(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = iter(["reload", "quit"])
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
    captured = capsys.readouterr()
    assert "demo: reloaded" in captured.out
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
            return "check"
        if step == 2:
            figures_path.write_text(figures_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            return "check"
        return "quit"

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)
    monkeypatch.setattr(core_cli, "load_publication_definition", wrapped_load)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert captured.out.count("demo: ok") == 2
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
            return "check"
        if step == 2:
            config_path.write_text(config_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            return "check"
        return "quit"

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)
    monkeypatch.setattr(core_cli, "load_publication_definition", wrapped_load)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert captured.out.count("demo: ok") == 2
    assert load_count == 2


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
            return "check"
        if step == 2:
            figures_path.write_text("def broken(:\n", encoding="utf-8")
            return "check"
        if step == 3:
            figures_path.write_text(original_text, encoding="utf-8")
            return "check"
        return "quit"

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert captured.out.count("demo: ok") == 2
    assert "Error:" in captured.err


def test_cli_shell_persists_history_in_publication_root(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_publication(load_publication_definition(repo, "demo"))
    history_path = repo / "papers" / "demo" / ".pubs-history"
    history_path.write_text("check\n", encoding="utf-8")
    fake_readline = FakeReadline()
    commands = iter(["export single", "quit"])

    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: fake_readline)

    assert main(["demo", "shell"]) == 0
    assert fake_readline.read_paths == [str(history_path)]
    assert history_path.read_text(encoding="utf-8").splitlines() == ["check", "export single", "quit"]


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

    libedit_readline = FakeReadline()
    libedit_readline.__doc__ = "libedit readline compatibility"
    monkeypatch.setitem(sys.modules, "readline", libedit_readline)
    assert core_cli._configure_shell_readline() is libedit_readline
    assert "bind ^[[C ed-next-char" in libedit_readline.bindings
    assert "bind ^[[D ed-prev-char" in libedit_readline.bindings
    assert "bind ^[OC ed-next-char" in libedit_readline.bindings
    assert "bind ^[OD ed-prev-char" in libedit_readline.bindings


def test_subfigure_index_is_one_based(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")

    output = run_figures(paper, "compare", 2)
    assert [path.name for path in output] == ["compare_2.pdf"]

    with pytest.raises(IndexError):
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

    monkeypatch.setattr(core_cli, "build_publication", fail_build)

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

    monkeypatch.setattr(core_cli, "build_publication", succeed_build)

    assert main(["demo", "build"]) == 0
    captured = capsys.readouterr()
    assert (
        captured.out.strip()
        == str(repo / "papers" / "demo" / "tex" / "build" / "main.pdf")
    )


def test_cli_build_does_not_export_by_default(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_run_figures(*args: object, **kwargs: object) -> list[Path]:
        calls.append("export")
        return [repo / "papers" / "demo" / "tex" / "autofigures" / "single.pdf"]

    def fake_build(paper_definition: object) -> object:
        calls.append("build")
        return None

    monkeypatch.setattr(core_cli, "run_figures", fake_run_figures)
    monkeypatch.setattr(core_cli, "build_publication", fake_build)

    assert main(["demo", "build"]) == 0
    captured = capsys.readouterr()
    assert calls == ["build"]
    assert captured.out.strip().endswith("/tex/build/main.pdf")


def test_cli_build_export_runs_full_export_before_build(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_run_figures(paper_definition: object, *args: object) -> list[Path]:
        calls.append("export")
        return [
            repo / "papers" / "demo" / "tex" / "autofigures" / "first.pdf",
            repo / "papers" / "demo" / "tex" / "autofigures" / "second.pdf",
        ]

    def fake_build(paper_definition: object) -> object:
        calls.append("build")
        return None

    monkeypatch.setattr(core_cli, "run_figures", fake_run_figures)
    monkeypatch.setattr(core_cli, "build_publication", fake_build)

    assert main(["demo", "build", "--export"]) == 0
    captured = capsys.readouterr()
    assert calls == ["export", "build"]
    assert captured.out.splitlines() == [
        "tex/autofigures/first.pdf",
        "tex/autofigures/second.pdf",
        str(repo / "papers" / "demo" / "tex" / "build" / "main.pdf"),
    ]


def test_cli_build_export_if_stale_runs_export_when_outputs_missing(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_build(paper_definition: object) -> object:
        calls.append("build")
        return None

    def fake_run_figures(paper_definition: object, *args: object) -> list[Path]:
        calls.append("export")
        return [repo / "papers" / "demo" / "tex" / "autofigures" / "single.pdf"]

    monkeypatch.setattr(core_cli, "build_publication", fake_build)
    monkeypatch.setattr(core_cli, "run_figures", fake_run_figures)

    paper = load_publication_definition(repo, "demo")
    if paper.paths.autofigures_root.exists():
        shutil.rmtree(paper.paths.autofigures_root)

    assert main(["demo", "build", "--export-if-stale"]) == 0
    assert calls == ["export", "build"]
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == "tex/autofigures/single.pdf"


def test_cli_build_export_if_stale_runs_export_when_outputs_empty(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_build(paper_definition: object) -> object:
        calls.append("build")
        return None

    def fake_run_figures(paper_definition: object, *args: object) -> list[Path]:
        calls.append("export")
        return [repo / "papers" / "demo" / "tex" / "autofigures" / "single.pdf"]

    monkeypatch.setattr(core_cli, "build_publication", fake_build)
    monkeypatch.setattr(core_cli, "run_figures", fake_run_figures)

    paper = load_publication_definition(repo, "demo")
    paper.paths.autofigures_root.mkdir(parents=True, exist_ok=True)

    assert main(["demo", "build", "--export-if-stale"]) == 0
    assert calls == ["export", "build"]
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == "tex/autofigures/single.pdf"


def test_cli_build_export_if_stale_runs_export_when_figures_py_is_newer(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_build(paper_definition: object) -> object:
        calls.append("build")
        return None

    def fake_run_figures(paper_definition: object, *args: object) -> list[Path]:
        calls.append("export")
        return [repo / "papers" / "demo" / "tex" / "autofigures" / "single.pdf"]

    monkeypatch.setattr(core_cli, "build_publication", fake_build)
    monkeypatch.setattr(core_cli, "run_figures", fake_run_figures)

    paper = load_publication_definition(repo, "demo")
    paper.paths.autofigures_root.mkdir(parents=True, exist_ok=True)
    exported = paper.paths.autofigures_root / "single.pdf"
    exported.write_text("old export\n", encoding="utf-8")
    entrypoint_mtime = paper.paths.entrypoint.stat().st_mtime
    old_mtime = max(entrypoint_mtime - 10, 1)
    os.utime(exported, (old_mtime, old_mtime))
    new_entrypoint_mtime = old_mtime + 20
    os.utime(paper.paths.entrypoint, (new_entrypoint_mtime, new_entrypoint_mtime))

    assert main(["demo", "build", "--export-if-stale"]) == 0
    assert calls == ["export", "build"]
    capsys.readouterr()


def test_cli_build_export_if_stale_skips_export_when_outputs_are_newer(
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

    monkeypatch.setattr(core_cli, "build_publication", fake_build)
    monkeypatch.setattr(core_cli, "run_figures", fake_run_figures)

    paper = load_publication_definition(repo, "demo")
    paper.paths.autofigures_root.mkdir(parents=True, exist_ok=True)
    exported = paper.paths.autofigures_root / "single.pdf"
    exported.write_text("new export\n", encoding="utf-8")
    entrypoint_mtime = paper.paths.entrypoint.stat().st_mtime
    new_mtime = entrypoint_mtime + 10
    os.utime(exported, (new_mtime, new_mtime))

    assert main(["demo", "build", "--export-if-stale"]) == 0
    captured = capsys.readouterr()
    assert calls == ["build"]
    assert captured.out.strip().endswith("/tex/build/main.pdf")


def test_cli_build_rejects_both_export_flags(repo: Path) -> None:
    with pytest.raises(SystemExit):
        main(["demo", "build", "--export", "--export-if-stale"])


def test_cli_build_still_rejects_force(repo: Path) -> None:
    with pytest.raises(SystemExit):
        main(["demo", "build", "--force"])


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
    fresh_data_root = repo / "output" / "papers" / "fresh"
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
    assert (
        "from pubify_pubs.data import load_publication_data_npz, publication_data_path, save_publication_data_npz"
        in figures_py
    )
    assert "from pubify_pubs.decorators import data, figure" in figures_py
    main_tex = fresh_root / "tex" / "main.tex"
    assert main_tex.exists()
    main_tex_text = main_tex.read_text(encoding="utf-8")
    assert r"\usepackage{pubify}" in main_tex_text
    assert r"\graphicspath{{figures/}}" not in main_tex_text
    assert (fresh_root / "tex" / "autofigures").exists()
    assert (fresh_root / "tex" / "build").exists()
    assert not (fresh_root / "preview").exists()
    assert (fresh_root / "tex" / "pubify.sty").exists()
    assert (fresh_root / "tex" / "pubify-template.tex").exists()
    assert fake_pubify_mpl.prepare_calls[0][0] == fresh_root / "tex"
    assert fake_pubify_mpl.prepare_calls[0][1]["textwidth_in"] == 5.39643
    assert fake_pubify_mpl.prepare_calls[0][1]["textheight_in"] == 7.5896
    assert fake_pubify_mpl.prepare_calls[0][1]["base_fontsize_pt"] == 12.0
    assert fake_pubify_mpl.prepare_calls[0][1]["caption_lineheight_pt"] == 13.6


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
    (repo / "output" / "papers" / "demo" / "training.npy").unlink()
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
            "from pubify_pubs.decorators import data, external_data, figure",
            "",
            "@data('training.npy')",
            "def load_training(ctx, path):",
            "    return path",
            "",
            "@data(model='bundle/model.txt', meta='bundle/meta.txt')",
            "def load_bundle(ctx, paths):",
            "    return paths",
            "",
            "@external_data('scratch', 'training.npy')",
            "def load_external_training(ctx, path):",
            "    return path",
            "",
            "@external_data('scratch', model='bundle/model.txt', meta='bundle/meta.txt')",
            "def load_external_bundle(ctx, paths):",
            "    return paths",
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
            "from pubify_pubs.decorators import external_data, figure",
            "",
            "@external_data('scratch', model_dir='models', tiptop_dir='tiptop')",
            "def load_one(ctx, paths):",
            "    return paths",
            "",
            "@external_data('scratch', model_dir='models', tiptop_dir='tiptop')",
            "def load_two(ctx, paths):",
            "    return paths",
            "",
            "@external_data('scratch', tiptop_dir='tiptop')",
            "def load_three(ctx, paths):",
            "    return paths",
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
            "from pubify_pubs.decorators import data, external_data, figure",
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
            "from pubify_pubs.decorators import figure",
            "",
            "@figure",
            "def plot_unused(ctx):",
            "    return None",
        ],
    )
    monkeypatch.setattr(core_cli.sys.stdout, "isatty", lambda: False)

    assert main(["nodata", "data", "list"]) == 0
    assert capsys.readouterr().out.strip() == "nodata: no declared data"


def test_data_pin_single_path_rewrites_loader_and_copies_file(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scratch_root = repo / "pin-src-single"
    scratch_root.mkdir(parents=True, exist_ok=True)
    (scratch_root / "training.npy").write_text("external-data", encoding="utf-8")
    _write_external_paper(
        repo,
        publication_id="pinsingle",
        external_root_lines=[f"  scratch: {scratch_root}"],
        figure_lines=[
            "from pubify_pubs.decorators import external_data, figure",
            "",
            "@external_data('scratch', 'training.npy')",
            "def load_training(ctx, path):",
            "    return path",
            "",
            "@figure",
            "def plot_unused(ctx, training):",
            "    return None",
        ],
    )
    paper = load_publication_definition(repo, "pinsingle")
    init_publication(paper)

    assert main(["pinsingle", "data", "training", "pin"]) == 0
    output = capsys.readouterr().out.strip().splitlines()

    assert output[0] == "pinsingle: pinned loader training"
    assert "output/papers/pinsingle/training.npy" in output
    assert output[-1] == "@data('training.npy')"
    assert (repo / "output" / "papers" / "pinsingle" / "training.npy").read_text(
        encoding="utf-8"
    ) == "external-data"
    figures_text = (repo / "papers" / "pinsingle" / "figures.py").read_text(
        encoding="utf-8"
    )
    assert "@data('training.npy')" in figures_text
    assert "@external_data('scratch', 'training.npy')" not in figures_text
    assert "from pubify_pubs.decorators import data, external_data, figure" in figures_text
    reloaded = load_publication_definition(repo, "pinsingle")
    assert reloaded.loaders["training"].kind == "data"
    check_publication(reloaded)


def test_data_pin_named_paths_preserves_nocache_and_copies_directories(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scratch_root = repo / "pin-src-named"
    (scratch_root / "models").mkdir(parents=True, exist_ok=True)
    (scratch_root / "models" / "weights.bin").write_text("weights", encoding="utf-8")
    (scratch_root / "meta").mkdir(parents=True, exist_ok=True)
    (scratch_root / "meta" / "bundle.json").write_text("{}", encoding="utf-8")
    _write_external_paper(
        repo,
        publication_id="pinnamed",
        external_root_lines=[f"  scratch: {scratch_root}"],
        figure_lines=[
            "from pubify_pubs.decorators import external_data, figure",
            "",
            "@external_data('scratch', model_dir='models', meta_dir='meta', nocache=True)",
            "def load_bundle(ctx, paths):",
            "    return paths",
            "",
            "@external_data('scratch', 'other.txt')",
            "def load_other(ctx, path):",
            "    return path",
            "",
            "@figure",
            "def plot_unused(ctx, bundle):",
            "    return None",
        ],
    )
    (scratch_root / "other.txt").write_text("other", encoding="utf-8")

    assert main(["pinnamed", "data", "bundle", "pin"]) == 0
    output = capsys.readouterr().out.strip().splitlines()

    assert output[-1] == "@data(model_dir='models', meta_dir='meta', nocache=True)"
    figures_text = (repo / "papers" / "pinnamed" / "figures.py").read_text(
        encoding="utf-8"
    )
    assert "@data(model_dir='models', meta_dir='meta', nocache=True)" in figures_text
    assert "@external_data('scratch', 'other.txt')" in figures_text
    assert (repo / "output" / "papers" / "pinnamed" / "models" / "weights.bin").exists()
    assert (repo / "output" / "papers" / "pinnamed" / "meta" / "bundle.json").exists()
    reloaded = load_publication_definition(repo, "pinnamed")
    assert reloaded.loaders["bundle"].kind == "data"
    assert reloaded.loaders["bundle"].nocache is True


def test_data_pin_fails_cleanly_for_unknown_loader(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "data", "missing", "pin"]) == 1
    assert "Unknown loader 'missing'" in capsys.readouterr().err


def test_data_pin_fails_cleanly_for_non_external_loader(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "data", "training", "pin"]) == 1
    assert "Loader 'training' is not declared with @external_data" in capsys.readouterr().err


def test_data_pin_large_copy_aborts_before_mutation(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch_root = repo / "pin-src-large"
    scratch_root.mkdir(parents=True, exist_ok=True)
    (scratch_root / "training.npy").write_text("0123456789", encoding="utf-8")
    _write_external_paper(
        repo,
        publication_id="pinlarge",
        external_root_lines=[f"  scratch: {scratch_root}"],
        figure_lines=[
            "from pubify_pubs.decorators import external_data, figure",
            "",
            "@external_data('scratch', 'training.npy')",
            "def load_training(ctx, path):",
            "    return path",
            "",
            "@figure",
            "def plot_unused(ctx, training):",
            "    return None",
        ],
    )
    monkeypatch.setattr(core_pinning, "PIN_SIZE_WARNING_BYTES", 5)
    original_text = (repo / "papers" / "pinlarge" / "figures.py").read_text(
        encoding="utf-8"
    )

    assert main(["pinlarge", "data", "training", "pin"]) == 1
    captured = capsys.readouterr()
    assert "exceeds the safe copy limit" in captured.err
    assert "training.npy" in captured.err
    assert (
        repo / "papers" / "pinlarge" / "figures.py"
    ).read_text(encoding="utf-8") == original_text
    assert not (repo / "output" / "papers" / "pinlarge" / "training.npy").exists()


def test_data_pin_fails_on_ambiguous_existing_target_path(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scratch_root = repo / "pin-src-ambiguous"
    (scratch_root / "models").mkdir(parents=True, exist_ok=True)
    (scratch_root / "models" / "weights.bin").write_text("weights", encoding="utf-8")
    _write_external_paper(
        repo,
        publication_id="pinambiguous",
        external_root_lines=[f"  scratch: {scratch_root}"],
        figure_lines=[
            "from pubify_pubs.decorators import external_data, figure",
            "",
            "@external_data('scratch', 'models')",
            "def load_bundle(ctx, path):",
            "    return path",
            "",
            "@figure",
            "def plot_unused(ctx, bundle):",
            "    return None",
        ],
    )
    target = repo / "output" / "papers" / "pinambiguous" / "models"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("existing file", encoding="utf-8")

    assert main(["pinambiguous", "data", "bundle", "pin"]) == 1
    assert "would replace an existing pinned path ambiguously" in capsys.readouterr().err


def test_data_pin_refuses_overwriting_existing_pinned_file_with_different_contents(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scratch_root = repo / "pin-src-existing-file"
    scratch_root.mkdir(parents=True, exist_ok=True)
    (scratch_root / "training.npy").write_text("external-data", encoding="utf-8")
    _write_external_paper(
        repo,
        publication_id="pinexistingfile",
        external_root_lines=[f"  scratch: {scratch_root}"],
        figure_lines=[
            "from pubify_pubs.decorators import external_data, figure",
            "",
            "@external_data('scratch', 'training.npy')",
            "def load_training(ctx, path):",
            "    return path",
            "",
            "@figure",
            "def plot_unused(ctx, training):",
            "    return None",
        ],
    )
    target = repo / "output" / "papers" / "pinexistingfile" / "training.npy"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old-pinned-data", encoding="utf-8")
    original_text = (repo / "papers" / "pinexistingfile" / "figures.py").read_text(
        encoding="utf-8"
    )

    assert main(["pinexistingfile", "data", "training", "pin"]) == 1
    captured = capsys.readouterr()
    assert "would overwrite an existing pinned file with different contents" in captured.err
    assert target.read_text(encoding="utf-8") == "old-pinned-data"
    assert (
        repo / "papers" / "pinexistingfile" / "figures.py"
    ).read_text(encoding="utf-8") == original_text


def test_data_pin_creates_output_parent_when_missing(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scratch_root = repo / "pin-src-first"
    scratch_root.mkdir(parents=True, exist_ok=True)
    (scratch_root / "training.npy").write_text("external-data", encoding="utf-8")
    _write_external_paper(
        repo,
        publication_id="pinfirst",
        external_root_lines=[f"  scratch: {scratch_root}"],
        figure_lines=[
            "from pubify_pubs.decorators import external_data, figure",
            "",
            "@external_data('scratch', 'training.npy')",
            "def load_training(ctx, path):",
            "    return path",
            "",
            "@figure",
            "def plot_unused(ctx, training):",
            "    return None",
        ],
    )
    shutil.rmtree(repo / "output" / "papers")

    assert main(["pinfirst", "data", "training", "pin"]) == 0
    capsys.readouterr()
    assert (repo / "output" / "papers" / "pinfirst" / "training.npy").exists()


def test_data_pin_preserves_unrelated_loaders(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scratch_root = repo / "pin-src-unrelated"
    scratch_root.mkdir(parents=True, exist_ok=True)
    (scratch_root / "a.txt").write_text("a", encoding="utf-8")
    (scratch_root / "b.txt").write_text("b", encoding="utf-8")
    _write_external_paper(
        repo,
        publication_id="pinunrelated",
        external_root_lines=[f"  scratch: {scratch_root}"],
        figure_lines=[
            "from pubify_pubs.decorators import external_data, figure",
            "",
            "@external_data('scratch', 'a.txt')",
            "def load_alpha(ctx, path):",
            "    return path",
            "",
            "@external_data('scratch', 'b.txt')",
            "def load_beta(ctx, path):",
            "    return path",
            "",
            "@figure",
            "def plot_unused(ctx, alpha):",
            "    return None",
        ],
    )

    assert main(["pinunrelated", "data", "alpha", "pin"]) == 0
    figures_text = (repo / "papers" / "pinunrelated" / "figures.py").read_text(
        encoding="utf-8"
    )
    assert "@data('a.txt')" in figures_text
    assert "@external_data('scratch', 'b.txt')" in figures_text


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
            "from pubify_pubs.decorators import external_data, figure",
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
            "from pubify_pubs.decorators import external_data, figure",
            "",
            "@external_data('shared', model='bundle/model.txt', meta='bundle/meta.txt')",
            "def load_bundle(ctx, paths):",
            "    return '|'.join(paths[name].read_text(encoding='utf-8') for name in sorted(paths))",
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
            "from pubify_pubs.decorators import external_data",
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
            "from pubify_pubs.decorators import external_data",
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
            "from pubify_pubs.decorators import external_data",
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
            "from pubify_pubs.decorators import external_data, figure",
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
            "from pubify_pubs.decorators import external_data, figure",
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


def test_push_requires_configured_mirror_root(repo: Path) -> None:
    publication_root = repo / "papers" / "nomirror"
    (publication_root / "tex").mkdir(parents=True, exist_ok=True)
    (repo / "output" / "papers" / "nomirror").mkdir(parents=True, exist_ok=True)
    (publication_root / "pub.yaml").write_text(
        "\n".join(
            [
                'mirror_root: ""',
                "main_tex: main.tex",
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
    (publication_root / "figures.py").write_text("from pubify_pubs.decorators import figure\n", encoding="utf-8")
    (publication_root / "tex" / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")

    paper = load_publication_definition(repo, "nomirror")

    with pytest.raises(ValueError, match="does not define mirror_root"):
        push_publication(paper)


def test_build_fails_with_init_message_when_pubify_support_files_are_missing(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    with pytest.raises(ValueError, match=r"Run `pubs demo init`"):
        build_publication(paper)


def test_ignore_adds_new_exact_relative_path(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["demo", "ignore", "sections/intro.tex"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == "demo: added sync ignore sections/intro.tex"
    config_text = (repo / "papers" / "demo" / "pub.yaml").read_text(encoding="utf-8")
    assert "  - drafts/*" in config_text
    assert '  - "sections/intro.tex"' in config_text


def test_ignore_is_noop_when_path_is_already_present(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "ignore", "sections/intro.tex"]) == 0
    capsys.readouterr()

    rc = main(["demo", "ignore", "sections/intro.tex"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == "demo: sync ignore already present sections/intro.tex"
    config_text = (repo / "papers" / "demo" / "pub.yaml").read_text(encoding="utf-8")
    assert config_text.count("sections/intro.tex") == 1


def test_ignore_rejects_absolute_paths(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["demo", "ignore", "/abs/path.tex"])

    assert rc == 1
    assert "ignore path must be relative to tex/" in capsys.readouterr().err


def test_ignore_rejects_path_traversal(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["demo", "ignore", "../outside.tex"])

    assert rc == 1
    assert "ignore path must stay under tex/" in capsys.readouterr().err


def test_ignore_rejects_glob_syntax(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["demo", "ignore", "drafts/*.tex"])

    assert rc == 1
    assert "exact relative path, not a glob pattern" in capsys.readouterr().err


def test_ignore_quotes_yaml_scalar_entries(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = 'notes/figure:1 # draft.tex'

    rc = main(["demo", "ignore", path])

    assert rc == 0
    capsys.readouterr()
    config_text = (repo / "papers" / "demo" / "pub.yaml").read_text(encoding="utf-8")
    assert '  - "notes/figure:1 # draft.tex"' in config_text


def test_push_excludes_build_uses_working_tree_and_updates_hash_manifest(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    (paper.paths.tex_root / "main.tex").write_text("working tree main\n", encoding="utf-8")
    (paper.paths.autofigures_root).mkdir(parents=True, exist_ok=True)
    (paper.paths.autofigures_root / "plot.pdf").write_text("figure data\n", encoding="utf-8")
    push_publication(paper)
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    assert (mirror_root / "main.tex").read_text(encoding="utf-8") == "working tree main\n"
    assert (mirror_root / "sections" / "intro.tex").read_text(encoding="utf-8") == "intro\n"
    assert (mirror_root / "autofigures" / "plot.pdf").read_text(encoding="utf-8") == "figure data\n"
    assert not (mirror_root / "build" / "ignored.aux").exists()
    assert not (mirror_root / "drafts" / "note.txt").exists()
    sync_text = (mirror_root / ".pubs-sync.yaml").read_text(encoding="utf-8")
    assert f"main.tex: {_hash_text('working tree main\n')}" in sync_text
    assert f"sections/intro.tex: {_hash_text('intro\n')}" in sync_text
    assert "autofigures/plot.pdf" not in sync_text
    assert (paper.paths.tex_root / ".pubs-sync.yaml").read_text(encoding="utf-8") == sync_text
    assert (paper.paths.sync_base_root / "main.tex").read_text(encoding="utf-8") == "working tree main\n"
    assert (paper.paths.sync_base_root / "sections" / "intro.tex").read_text(encoding="utf-8") == "intro\n"


def test_push_does_not_delete_mirror_only_files(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (mirror_root / "mirror-only.tex").write_text("remote only\n", encoding="utf-8")
    (mirror_root / "autofigures").mkdir(parents=True, exist_ok=True)
    (mirror_root / "autofigures" / "remote-only.pdf").write_text("remote figure\n", encoding="utf-8")
    push_publication(paper)
    assert (mirror_root / "mirror-only.tex").read_text(encoding="utf-8") == "remote only\n"
    assert (mirror_root / "autofigures" / "remote-only.pdf").read_text(encoding="utf-8") == "remote figure\n"
    assert (mirror_root / "main.tex").read_text(encoding="utf-8") == (
        paper.paths.tex_root / "main.tex"
    ).read_text(encoding="utf-8")
    assert (mirror_root / "sections" / "intro.tex").read_text(encoding="utf-8") == "intro\n"


def test_initial_push_adopts_full_non_conflicting_union_into_manifest(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "local-only.tex").write_text("local only\n", encoding="utf-8")
    (mirror_root / "mirror-only.tex").write_text("mirror only\n", encoding="utf-8")
    shared = (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8")
    (mirror_root / "main.tex").write_text(shared, encoding="utf-8")

    push_publication(paper)

    sync_text = (paper.paths.tex_root / ".pubs-sync.yaml").read_text(encoding="utf-8")
    assert f"local-only.tex: {_hash_text('local only\n')}" in sync_text
    assert f"mirror-only.tex: {_hash_text('mirror only\n')}" in sync_text
    assert f"main.tex: {_hash_text(shared)}" in sync_text
    assert "autofigures/" not in sync_text
    assert (mirror_root / ".pubs-sync.yaml").read_text(encoding="utf-8") == sync_text


def test_initial_push_aborts_before_changes_on_overlaps(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    original_local = (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="block push"):
        push_publication(paper)

    assert (mirror_root / "main.tex").read_text(encoding="utf-8") == "mirror changed\n"
    assert not (paper.paths.tex_root / ".pubs-sync.yaml").exists()
    assert not (mirror_root / ".pubs-sync.yaml").exists()
    assert (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8") == original_local


def test_push_aborts_before_changes_when_conflicting_exists(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (paper.paths.tex_root / "local-only.tex").write_text("local only\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline\n')}",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")

    with pytest.raises(RuntimeError, match="Conflicting local and mirror changes block push"):
        push_publication(paper)

    assert (mirror_root / "main.tex").read_text(encoding="utf-8") == "mirror changed\n"


def test_push_force_overwrites_conflicting_mirror_file(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    (mirror_root / "mirror-only.tex").write_text("keep mirror\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline\n')}",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")

    result = push_publication(paper, force=True)

    assert result.forced_paths == ("main.tex",)
    assert (mirror_root / "main.tex").read_text(encoding="utf-8") == "local changed\n"
    assert (mirror_root / "mirror-only.tex").read_text(encoding="utf-8") == "keep mirror\n"
    sync_text = (paper.paths.tex_root / ".pubs-sync.yaml").read_text(encoding="utf-8")
    assert f"main.tex: {_hash_text('local changed\n')}" in sync_text


def test_push_copies_local_changed_files_and_preserves_mirror_changed_files(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("baseline main\n", encoding="utf-8")
    (paper.paths.tex_root / "sections" / "intro.tex").write_text("intro\n", encoding="utf-8")
    (mirror_root / "sections").mkdir(parents=True, exist_ok=True)
    (mirror_root / "sections" / "intro.tex").write_text("mirror changed\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline main\n')}",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")

    push_publication(paper)

    assert (mirror_root / "main.tex").read_text(encoding="utf-8") == "local changed\n"
    assert (mirror_root / "sections" / "intro.tex").read_text(encoding="utf-8") == "mirror changed\n"
    sync_text = (paper.paths.tex_root / ".pubs-sync.yaml").read_text(encoding="utf-8")
    assert f"main.tex: {_hash_text('local changed\n')}" in sync_text
    assert f"sections/intro.tex: {_hash_text('intro\n')}" in sync_text


def test_pull_copies_mirror_only_files(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    local_main = (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8")
    local_intro = (paper.paths.tex_root / "sections" / "intro.tex").read_text(encoding="utf-8")
    (mirror_root / "main.tex").write_text(local_main, encoding="utf-8")
    (mirror_root / "sections").mkdir(parents=True, exist_ok=True)
    (mirror_root / "sections" / "intro.tex").write_text(local_intro, encoding="utf-8")
    (paper.paths.tex_root / "mirror-copy.tex").unlink(missing_ok=True)
    (mirror_root / "mirror-copy.tex").write_text("from mirror\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text(local_main)}",
            f"  sections/intro.tex: {_hash_text(local_intro)}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")

    result = pull_publication(paper)

    assert result.warnings == ()
    assert (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8") == local_main
    assert (paper.paths.tex_root / "mirror-copy.tex").read_text(encoding="utf-8") == "from mirror\n"
    sync_text = (paper.paths.tex_root / ".pubs-sync.yaml").read_text(encoding="utf-8")
    assert f"mirror-copy.tex: {_hash_text('from mirror\n')}" in sync_text
    assert (paper.paths.sync_base_root / "mirror-copy.tex").read_text(encoding="utf-8") == "from mirror\n"


def test_initial_pull_adopts_full_non_conflicting_union_into_manifest(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "local-only.tex").write_text("local only\n", encoding="utf-8")
    (mirror_root / "mirror-only.tex").write_text("mirror only\n", encoding="utf-8")
    shared = (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8")
    (mirror_root / "main.tex").write_text(shared, encoding="utf-8")

    result = pull_publication(paper)

    assert result.warnings == (
        "Local-only file kept during pull: local-only.tex",
        "Local-only file kept during pull: sections/intro.tex",
    )
    sync_text = (paper.paths.tex_root / ".pubs-sync.yaml").read_text(encoding="utf-8")
    assert f"local-only.tex: {_hash_text('local only\n')}" in sync_text
    assert f"mirror-only.tex: {_hash_text('mirror only\n')}" in sync_text
    assert f"main.tex: {_hash_text(shared)}" in sync_text
    assert f"sections/intro.tex: {_hash_text('intro\n')}" in sync_text
    assert "autofigures/" not in sync_text
    assert (mirror_root / ".pubs-sync.yaml").read_text(encoding="utf-8") == sync_text


def test_initial_pull_aborts_before_changes_on_overlaps(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    original_local = (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="block pull"):
        pull_publication(paper)

    assert (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8") == original_local
    assert not (paper.paths.tex_root / ".pubs-sync.yaml").exists()
    assert not (mirror_root / ".pubs-sync.yaml").exists()


def test_pull_warns_and_does_not_delete_local_only_files(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (mirror_root / "main.tex").write_text(
        (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (mirror_root / "sections").mkdir(parents=True, exist_ok=True)
    (mirror_root / "sections" / "intro.tex").write_text("intro\n", encoding="utf-8")
    (paper.paths.tex_root / "local-only.tex").write_text("keep local\n", encoding="utf-8")

    result = pull_publication(paper)

    assert result.warnings == ("Local-only file kept during pull: local-only.tex",)
    assert (paper.paths.tex_root / "local-only.tex").read_text(encoding="utf-8") == "keep local\n"
    assert not (mirror_root / "local-only.tex").exists()


def test_pull_force_overwrites_conflicting_local_file(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    (paper.paths.tex_root / "local-only.tex").write_text("keep local\n", encoding="utf-8")
    (mirror_root / "sections").mkdir(parents=True, exist_ok=True)
    (mirror_root / "sections" / "intro.tex").write_text("intro\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline\n')}",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")

    result = pull_publication(paper, force=True)

    assert result.forced_paths == ("main.tex",)
    assert result.warnings == ("Local-only file kept during pull: local-only.tex",)
    assert (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8") == "mirror changed\n"
    assert (paper.paths.tex_root / "local-only.tex").read_text(encoding="utf-8") == "keep local\n"
    sync_text = (paper.paths.tex_root / ".pubs-sync.yaml").read_text(encoding="utf-8")
    assert f"main.tex: {_hash_text('mirror changed\n')}" in sync_text


def test_pull_copies_mirror_changed_files(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "main.tex").write_text("baseline\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    (mirror_root / "sections").mkdir(parents=True, exist_ok=True)
    (mirror_root / "sections" / "intro.tex").write_text("intro\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline\n')}",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")

    result = pull_publication(paper)

    assert result.warnings == ()
    assert (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8") == "mirror changed\n"
    sync_text = (paper.paths.tex_root / ".pubs-sync.yaml").read_text(encoding="utf-8")
    assert f"main.tex: {_hash_text('mirror changed\n')}" in sync_text


def test_pull_does_not_import_mirror_figure_files(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (mirror_root / "autofigures").mkdir(parents=True, exist_ok=True)
    (mirror_root / "autofigures" / "remote.pdf").write_text("mirror figure\n", encoding="utf-8")

    result = pull_publication(paper)

    assert not (paper.paths.autofigures_root / "remote.pdf").exists()


def test_force_does_not_apply_to_ignored_or_figure_files(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.autofigures_root).mkdir(parents=True, exist_ok=True)
    (paper.paths.autofigures_root / "plot.pdf").write_text("local figure\n", encoding="utf-8")
    (mirror_root / "autofigures").mkdir(parents=True, exist_ok=True)
    (mirror_root / "autofigures" / "plot.pdf").write_text("mirror figure\n", encoding="utf-8")
    (paper.paths.tex_root / "drafts" / "note.txt").write_text("local ignored\n", encoding="utf-8")
    (mirror_root / "drafts").mkdir(parents=True, exist_ok=True)
    (mirror_root / "drafts" / "note.txt").write_text("mirror ignored\n", encoding="utf-8")

    push_publication(paper, force=True)
    pull_publication(paper, force=True)

    assert (mirror_root / "autofigures" / "plot.pdf").read_text(encoding="utf-8") == "local figure\n"
    assert (paper.paths.autofigures_root / "plot.pdf").read_text(encoding="utf-8") == "local figure\n"
    assert (mirror_root / "drafts" / "note.txt").read_text(encoding="utf-8") == "mirror ignored\n"
    assert (paper.paths.tex_root / "drafts" / "note.txt").read_text(encoding="utf-8") == "local ignored\n"


def test_pull_warns_when_local_only_file_exists_on_both_sides(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    baseline_main = (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8")
    (mirror_root / "main.tex").write_text(baseline_main, encoding="utf-8")
    (mirror_root / "sections").mkdir(parents=True, exist_ok=True)
    (mirror_root / "sections" / "intro.tex").write_text("intro\n", encoding="utf-8")
    (paper.paths.tex_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text(baseline_main)}",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")

    result = pull_publication(paper)

    assert result.warnings == ("Local-changed file kept during pull: main.tex",)
    assert (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8") == "local changed\n"


def test_pull_aborts_before_changes_when_conflicting_exists(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline\n')}",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")

    with pytest.raises(RuntimeError, match="Conflicting local and mirror changes block pull"):
        pull_publication(paper)

    assert (paper.paths.tex_root / "main.tex").read_text(encoding="utf-8") == "local changed\n"


def test_ignored_files_are_untouched_by_sync(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (mirror_root / "drafts").mkdir(parents=True, exist_ok=True)
    (mirror_root / "drafts" / "note.txt").write_text("mirror ignored\n", encoding="utf-8")
    push_publication(paper)
    assert (mirror_root / "drafts" / "note.txt").read_text(encoding="utf-8") == "mirror ignored\n"
    pull_publication(paper)
    assert (paper.paths.tex_root / "drafts" / "note.txt").read_text(encoding="utf-8") == "skip\n"


def test_diff_reports_hash_based_statuses(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (mirror_root / "main.tex").write_text("mirror main\n", encoding="utf-8")
    (mirror_root / "sections").mkdir(parents=True, exist_ok=True)
    (mirror_root / "sections" / "intro.tex").write_text("shared new\n", encoding="utf-8")
    (mirror_root / "mirror-only.tex").write_text("mirror only\n", encoding="utf-8")
    (mirror_root / "synced.tex").write_text("same changed\n", encoding="utf-8")

    (paper.paths.tex_root / "main.tex").write_text("local main\n", encoding="utf-8")
    (paper.paths.tex_root / "sections" / "intro.tex").write_text("shared new\n", encoding="utf-8")
    (paper.paths.tex_root / "local-only.tex").write_text("local only\n", encoding="utf-8")
    (paper.paths.tex_root / "synced.tex").write_text("same changed\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline main\n')}",
            f"  sections/intro.tex: {_hash_text('baseline intro\n')}",
            f"  synced.tex: {_hash_text('baseline synced\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")

    entries = {entry.path: entry for entry in diff_publication(paper)}
    assert entries["local-only.tex"].status == "local-only"
    assert entries["mirror-only.tex"].status == "mirror-only"
    assert entries["main.tex"].status == "conflicting"
    assert entries["sections/intro.tex"].status == "in-sync"
    assert entries["synced.tex"].status == "in-sync"
    assert entries["local-only.tex"].diff is None
    assert entries["mirror-only.tex"].diff is None
    assert "local/main.tex" in entries["main.tex"].diff
    assert all(not path.startswith("autofigures/") for path in entries)


def test_diff_distinguishes_existence_only_and_unilateral_change_statuses(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "local-only.tex").write_text("local only\n", encoding="utf-8")
    (mirror_root / "mirror-only.tex").write_text("mirror only\n", encoding="utf-8")
    (paper.paths.tex_root / "local-changed.tex").write_text("local changed\n", encoding="utf-8")
    (mirror_root / "local-changed.tex").write_text("baseline local\n", encoding="utf-8")
    (paper.paths.tex_root / "mirror-changed.tex").write_text("baseline mirror\n", encoding="utf-8")
    (mirror_root / "mirror-changed.tex").write_text("mirror changed\n", encoding="utf-8")
    (paper.paths.tex_root / "in-sync.tex").write_text("same changed\n", encoding="utf-8")
    (mirror_root / "in-sync.tex").write_text("same changed\n", encoding="utf-8")
    (paper.paths.tex_root / "conflicting.tex").write_text("local conflict\n", encoding="utf-8")
    (mirror_root / "conflicting.tex").write_text("mirror conflict\n", encoding="utf-8")
    (paper.paths.tex_root / "unchanged.tex").write_text("baseline unchanged\n", encoding="utf-8")
    (mirror_root / "unchanged.tex").write_text("baseline unchanged\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  local-changed.tex: {_hash_text('baseline local\n')}",
            f"  mirror-changed.tex: {_hash_text('baseline mirror\n')}",
            f"  in-sync.tex: {_hash_text('baseline sync\n')}",
            f"  conflicting.tex: {_hash_text('baseline conflict\n')}",
            f"  unchanged.tex: {_hash_text('baseline unchanged\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")

    entries = {entry.path: entry for entry in diff_publication(paper)}

    assert entries["local-only.tex"].status == "local-only"
    assert entries["mirror-only.tex"].status == "mirror-only"
    assert entries["local-changed.tex"].status == "local-changed"
    assert entries["mirror-changed.tex"].status == "mirror-changed"
    assert entries["in-sync.tex"].status == "in-sync"
    assert entries["conflicting.tex"].status == "conflicting"
    assert entries["unchanged.tex"].status == "unchanged"


def test_cli_diff_list_and_single_path(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (paper.paths.tex_root / "local-only.tex").write_text("local only\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline\n')}",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (paper.paths.sync_base_root / "main.tex").parent.mkdir(parents=True, exist_ok=True)
    (paper.paths.sync_base_root / "main.tex").write_text("baseline\n", encoding="utf-8")
    launched: list[str] = []
    monkeypatch.setattr(core_cli, "merge_conflicting_file", lambda paper, path: launched.append(path))
    monkeypatch.setattr(core_cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(core_cli.sys.stdin, "isatty", lambda: True)

    assert main(["demo", "diff", "list"]) == 0
    list_output = capsys.readouterr().out
    assert "conflicting    main.tex" in _strip_ansi(list_output)
    assert "local/main.tex" not in list_output

    assert main(["demo", "diff", "main.tex"]) == 0
    one_output = capsys.readouterr().out
    assert _strip_ansi(one_output).startswith("conflicting    main.tex")
    assert "local/main.tex" not in one_output
    assert launched == ["main.tex"]

    assert main(["demo", "diff", "local-only.tex"]) == 0
    local_only_output = capsys.readouterr().out
    assert _strip_ansi(local_only_output).strip() == "local-only     local-only.tex"


def test_cli_diff_displays_internal_in_sync_as_unchanged(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "sections" / "intro.tex").write_text("shared new\n", encoding="utf-8")
    (mirror_root / "sections").mkdir(parents=True, exist_ok=True)
    (mirror_root / "sections" / "intro.tex").write_text("shared new\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    monkeypatch.setattr(core_cli.sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(core_cli.sys.stdin, "isatty", lambda: False)

    assert main(["demo", "diff", "sections/intro.tex"]) == 0
    output = capsys.readouterr().out
    assert output.strip() == "unchanged      sections/intro.tex"


def test_cli_diff_colors_status_only_on_tty(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline\n')}",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (paper.paths.sync_base_root / "main.tex").parent.mkdir(parents=True, exist_ok=True)
    (paper.paths.sync_base_root / "main.tex").write_text("baseline\n", encoding="utf-8")
    monkeypatch.setattr(core_cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(core_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(core_cli, "merge_conflicting_file", lambda paper, path: None)

    assert main(["demo", "diff", "main.tex"]) == 0
    output = capsys.readouterr().out

    assert "\033[31m" in output
    assert "main.tex" in output
    assert "\033[31mconflicting   \033[0m main.tex" in output
    assert "mirror/main.tex" not in output
    assert "local/main.tex" not in output
    assert _strip_ansi(output).startswith("conflicting    main.tex")


def test_cli_diff_emits_no_color_when_not_tty(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline\n')}",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (paper.paths.sync_base_root / "main.tex").parent.mkdir(parents=True, exist_ok=True)
    (paper.paths.sync_base_root / "main.tex").write_text("baseline\n", encoding="utf-8")
    monkeypatch.setattr(core_cli.sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(core_cli, "merge_conflicting_file", lambda paper, path: None)

    assert main(["demo", "diff", "main.tex"]) == 0
    output = capsys.readouterr().out

    assert "\033[" not in output
    assert output.startswith("conflicting    main.tex")


def test_cli_diff_colors_internal_in_sync_using_unchanged_style(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "sections" / "intro.tex").write_text("shared new\n", encoding="utf-8")
    (mirror_root / "sections").mkdir(parents=True, exist_ok=True)
    (mirror_root / "sections" / "intro.tex").write_text("shared new\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    monkeypatch.setattr(core_cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(core_cli.sys.stdin, "isatty", lambda: False)

    assert main(["demo", "diff", "sections/intro.tex"]) == 0
    output = capsys.readouterr().out
    assert "\033[2munchanged" in output
    assert "\033[32m" not in output
    assert _strip_ansi(output).strip() == "unchanged      sections/intro.tex"


def test_cli_conflicting_diff_path_does_not_launch_kdiff3_when_not_tty(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (paper.paths.sync_base_root / "main.tex").parent.mkdir(parents=True, exist_ok=True)
    (paper.paths.sync_base_root / "main.tex").write_text("baseline\n", encoding="utf-8")
    called = False

    def fail_merge(paper: object, path: str) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(core_cli, "merge_conflicting_file", fail_merge)
    monkeypatch.setattr(core_cli.sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(core_cli.sys.stdin, "isatty", lambda: False)

    assert main(["demo", "diff", "main.tex"]) == 0
    output = capsys.readouterr().out

    assert called is False
    assert output.startswith("conflicting    main.tex")
    assert "local/main.tex" in output


def test_cli_pull_reports_simple_success_without_branch_language(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mirror_root = repo / "mirror" / "demo"
    (mirror_root / "mirror-copy.tex").write_text("from mirror\n", encoding="utf-8")

    assert main(["demo", "pull"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "demo: pulled"
    assert "overleaf-sync" not in captured.out


def test_cli_force_reports_forced_overwrite_paths(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mirror_root = repo / "mirror" / "demo"
    publication_root = repo / "papers" / "demo" / "tex"
    (publication_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline\n')}",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (publication_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")

    assert main(["demo", "push", "--force"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "demo: pushed"
    assert "Forced overwrite: main.tex" in captured.err


def test_diff_rejects_figure_paths_as_outside_managed_set(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    paper.paths.autofigures_root.mkdir(parents=True, exist_ok=True)
    (paper.paths.autofigures_root / "foo.pdf").write_text("figure\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Managed tex path not found: autofigures/foo.pdf"):
        diff_publication(paper, "autofigures/foo.pdf")


def test_sync_base_is_excluded_from_managed_sync_and_diff(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    paper.paths.sync_base_root.mkdir(parents=True, exist_ok=True)
    (paper.paths.sync_base_root / "stale.tex").write_text("stale\n", encoding="utf-8")

    entries = {entry.path: entry for entry in diff_publication(paper)}

    assert ".pubs-sync-base/stale.tex" not in entries


def test_vscode_directory_is_excluded_from_managed_sync_and_diff(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None

    (paper.paths.tex_root / ".vscode").mkdir(parents=True, exist_ok=True)
    (paper.paths.tex_root / ".vscode" / "settings.json").write_text(
        '{"latex-workshop.latex.autoBuild.run":"onSave"}\n',
        encoding="utf-8",
    )
    (mirror_root / ".vscode").mkdir(parents=True, exist_ok=True)
    (mirror_root / ".vscode" / "settings.json").write_text(
        '{"latex-workshop.latex.autoBuild.run":"never"}\n',
        encoding="utf-8",
    )

    entries = {entry.path: entry for entry in diff_publication(paper)}
    assert ".vscode/settings.json" not in entries

    push_publication(paper)
    assert (mirror_root / ".vscode" / "settings.json").read_text(encoding="utf-8") == (
        '{"latex-workshop.latex.autoBuild.run":"never"}\n'
    )
    sync_text = (paper.paths.tex_root / ".pubs-sync.yaml").read_text(encoding="utf-8")
    assert ".vscode/" not in sync_text

    (paper.paths.tex_root / ".vscode" / "settings.json").write_text(
        '{"latex-workshop.latex.autoBuild.run":"onSave"}\n',
        encoding="utf-8",
    )
    (mirror_root / ".vscode" / "settings.json").write_text(
        '{"latex-workshop.latex.autoBuild.run":"onFocusChange"}\n',
        encoding="utf-8",
    )

    pull_publication(paper)
    assert (paper.paths.tex_root / ".vscode" / "settings.json").read_text(encoding="utf-8") == (
        '{"latex-workshop.latex.autoBuild.run":"onSave"}\n'
    )


def test_merge_conflicting_file_launches_kdiff3_with_expected_paths(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    (paper.paths.sync_base_root / "main.tex").parent.mkdir(parents=True, exist_ok=True)
    (paper.paths.sync_base_root / "main.tex").write_text("baseline\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    calls: list[list[str]] = []

    core_mirror.merge_conflicting_file(paper, "main.tex", runner=lambda command: calls.append(command))

    expected = [
        "kdiff3",
        (paper.paths.sync_base_root / "main.tex").as_posix(),
        (paper.paths.tex_root / "main.tex").as_posix(),
        (mirror_root / "main.tex").as_posix(),
        "-o",
        (paper.paths.tex_root / "main.tex").as_posix(),
    ]
    assert calls == [expected]


def test_non_conflicting_diff_path_does_not_launch_kdiff3(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mirror_root = repo / "mirror" / "demo"
    (mirror_root / "sections").mkdir(parents=True, exist_ok=True)
    (mirror_root / "sections" / "intro.tex").write_text("intro\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  sections/intro.tex: {_hash_text('intro\n')}",
        ]
    ) + "\n"
    (repo / "papers" / "demo" / "tex" / ".pubs-sync.yaml").write_text(
        manifest,
        encoding="utf-8",
    )
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    called = False

    def fail_merge(paper: object, path: str) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(core_cli, "merge_conflicting_file", fail_merge)

    assert main(["demo", "diff", "sections/intro.tex"]) == 0
    output = capsys.readouterr().out
    assert output.strip() == "unchanged      sections/intro.tex"
    assert called is False


def test_missing_sync_base_file_blocks_only_conflicting_merge(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mirror_root = repo / "mirror" / "demo"
    publication_root = repo / "papers" / "demo" / "tex"
    (publication_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline\n')}",
        ]
    ) + "\n"
    (publication_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    monkeypatch.setattr(core_cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(core_cli.sys.stdin, "isatty", lambda: True)

    assert main(["demo", "diff", "main.tex"]) == 1
    assert "Missing sync-base snapshot for conflicting file:" in capsys.readouterr().err


def test_merge_does_not_update_sync_metadata_or_sync_base(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    (paper.paths.sync_base_root / "main.tex").parent.mkdir(parents=True, exist_ok=True)
    (paper.paths.sync_base_root / "main.tex").write_text("baseline\n", encoding="utf-8")
    manifest = "\n".join(
        [
            "files:",
            f"  main.tex: {_hash_text('baseline\n')}",
        ]
    ) + "\n"
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    (mirror_root / ".pubs-sync.yaml").write_text(manifest, encoding="utf-8")
    before_manifest = (paper.paths.tex_root / ".pubs-sync.yaml").read_text(encoding="utf-8")
    before_base = (paper.paths.sync_base_root / "main.tex").read_text(encoding="utf-8")

    core_mirror.merge_conflicting_file(paper, "main.tex", runner=lambda command: None)

    assert (paper.paths.tex_root / ".pubs-sync.yaml").read_text(encoding="utf-8") == before_manifest
    assert (paper.paths.sync_base_root / "main.tex").read_text(encoding="utf-8") == before_base


def test_diff_remains_available_when_sync_manifests_differ(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    (paper.paths.tex_root / "main.tex").write_text("local changed\n", encoding="utf-8")
    (mirror_root / "main.tex").write_text("mirror changed\n", encoding="utf-8")
    (paper.paths.tex_root / ".pubs-sync.yaml").write_text(
        "\n".join(
            [
                "files:",
                f"  main.tex: {_hash_text('local baseline\n')}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (mirror_root / ".pubs-sync.yaml").write_text(
        "\n".join(
            [
                "files:",
                f"  main.tex: {_hash_text('mirror baseline\n')}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    entries = {entry.path: entry for entry in diff_publication(paper)}

    assert entries["main.tex"].status in {"local-only", "conflicting"}
    assert "local/main.tex" in entries["main.tex"].diff


def test_cli_normalizes_documented_short_form(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["init", "demo"]) == 0
    init_output = capsys.readouterr().out.strip()
    assert init_output.endswith("/papers/demo")
    rc = main(["demo", "check"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "demo: ok"


def test_old_init_syntax_is_rejected() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        main(["demo", "init"])


def test_no_arg_invocation_prints_multiline_help_block(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main([])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage: pubs [--force] [--export] [--export-if-stale] <command>" in captured.err
    assert "Commands:" in captured.err
    assert "  pubs list" in captured.err
    assert "  pubs init <publication-id>" in captured.err
    assert "  pubs <publication-id> shell" in captured.err
    assert "  pubs <publication-id> build [--export|--export-if-stale]" in captured.err
    assert "  pubs <publication-id> preview" in captured.err
    assert "  pubs <publication-id> data [list]" in captured.err
    assert "  pubs <publication-id> data <loader-id> pin" in captured.err
    assert "  pubs <publication-id> diff [list|<relative-path>]" in captured.err
    assert "positional arguments:" not in captured.err
    assert "subject" not in captured.err
    assert "arg2" not in captured.err
    assert (
        "expected 'list', 'init <publication-id>', or '<publication-id> <command>'" in captured.err
    )


def test_check_reports_validation_failure_without_traceback(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mirror_root = repo / "mirror" / "demo"
    mirror_root.rmdir()

    rc = main(["demo", "check"])

    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error: Publication 'demo' failed validation:" in captured.err
    assert "Mirror does not exist:" in captured.err
