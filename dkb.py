#!/usr/bin/env python3
"""Knowledge Base Manager - Clean architecture rewrite."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import urllib.request
import urllib.parse
import html
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

# XDG Base Directory Specification
XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
DATA_DIR = XDG_DATA_HOME / "dkb"
CONFIG_FILE = DATA_DIR / "config.json"

CLAUDE_GUIDANCE = """## 📚 Search Tips

Use `LS` first to see file structure - repos may use .md, .mdx, .rst or other formats.
"""


class RepositoryProvider(ABC):
    """Abstract base for repository providers."""

    @abstractmethod
    def parse_url(self, url: str) -> Optional[Tuple[str, str, Optional[str]]]:
        """Parse URL and return (owner, repo, path) or None if invalid."""
        pass

    @abstractmethod
    def fetch_metadata(self, owner: str, repo: str) -> Dict[str, Any]:
        """Fetch repository metadata."""
        pass

    @abstractmethod
    def normalize_url(self, url: str) -> str:
        """Normalize URL to canonical form."""
        pass

    @abstractmethod
    def supports_url(self, url: str) -> bool:
        """Check if this provider supports the given URL."""
        pass


class GitHubProvider(RepositoryProvider):
    """GitHub repository provider."""

    def supports_url(self, url: str) -> bool:
        """Check if URL is a GitHub URL or shorthand."""
        # Check for full URLs
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc in ["github.com", "www.github.com"]:
            return True

        # Check for shorthand like "owner/repo" or "owner/repo/path"
        if not parsed.scheme and "/" in url:
            parts = url.split("/")
            return len(parts) >= 2

        return False

    def parse_url(self, url: str) -> Optional[Tuple[str, str, Optional[str]]]:
        """Parse GitHub URL and return (owner, repo, path)."""
        parsed = urllib.parse.urlparse(url)

        # Handle shorthand notation like "owner/repo/path"
        if not parsed.scheme:
            parts = url.split("/")
            if len(parts) >= 2:
                owner, repo = parts[0], parts[1]
                path = "/".join(parts[2:]) if len(parts) > 2 else None
                return owner, repo, path
            return None

        if not self.supports_url(url):
            return None

        path = parsed.path.strip("/")

        # Remove .git suffix if present
        if path.endswith(".git"):
            path = path[:-4]

        parts = path.split("/")
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]

            # Check for tree/branch/path structure
            if len(parts) > 2 and parts[2] == "tree" and len(parts) > 4:
                # Format: owner/repo/tree/branch/path...
                subpath = "/".join(parts[4:])
                return owner, repo, subpath
            elif len(parts) > 2 and parts[2] not in [
                "tree",
                "blob",
                "commits",
                "releases",
                "issues",
                "pulls",
            ]:
                # Direct path format: owner/repo/path...
                subpath = "/".join(parts[2:])
                return owner, repo, subpath

            return owner, repo, None

        return None

    def normalize_url(self, url: str) -> str:
        """Normalize GitHub URL to HTTPS with .git suffix."""
        if not self.supports_url(url):
            return url

        parsed = self.parse_url(url)
        if not parsed:
            return url

        owner, repo, _ = parsed
        return f"https://github.com/{owner}/{repo}.git"

    def fetch_metadata(self, owner: str, repo: str) -> Dict[str, Any]:
        """Fetch repository metadata from GitHub API."""
        # Try gh CLI first
        if self._has_gh_cli():
            try:
                return self._fetch_with_gh(owner, repo)
            except Exception:
                pass

        # Fallback to direct API
        return self._fetch_with_api(owner, repo)

    def _has_gh_cli(self) -> bool:
        """Check if gh CLI is available."""
        try:
            subprocess.run(["gh", "--version"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _fetch_with_gh(self, owner: str, repo: str) -> Dict[str, Any]:
        """Fetch metadata using gh CLI."""
        # Get repo info
        repo_data = subprocess.check_output(
            ["gh", "api", f"repos/{owner}/{repo}"], text=True
        )
        data = json.loads(repo_data)

        metadata = {
            "description": data.get("description", "No description available"),
            "default_branch": data.get("default_branch", "main"),
            "latest_version": None,
        }

        # Try to get latest release
        try:
            release_data = subprocess.check_output(
                ["gh", "api", f"repos/{owner}/{repo}/releases/latest"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            release = json.loads(release_data)
            version = release.get("tag_name", "").lstrip("v")
            if version:
                metadata["latest_version"] = version
        except subprocess.CalledProcessError:
            pass

        return metadata

    def _fetch_with_api(self, owner: str, repo: str) -> Dict[str, Any]:
        """Fetch metadata using GitHub API directly."""
        metadata = {
            "description": "No description available",
            "default_branch": "main",
            "latest_version": None,
        }

        # Get repo info
        api_url = f"https://api.github.com/repos/{owner}/{repo}"
        try:
            req = urllib.request.Request(api_url)
            req.add_header("Accept", "application/vnd.github.v3+json")
            req.add_header("User-Agent", f"dkb/{VERSION}")

            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                metadata["description"] = data.get(
                    "description", "No description available"
                )
                metadata["default_branch"] = data.get("default_branch", "main")
        except Exception:
            pass

        # Get latest release
        release_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        try:
            req = urllib.request.Request(release_url)
            req.add_header("Accept", "application/vnd.github.v3+json")
            req.add_header("User-Agent", f"dkb/{VERSION}")

            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                version = data.get("tag_name", "").lstrip("v")
                if version:
                    metadata["latest_version"] = version
        except Exception:
            pass

        return metadata


class ProviderRegistry:
    """Registry for repository providers."""

    def __init__(self):
        self.providers = [
            GitHubProvider(),
            # Add more providers here (GitLab, Bitbucket, etc)
        ]

    def get_provider(self, url: str) -> Optional[RepositoryProvider]:
        """Get the appropriate provider for a URL."""
        for provider in self.providers:
            if provider.supports_url(url):
                return provider
        return None


# Global provider registry
provider_registry = ProviderRegistry()


@dataclass
class GitRepository:
    """Represents a git repository with all its metadata."""

    url: str
    provider: str
    owner: str
    repo: str
    description: str = "No description available"
    default_branch: str = "main"
    latest_version: Optional[str] = None

    @classmethod
    def from_url(cls, url: str) -> "GitRepository":
        """Create GitRepository from URL by fetching metadata."""
        provider = provider_registry.get_provider(url)
        if not provider:
            raise ValueError(f"Unsupported repository URL: {url}")

        # Normalize URL
        normalized_url = provider.normalize_url(url)

        # Parse URL
        parsed = provider.parse_url(url)  # Use original URL for path parsing
        if not parsed:
            raise ValueError(f"Invalid repository URL: {url}")

        owner, repo, _ = parsed

        # Fetch metadata
        metadata = provider.fetch_metadata(owner, repo)

        return cls(
            url=normalized_url,
            provider=provider.__class__.__name__.replace("Provider", "").lower(),
            owner=owner,
            repo=repo,
            description=metadata["description"],
            default_branch=metadata["default_branch"],
            latest_version=metadata["latest_version"],
        )

    @property
    def display_name(self) -> str:
        """Get display name for the repository."""
        return f"{self.owner}/{self.repo}"


@dataclass
class RepositoryConfig:
    """Configuration for a repository in dkb."""

    name: str
    repository: GitRepository
    paths: list[str] = field(default_factory=list)
    branch: Optional[str] = None
    version_source: Optional[GitRepository] = None

    # Runtime metadata
    last_updated: Optional[datetime] = None
    last_commit: Optional[str] = None

    @property
    def effective_branch(self) -> str:
        """Get the branch to use (specified or default)."""
        return self.branch or self.repository.default_branch

    @property
    def effective_version(self) -> Optional[str]:
        """Get the version to display."""
        if self.version_source:
            return self.version_source.latest_version
        return self.repository.latest_version

    @property
    def effective_description(self) -> str:
        """Get the description to display."""
        return self.repository.description

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        data = {
            "url": self.repository.url,
            "branch": self.branch,
            "paths": self.paths,
            "description": self.repository.description,
        }

        if self.version_source:
            data["version_url"] = self.version_source.url

        if self.last_updated:
            data["last_updated"] = self.last_updated.isoformat()

        if self.last_commit:
            data["commit"] = self.last_commit

        if self.effective_version:
            data["version"] = self.effective_version

        return data

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "RepositoryConfig":
        """Create from dictionary (JSON storage) WITHOUT fetching metadata."""
        # Parse URL to get owner/repo for display
        provider = provider_registry.get_provider(data["url"])
        if provider:
            parsed = provider.parse_url(data["url"])
            if parsed:
                owner, repo, _ = parsed
            else:
                owner = "unknown"
                repo = "unknown"
            provider_name = provider.__class__.__name__.replace("Provider", "").lower()
        else:
            owner = "unknown"
            repo = "unknown"
            provider_name = "unknown"

        # Create repository object from stored data (no API calls!)
        repository = GitRepository(
            url=data["url"],
            provider=provider_name,
            owner=owner,
            repo=repo,
            description=data.get("description", "No description available"),
            default_branch=data.get("branch", "main"),
            latest_version=data.get("version"),
        )

        # Handle version source if different
        version_source = None
        if "version_url" in data and data["version_url"] != data["url"]:
            # Just parse it, don't fetch
            vs_provider = provider_registry.get_provider(data["version_url"])
            if vs_provider:
                vs_parsed = vs_provider.parse_url(data["version_url"])
                if vs_parsed:
                    vs_owner, vs_repo, _ = vs_parsed
                else:
                    vs_owner = "unknown"
                    vs_repo = "unknown"
                version_source = GitRepository(
                    url=data["version_url"],
                    provider=vs_provider.__class__.__name__.replace(
                        "Provider", ""
                    ).lower(),
                    owner=vs_owner,
                    repo=vs_repo,
                    description="Version source",
                    default_branch="main",
                    latest_version=data.get("version"),
                )

        # Create config
        config = cls(
            name=name,
            repository=repository,
            paths=data.get("paths", []),
            branch=data.get("branch"),
            version_source=version_source,
        )

        # Set runtime metadata if available
        if "last_updated" in data:
            config.last_updated = datetime.fromisoformat(data["last_updated"])

        if "commit" in data:
            config.last_commit = data["commit"]

        return config


class ConfigManager:
    """Manages dkb configuration."""

    def __init__(self, config_file: Path):
        self.config_file = config_file
        self._ensure_config()

    def _ensure_config(self):
        """Ensure config file exists."""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.config_file.exists():
            self.config_file.write_text('{"repositories": {}}')

    def load(self) -> Dict[str, RepositoryConfig]:
        """Load all repository configurations."""
        with open(self.config_file) as f:
            data = json.load(f)

        configs = {}
        for name, repo_data in data.get("repositories", {}).items():
            try:
                configs[name] = RepositoryConfig.from_dict(name, repo_data)
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to load {name}: {e}[/yellow]")

        return configs

    def save(self, configs: Dict[str, RepositoryConfig]):
        """Save all repository configurations."""
        data = {
            "repositories": {name: config.to_dict() for name, config in configs.items()}
        }

        with open(self.config_file, "w") as f:
            json.dump(data, f, indent=2)

    def add(self, config: RepositoryConfig):
        """Add a repository configuration."""
        configs = self.load()
        if config.name in configs:
            raise ValueError(f"Repository '{config.name}' already exists")

        configs[config.name] = config
        self.save(configs)

    def remove(self, name: str):
        """Remove a repository configuration."""
        configs = self.load()
        if name not in configs:
            raise ValueError(f"Repository '{name}' not found")

        del configs[name]
        self.save(configs)

    def get(self, name: str) -> RepositoryConfig:
        """Get a specific repository configuration."""
        configs = self.load()
        if name not in configs:
            raise ValueError(f"Repository '{name}' not found")

        return configs[name]


class ClaudeDocManager:
    """Manages CLAUDE.md documentation."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.claude_md = data_dir / "CLAUDE.md"

    def update(self, configs: Dict[str, RepositoryConfig]):
        """Update CLAUDE.md with current repositories."""
        content = ["# Knowledge Base Context\n"]
        content.append(CLAUDE_GUIDANCE)
        content.append("## Documentation Cache\n")
        content.append(f"Local documentation cache at `{self.data_dir}/` with:\n")
        content.append("<repositories>")

        for name in sorted(configs.keys()):
            config = configs[name]
            content.extend(
                [
                    "<item>",
                    f"  <name>{html.escape(name)}</name>",
                    f"  <description>{html.escape(config.effective_description)}</description>",
                    f"  <version>{html.escape(config.effective_version or '-')}</version>",
                    f"  <location>{html.escape(str(self.data_dir / name))}</location>",
                    "</item>",
                ]
            )

        content.append("</repositories>")
        content.append("\n## Usage\n")
        content.append("```")
        content.append(self._get_help_text())
        content.append("```")

        self.claude_md.write_text("\n".join(content))
        console.print(f"   [green]✓[/green] Updated {self.claude_md}")

        # Check if user has ~/CLAUDE.md and prompt to add import
        self._check_user_claude_md(len(configs))

    def _get_help_text(self) -> str:
        """Get help text for inclusion in CLAUDE.md."""
        env = os.environ.copy()
        env["NO_COLOR"] = "1"

        help_output = subprocess.check_output(
            [sys.executable, __file__, "-h"], text=True, env=env
        )

        # Strip ANSI codes
        import re

        return re.sub(r"\033\[[0-9;]*m", "", help_output).strip()

    def _check_user_claude_md(self, repo_count: int):
        """Check if user has ~/CLAUDE.md and prompt to add import."""
        user_claude_md = Path.home() / "CLAUDE.md"
        import_line = f"@{self.claude_md}"

        if user_claude_md.exists():
            user_content = user_claude_md.read_text()
            if import_line not in user_content:
                console.print()
                panel_content = f"""[yellow]Your ~/CLAUDE.md doesn't import dkb's CLAUDE.md[/yellow]

Adding [cyan]@{self.claude_md}[/cyan] would give Claude Code access to:

  • All your [bold]{repo_count}[/bold] documentation repos
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
[cyan]echo '@{self.claude_md}' > ~/CLAUDE.md[/cyan]

This gives Claude Code access to your documentation cache"""
            console.print(
                Panel(
                    panel_content, title="💡 Claude Code Setup", border_style="yellow"
                )
            )


