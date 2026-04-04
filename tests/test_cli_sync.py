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
import pubify_pubs.commands.sync as commands_sync
import pubify_pubs.export as core_export
import pubify_pubs.mirror as core_mirror
import pubify_pubs.pinning as core_pinning
import pubify_pubs.runtime as core_runtime
import pubify_pubs.shell_incremental as core_shell_incremental
from pubify_pubs.data import load_publication_data_npz, publication_data_path, save_publication_data_npz
from pubify_pubs import TableResult
from pubify_pubs.decorators import data, external_data, figure, stat, table
from pubify_pubs.discovery import find_workspace_root, list_publication_ids, load_publication_definition
from pubify_pubs.mirror import diff_publication, pull_publication, push_publication
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

def test_parser_supports_sync_surface() -> None:
    parser = build_parser()

    ignore_args = parser.parse_args(["demo", "ignore", "sections/intro.tex"])
    assert ignore_args.subject == "demo"
    assert ignore_args.arg2 == "ignore"
    assert ignore_args.arg3 == "sections/intro.tex"


def test_help_includes_sync_commands(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = iter(["help", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert "  ignore <relative-path>" in captured.out
    assert "  push [--force]" in captured.out
    assert "  pull [--force]" in captured.out
    assert "  diff [list|<relative-path>]" in captured.out

    with pytest.raises(SystemExit):
        main([])
    help_capture = capsys.readouterr()
    assert "  pubs <publication-id> diff [list|<relative-path>]" in help_capture.err
    assert "  pubs <publication-id> push [--force]" in help_capture.err
    assert "  pubs <publication-id> pull [--force]" in help_capture.err
    assert "  pubs <publication-id> ignore <relative-path>" in help_capture.err

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
    paper.paths.autostats_path.write_text(r"\newcommand{\StatTrainingSummary}{training}" + "\n", encoding="utf-8")
    paper.paths.autotables_path.write_text(r"\newcommand{\TableSummary}{Count & 3 \\}" + "\n", encoding="utf-8")
    push_publication(paper)
    mirror_root = paper.config.mirror_root_path
    assert mirror_root is not None
    assert (mirror_root / "main.tex").read_text(encoding="utf-8") == "working tree main\n"
    assert (mirror_root / "sections" / "intro.tex").read_text(encoding="utf-8") == "intro\n"
    assert (mirror_root / "autofigures" / "plot.pdf").read_text(encoding="utf-8") == "figure data\n"
    assert (mirror_root / "autostats.tex").read_text(encoding="utf-8") == (
        r"\newcommand{\StatTrainingSummary}{training}" + "\n"
    )
    assert (mirror_root / "autotables.tex").read_text(encoding="utf-8") == (
        r"\newcommand{\TableSummary}{Count & 3 \\}" + "\n"
    )
    assert not (mirror_root / "build" / "ignored.aux").exists()
    assert not (mirror_root / "drafts" / "note.txt").exists()
    sync_text = (mirror_root / ".pubs-sync.yaml").read_text(encoding="utf-8")
    assert f"main.tex: {_hash_text('working tree main\n')}" in sync_text
    assert f"sections/intro.tex: {_hash_text('intro\n')}" in sync_text
    assert "autofigures/plot.pdf" not in sync_text
    assert "autostats.tex" not in sync_text
    assert "autotables.tex" not in sync_text
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
    monkeypatch.setattr(commands_sync, "merge_conflicting_file", lambda paper, path: launched.append(path))
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
    monkeypatch.setattr(commands_sync, "merge_conflicting_file", lambda paper, path: None)

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
    monkeypatch.setattr(commands_sync, "merge_conflicting_file", lambda paper, path: None)

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

def test_diff_rejects_figure_paths_as_outside_managed_set(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    paper.paths.autofigures_root.mkdir(parents=True, exist_ok=True)
    (paper.paths.autofigures_root / "foo.pdf").write_text("figure\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Managed tex path not found: autofigures/foo.pdf"):
        diff_publication(paper, "autofigures/foo.pdf")

def test_diff_rejects_autostats_as_outside_managed_set(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    paper.paths.autostats_path.write_text(r"\newcommand{\StatTrainingSummary}{training}" + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Managed tex path not found: autostats.tex"):
        diff_publication(paper, "autostats.tex")

def test_diff_rejects_autotables_as_outside_managed_set(repo: Path) -> None:
    paper = load_publication_definition(repo, "demo")
    paper.paths.autotables_path.write_text(r"\newcommand{\TableSummary}{Count & 3 \\}" + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Managed tex path not found: autotables.tex"):
        diff_publication(paper, "autotables.tex")

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

    monkeypatch.setattr(commands_sync, "merge_conflicting_file", fail_merge)

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
