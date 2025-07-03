#!/usr/bin/env python3
"""Knowledge Base Manager - Fetch and manage documentation from Git repositories."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROGRAM_DIR = Path(__file__).parent
# XDG Base Directory Specification
XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
DATA_DIR = XDG_DATA_HOME / "dkb"
CONFIG = DATA_DIR / "config.json"


@dataclass
class RepoConfig:
    name: str
    url: str
    branch: str
    paths: list[str]


def run(cmd: list[str], cwd: Path | None = None) -> str:
    """Run a shell command and return output."""
    return subprocess.check_output(cmd, cwd=cwd, text=True).strip()


def update_repo(repo: RepoConfig) -> bool:
    """Update a single repository. Returns True if updated."""
    kb_dir = DATA_DIR / repo.name

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Clone to temp directory
        run(
            [
                "git",
                "clone",
                "--depth=1",
                "--branch",
                repo.branch,
                "--filter=blob:none",
                "--quiet",
                repo.url,
                str(tmp_path / "repo"),
            ]
        )
        repo_path = tmp_path / "repo"

        # Get commit info
        commit = run(["git", "rev-parse", "HEAD"], cwd=repo_path)
        try:
            tag = subprocess.check_output(
                ["git", "describe", "--tags", "--abbrev=0"],
                cwd=repo_path,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except subprocess.CalledProcessError:
            tag = "no-tags"

        # Load config to check old commit
        with open(CONFIG) as f:
            config = json.load(f)

        old_commit = None
        if repo.name in config["repositories"]:
            old_commit = config["repositories"][repo.name].get("commit")

        # Clear existing directory
        if kb_dir.exists():
            shutil.rmtree(kb_dir)
        kb_dir.mkdir(parents=True, exist_ok=True)

        # Copy only the requested paths
        for path in repo.paths:
            src = repo_path / path
            assert src.exists(), f"Path '{path}' not found in repository"

            if src.is_dir():
                # Copy directory contents directly to kb_dir
                for item in src.iterdir():
                    if item.is_dir():
                        shutil.copytree(item, kb_dir / item.name)
                    else:
                        shutil.copy2(item, kb_dir / item.name)
            else:
                # Copy single file
                shutil.copy2(src, kb_dir / src.name)

        # Update config with metadata
        config["repositories"][repo.name].update(
            {
                "last_updated": datetime.now().isoformat(),
                "commit": commit,
                "tag": tag,
            }
        )

        with open(CONFIG, "w") as f:
            json.dump(config, f, indent=2)

        return old_commit != commit


def add_repo(name: str, url: str, paths: list[str], branch: str = "main") -> None:
    """Add a new repository and fetch its contents."""
    with open(CONFIG) as f:
        config = json.load(f)

    assert name not in config["repositories"], f"Repository '{name}' already exists"

    config["repositories"][name] = {
        "url": url,
        "branch": branch,
        "paths": paths,
    }

    with open(CONFIG, "w") as f:
        json.dump(config, f, indent=2)

    # Automatically update
    repo = RepoConfig(name=name, url=url, branch=branch, paths=paths)
    print(f"Fetching {name} from {url}")
    print(f"Branch: {branch}")
    print(f"Paths: {', '.join(paths)}")

    if update_repo(repo):
        print(f"✓ {name} updated")
    else:
        print(f"✓ {name} fetched")


def remove_repo(name: str) -> None:
    """Remove a repository from config and delete its directory."""
    with open(CONFIG) as f:
        config = json.load(f)

    assert name in config["repositories"], f"Repository '{name}' not found"

    del config["repositories"][name]

    with open(CONFIG, "w") as f:
        json.dump(config, f, indent=2)

    repo_path = DATA_DIR / name
    if repo_path.exists():
        shutil.rmtree(repo_path)
    print(f"✗ {name} removed")


def update_repos(names: list[str] | None = None) -> None:
    """Update all repositories or specific ones if names provided."""
    with open(CONFIG) as f:
        config = json.load(f)

    updated = []
    repos_to_update = names if names else config["repositories"].keys()

    for name in repos_to_update:
        assert name in config["repositories"], f"Repository '{name}' not found"

        cfg = config["repositories"][name]
        repo = RepoConfig(
            name=name,
            url=cfg["url"],
            branch=cfg.get("branch", "main"),
            paths=cfg["paths"],
        )

        print(f"Updating {name}...", end="", flush=True)
        if update_repo(repo):
            updated.append(name)
            print(" ✓ updated")
        else:
            print(" - unchanged")

    if updated:
        print(f"\nUpdated: {', '.join(updated)}")


def show_status() -> None:
    """Display status of all repositories."""
    with open(CONFIG) as f:
        config = json.load(f)

    if not config["repositories"]:
        print("No repositories found")
        return

    print("Knowledge Base Status\n")
    for name, repo in sorted(config["repositories"].items()):
        if "last_updated" in repo:
            updated = datetime.fromisoformat(repo["last_updated"])
            age = datetime.now() - updated

            hours = age.total_seconds() / 3600
            if hours < 1:
                age_str = f"{int(age.total_seconds() / 60)}m ago"
            elif hours < 24:
                age_str = f"{int(hours)}h ago"
            else:
                age_str = f"{int(hours / 24)}d ago"

            tag = repo.get("tag", "no-tags")
            commit = repo.get("commit", "unknown")[:8]
        else:
            age_str = "never"
            tag = "not-fetched"
            commit = "unknown"

        print(f"{name:15} {tag:20} {commit}  {age_str}")


def run_cron(interval: int = 6 * 60 * 60) -> None:
    """Run continuous update loop."""
    while True:
        print(f"Running update at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        update_repos()
        print(f"Next update in {interval // 3600} hours\n")
        time.sleep(interval)


def main():
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        prog="dkb",
        description="Knowledge Base Manager - Fetch and manage documentation from Git repositories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  dkb add vue https://github.com/vuejs/docs.git src/guide src/api
  dkb remove vue
  dkb update
  dkb status
        """,
    )

    subparsers = parser.add_subparsers(
        dest="command", help="Available commands", required=True
    )

    # Add command
    add_parser = subparsers.add_parser("add", help="Add a new repository")
    add_parser.add_argument("name", help="Name for the repository")
    add_parser.add_argument("url", help="Git repository URL")
    add_parser.add_argument(
        "paths", nargs="+", help="Path(s) to fetch from the repository"
    )
    add_parser.add_argument(
        "-b", "--branch", default="main", help="Branch to fetch (default: main)"
    )

    # Remove command
    remove_parser = subparsers.add_parser("remove", help="Remove a repository")
    remove_parser.add_argument("name", help="Name of the repository to remove")

    # Update command
    update_parser = subparsers.add_parser("update", help="Update all repositories")
    update_parser.add_argument(
        "names", nargs="*", help="Specific repositories to update (default: all)"
    )

    # Status command
    subparsers.add_parser("status", help="Show status of all repositories")

    # Cron command
    cron_parser = subparsers.add_parser("cron", help="Run continuous update loop")
    cron_parser.add_argument(
        "-i",
        "--interval",
        type=int,
        default=6 * 60 * 60,
        help="Update interval in seconds (default: 6 hours)",
    )

    args = parser.parse_args()

    # Initialize data directory and config if needed
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists():
        CONFIG.write_text('{"repositories": {}}')

    # Execute command
    if args.command == "add":
        add_repo(args.name, args.url, args.paths, args.branch)
    elif args.command == "remove":
        remove_repo(args.name)
    elif args.command == "update":
        update_repos(args.names)
    elif args.command == "status":
        show_status()
    elif args.command == "cron":
        run_cron(args.interval)


if __name__ == "__main__":
    main()
