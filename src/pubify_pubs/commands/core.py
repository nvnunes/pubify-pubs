from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import re
import subprocess
import sys

from pubify_pubs.config import load_workspace_config
from pubify_pubs.discovery import PublicationDefinition
from pubify_pubs.export import close_figure_export_sources
from pubify_pubs.latex_bootstrap import (
    build_figure_latex_spec,
    render_figure_latex,
    render_stat_latex,
    render_table_latex,
)
from pubify_pubs.runtime import (
    RunContext,
    UserCodeExecutionError,
    build_pdf_path,
    build_publication,
    build_run_context,
    check_publication,
    clear_autofigures,
    clear_publication_build,
    init_publication,
    inspect_figure,
    preload_loaders,
    resolve_loader,
    run_figures,
    run_stats,
    run_tables,
    write_computed_stats,
    write_computed_tables,
)
from pubify_pubs.shell_incremental import figure_output_belongs_to_id
from pubify_pubs.stats import ComputedStat
from pubify_pubs.stubs import (
    add_stub_to_figures_module,
    generated_stub_function_name,
    module_function_names,
    validate_stub_id,
)
from pubify_pubs.tables import ComputedTable

from pubify_pubs.commands.common import (
    DataInventoryRow,
    FigureInventoryRow,
    LiveSectionPrinter,
    PublicationCommand,
    PublicationShellSession,
    ReportedExecutionError,
    StatInventoryRow,
    TableInventoryRow,
    render_detail_line,
    render_execution_status_line,
    render_section_heading,
    reject_build_flags_from_command,
)


