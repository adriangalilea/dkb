"""Everything dkb shows a person. The manager does the work and returns data;
this module draws it, on py-utils `cli` (the same tables, palette and live
regions as ts-utils), so a terminal gets color and motion and a pipe gets
plain aligned text from the same code.

Data goes to stdout (`status`, `status --json`); messages and progress go to
stderr, so `dkb status | grep zod` never meets a spinner.
"""

import json
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from py_utils.cli import live, spin, spinner, table, ui

from dkb.models import RepositoryConfig


def message(text: str):
    print(text, file=sys.stderr)


def error(e: Exception):
    message(f"{ui.bad('Error:')} {e}")


@contextmanager
def adding(name: str) -> Iterator[Callable[[RepositoryConfig, Path], None]]:
    """A spinner while an entry is fetched; the caller reports the result,
    which becomes the persisted line: `✓ zod 4.6.7 → ~/.local/share/dkb/zod`.
    A failure persists as the spinner's ⨯ line and propagates."""
    with spinner(f"fetching {ui.accent(name)}") as set_label:

        def added(config: RepositoryConfig, location: Path):
            version = f" {config.effective_version}" if config.effective_version else ""
            set_label(f"{ui.accent(config.name)}{version} → {location}")

        yield added


def removed(name: str):
    message(f"{ui.bad('✗')} {name} removed")


def pruned(name: str):
    message(f"{ui.bad('✗')} {name} pruned (not in catalogue)")


def nothing_cached():
    message(
        f"{ui.warn('Nothing cached yet.')} Add docs with "
        "`dkb add owner/repo/path` (see `dkb add -h`)"
    )


@contextmanager
def updating(names: list[str]) -> Iterator[Callable[[str, str, str | Exception], None]]:
    """A live table, one row per repo, that worker threads move through
    waiting → syncing → updated / unchanged / failed. A pipe gets the final
    table once. Yields `report(name, state, detail)`; a failed repo's
    detail is its exception."""
    rows: dict[str, tuple[str, str]] = {name: ("waiting", "") for name in names}

    def report(name: str, state: str, detail: str | Exception):
        text = ui.bad(str(detail)) if isinstance(detail, Exception) else detail
        rows[name] = (state, text)

    def frame() -> str:
        return table(
            [
                [_mark(state), ui.accent(name), _label(state), detail]
                for name, (state, detail) in rows.items()
            ]
        )

    with live(frame):
        yield report


def _mark(state: str) -> str:
    marks = {
        "waiting": ui.muted("·"),
        "updated": ui.ok("✓"),
        "unchanged": ui.muted("-"),
        "failed": ui.bad("✗"),
    }
    return marks.get(state) or spin()


def _label(state: str) -> str:
    labels = {
        "updated": ui.ok("updated"),
        "unchanged": ui.muted("unchanged"),
        "failed": ui.bad("failed"),
    }
    return labels.get(state, ui.muted(state))


def status(configs: dict[str, RepositoryConfig], data_dir: Path):
    """One table for terminals and pipes: colored in a terminal, plain aligned
    columns with a header when piped (greppable, one line per repo)."""
    rows = []
    for name, config in sorted(configs.items()):
        present = (data_dir / name).exists()
        changed = (
            ("live" if config.local else _age(config.last_updated)) if present else None
        )
        rows.append(
            [
                ui.accent(name),
                config.effective_version or "-",
                config.source,
                config.version_source.display_name if config.version_source else "-",
                ui.warn(changed) if changed else ui.bad("MISSING"),
                ui.muted(_age(config.last_checked))
                if present
                else ui.bad("run dkb update"),
            ]
        )
    print(
        table(
            rows,
            head=[
                "NAME",
                "VERSION",
                "SOURCE",
                "VERSION FROM",
                "DOCS CHANGED",
                "CHECKED",
            ],
        )
    )
    message(ui.muted(f"docs at {data_dir}/<name> · full config: dkb status --json"))


def status_json(configs: dict[str, RepositoryConfig], data_dir: Path):
    payload = {
        name: {**config.to_dict(), "location": str(data_dir / name)}
        for name, config in sorted(configs.items())
    }
    print(json.dumps(payload, indent=2))


def _age(when: datetime | None) -> str:
    if when is None:
        return "never"
    seconds = (datetime.now(UTC) - when).total_seconds()
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    return f"{int(seconds / 86400)}d ago"
