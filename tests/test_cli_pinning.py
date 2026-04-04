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

def test_parser_supports_data_pin_surface() -> None:
    parser = build_parser()

    pin_args = parser.parse_args(["demo", "data", "training", "pin"])
    assert pin_args.subject == "demo"
    assert pin_args.arg2 == "data"
    assert pin_args.arg3 == "training"
    assert pin_args.arg4 == "pin"


def test_help_includes_data_pin_command(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = iter(["help", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(core_cli, "_configure_shell_readline", lambda: None)

    assert main(["demo", "shell"]) == 0
    captured = capsys.readouterr()
    assert "  data <loader-id> pin" in captured.out

    with pytest.raises(SystemExit):
        main([])
    help_capture = capsys.readouterr()
    assert "  pubs <publication-id> data <loader-id> pin" in help_capture.err

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
            "def load_bundle(ctx, model_dir, meta_dir):",
            "    return model_dir, meta_dir",
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