def handle_command(
    publication: PublicationDefinition,
    command: PublicationCommand,
    *,
    error: Callable[[str], None],
    use_color: bool,
    loader_cache: dict[str, object] | None = None,
    pending_data_output: dict[str, list[str]] | None = None,
    shell_session: PublicationShellSession | None = None,
) -> int | None:
    if command.command == "update":
        reject_build_flags_from_command(command, error)
        if command.force:
            error("update does not accept --force")
        if command.arg3 is not None or command.arg4 is not None or command.arg5 is not None:
            error("update does not accept additional arguments")
        run_full_refresh(
            publication,
            use_color=use_color,
            loader_cache=loader_cache,
            pending_data_output=pending_data_output,
            shell_session=shell_session,
            refresh_support=True,
        )
        return 0

    if command.command == "data":
        reject_build_flags_from_command(command, error)
        if command.force:
            error("data does not accept --force")
        if command.arg3 in {None, "list"}:
            if command.arg4 is not None or command.arg5 is not None:
                error("data list does not accept additional arguments")
            rows = build_data_inventory_rows(publication)
            if not rows:
                print(f"{publication.publication_id}: no declared data")
                return 0
            loader_width = max(len(row.loader_id) for row in rows)
            for row in rows:
                print(render_data_inventory_line(row, loader_width=loader_width, use_color=use_color))
            return 0
        if command.arg3 == "add":
            if command.arg4 is None:
                error("data add requires <data-id>")
            if command.arg5 is not None:
                error("data add accepts only <data-id>")
            add_publication_stub(publication, kind="data", stub_id=command.arg4)
            print_added_stub("Data", command.arg4, use_color=use_color)
            return 0
        return None

    if command.command == "figure":
        reject_build_flags_from_command(command, error)
        if command.force:
            error("figure does not accept --force")
        if command.arg3 in {None, "list"}:
            if command.arg4 is not None or command.arg5 is not None:
                error("figure list does not accept additional arguments")
            rows = build_figure_inventory_rows(publication)
            if not rows:
                print(f"{publication.publication_id}: no figures")
                return 0
            figure_id_width = max(len(row.figure_id) for row in rows)
            for row in rows:
                print(render_figure_inventory_line(row, figure_id_width=figure_id_width, use_color=use_color))
            return 0
        if command.arg3 == "add":
            if command.arg4 is None:
                error("figure add requires <figure-id>")
            if command.arg5 is not None:
                error("figure add accepts only <figure-id>")
            add_publication_stub(publication, kind="figure", stub_id=command.arg4)
            print_added_stub("Figures", command.arg4, use_color=use_color)
            return 0
        if command.arg3 == "update":
            if command.arg4 is not None or command.arg5 is not None:
                error("figure update does not accept additional arguments")
            ctx = command_run_context(publication, loader_cache=loader_cache, pending_data_output=pending_data_output)
            run_data_updates(ctx, figure_loader_ids(publication), use_color=use_color, include_nocache=True)
            run_figure_updates(publication, ctx, selected_figure_ids(publication), use_color=use_color, clear_existing=True)
            return 0
        if command.arg4 == "update":
            if command.arg5 is not None:
                error("figure <figure-id> update does not accept additional arguments")
            ctx = command_run_context(publication, loader_cache=loader_cache, pending_data_output=pending_data_output)
            run_data_updates(ctx, figure_loader_ids(publication, command.arg3), use_color=use_color, include_nocache=True)
            run_figure_updates(
                publication,
                ctx,
                selected_figure_ids(publication, command.arg3),
                use_color=use_color,
                clear_existing=True,
            )
            return 0
        if is_latex_alias(command.arg4):
            selected_id = command.arg3
            if selected_id not in publication.figures:
                raise KeyError(f"Unknown figure '{selected_id}'")
            if command.arg5 not in {None, "subcaption"}:
                error("figure <figure-id> latex accepts only optional 'subcaption'")
            ctx = command_run_context(publication, loader_cache=loader_cache)
            export = inspect_figure(publication, selected_id, ctx=ctx)
            try:
                snippet = render_figure_latex(
                    build_figure_latex_spec(selected_id, export),
                    subcaption=command.arg5 == "subcaption",
                )
            finally:
                close_figure_export_sources(export)
            print_emitted_latex(with_main_tex_prelude(publication, "figure", snippet))
            return 0
        if command.arg4 != "preview":
            error(
                "figure supports only 'list', 'add <figure-id>', 'update', '<figure-id> update', "
                "'<figure-id> preview [<subfig-idx>]', or '<figure-id> latex [subcaption]'"
            )
        subfigure_index = None if command.arg5 is None else parse_subfig_idx_value(command.arg5, error)
        preview_paths = preview_figure_paths(publication, command.arg3, subfigure_index=subfigure_index)
        workspace = load_workspace_config(publication.paths.workspace_root)
        open_publication_previews(preview_paths, backend=workspace.preview.figure)
        for path in preview_paths:
            print(path.relative_to(publication.paths.publication_root))
        return 0

    if command.command == "stat":
        reject_build_flags_from_command(command, error)
        if command.force:
            error("stat does not accept --force")
        if command.arg3 in {None, "list"}:
            if command.arg4 is not None or command.arg5 is not None:
                error("stat list does not accept additional arguments")
            rows = build_stat_inventory_rows(publication)
            if not rows:
                print(f"{publication.publication_id}: no stats")
                return 0
            for row in rows:
                print(row.stat_id)
            return 0
        if command.arg3 == "add":
            if command.arg4 is None:
                error("stat add requires <stat-id>")
            if command.arg5 is not None:
                error("stat add accepts only <stat-id>")
            add_publication_stub(publication, kind="stat", stub_id=command.arg4)
            print_added_stub("Stats", command.arg4, use_color=use_color)
            return 0
        if command.arg3 == "update":
            if command.arg4 is not None or command.arg5 is not None:
                error("stat update does not accept additional arguments")
            ctx = command_run_context(publication, loader_cache=loader_cache, pending_data_output=pending_data_output)
            run_data_updates(ctx, stat_loader_ids(publication), use_color=use_color, include_nocache=True)
            run_stat_updates(publication, ctx, tuple(sorted(publication.stats)), use_color=use_color)
            return 0
        if is_latex_alias(command.arg4):
            if command.arg5 is not None:
                error("stat <stat-id> latex does not accept additional arguments")
            selected_id = command.arg3
            if selected_id not in publication.stats:
                raise KeyError(f"Unknown stat '{selected_id}'")
            ctx = command_run_context(publication, loader_cache=loader_cache)
            print_emitted_latex(
                with_main_tex_prelude(
                    publication,
                    "stat",
                    render_stat_latex(run_stats(publication, selected_id, ctx=ctx)[0]),
                )
            )
            return 0
        if command.arg4 != "update" or command.arg5 is not None:
            error("stat supports only 'list', 'add <stat-id>', 'update', '<stat-id> update', or '<stat-id> latex'")
        selected_id = command.arg3
        if selected_id not in publication.stats:
            raise KeyError(f"Unknown stat '{selected_id}'")
        ctx = command_run_context(publication, loader_cache=loader_cache, pending_data_output=pending_data_output)
        run_data_updates(ctx, stat_loader_ids(publication), use_color=use_color, include_nocache=True)
        run_stat_updates(publication, ctx, (selected_id,), use_color=use_color)
        return 0

    if command.command == "table":
        reject_build_flags_from_command(command, error)
        if command.force:
            error("table does not accept --force")
        if command.arg3 in {None, "list"}:
            if command.arg4 is not None or command.arg5 is not None:
                error("table list does not accept additional arguments")
            rows = build_table_inventory_rows(publication)
            if not rows:
                print(f"{publication.publication_id}: no tables")
                return 0
            for row in rows:
                print(row.table_id)
            return 0
        if command.arg3 == "add":
            if command.arg4 is None:
                error("table add requires <table-id>")
            if command.arg5 is not None:
                error("table add accepts only <table-id>")
            add_publication_stub(publication, kind="table", stub_id=command.arg4)
            print_added_stub("Tables", command.arg4, use_color=use_color)
            return 0
        if command.arg3 == "update":
            if command.arg4 is not None or command.arg5 is not None:
                error("table update does not accept additional arguments")
            ctx = command_run_context(publication, loader_cache=loader_cache, pending_data_output=pending_data_output)
            run_data_updates(ctx, table_loader_ids(publication), use_color=use_color, include_nocache=True)
            run_table_updates(publication, ctx, tuple(sorted(publication.tables)), use_color=use_color)
            return 0
        if is_latex_alias(command.arg4):
            if command.arg5 is not None:
                error("table <table-id> latex does not accept additional arguments")
            selected_id = command.arg3
            if selected_id not in publication.tables:
                raise KeyError(f"Unknown table '{selected_id}'")
            ctx = command_run_context(publication, loader_cache=loader_cache)
            print_emitted_latex(
                with_main_tex_prelude(
                    publication,
                    "table",
                    render_table_latex(run_tables(publication, selected_id, ctx=ctx)[0]),
                )
            )
            return 0
        if command.arg4 == "update":
            if command.arg5 is not None:
                error("table <table-id> update does not accept additional arguments")
            selected_id = command.arg3
            if selected_id not in publication.tables:
                raise KeyError(f"Unknown table '{selected_id}'")
            ctx = command_run_context(publication, loader_cache=loader_cache, pending_data_output=pending_data_output)
            run_data_updates(ctx, table_loader_ids(publication, selected_id), use_color=use_color, include_nocache=True)
            run_table_updates(publication, ctx, (selected_id,), use_color=use_color)
            return 0
        error("table supports only 'list', 'add <table-id>', 'update', '<table-id> update', or '<table-id> latex'")

    if command.command == "build":
        if command.force:
            error("build does not accept --force")
        if command.arg3 is not None or command.arg4 is not None or command.arg5 is not None:
            error("build does not accept additional arguments")
        if command.clear_build:
            clear_publication_build(publication)
        refresh_and_validate_publication(publication, use_color=use_color)
        build_publication(publication)
        print_updated_pdf(build_pdf_path(publication), use_color=use_color)
        return 0

    if command.command == "preview":
        reject_build_flags_from_command(command, error)
        if command.force:
            error("preview does not accept --force")
        if command.arg3 is not None or command.arg4 is not None or command.arg5 is not None:
            error("preview does not accept additional arguments")
        pdf_path = build_pdf_path(publication)
        if not pdf_path.exists():
            raise FileNotFoundError(
                f"Built publication PDF does not exist: {pdf_path.resolve()}. "
                f"Run `pubs {publication.publication_id} build` first."
            )
        workspace = load_workspace_config(publication.paths.workspace_root)
        open_publication_previews([pdf_path], backend=workspace.preview.publication)
        print(pdf_path)
        return 0

    return None


