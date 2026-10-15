"""Command-line interface."""

import argparse
import subprocess
import sys

from dkb import DESCRIPTION, NAME, VERSION, view
from dkb.config import CONFIG_FILE, DATA_DIR
from dkb.manager import RepositoryManager
from dkb.skill import SKILL_FILE

ADD_EXAMPLES = """examples:
  dkb add tailwindlabs/tailwindcss.com/src/docs     only a docs folder (sparse clone)
  dkb add denoland/docs                             the whole repo
  dkb add https://github.com/astral-sh/uv/tree/main/docs
  dkb add gramiojs/documentation/docs --version-url gramiojs/gramio
                                                    docs repo + version from the library
  dkb add https://codeberg.org/owner/repo/src/branch/main/docs
  dkb add colinhacks/zod --pin v3.24.0 --name zod-3   the docs of the version you use
  dkb add ~/Developer/my-lib                        your working copy, always current"""


def build_parser(width: int | None = None) -> argparse.ArgumentParser:
    """Build the argument parser; `width` pins help formatting (default:
    the terminal's)."""

    def formatter(prog: str) -> argparse.HelpFormatter:
        return argparse.RawDescriptionHelpFormatter(prog, width=width)

    parser = argparse.ArgumentParser(
        prog=NAME,
        description=f"{NAME} v{VERSION}\n\n{DESCRIPTION}",
        formatter_class=formatter,
        epilog=f"""Running `dkb` alone shows the status.

files:
  {CONFIG_FILE}   the catalogue (track it in dotfiles)
  {DATA_DIR}/<name>   the docs, read them directly
  {SKILL_FILE}   what Claude Code sees

{ADD_EXAMPLES}""",
    )
    parser.add_argument("-V", "--version", action="version", version=VERSION)

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    add_parser = subparsers.add_parser(
        "add",
        help="Cache docs: a repo, a folder of one, a pinned version, or a local dir",
        description=(
            "Cache docs and list them in the skill. A remote entry follows a "
            "branch (re-fetched when it moves) or is pinned to a tag or commit "
            "(never re-fetched); a local entry links your working copy."
        ),
        epilog=ADD_EXAMPLES,
        formatter_class=formatter,
    )
    add_parser.add_argument(
        "source",
        help="owner/repo[/path], a GitHub / Gitea / Forgejo URL, or a local directory (/, ~, .)",
    )
    ref = add_parser.add_mutually_exclusive_group()
    ref.add_argument(
        "-b", "--branch", help="Branch to follow (default: the remote's default branch)"
    )
    ref.add_argument(
        "--pin",
        metavar="REF",
        help="Tag or full commit to freeze at; the version shown is the pin",
    )
    add_parser.add_argument(
        "--version-url",
        metavar="URL",
        help="Repo whose releases version these docs (when docs live apart from the code)",
    )
    add_parser.add_argument(
        "--name",
        help="Name in the cache (default: the version repo's name, else the docs repo's)",
    )

    remove_parser = subparsers.add_parser("remove", help="Drop cached repos")
    remove_parser.add_argument("names", nargs="+", metavar="NAME")

    update_parser = subparsers.add_parser(
        "update",
        help="Sync the cache with the catalogue",
        description=(
            "Fetch what is missing or changed upstream, refresh versions, prune "
            "what the catalogue no longer declares, regenerate the skill. "
            "Exits 1 if any repo failed."
        ),
    )
    update_parser.add_argument(
        "names",
        nargs="*",
        metavar="NAME",
        help="Only these (default: all, with pruning)",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="List cached repos (exits 1 if one is missing)",
    )
    status_parser.add_argument(
        "names", nargs="*", metavar="NAME", help="Only these (default: all)"
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Full configuration (url, paths, version source, location) as JSON",
    )

    subparsers.add_parser("skill", help="Regenerate the Claude Code skill")

    return parser


def main():
    """Main entry point."""
    args = build_parser().parse_args()

    manager = RepositoryManager(DATA_DIR)

    try:
        sys.exit(run(args, manager))
    except KeyboardInterrupt:
        view.message("interrupted")
        sys.exit(130)
    except view.Shown:
        sys.exit(1)
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as e:
        # Expected failures (bad input, git errors) get one line; anything
        # else is a dkb bug and keeps its traceback
        view.error(e)
        sys.exit(1)


def run(args: argparse.Namespace, manager: RepositoryManager) -> int:
    """One command: the manager works, the view shows. Returns the exit code."""
    if args.command == "add":
        declared = manager.declare(
            args.source, args.branch, args.pin, args.version_url, args.name
        )
        with view.adding(declared.name) as added:
            config = manager.add(declared)
            added(config, manager.data_dir / config.name)
        return 0

    if args.command == "remove":
        manager.remove(args.names)
        for name in args.names:
            view.removed(name)
        return 0

    if args.command == "update":
        if not args.names:
            for name in manager.prune():
                view.pruned(name)
        targets = manager.known(args.names)
        if not targets:
            view.nothing_cached()
            return 0
        with view.updating(sorted(targets)) as report:
            failed = manager.update(args.names, report)
        if failed:
            raise RuntimeError(f"failed to update: {', '.join(failed)}")
        return 0

    if args.command == "skill":
        manager.skill_manager.update(manager.config_manager.load())
        return 0

    # status, also what `dkb` alone runs
    configs = manager.known(getattr(args, "names", []))
    if getattr(args, "json", False):
        view.status_json(configs, manager.data_dir)
    elif configs:
        view.status(configs, manager.data_dir)
    else:
        view.nothing_cached()
    return 1 if manager.missing(configs) else 0


if __name__ == "__main__":
    main()
