"""Claude Code skill generation.

The skill description is always visible in the agent's system prompt, so it
carries the catalogue index (repo names). The body only loads on invocation
and carries versions, locations, and dkb usage.
"""

import html
from collections import Counter
from pathlib import Path
from typing import Dict

from dkb import console
from dkb.models import RepositoryConfig

SKILL_FILE = Path.home() / ".claude" / "skills" / "dkb" / "SKILL.md"
MAX_DESCRIPTION_LENGTH = 1024

MAX_STRUCTURE_ENTRIES = 15

READING_DOCS = """## Reading docs

Each `<location>` is a plain directory on disk; read it with your file tools directly, no dkb command needed:

- `<structure>` lists the top-level entries (dirs end with `/`) so you can jump straight to the right area
- Grep the location for API names, options, or error messages to land on the exact page
- Glob `**/*.md*` (formats vary per repo: .md, .mdx, .rst) to enumerate pages when grep is too narrow
- Read files directly once located; these are the exact docs for the cached version, prefer them over memory or web fetches
"""


class SkillManager:
    """Manages the Claude Code skill for the documentation cache."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def update(self, configs: Dict[str, RepositoryConfig]):
        """Regenerate the skill with the current catalogue."""
        description = self._description(sorted(configs))

        content = [
            "---",
            "name: dkb",
            f'description: "{description}"',
            "---",
            "",
            "# Developer Knowledge Base",
            "",
            f"Local documentation cache at `{self.data_dir}/`:",
            "",
            "<repositories>",
        ]

        for name in sorted(configs.keys()):
            config = configs[name]
            content.extend(
                [
                    "<item>",
                    f"  <name>{html.escape(name)}</name>",
                    f"  <description>{html.escape(config.effective_description)}</description>",
                    f"  <version>{html.escape(config.effective_version or '-')}</version>",
                    f"  <location>{html.escape(str(self.data_dir / name))}</location>",
                    f"  <structure>{html.escape(self._structure(name))}</structure>",
                    "</item>",
                ]
            )

        content.extend(
            [
                "</repositories>",
                "",
                READING_DOCS,
                "## Managing the cache (dkb CLI)",
                "",
                "```",
                self._get_help_text(),
                "```",
                "",
            ]
        )

        SKILL_FILE.parent.mkdir(parents=True, exist_ok=True)
        SKILL_FILE.write_text("\n".join(content))
        console.print(f"   [green]✓[/green] Updated {SKILL_FILE}")

        self._migrate_legacy_claude_md()

    def _description(self, names: list) -> str:
        """Always-visible skill description: docs location pattern plus the
        repo-name index, dropping trailing names to stay within the limit."""
        shown = list(names)
        dropped = 0
        while True:
            listing = ", ".join(shown)
            if dropped:
                listing = f"{listing} +{dropped} more" if shown else f"{dropped} repos"
            description = (
                f"Locally cached, versioned documentation at "
                f"{self.data_dir}/<name> for: {listing}. "
                "Read these docs from disk instead of guessing APIs. "
                "Also manages the cache (dkb add/remove/update)."
            )
            if len(description) <= MAX_DESCRIPTION_LENGTH or not shown:
                return description
            shown.pop()
            dropped += 1

    def _structure(self, name: str) -> str:
        """Top-level entries of a cached repo, dirs first; file swarms
        collapse to counts by extension."""
        repo_dir = self.data_dir / name
        if not repo_dir.exists():
            return "-"

        dirs = sorted(p.name + "/" for p in repo_dir.iterdir() if p.is_dir())
        files = sorted(p.name for p in repo_dir.iterdir() if not p.is_dir())

        if len(dirs) + len(files) <= MAX_STRUCTURE_ENTRIES:
            return ", ".join(dirs + files) or "-"

        if len(dirs) > MAX_STRUCTURE_ENTRIES:
            dirs = dirs[:MAX_STRUCTURE_ENTRIES] + [
                f"+{len(dirs) - MAX_STRUCTURE_ENTRIES} more dirs"
            ]

        counts = Counter(Path(f).suffix or "no extension" for f in files)
        summary = ", ".join(f"{n}x {ext}" for ext, n in counts.most_common())
        return ", ".join(dirs + [f"{len(files)} files ({summary})"])

    def _get_help_text(self) -> str:
        from dkb.cli import build_parser

        return build_parser().format_help().strip()

    def _migrate_legacy_claude_md(self):
        """Nuke the CLAUDE.md injection the skill replaces."""
        legacy = self.data_dir / "CLAUDE.md"
        if legacy.exists():
            legacy.unlink()
            console.print(f"   [yellow]✗[/yellow] Removed legacy {legacy}")

        user_claude_md = Path.home() / "CLAUDE.md"
        if not user_claude_md.exists():
            return

        import_line = f"@{legacy}"
        lines = user_claude_md.read_text().splitlines()
        if import_line not in [line.strip() for line in lines]:
            return

        kept = [line for line in lines if line.strip() != import_line]
        user_claude_md.write_text("\n".join(kept) + "\n")
        console.print(
            f"   [yellow]✗[/yellow] Removed [cyan]{import_line}[/cyan] import "
            f"from {user_claude_md} (replaced by the skill)"
        )