def add_publication_stub(publication: PublicationDefinition, *, kind: str, stub_id: str) -> None:
    validate_stub_id(stub_id)
    if kind == "data" and stub_id in publication.loaders:
        raise ValueError(f"Loader '{stub_id}' already exists")
    if kind == "figure" and stub_id in publication.figures:
        raise ValueError(f"Figure '{stub_id}' already exists")
    if kind == "stat" and stub_id in publication.stats:
        raise ValueError(f"Stat '{stub_id}' already exists")
    if kind == "table" and stub_id in publication.tables:
        raise ValueError(f"Table '{stub_id}' already exists")
    function_name = generated_stub_function_name(kind, stub_id)
    if function_name in module_function_names(publication.paths.entrypoint):
        raise ValueError(f"Function '{function_name}' already exists in {publication.paths.entrypoint}")
    add_stub_to_figures_module(publication.paths.entrypoint, kind=kind, stub_id=stub_id)


def is_add_stub_command(command: PublicationCommand) -> bool:
    return command.command in {"data", "figure", "stat", "table"} and command.arg3 == "add"


def is_latex_alias(value: str | None) -> bool:
    return value in {"latex", "tex"}


def parse_subfig_idx_value(value: str, error: Callable[[str], None]) -> int:
    try:
        return int(value)
    except ValueError as exc:
        error(f"invalid <subfig-idx> '{value}'")
        raise exc