class RepositoryManager:
    """Manages repository operations."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.config_manager = ConfigManager(CONFIG_FILE)
        self.claude_manager = ClaudeDocManager(data_dir)

    def add(
        self,
        url: str,
        branch: Optional[str] = None,
        version_url: Optional[str] = None,
    ):
        """Add a new repository."""
        # Parse URL to extract path if present
        provider = provider_registry.get_provider(url)
        if not provider:
            raise ValueError(f"Unsupported repository URL: {url}")

        parsed = provider.parse_url(url)
        if not parsed:
            raise ValueError(f"Invalid repository URL: {url}")

        owner, repo, extracted_path = parsed
        paths = [extracted_path] if extracted_path else []

        # Determine the name early (before metadata fetching)
        if version_url and version_url != url:
            # Parse version URL to get the repo name
            version_provider = provider_registry.get_provider(version_url)
            if version_provider:
                version_parsed = version_provider.parse_url(version_url)
                if version_parsed:
                    _, version_repo, _ = version_parsed
                    name = version_repo
                else:
                    name = repo
            else:
                name = repo
        else:
            name = repo

        # Check if repository already exists BEFORE fetching metadata
        existing_configs = self.config_manager.load()
        if name in existing_configs:
            raise ValueError(f"Repository '{name}' already exists")

        console.print(f"\n📦 Adding [cyan]{name}[/cyan]...")

        # Create repository objects
        with Progress(
            TextColumn("   "),
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Fetching repository metadata...", total=None)

            repository = GitRepository.from_url(url)

            version_source = None
            if version_url and version_url != url:
                progress.update(task, description="Fetching version source metadata...")
                version_source = GitRepository.from_url(version_url)

        with Progress(
            TextColumn("   "),
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Creating configuration...", total=None)

            # Create config
            config = RepositoryConfig(
                name=name,
                repository=repository,
                paths=paths,
                branch=branch,
                version_source=version_source,
            )

            # Clone and update
            progress.update(task, description="Cloning repository...")
            self._update_repository(config, progress, task)

            # Save config
            self.config_manager.add(config)

            # Update CLAUDE.md
            configs = self.config_manager.load()
            self.claude_manager.update(configs)

            version_str = (
                f" {config.effective_version}" if config.effective_version else ""
            )
            console.print(f"   [green]✓[/green]{version_str}")

    def remove(self, name: str):
        """Remove a repository."""
        console.print()

        # Remove from config
        self.config_manager.remove(name)

        # Remove directory
        repo_path = self.data_dir / name
        if repo_path.exists():
            shutil.rmtree(repo_path)

        console.print(f"[red]✗[/red] {name} removed")

        # Update CLAUDE.md
        configs = self.config_manager.load()
        self.claude_manager.update(configs)

    def update(self, names: Optional[list[str]] = None):
        """Update repositories."""
        console.print()
        configs = self.config_manager.load()

        if names:
            # Update specific repositories
            configs_to_update = {
                name: configs[name] for name in names if name in configs
            }
        else:
            # Update all
            configs_to_update = configs

        updated = []
        for name, config in configs_to_update.items():
            console.print(f"Updating [cyan]{name}[/cyan]...", end="")

            old_commit = config.last_commit

            # Also update version metadata
            provider = provider_registry.get_provider(config.repository.url)
            if provider:
                metadata = provider.fetch_metadata(
                    config.repository.owner, config.repository.repo
                )
                config.repository.latest_version = metadata.get("latest_version")

                # Update version source if it exists
                if config.version_source:
                    vs_metadata = provider.fetch_metadata(
                        config.version_source.owner, config.version_source.repo
                    )
                    config.version_source.latest_version = vs_metadata.get(
                        "latest_version"
                    )

            self._update_repository(config)

            if config.last_commit != old_commit:
                updated.append(name)
                console.print(" [green]✓ updated[/green]")
            else:
                console.print(" [dim]- unchanged[/dim]")

        # Save all configs
        self.config_manager.save(configs)

        # Update CLAUDE.md
        self.claude_manager.update(configs)

        if updated:
            console.print(
                f"\n[bold]Updated:[/bold] [green]{', '.join(updated)}[/green]"
            )

    def status(self):
        """Show status of all repositories."""
        console.print()
        configs = self.config_manager.load()

        if not configs:
            console.print("[yellow]No repositories found[/yellow]")
            return

        table = Table(title="Knowledge Base Status", title_style="bold")
        table.add_column("Repository", style="cyan", no_wrap=True)
        table.add_column("Version", style="green")
        table.add_column("Docs", style="blue")
        table.add_column("Source", style="dim")
        table.add_column("Last Updated", style="yellow")

        for name, config in sorted(configs.items()):
            # Calculate age
            if config.last_updated:
                age = datetime.now() - config.last_updated
                hours = age.total_seconds() / 3600

                if hours < 1:
                    age_str = f"{int(age.total_seconds() / 60)}m ago"
                elif hours < 24:
                    age_str = f"{int(hours)}h ago"
                else:
                    age_str = f"{int(hours / 24)}d ago"
            else:
                age_str = "never"

            # Version source display
            source_display = "-"
            if config.version_source:
                source_display = config.version_source.display_name

            table.add_row(
                name,
                config.effective_version or "-",
                config.repository.display_name,
                source_display,
                age_str,
            )

        console.print(table)

    def _update_repository(
        self,
        config: RepositoryConfig,
        progress: Optional[Progress] = None,
        task_id: Optional[Any] = None,
    ):
        """Update a single repository."""
        repo_dir = self.data_dir / config.name

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # If branch is None, fetch metadata to get default branch
            branch_to_use = config.branch
            if branch_to_use is None:
                provider = provider_registry.get_provider(config.repository.url)
                if provider:
                    metadata = provider.fetch_metadata(
                        config.repository.owner, config.repository.repo
                    )
                    branch_to_use = metadata.get("default_branch", "main")
                else:
                    branch_to_use = "main"

            # Clone repository
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth=1",
                    "--branch",
                    branch_to_use,
                    "--filter=blob:none",
                    "--quiet",
                    config.repository.url,
                    str(tmp_path / "repo"),
                ],
                check=True,
                capture_output=True,
            )

            repo_path = tmp_path / "repo"

            # Get commit hash
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            commit = result.stdout.strip()

            # Clear and recreate directory
            if repo_dir.exists():
                shutil.rmtree(repo_dir)
            repo_dir.mkdir(parents=True, exist_ok=True)

            # Copy requested paths
            if not config.paths:
                # Copy entire repository
                for item in repo_path.iterdir():
                    if item.name == ".git":
                        continue

                    if item.is_dir():
                        shutil.copytree(item, repo_dir / item.name)
                    else:
                        shutil.copy2(item, repo_dir / item.name)
            else:
                # Copy specific paths
                for path in config.paths:
                    src = repo_path / path
                    if not src.exists():
                        raise ValueError(f"Path '{path}' not found in repository")

                    if src.is_dir():
                        # Copy directory contents
                        for item in src.iterdir():
                            if item.is_dir():
                                shutil.copytree(item, repo_dir / item.name)
                            else:
                                shutil.copy2(item, repo_dir / item.name)
                    else:
                        # Copy single file
                        shutil.copy2(src, repo_dir / src.name)

            # Update metadata
            config.last_updated = datetime.now()
            config.last_commit = commit


def main():
    """Main entry point."""
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
  
  # Other commands
  dkb remove tailwind
  dkb update
  dkb status""",
    )

    subparsers = parser.add_subparsers(
        dest="command", help="Available commands", required=True
    )

    # Add command
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

    # Remove command
    remove_parser = subparsers.add_parser("remove", help="Remove a repository")
    remove_parser.add_argument("name", help="Name of the repository to remove")

    # Update command
    update_parser = subparsers.add_parser("update", help="Update repositories")
    update_parser.add_argument(
        "names", nargs="*", help="Specific repositories to update (default: all)"
    )

    # Status command
    subparsers.add_parser("status", help="Show status of all repositories")

    # Claude command
    subparsers.add_parser("claude", help="Regenerate CLAUDE.md file")

    args = parser.parse_args()

    # Create manager
    manager = RepositoryManager(DATA_DIR)

    # Execute command
    try:
        if args.command == "add":
            manager.add(args.url, args.branch, args.version_url)
        elif args.command == "remove":
            manager.remove(args.name)
        elif args.command == "update":
            manager.update(args.names or None)
        elif args.command == "status":
            manager.status()
        elif args.command == "claude":
            configs = manager.config_manager.load()
            manager.claude_manager.update(configs)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
