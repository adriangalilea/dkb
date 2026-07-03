"""Command-line interface."""

import argparse
import sys

from dkb import DESCRIPTION, NAME, VERSION, console
from dkb.config import DATA_DIR
from dkb.manager import RepositoryManager


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog=NAME,
        description=f"{NAME} v{VERSION}\n\n{DESCRIPTION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Add entire repository
  dkb add https://github.com/denoland/docs.git

  # Add specific paths using shorthand notation
  dkb add tailwindlabs/tailwindcss.com/src/docs
  dkb add gramiojs/documentation/docs --version-url gramiojs/gramio

  # Add specific paths using full URLs
  dkb add https://github.com/astral-sh/uv/tree/main/docs

  # Add from Gitea/Forgejo forges (codeberg.org, self-hosted)
  dkb add https://codeberg.org/owner/repo/src/branch/main/docs

  # Other commands
  dkb remove tailwind
  dkb update
  dkb status""",
    )

    subparsers = parser.add_subparsers(
        dest="command", help="Available commands", required=True
    )

    add_parser = subparsers.add_parser("add", help="Add a new repository")
    add_parser.add_argument(
        "url",
        help="Repository URL (e.g., github.com/owner/repo/path or owner/repo/path)",
    )
    add_parser.add_argument(
        "-b", "--branch", help="Branch to fetch (default: repository's default branch)"
    )
    add_parser.add_argument(
        "--version-url",
        help="Source repository URL to fetch version from",
    )
    add_parser.add_argument(
        "--name",
        help="Name for the cached docs (default: derived from the repository)",
    )

    remove_parser = subparsers.add_parser("remove", help="Remove a repository")
    remove_parser.add_argument("name", help="Name of the repository to remove")

    update_parser = subparsers.add_parser("update", help="Update repositories")
    update_parser.add_argument(
        "names", nargs="*", help="Specific repositories to update (default: all)"
    )

    subparsers.add_parser("status", help="Show status of all repositories")

    subparsers.add_parser("skill", help="Regenerate the Claude Code skill")

    return parser


def main():
    """Main entry point."""
    args = build_parser().parse_args()

    manager = RepositoryManager(DATA_DIR)

    try:
        if args.command == "add":
            manager.add(args.url, args.branch, args.version_url, args.name)
        elif args.command == "remove":
            manager.remove(args.name)
        elif args.command == "update":
            manager.update(args.names or None)
        elif args.command == "status":
            manager.status()
        elif args.command == "skill":
            configs = manager.config_manager.load()
            manager.skill_manager.update(configs)
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/dim]")
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