def build_stat_inventory_rows(publication: PublicationDefinition) -> list[StatInventoryRow]:
    return [StatInventoryRow(stat_id=stat_id) for stat_id in sorted(publication.stats)]


def build_table_inventory_rows(publication: PublicationDefinition) -> list[TableInventoryRow]:
    return [TableInventoryRow(table_id=table_id) for table_id in sorted(publication.tables)]


def build_data_inventory_rows(publication: PublicationDefinition) -> list[DataInventoryRow]:
    rows: list[DataInventoryRow] = []
    for loader in publication.loaders.values():
        for relative_path in loader.relative_paths.values():
            if loader.kind == "data":
                row = DataInventoryRow(status="pinned", loader_id=loader.loader_id, path=relative_path)
            elif loader.kind == "external_data":
                row = DataInventoryRow(
                    status="external",
                    loader_id=loader.loader_id,
                    path=render_external_inventory_path(loader.root_name, relative_path),
                )
            else:
                continue
            rows.append(row)
    return rows


def build_figure_inventory_rows(publication: PublicationDefinition) -> list[FigureInventoryRow]:
    return [
        FigureInventoryRow(
            status="figure",
            figure_id=figure.figure_id,
            dependencies=", ".join(figure.dependency_ids),
        )
        for figure in publication.figures.values()
    ]


def render_external_inventory_path(root_name: str | None, relative_path: str) -> str:
    if root_name is None:
        return relative_path
    return f"{root_name}:{relative_path}"


def render_data_inventory_line(row: DataInventoryRow, *, loader_width: int, use_color: bool) -> str:
    from pubify_pubs.commands.common import render_status_token_variant

    status = render_status_token_variant(row.status, use_color=use_color, padded=False)
    return f"{status}   {row.loader_id:<{loader_width}}   {row.path}"


def render_figure_inventory_line(row: FigureInventoryRow, *, figure_id_width: int, use_color: bool) -> str:
    from pubify_pubs.commands.common import render_status_token_variant

    status = render_status_token_variant(row.status, use_color=use_color, padded=False)
    return f"{status}   {row.figure_id:<{figure_id_width}}   {row.dependencies}"


