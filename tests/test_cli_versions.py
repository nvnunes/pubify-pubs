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
import pubify_pubs.versioning as core_versioning
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

def test_parser_supports_version_surface() -> None:
    parser = build_parser()

    version_create_args = parser.parse_args(["demo", "version", "create", "draft note"])
    assert version_create_args.arg2 == "version"
    assert version_create_args.arg3 == "create"
    assert version_create_args.arg4 == "draft note"
    version_diff_args = parser.parse_args(["demo", "version", "diff", "v2", "v1"])
    assert version_diff_args.arg2 == "version"
    assert version_diff_args.arg3 == "diff"
    assert version_diff_args.arg4 == "v2"
    assert version_diff_args.arg5 == "v1"


def test_help_includes_version_commands(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = iter(["help", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert "  version [list|create [note]|diff <version-id> [<version-id>]]" in captured.out

    with pytest.raises(SystemExit):
        main([])
    help_capture = capsys.readouterr()
    assert "  pubs <publication-id> version [list|create [note]|diff <version-id> [<version-id>]]" in help_capture.err

def test_cli_version_create_and_list_snapshot_non_build_tex_tree(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    paper.paths.autofigures_root.mkdir(parents=True, exist_ok=True)
    (paper.paths.autofigures_root / "plot.pdf").write_text("figure\n", encoding="utf-8")
    paper.paths.autostats_path.write_text(r"\newcommand{\StatDemo}{1}" + "\n", encoding="utf-8")
    paper.paths.autotables_path.write_text(r"\newcommand{\TableDemo}{A & B \\}" + "\n", encoding="utf-8")
    (paper.paths.tex_root / "refs.bib").write_text("@article{demo}\n", encoding="utf-8")
    (paper.paths.build_root / "main.pdf").parent.mkdir(parents=True, exist_ok=True)
    (paper.paths.build_root / "main.pdf").write_text("pdf\n", encoding="utf-8")
    (paper.paths.tex_root / "main.aux").write_text("aux\n", encoding="utf-8")

    assert main(["demo", "version", "create", "first draft"]) == 0

    captured = capsys.readouterr()
    created_line = captured.out.strip()
    assert created_line.startswith("v1  ")
    assert "T" not in created_line
    assert "first draft" in created_line

    snapshot_root = paper.paths.versions_root / "v1"
    assert snapshot_root.exists()
    assert (snapshot_root / "main.tex").exists()
    assert (snapshot_root / "pubify.sty").exists()
    assert (snapshot_root / "refs.bib").exists()
    assert (snapshot_root / "autofigures" / "plot.pdf").exists()
    assert (snapshot_root / "autostats.tex").exists()
    assert (snapshot_root / "autotables.tex").exists()
    assert not (snapshot_root / "build").exists()
    assert not (snapshot_root / "main.aux").exists()

    versions = core_versioning.list_publication_versions(paper)
    assert [(version.version_id, version.note, version.main_tex.as_posix()) for version in versions] == [
        ("v1", "first draft", "main.tex")
    ]

    assert main(["demo", "version", "list"]) == 0
    listed_line = capsys.readouterr().out.strip()
    assert listed_line.startswith("v1  ")
    assert "T" not in listed_line
    assert "first draft" in listed_line

def test_cli_version_create_undo_removes_newest_identical_snapshot(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)

    assert main(["demo", "version", "create", "baseline"]) == 0
    capsys.readouterr()

    assert main(["demo", "version", "create", "undo"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "v1: removed"
    assert not paper.paths.versions_root.exists()

def test_cli_version_create_undo_rejects_changed_current_tex_state(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)

    assert main(["demo", "version", "create", "baseline"]) == 0
    capsys.readouterr()
    (paper.paths.tex_root / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nchanged\n\\end{document}\n",
        encoding="utf-8",
    )

    assert main(["demo", "version", "create", "undo"]) == 1
    captured = capsys.readouterr()
    assert "newest version 'v1' differs from current tex/ state" in captured.err
    assert (paper.paths.versions_root / "v1").exists()

def test_version_diff_compares_snapshot_to_current_and_copies_pdf(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    (paper.paths.tex_root / "sections" / "intro.tex").write_text("old intro\n", encoding="utf-8")
    (paper.paths.tex_root / "main.tex").write_text(
        "\n".join(
            [
                r"\documentclass{article}",
                r"\begin{document}",
                r"\input{sections/intro.tex}",
                r"\end{document}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    core_versioning.create_publication_version(paper, note="baseline")
    (paper.paths.tex_root / "sections" / "intro.tex").write_text("new intro\n", encoding="utf-8")

    calls: list[tuple[list[str], Path]] = []

    def runner(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append((list(command), cwd))
        if command[0] == "latexdiff":
            assert command[1].endswith("/versions/v1/main.tex")
            assert command[2].endswith("/tex/main.tex")
            return subprocess.CompletedProcess(list(command), 0, stdout="DIFF TEX\n", stderr="")
        if command[0] == "latexmk":
            assert (cwd / "sections" / "intro.tex").read_text(encoding="utf-8") == "new intro\n"
            outdir = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("-outdir=")))
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "main.pdf").write_text("diff pdf\n", encoding="utf-8")
            return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected command: {command}")

    pdf_path = core_versioning.build_publication_version_diff(paper, "v1", runner=runner)

    assert pdf_path == paper.paths.build_root / "main-diff-v1-current.pdf"
    assert pdf_path.read_text(encoding="utf-8") == "diff pdf\n"
    assert [command[0] for command, _ in calls] == ["latexdiff", "latexmk"]

def test_version_diff_normalizes_order_between_two_versions(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    (paper.paths.tex_root / "sections" / "intro.tex").write_text("first\n", encoding="utf-8")
    core_versioning.create_publication_version(paper, note="first")
    (paper.paths.tex_root / "sections" / "intro.tex").write_text("second\n", encoding="utf-8")
    core_versioning.create_publication_version(paper, note="second")

    latexdiff_commands: list[list[str]] = []

    def runner(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[0] == "latexdiff":
            latexdiff_commands.append(list(command))
            return subprocess.CompletedProcess(list(command), 0, stdout="DIFF TEX\n", stderr="")
        if command[0] == "latexmk":
            outdir = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("-outdir=")))
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "main.pdf").write_text("diff pdf\n", encoding="utf-8")
            return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected command: {command}")

    pdf_path = core_versioning.build_publication_version_diff(paper, "v2", "v1", runner=runner)

    assert pdf_path == paper.paths.build_root / "main-diff-v1-v2.pdf"
    assert latexdiff_commands[0][1].endswith("/versions/v1/main.tex")
    assert latexdiff_commands[0][2].endswith("/versions/v2/main.tex")

def test_version_diff_sanitizes_latexdiff_wrappers_in_optional_args(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    core_versioning.create_publication_version(paper, note="baseline")

    def runner(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[0] == "latexdiff":
            return subprocess.CompletedProcess(
                list(command),
                0,
                stdout="\\figfloat\n    {\\figone{autofigures/example}}\n    [\\DIFadd{Example caption.}]\n    [\\DIFadd{fig:example}]\n",
                stderr="",
            )
        if command[0] == "latexmk":
            diff_text = (cwd / "main.tex").read_text(encoding="utf-8")
            assert "[\\DIFadd{Example caption.}]" not in diff_text
            assert "[\\DIFadd{fig:example}]" not in diff_text
            assert "[Example caption.]" in diff_text
            assert "[fig:example]" in diff_text
            outdir = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("-outdir=")))
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "main.pdf").write_text("diff pdf\n", encoding="utf-8")
            return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected command: {command}")

    pdf_path = core_versioning.build_publication_version_diff(paper, "v1", runner=runner)
    assert pdf_path == paper.paths.build_root / "main-diff-v1-current.pdf"

def test_restore_figfloat_blocks_prefers_newer_exact_block_text() -> None:
    diff_text = (
        "before\n"
        "\\figfloat[h!]\n"
        "    {\n"
        "        \\figfour\n"
        "        {\\DIFadd{autofigures/aoff_star_maps_1}}\n"
        "        {\\DIFadd{autofigures/aoff_star_maps_2}}\n"
        "    }\n"
        "    [Example caption.]\n"
        "    [fig:aoff_star_maps]\n"
        "after\n"
    )
    newer_text = (
        "before\n"
        "\\figfloat[h!]\n"
        "    {\n"
        "        \\figfour\n"
        "        {autofigures/aoff_asterism_maps_1}\n"
        "        {autofigures/aoff_asterism_maps_2}\n"
        "    }\n"
        "    [Example caption.]\n"
        "    [fig:aoff_asterism_maps]\n"
        "after\n"
    )

    restored = core_versioning._restore_figfloat_blocks(diff_text, newer_text)

    assert "aoff_star_maps" not in restored
    assert "autofigures/aoff_asterism_maps_1" in restored
    assert "[fig:aoff_asterism_maps]" in restored

def test_version_diff_copies_failure_log_to_stable_build_path(
    repo: Path,
    fake_pubify_mpl: FakePubifyBackend,
) -> None:
    paper = load_publication_definition(repo, "demo")
    init_publication(paper)
    core_versioning.create_publication_version(paper, note="baseline")

    def runner(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if command[0] == "latexdiff":
            return subprocess.CompletedProcess(list(command), 0, stdout="DIFF TEX\n", stderr="")
        if command[0] == "latexmk":
            outdir = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("-outdir=")))
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "main.log").write_text("! Missing endcsname inserted.\n", encoding="utf-8")
            raise subprocess.CalledProcessError(12, list(command), output="", stderr="")
        raise AssertionError(f"Unexpected command: {command}")

    with pytest.raises(ValueError) as exc_info:
        core_versioning.build_publication_version_diff(paper, "v1", runner=runner)

    message = str(exc_info.value)
    stable_log_path = paper.paths.build_root / "main-diff-v1-current.log"
    assert f"Log file: {stable_log_path}" in message
    assert stable_log_path.read_text(encoding="utf-8") == "! Missing endcsname inserted.\n"
