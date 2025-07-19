#!/usr/bin/env python3
"""Knowledge Base Manager - Fetch and manage documentation from Git repositories."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import urllib.request
import urllib.parse
import importlib.metadata

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# Get package metadata
METADATA = importlib.metadata.metadata("dkb")
VERSION = METADATA["Version"]
DESCRIPTION = METADATA["Summary"]
NAME = METADATA["Name"]

PROGRAM_DIR = Path(__file__).parent
# XDG Base Directory Specification
XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
DATA_DIR = XDG_DATA_HOME / "dkb"
CONFIG = DATA_DIR / "config.json"

CLAUDE_GUIDANCE = """## 📚 Search Tips

Use `LS` first to see file structure - repos may use .md, .mdx, .rst or other formats.
"""


@dataclass
class RepoConfig:
    name: str
    url: str
    branch: str
    paths: list[str]
    version_url: str


def run(cmd: list[str], cwd: Path | None = None) -> str:
    """Run a shell command and return output."""
    return subprocess.check_output(cmd, cwd=cwd, text=True).strip()


def get_github_info(url: str) -> tuple[str, str]:
    """Fetch repository description and latest version from GitHub API."""
    # Extract owner/repo from URL
    parts = url.replace(".git", "").split("/")
    if "github.com" in url:
        owner, repo = parts[-2], parts[-1]

        # Check if gh CLI is available
        try:
            subprocess.run(["gh", "--version"], capture_output=True, check=True)
            use_gh = True
        except (subprocess.CalledProcessError, FileNotFoundError):
            use_gh = False

        if use_gh:
            # Use gh CLI for authenticated requests
            try:
                # Get repo info
                repo_data = subprocess.check_output(
                    ["gh", "api", f"repos/{owner}/{repo}"], text=True
                )
                data = json.loads(repo_data)
                description = data.get("description", "No description available")

                # Get latest release
                try:
                    release_data = subprocess.check_output(
                        ["gh", "api", f"repos/{owner}/{repo}/releases/latest"],
                        text=True,
                        stderr=subprocess.DEVNULL,  # Suppress 404 errors
                    )
                    release = json.loads(release_data)
                    version = release.get("tag_name", "").lstrip("v")
                    if not version:
                        version = "-"
                except subprocess.CalledProcessError:
                    # No releases found - this is normal for many repos
                    version = "-"

                return description, version
            except subprocess.CalledProcessError as e:
                console.print(f"[yellow]⚠ Failed to fetch GitHub data: {e}[/yellow]")
                return "No description available", "-"
        else:
            # Fallback to direct API calls
            # Get repo info
            api_url = f"https://api.github.com/repos/{owner}/{repo}"
            try:
                req = urllib.request.Request(api_url)
                req.add_header("Accept", "application/vnd.github.v3+json")
                req.add_header("User-Agent", f"dkb/{VERSION}")
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode())
                    description = data.get("description", "No description available")
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    response_data = json.loads(e.read().decode())
                    if "rate limit" in response_data.get("message", "").lower():
                        console.print(
                            "[yellow]⚠ GitHub API rate limit exceeded. Install gh CLI for authenticated requests.[/yellow]"
                        )
                description = "No description available"
            except Exception:
                description = "No description available"

            # Get latest release
            release_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
            try:
                req = urllib.request.Request(release_url)
                req.add_header("Accept", "application/vnd.github.v3+json")
                req.add_header("User-Agent", f"dkb/{VERSION}")
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode())
                    version = data.get("tag_name", "").lstrip("v")
                    if not version:
                        version = "-"
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    response_data = json.loads(e.read().decode())
                    if "rate limit" in response_data.get("message", "").lower():
                        console.print(
                            "[yellow]⚠ GitHub API rate limit exceeded. Install gh CLI for authenticated requests.[/yellow]"
                        )
                version = "-"
            except Exception:
                version = "-"

            return description, version

    return "No description available", "-"


def generate_claude_md() -> None:
    """Generate CLAUDE.md file with repository information."""
    console.print("📚 Updating documentation index...")
    with Progress(
        TextColumn("   "),  # Manual indent
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Loading repositories...", total=None)

        with open(CONFIG) as f:
            config = json.load(f)

        # Get help output without colors
        env = os.environ.copy()
        env["NO_COLOR"] = "1"
        help_output = subprocess.check_output(
            [sys.executable, __file__, "-h"], text=True, env=env
        )

        # Strip any remaining ANSI codes
        import re

        help_output = re.sub(r"\033\[[0-9;]*m", "", help_output)

        content = ["# Knowledge Base Context\n"]
        content.append(CLAUDE_GUIDANCE)
        content.append("## Documentation Cache\n")
        content.append(f"Local documentation cache at `{DATA_DIR}/` with:\n")

        # Add repository descriptions with paths
        repos = sorted(config["repositories"].items())
        for i, (name, repo_info) in enumerate(repos):
            progress.update(task, description=f"Fetching GitHub description for {name}...")
            desc, _ = get_github_info(repo_info["url"])
            content.append(f"- **{name}** (`{DATA_DIR}/{name}`): {desc}")

        content.append("\n## Usage\n")
        content.append("```")
        content.append(help_output.strip())
        content.append("```")

        # Write CLAUDE.md to dkb data directory
        claude_md = DATA_DIR / "CLAUDE.md"
        claude_md.write_text("\n".join(content))
        
        progress.update(task, description="Writing CLAUDE.md...", completed=True)
    
        console.print(f"   [green]✓[/green] Updated {claude_md}")

    # Check if ~/CLAUDE.md exists and has the import
    user_claude_md = Path.home() / "CLAUDE.md"
    import_line = f"@{claude_md}"

    if user_claude_md.exists():
        user_content = user_claude_md.read_text()
        if import_line not in user_content:
            console.print()
            panel_content = f"""[yellow]Your ~/CLAUDE.md doesn't import dkb's CLAUDE.md[/yellow]