def run_full_refresh(
    publication: PublicationDefinition,
    *,
    use_color: bool,
    loader_cache: dict[str, object] | None,
    pending_data_output: dict[str, list[str]] | None,
    shell_session: PublicationShellSession | None,
    force_loader_reload: bool = True,
    refresh_support: bool,
) -> None:
    changed_paths = refresh_publication_support(publication) if refresh_support else ()
    check_publication(publication)
    ctx = command_run_context(
        publication,
        loader_cache={} if shell_session is not None and force_loader_reload else loader_cache,
        pending_data_output=pending_data_output,
    )
    run_data_updates(
        ctx,
        build_refresh_loader_ids(publication),
        use_color=use_color,
        include_nocache=True,
        show_all=force_loader_reload,
    )
    print_updated_publication_files(publication, changed_paths, use_color=use_color)
    figure_output_names: dict[str, tuple[str, ...]] = {}
    stats_cache: dict[str, ComputedStat] = {}
    tables_cache: dict[str, ComputedTable] = {}
    if publication.figures:
        figure_output_names = run_figure_updates(
            publication,
            ctx,
            selected_figure_ids(publication),
            use_color=use_color,
            clear_existing=True,
        )
    if publication.stats:
        stats_cache = run_stat_updates(publication, ctx, tuple(sorted(publication.stats)), use_color=use_color)
    if publication.tables:
        tables_cache = run_table_updates(publication, ctx, tuple(sorted(publication.tables)), use_color=use_color)
    if shell_session is not None:
        shell_session.loader_cache = {key: value for key, value in ctx.loader_cache.items() if key in publication.loaders}
        shell_session.pending_data_output = ctx.captured_data_output
        shell_session.cached_figure_output_names = figure_output_names
        shell_session.cached_stats = stats_cache
        shell_session.cached_tables = tables_cache
        shell_session.last_success_method_state = shell_session.method_state


def refresh_publication_support(publication: PublicationDefinition) -> tuple[Path, ...]:
    before_contents: dict[Path, bytes | None] = {}
    support_paths = (
        publication.paths.tex_root / "pubify.sty",
        publication.paths.tex_root / "pubify-template.tex",
    )
    for path in support_paths:
        before_contents[path] = path.read_bytes() if path.exists() else None
    changed_paths: list[Path] = []
    for path in init_publication(publication):
        if path_content_changed(path, before_contents.get(path)):
            changed_paths.append(path)
    return tuple(changed_paths)


def refresh_and_validate_publication(publication: PublicationDefinition, *, use_color: bool) -> None:
    changed_paths = refresh_publication_support(publication)
    check_publication(publication)
    print_updated_publication_files(publication, changed_paths, use_color=use_color)


def path_content_changed(path: Path, previous_content: bytes | None) -> bool:
    current_content = path.read_bytes() if path.exists() else None
    return previous_content != current_content


def print_updated_publication_files(
    publication: PublicationDefinition,
    changed_paths: tuple[Path, ...],
    *,
    use_color: bool,
) -> None:
    if not changed_paths:
        return
    print(render_section_heading("Publication Files", use_color=use_color))
    for path in changed_paths:
        try:
            display = path.relative_to(publication.paths.publication_root)
        except ValueError:
            display = path
        print(render_execution_status_line(str(display), "updated", use_color=use_color, state="success"))
    print()


def print_added_stub(section: str, stub_id: str, *, use_color: bool) -> None:
    print(render_section_heading(section, use_color=use_color))
    print(render_execution_status_line(stub_id, "added", use_color=use_color, state="success"))
    print()


def print_emitted_latex(snippet: str) -> None:
    print()
    print(snippet)
    print()


def with_main_tex_prelude(publication: PublicationDefinition, kind: str, snippet: str) -> str:
    prelude_lines = missing_latex_prelude_lines(publication, kind)
    if not prelude_lines:
        return snippet
    return "\n".join([*prelude_lines, snippet])


def missing_latex_prelude_lines(publication: PublicationDefinition, kind: str) -> list[str]:
    main_tex_text = read_main_tex_text(publication)
    missing: list[str] = []
    if kind == "figure" and not main_tex_has_pubify_package(main_tex_text):
        missing.append(r"\usepackage{pubify}")
    if kind == "stat" and r"\input{autostats.tex}" not in main_tex_text:
        missing.append(r"\input{autostats.tex}")
    if kind == "table" and r"\input{autotables.tex}" not in main_tex_text:
        missing.append(r"\input{autotables.tex}")
    return missing


