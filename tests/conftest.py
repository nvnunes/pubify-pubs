from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SIBLING_PUBIFY_DATA = ROOT.parent / "pubify-data" / "src"
SIBLING_PUBIFY_TEX = ROOT.parent / "pubify-tex" / "src"
SIBLING_PUBIFY_MPL = ROOT.parent / "pubify-mpl" / "src"

for path in (SRC, SIBLING_PUBIFY_DATA, SIBLING_PUBIFY_TEX, SIBLING_PUBIFY_MPL):
    if path.exists() and str(path) not in sys.path:
        sys.path.insert(0, str(path))

import pubify_pubs.export as core_export
import pubify_pubs.runtime as core_runtime


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
        self.auto_history_values: list[bool] = []
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

    def set_auto_history(self, enabled: bool) -> None:
        self.auto_history_values.append(enabled)


@pytest.fixture(autouse=True)
def fake_pubify(monkeypatch: pytest.MonkeyPatch) -> FakePubifyBackend:
    backend = FakePubifyBackend()
    monkeypatch.setattr(core_export, "pubify_tex", backend)
    monkeypatch.setattr(core_runtime, "pubify_tex", backend)
    return backend


@pytest.fixture()
def fake_pubify_mpl(fake_pubify: FakePubifyBackend) -> FakePubifyBackend:
    return fake_pubify


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (repo_root / "pubify.yaml").write_text(
        "pubify-pubs:\n  publications_root: papers\n",
        encoding="utf-8",
    )
    (repo_root / "mirror" / "demo").mkdir(parents=True)
    (repo_root / "papers" / "demo" / "tex" / "sections").mkdir(parents=True)
    (repo_root / "papers" / "demo" / "data" / "bundle").mkdir(parents=True)
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
                "from pubify_data import data, figure, stat",
                "",
                "CALLS = {'training': 0, 'bundle': 0}",
                "",
                "@data('training.npy')",
                "def load_training(ctx, path):",
                "    CALLS['training'] += 1",
                "    return path.read_text(encoding='utf-8')",
                "",
                "@data(model='bundle/model.txt', meta='bundle/meta.txt')",
                "def load_bundle(ctx, model, meta):",
                "    CALLS['bundle'] += 1",
                "    return '|'.join(path.read_text(encoding='utf-8') for path in (meta, model))",
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
                "",
                "@stat",
                "def compute_training_summary(ctx, training, bundle):",
                "    return {'Value': training, 'Bundle': rf'\\texttt{{{bundle}}}'}",
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
    (repo_root / "papers" / "demo" / "data" / "training.npy").write_text("training", encoding="utf-8")
    (repo_root / "papers" / "demo" / "data" / "bundle" / "model.txt").write_text("model", encoding="utf-8")
    (repo_root / "papers" / "demo" / "data" / "bundle" / "meta.txt").write_text("meta", encoding="utf-8")
    monkeypatch.chdir(repo_root)
    return repo_root


def _hash_text(value: str) -> str:
    import hashlib

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
    (publication_root / "data").mkdir(parents=True, exist_ok=True)
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


def _write_table_paper(
    repo: Path,
    *,
    publication_id: str,
    figures_lines: list[str],
    main_tex_lines: list[str],
) -> Path:
    publication_root = repo / "papers" / publication_id
    (publication_root / "tex").mkdir(parents=True, exist_ok=True)
    (publication_root / "data").mkdir(parents=True, exist_ok=True)
    (publication_root / "pub.yaml").write_text(
        "\n".join(
            [
                'mirror_root: ""',
                "main_tex: main.tex",
                "pubify-mpl-template:",
                "  textwidth_in: 6.75",
                "  textheight_in: 9.7",
                "  base_fontsize_pt: 10",
                "pubify-mpl-defaults:",
                "  layout: one",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (publication_root / "figures.py").write_text("\n".join(figures_lines) + "\n", encoding="utf-8")
    (publication_root / "tex" / "main.tex").write_text("\n".join(main_tex_lines) + "\n", encoding="utf-8")
    return publication_root
