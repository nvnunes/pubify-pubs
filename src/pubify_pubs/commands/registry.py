from __future__ import annotations

TOP_LEVEL_COMMANDS = (
    "  pubs list",
    "  pubs init <publication-id>",
)

CORE_PUBLICATION_COMMANDS = (
    "  pubs <publication-id> shell",
    "  pubs <publication-id> data [list|add <data-id>]",
    "  pubs <publication-id> figure [list|add <figure-id>|update|<figure-id> update|<figure-id> preview [<subfig-idx>]|<figure-id> latex [subcaption]]",
    "  pubs <publication-id> stat [list|add <stat-id>|update|<stat-id> update|<stat-id> latex]",
    "  pubs <publication-id> table [list|add <table-id>|update|<table-id> update|<table-id> latex]",
    "  pubs <publication-id> update",
    "  pubs <publication-id> build [--clear]",
    "  pubs <publication-id> preview",
)

DEFERRED_PUBLICATION_COMMANDS = (
    "  pubs <publication-id> data <loader-id> pin",
    "  pubs <publication-id> ignore <relative-path>",
    "  pubs <publication-id> push [--force]",
    "  pubs <publication-id> pull [--force]",
    "  pubs <publication-id> diff [list|<relative-path>]",
)

CORE_SHELL_COMMANDS = (
    "  data [list|add <data-id>]",
    "  figure [list|add <figure-id>|update|<figure-id> update|<figure-id> preview [<subfig-idx>]|<figure-id> latex [subcaption]]",
    "  stat [list|add <stat-id>|update|<stat-id> update|<stat-id> latex]",
    "  table [list|add <table-id>|update|<table-id> update|<table-id> latex]",
    "  update",
    "  build [--clear]",
    "  preview",
)

DEFERRED_SHELL_COMMANDS = (
    "  data <loader-id> pin",
    "  ignore <relative-path>",
    "  push [--force]",
    "  pull [--force]",
    "  diff [list|<relative-path>]",
)


def build_cli_description() -> str:
    lines = [
        "Commands:",
        *TOP_LEVEL_COMMANDS,
        "",
        *CORE_PUBLICATION_COMMANDS,
        *DEFERRED_PUBLICATION_COMMANDS,
    ]
    return "\n".join(lines)


def build_shell_help_text(publication_id: str) -> str:
    return "\n".join(
        [
            f"Shell commands for {publication_id}:",
            *CORE_SHELL_COMMANDS,
            *DEFERRED_SHELL_COMMANDS,
            "  help",
            "  quit",
        ]
    )