def read_main_tex_text(publication: PublicationDefinition) -> str:
    return (publication.paths.tex_root / publication.config.main_tex_path).read_text(encoding="utf-8")


def main_tex_has_pubify_package(main_tex_text: str) -> bool:
    return re.search(r"\\usepackage(?:\[[^\]]*\])?\{pubify\}", main_tex_text) is not None


def run_figure_updates(
    publication: PublicationDefinition,
    ctx: RunContext,
    figure_ids: tuple[str, ...],
    *,
    use_color: bool,
    clear_existing: bool,
) -> dict[str, tuple[str, ...]]:
    if not figure_ids:
        return {}
    if clear_existing:
        clear_autofigures(publication)
    else:
        for figure_id in figure_ids:
            clear_selected_figure_outputs(publication, figure_id)
    printer = LiveSectionPrinter("Figures", use_color=use_color)
    figure_output_names: dict[str, tuple[str, ...]] = {}
    try:
        for figure_id in figure_ids:
            printer.start_item(figure_id, "updating")
            try:
                output_paths = run_figures(publication, figure_id, ctx=ctx)
            except UserCodeExecutionError as exc:
                printer.fail(figure_id, detail_lines=list(exc.lines))
                raise ReportedExecutionError() from exc
            detail_lines = consume_dynamic_output(ctx, "figure")
            label = figure_id
            count = count_figure_outputs(figure_id, output_paths)
            figure_output_names[figure_id] = tuple(sorted(path.name for path in output_paths))
            if count > 1:
                label = f"{figure_id} ({count})"
            printer.succeed(label, detail_lines=detail_lines)
    finally:
        printer.close()
    return figure_output_names


def run_stat_updates(
    publication: PublicationDefinition,
    ctx: RunContext,
    stat_ids: tuple[str, ...],
    *,
    use_color: bool,
    existing: dict[str, ComputedStat] | None = None,
) -> dict[str, ComputedStat]:
    if not stat_ids:
        return dict(existing or {})
    printer = LiveSectionPrinter("Stats", use_color=use_color)
    computed_stats = dict(existing or {})
    try:
        for stat_id in stat_ids:
            printer.start_item(stat_id, "updating")
            try:
                computed = run_stats(publication, stat_id, ctx=ctx)
            except UserCodeExecutionError as exc:
                printer.fail(stat_id, detail_lines=list(exc.lines))
                raise ReportedExecutionError() from exc
            computed_stat = computed[0]
            computed_stats[stat_id] = computed_stat
            detail_lines = consume_dynamic_output(ctx, "stat")
            detail_lines.extend([f"\\{value.macro_name} = {value.display}" for value in computed_stat.values])
            printer.succeed(stat_id, detail_lines=detail_lines)
    finally:
        printer.close()
    write_computed_stats(publication, ordered_computed_stats(publication, computed_stats))
    return computed_stats


def run_table_updates(
    publication: PublicationDefinition,
    ctx: RunContext,
    table_ids: tuple[str, ...],
    *,
    use_color: bool,
    existing: dict[str, ComputedTable] | None = None,
) -> dict[str, ComputedTable]:
    if not table_ids:
        return dict(existing or {})
    printer = LiveSectionPrinter("Tables", use_color=use_color)
    computed_tables = dict(existing or {})
    try:
        for table_id in table_ids:
            printer.start_item(table_id, "updating")
            try:
                computed = run_tables(publication, table_id, ctx=ctx)
            except UserCodeExecutionError as exc:
                printer.fail(table_id, detail_lines=list(exc.lines))
                raise ReportedExecutionError() from exc
            computed_table = computed[0]
            computed_tables[table_id] = computed_table
            detail_lines = consume_dynamic_output(ctx, "table")
            if len(computed_table.body_texts) > 1:
                detail_lines.append(f"{len(computed_table.body_texts)} bodies")
            printer.succeed(table_id, detail_lines=detail_lines)
    finally:
        printer.close()
    write_computed_tables(publication, ordered_computed_tables(publication, computed_tables))
    return computed_tables


