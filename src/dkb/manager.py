"""Repository operations: add, remove, update, status, clone."""

import json
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from dkb import console
from dkb.skill import SkillManager
from dkb.config import CONFIG_FILE, ConfigManager
from dkb.models import GitRepository, RepositoryConfig
from dkb.providers import provider_registry


class RepositoryManager:
    """Manages repository operations."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.config_manager = ConfigManager(CONFIG_FILE)
        self.skill_manager = SkillManager(data_dir)

    def add(
        self,
        url: str,
        branch: Optional[str] = None,
        version_url: Optional[str] = None,
        name: Optional[str] = None,
    ):
        """Add a new repository."""
        provider = provider_registry.get_provider(url)
        if not provider:
            raise ValueError(f"Unsupported repository URL: {url}")

        parsed = provider.parse_url(url)
        if not parsed:
            raise ValueError(f"Invalid repository URL: {url}")

        owner, repo, extracted_path = parsed
        paths = [extracted_path] if extracted_path else []

        # Explicit name wins, then the version source repo, then the docs repo
        if not name:
            name = repo
            if version_url and version_url != url:
                version_provider = provider_registry.get_provider(version_url)
                if version_provider:
                    version_parsed = version_provider.parse_url(version_url)
                    if version_parsed:
                        _, name, _ = version_parsed

        # Check existence BEFORE fetching metadata
        existing_configs = self.config_manager.load()
        if name in existing_configs:
            raise ValueError(f"Repository '{name}' already exists")

        console.print(f"\n📦 Adding [cyan]{name}[/cyan]...")

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

            config = RepositoryConfig(
                name=name,
                repository=repository,
                paths=paths,
                branch=branch,
                version_source=version_source,
            )

            progress.update(task, description="Cloning repository...")
            self._clone_repository(config)

            self.config_manager.add(config)

            configs = self.config_manager.load()
            self.skill_manager.update(configs)

            version_str = (
                f" {config.effective_version}" if config.effective_version else ""
            )
            console.print(f"   [green]✓[/green]{version_str}")

    def remove(self, name: str):
        """Remove a repository."""
        console.print()

        self.config_manager.remove(name)

        repo_path = self.data_dir / name
        if repo_path.exists():
            shutil.rmtree(repo_path)

        console.print(f"[red]✗[/red] {name} removed")

        configs = self.config_manager.load()
        self.skill_manager.update(configs)

    def _update_one(
        self,
        name: str,
        config: RepositoryConfig,
        progress: Progress,
        task_id,
        pad: int,
    ) -> bool:
        """Worker for parallel update. Returns True if content changed."""
        padded = name.ljust(pad)
        try:
            branch_to_use = config.branch or config.repository.default_branch

            progress.update(task_id, description=f"[cyan]{padded}[/cyan] checking...")
            remote_commit = self._get_remote_commit(
                config.repository.url, branch_to_use
            )
            if remote_commit and remote_commit == config.last_commit:
                progress.update(
                    task_id,
                    description=f"[dim]- {padded} unchanged[/dim]",
                    completed=1,
                )
                return False

            progress.update(
                task_id, description=f"[cyan]{padded}[/cyan] fetching metadata..."
            )
            provider = provider_registry.get_provider(config.repository.url)
            if provider:
                metadata = provider.fetch_metadata(
                    config.repository.url,
                    config.repository.owner,
                    config.repository.repo,
                )
                config.repository.latest_version = metadata.get("latest_version")
                if config.branch is None:
                    branch_to_use = metadata.get("default_branch", branch_to_use)

            if config.version_source:
                vs_provider = provider_registry.get_provider(config.version_source.url)
                if vs_provider:
                    vs_metadata = vs_provider.fetch_metadata(
                        config.version_source.url,
                        config.version_source.owner,
                        config.version_source.repo,
                    )
                    config.version_source.latest_version = vs_metadata.get(
                        "latest_version"
                    )

            progress.update(task_id, description=f"[cyan]{padded}[/cyan] cloning...")
            self._clone_repository(config, branch_to_use)

            progress.update(
                task_id,
                description=f"[green]✓ {padded} updated[/green]",
                completed=1,
            )
            return True
        except Exception as e:
            progress.update(
                task_id,
                description=f"[red]✗ {padded} {e}[/red]",
                completed=1,
            )
            return False

    def update(self, names: Optional[list[str]] = None):
        """Update repositories in parallel."""
        console.print()
        configs = self.config_manager.load()

        if names:
            configs_to_update = {
                name: configs[name] for name in names if name in configs
            }
        else:
            configs_to_update = configs

        if not configs_to_update:
            console.print("[yellow]No repositories to update[/yellow]")
            return

        pad = max(len(n) for n in configs_to_update)
        updated = []
        lock = threading.Lock()

        progress = Progress(
            SpinnerColumn(finished_text=" "),
            TextColumn("{task.description}"),
            console=console,
        )

        with progress:
            tasks = {}
            for name in configs_to_update:
                padded = name.ljust(pad)
                tid = progress.add_task(f"[cyan]{padded}[/cyan] waiting...", total=1)
                tasks[name] = tid

            def worker(name: str, config: RepositoryConfig) -> None:
                changed = self._update_one(name, config, progress, tasks[name], pad)
                if changed:
                    with lock:
                        updated.append(name)

            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [
                    pool.submit(worker, name, config)
                    for name, config in configs_to_update.items()
                ]
                for future in as_completed(futures):
                    future.result()

        if updated:
            self.config_manager.save(configs)
            self.skill_manager.update(configs)
            console.print(
                f"\n[bold]Updated:[/bold] [green]{', '.join(updated)}[/green]"
            )
        else:
            self.config_manager.save(configs)

    def status(self, names: Optional[list[str]] = None, as_json: bool = False):
        """Show status of repositories: table for humans, --json for tools."""
        configs = self.config_manager.load()

        if names:
            missing = [n for n in names if n not in configs]
            if missing:
                raise ValueError(f"Repository '{missing[0]}' not found")
            configs = {n: configs[n] for n in names}

        if as_json:
            payload = {
                name: {
                    **config.to_dict(),
                    "location": str(self.data_dir / name),
                }
                for name, config in sorted(configs.items())
            }
            print(json.dumps(payload, indent=2))
            return

        console.print()
        if not configs:
            console.print("[yellow]No repositories found[/yellow]")
            return

        table = Table(title="Knowledge Base Status", title_style="bold")
        table.add_column("Repository", style="cyan", overflow="fold")
        table.add_column("Version", style="green", overflow="fold")
        table.add_column("Docs", style="blue", overflow="fold")
        table.add_column("Source", style="dim", overflow="fold")
        table.add_column("Last Updated", style="yellow")

        for name, config in sorted(configs.items()):
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

    def _get_remote_commit(self, url: str, branch: str) -> Optional[str]:
        """Get the latest commit hash from remote without cloning."""
        try:
            result = subprocess.run(
                ["git", "ls-remote", url, f"refs/heads/{branch}"],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )
            line = result.stdout.strip()
            if line:
                return line.split()[0]
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        return None

    def _clone_repository(
        self,
        config: RepositoryConfig,
        branch: Optional[str] = None,
    ):
        """Clone a repository and copy contents to data dir."""
        repo_dir = self.data_dir / config.name
        branch_to_use = branch or config.branch or config.repository.default_branch

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "repo"

            if config.paths:
                # Sparse checkout: only fetch the paths we need
                subprocess.run(
                    [
                        "git",
                        "clone",
                        "--depth=1",
                        "--filter=blob:none",
                        "--sparse",
                        "--no-checkout",
                        "--branch",
                        branch_to_use,
                        "--quiet",
                        config.repository.url,
                        str(repo_path),
                    ],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "sparse-checkout", "set", *config.paths],
                    cwd=repo_path,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "checkout"],
                    cwd=repo_path,
                    check=True,
                    capture_output=True,
                )
            else:
                # Full shallow clone
                subprocess.run(
                    [
                        "git",
                        "clone",
                        "--depth=1",
                        "--filter=blob:none",
                        "--branch",
                        branch_to_use,
                        "--quiet",
                        config.repository.url,
                        str(repo_path),
                    ],
                    check=True,
                    capture_output=True,
                )

            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            if repo_dir.exists():
                shutil.rmtree(repo_dir)
            repo_dir.mkdir(parents=True, exist_ok=True)

            if not config.paths:
                for item in repo_path.iterdir():
                    if item.name == ".git":
                        continue
                    if item.is_dir():
                        shutil.copytree(item, repo_dir / item.name)
                    else:
                        shutil.copy2(item, repo_dir / item.name)
            else:
                for path in config.paths:
                    src = repo_path / path
                    if not src.exists():
                        raise ValueError(f"Path '{path}' not found in repository")
                    if src.is_dir():
                        for item in src.iterdir():
                            if item.is_dir():
                                shutil.copytree(item, repo_dir / item.name)
                            else:
                                shutil.copy2(item, repo_dir / item.name)
                    else:
                        shutil.copy2(src, repo_dir / src.name)

            config.last_updated = datetime.now()
            config.last_commit = commit