Adding [cyan]@{claude_md}[/cyan] would give Claude Code access to:

  • All your [bold]{len(config["repositories"])}[/bold] documentation repos
  • dkb usage instructions for fetching new docs
"""
            console.print(
                Panel(
                    panel_content,
                    title="💡 Claude Code Integration",
                    border_style="yellow",
                )
            )

            if Confirm.ask("\nWould you like to add it?", default=False):
                # Add import at the end of the file
                with open(user_claude_md, "a") as f:
                    f.write(f"\n{import_line}\n")
                console.print("[green]✓[/green] Added import to ~/CLAUDE.md")
    else:
        console.print()
        panel_content = f"""[yellow]No ~/CLAUDE.md found[/yellow]

Create one with:
[cyan]echo '@{claude_md}' > ~/CLAUDE.md[/cyan]

This gives Claude Code access to your documentation cache"""
        console.print(
            Panel(panel_content, title="💡 Claude Code Setup", border_style="yellow")
        )


def update_repo(repo: RepoConfig) -> tuple[bool, dict]:
    """Update a single repository. Returns (updated, metadata)."""
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
        if not repo.paths:  # Empty paths means clone entire repo
            # Copy entire repository contents
            for item in repo_path.iterdir():
                if item.name == ".git":  # Skip .git directory
                    continue
                if item.is_dir():
                    shutil.copytree(item, kb_dir / item.name)
                else:
                    shutil.copy2(item, kb_dir / item.name)
        else:
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

        # Get version from GitHub API (using version_url)
        _, version = get_github_info(repo.version_url)

        # Create metadata dict
        metadata = {
            "last_updated": datetime.now().isoformat(),
            "commit": commit,
            "tag": tag,
            "version": version,
        }

        # Update config with metadata (only if repo already exists)
        if repo.name in config["repositories"]:
            config["repositories"][repo.name].update(metadata)
            with open(CONFIG, "w") as f:
                json.dump(config, f, indent=2)

        return old_commit != commit, metadata


def add_repo(
    name: str,
    url: str,
    paths: list[str],
    branch: str = "main",
    version_url: str | None = None,
) -> None:
    """Add a new repository and fetch its contents."""
    with open(CONFIG) as f:
        config = json.load(f)

    assert name not in config["repositories"], f"Repository '{name}' already exists"

    # Prepare repo config but don't save yet
    repo = RepoConfig(
        name=name, url=url, branch=branch, paths=paths, version_url=version_url or url
    )
    console.print(f"Fetching [cyan]{name}[/cyan] from {url}")
    console.print(f"Branch: [yellow]{branch}[/yellow]")
    if paths:
        console.print(f"Paths: [green]{', '.join(paths)}[/green]")
    else:
        console.print("Paths: [green]<entire repository>[/green]")

    # Try to fetch the repository first
    try:
        updated, metadata = update_repo(repo)

        # Only save config after successful fetch
        config["repositories"][name] = {
            "url": url,
            "branch": branch,
            "paths": paths,
            "version_url": version_url or url,  # Default to main URL if not specified
            **metadata,  # Include all metadata from update_repo
        }

        with open(CONFIG, "w") as f:
            json.dump(config, f, indent=2)

        if updated:
            console.print(f"[green]✓[/green] {name} updated")
        else:
            console.print(f"[green]✓[/green] {name} fetched")

        generate_claude_md()

    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] Failed to fetch {name}")
        console.print(
            "  [red]Error:[/red] Git command failed - check branch name or repository access"
        )
        if "does not exist" in str(e):
            console.print(
                f"  [yellow]Hint:[/yellow] Branch '{branch}' may not exist. Try a different branch with -b"
            )
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to add {name}: {str(e)}")
        raise SystemExit(1)


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
    console.print(f"[red]✗[/red] {name} removed")
    console.print()  # Add newline before progress

    generate_claude_md()


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
            version_url=cfg.get("version_url", cfg["url"]),  # Default to main URL
        )

        console.print(f"Updating [cyan]{name}[/cyan]...", end="")
        repo_updated, _ = update_repo(repo)
        if repo_updated:
            updated.append(name)
            console.print(" [green]✓ updated[/green]")
        else:
            console.print(" [dim]- unchanged[/dim]")

    if updated:
        console.print(f"\n[bold]Updated:[/bold] [green]{', '.join(updated)}[/green]")
        generate_claude_md()
    elif names is None:
        # Even if nothing updated, regenerate CLAUDE.md during full update
        generate_claude_md()


def show_status() -> None:
    """Display status of all repositories."""
    with open(CONFIG) as f:
        config = json.load(f)

    if not config["repositories"]:
        console.print("[yellow]No repositories found[/yellow]")
        return

    table = Table(title="Knowledge Base Status", title_style="bold")
    table.add_column("Repository", style="cyan", no_wrap=True)
    table.add_column("Version", style="green")
    table.add_column("Docs", style="blue")
    table.add_column("Source", style="dim")
    table.add_column("Last Updated", style="yellow")

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

            version = repo.get("version", "-")
        else:
            age_str = "never"
            version = "-"

        # Extract owner/repo from URLs
        docs_url = repo.get("url", "")
        version_url = repo.get("version_url", docs_url)
        
        # Format docs URL
        if "github.com" in docs_url:
            parts = docs_url.replace(".git", "").split("/")
            docs_repo = f"{parts[-2]}/{parts[-1]}"
        else:
            docs_repo = docs_url
            
        # Format version URL (source)
        if version_url != docs_url and "github.com" in version_url:
            parts = version_url.replace(".git", "").split("/")
            source_repo = f"{parts[-2]}/{parts[-1]}"
        else:
            source_repo = "-"

        table.add_row(name, version, docs_repo, source_repo, age_str)

    console.print(table)


def run_cron(interval: int = 6 * 60 * 60) -> None:
    """Run continuous update loop."""
    while True:
        console.print(
            f"[bold]Running update at {time.strftime('%Y-%m-%d %H:%M:%S')}[/bold]"
        )
        update_repos()
        console.print(f"[dim]Next update in {interval // 3600} hours[/dim]\n")
        time.sleep(interval)


def main():
    """Main entry point with argument parsing."""
    console.print()  # Add newline at start of every command
    parser = argparse.ArgumentParser(
        prog=NAME,
        description=f"\033[1;33m{NAME}\033[0m \033[2;33mv{VERSION}\033[0m\n\n{DESCRIPTION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
\033[2mExamples:\033[0m
  \033[34mdkb add\033[0m \033[2mdeno https://github.com/denoland/docs.git\033[0m
  \033[34mdkb add\033[0m \033[2mtailwind https://github.com/tailwindlabs/tailwindcss.com.git src/docs\033[0m
  \033[34mdkb remove\033[0m \033[2mtailwind\033[0m
  \033[34mdkb update\033[0m
  \033[34mdkb status\033[0m
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
        "paths",
        nargs="*",
        help="Path(s) to fetch from the repository (empty for entire repo)",
    )
    add_parser.add_argument(
        "-b", "--branch", default="main", help="Branch to fetch (default: main)"
    )
    add_parser.add_argument(
        "--version-url",
        help="Source repository URL to fetch version from (e.g., tailwindcss for tailwindcss.com docs)",
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

    # Claude command
    subparsers.add_parser("claude", help="Regenerate CLAUDE.md file")

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
        add_repo(args.name, args.url, args.paths, args.branch, args.version_url)
    elif args.command == "remove":
        remove_repo(args.name)
    elif args.command == "update":
        update_repos(args.names)
    elif args.command == "status":
        show_status()
    elif args.command == "claude":
        generate_claude_md()
    elif args.command == "cron":
        run_cron(args.interval)


if __name__ == "__main__":
    main()