def ordered_computed_stats(
    publication: PublicationDefinition,
    computed_stats: dict[str, ComputedStat],
) -> tuple[ComputedStat, ...]:
    return tuple(computed_stats[stat_id] for stat_id in sorted(publication.stats) if stat_id in computed_stats)


def ordered_computed_tables(
    publication: PublicationDefinition,
    computed_tables: dict[str, ComputedTable],
) -> tuple[ComputedTable, ...]:
    return tuple(computed_tables[table_id] for table_id in sorted(publication.tables) if table_id in computed_tables)


def count_figure_outputs(figure_id: str, output_paths: list[Path]) -> int:
    return sum(1 for path in output_paths if figure_output_belongs_to_id(path, figure_id))


def clear_selected_figure_outputs(publication: PublicationDefinition, figure_id: str) -> None:
    if not publication.paths.autofigures_root.exists():
        return
    for path in publication.paths.autofigures_root.iterdir():
        if path.is_file() and figure_output_belongs_to_id(path, figure_id):
            path.unlink()


def consume_dynamic_output(ctx: RunContext, group: str) -> list[str]:
    lines = list(ctx.captured_output[group])
    ctx.captured_output[group].clear()
    return lines


def run_data_updates(
    ctx: RunContext,
    loader_ids: tuple[str, ...],
    *,
    heading: str = "Data",
    show_all: bool = False,
    include_nocache: bool,
    use_color: bool,
) -> None:
    visible_loader_ids = loader_ids if show_all else tuple(
        loader_id
        for loader_id in loader_ids
        if loader_id in ctx.updated_loader_ids or loader_needs_execution(ctx, loader_id, include_nocache)
    )
    if not visible_loader_ids:
        return
    printer = LiveSectionPrinter(heading, use_color=use_color)
    try:
        for loader_id in visible_loader_ids:
            if loader_id in ctx.updated_loader_ids:
                detail_lines = list(ctx.captured_data_output.pop(loader_id, []))
                printer.succeed(loader_id, detail_lines=detail_lines)
                ctx.updated_loader_ids.discard(loader_id)
                continue
            printer.start_item(loader_id, "loading")
            try:
                resolve_loader(ctx, loader_id)
            except UserCodeExecutionError as exc:
                printer.fail(loader_id, detail_lines=list(exc.lines))
                raise ReportedExecutionError() from exc
            detail_lines = list(ctx.captured_data_output.pop(loader_id, []))
            printer.succeed(loader_id, detail_lines=detail_lines)
            ctx.updated_loader_ids.discard(loader_id)
    finally:
        printer.close()


def loader_needs_execution(ctx: RunContext, loader_id: str, include_nocache: bool) -> bool:
    loader = ctx.publication.loaders[loader_id]
    if loader.nocache:
        return include_nocache and loader_id not in ctx.command_loader_cache
    return loader_id not in ctx.loader_cache


def print_updated_pdf(path: Path, *, use_color: bool) -> None:
    printer = LiveSectionPrinter("PDF", use_color=use_color)
    try:
        printer.succeed(str(path))
    finally:
        printer.close()


def command_run_context(
    publication: PublicationDefinition,
    *,
    loader_cache: dict[str, object] | None = None,
    pending_data_output: dict[str, list[str]] | None = None,
) -> RunContext:
    ctx = build_run_context(publication, loader_cache=loader_cache.copy() if loader_cache is not None else None)
    if pending_data_output is not None:
        ctx.captured_data_output = pending_data_output
    return ctx


def preload_shell_loader_cache(publication: PublicationDefinition) -> tuple[dict[str, object], dict[str, list[str]]]:
    loader_cache: dict[str, object] = {}
    ctx = build_run_context(publication, loader_cache=loader_cache)
    preload_loaders(ctx, tuple(sorted(publication.loaders)), include_nocache=False)
    return loader_cache, {key: list(value) for key, value in ctx.captured_data_output.items()}


def selected_figure_ids(publication: PublicationDefinition, figure_id: str | None = None) -> tuple[str, ...]:
    if figure_id is None:
        return tuple(sorted(publication.figures))
    if figure_id not in publication.figures:
        raise KeyError(f"Unknown figure '{figure_id}'")
    return (figure_id,)


def figure_loader_ids(publication: PublicationDefinition, figure_id: str | None = None) -> tuple[str, ...]:
    loader_ids: set[str] = set()
    for current_id in selected_figure_ids(publication, figure_id):
        loader_ids.update(publication.figures[current_id].dependency_ids)
    return tuple(sorted(loader_ids))


def stat_loader_ids(publication: PublicationDefinition) -> tuple[str, ...]:
    loader_ids: set[str] = set()
    for stat in publication.stats.values():
        loader_ids.update(stat.dependency_ids)
    return tuple(sorted(loader_ids))


def table_loader_ids(publication: PublicationDefinition, table_id: str | None = None) -> tuple[str, ...]:
    if table_id is None:
        tables = publication.tables.values()
    else:
        if table_id not in publication.tables:
            raise KeyError(f"Unknown table '{table_id}'")
        tables = (publication.tables[table_id],)
    loader_ids: set[str] = set()
    for table in tables:
        loader_ids.update(table.dependency_ids)
    return tuple(sorted(loader_ids))


def build_refresh_loader_ids(publication: PublicationDefinition) -> tuple[str, ...]:
    loader_ids = set(figure_loader_ids(publication))
    loader_ids.update(stat_loader_ids(publication))
    loader_ids.update(table_loader_ids(publication))
    return tuple(sorted(loader_ids))


def open_publication_previews(paths: list[Path], *, backend: str) -> None:
    resolved_paths = [path.resolve() for path in paths]
    if not resolved_paths:
        raise ValueError("No preview paths were provided")
    for resolved_path in resolved_paths:
        if not resolved_path.exists():
            raise FileNotFoundError(f"Preview target does not exist: {resolved_path}")
    if backend == "preview":
        open_with_preview(resolved_paths)
        return
    if backend == "vscode":
        open_with_vscode(resolved_paths)
        return
    raise ValueError(f"Unsupported preview backend: {backend}")


def open_with_preview(paths: list[Path]) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("The 'preview' backend is supported only on macOS")
    try:
        subprocess.run(
            ["open", "-a", "Preview", *(str(path) for path in paths)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Could not find the macOS `open` command") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        if stderr:
            raise RuntimeError(f"Preview failed: {stderr}") from None
        raise RuntimeError(f"Preview failed with exit code {exc.returncode}") from None


def open_with_vscode(paths: list[Path]) -> None:
    try:
        subprocess.run(
            ["code", "-n", *(str(path) for path in paths)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Could not find the VS Code `code` command on PATH") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        if stderr:
            raise RuntimeError(f"VS Code preview failed: {stderr}") from None
        raise RuntimeError(f"VS Code preview failed with exit code {exc.returncode}") from None


def preview_figure_paths(
    publication: PublicationDefinition,
    figure_id: str,
    *,
    subfigure_index: int | None = None,
) -> list[Path]:
    if figure_id not in publication.figures:
        raise KeyError(f"Unknown figure '{figure_id}'")
    root = publication.paths.autofigures_root
    primary = root / f"{figure_id}.pdf"
    if primary.exists():
        if subfigure_index is not None and subfigure_index != 1:
            raise IndexError(f"Figure '{figure_id}' has 1 panel(s); requested subfigure {subfigure_index}")
        return [primary]
    paths = sorted(root.glob(f"{figure_id}_*.pdf"))
    if paths:
        if subfigure_index is not None:
            if subfigure_index < 1 or subfigure_index > len(paths):
                raise IndexError(
                    f"Figure '{figure_id}' has {len(paths)} panel(s); requested subfigure {subfigure_index}"
                )
            return [paths[subfigure_index - 1]]
        return paths
    raise FileNotFoundError(
        f"Exported figure PDF does not exist for '{figure_id}'. "
        f"Run `pubs {publication.publication_id} figure {figure_id} update` first."
    )
