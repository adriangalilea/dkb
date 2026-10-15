"""Repository operations: add, remove, update, status, clone."""

import json
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from dkb import console
from dkb.config import STATE_FILE, ConfigManager
from dkb.models import GitRepository, RepositoryConfig
from dkb.providers import provider_registry
from dkb.skill import SkillManager


class RepositoryManager:
    """Manages repository operations."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.config_manager = ConfigManager()
        self.skill_manager = SkillManager(data_dir)

    def add(
        self,
        url: str,
        branch: str | None = None,
        version_url: str | None = None,
        name: str | None = None,
    ):
        """Add a new repository."""
        provider = provider_registry.get_provider(url)
        if not provider:
            raise ValueError(f"Unsupported repository URL: {url}")

        parsed = provider.parse_url(url)
        if not parsed:
            raise ValueError(f"Invalid repository URL: {url}")

        _owner, repo, extracted_path = parsed
        paths = [extracted_path] if extracted_path else []

        # The docs repo already carries its own releases: no separate source
        if version_url and _repo_url(version_url) == _repo_url(url):
            version_url = None

        # Explicit name wins, then the version source repo, then the docs repo
        if not name:
            name = repo
            if version_url:
                _, name, _ = provider_registry.get_provider(version_url).parse_url(
                    version_url
                )

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
            if version_url:
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

        _delete(self.data_dir / name)

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
    ) -> str:
        """Worker for parallel update: 'updated', 'unchanged' or 'failed'.
        Metadata (version, description) refreshes on every run, since a
        release can land without the docs changing; the clone is redone
        only when the upstream commit moved or the clone is missing."""
        padded = name.ljust(pad)
        try:
            progress.update(task_id, description=f"[cyan]{padded}[/cyan] checking...")
            remote_commit = self._get_remote_commit(
                config.repository.url, config.branch
            )

            _refresh_metadata(config.repository)
            if config.version_source:
                _refresh_metadata(config.version_source)

            present = (self.data_dir / name).exists()
            if present and remote_commit == config.last_commit:
                progress.update(
                    task_id,
                    description=f"[dim]- {padded} unchanged[/dim]",
                    completed=1,
                )
                return "unchanged"

            progress.update(task_id, description=f"[cyan]{padded}[/cyan] cloning...")
            self._clone_repository(config)

            progress.update(
                task_id,
                description=f"[green]✓ {padded} updated[/green]",
                completed=1,
            )
            return "updated"
        except Exception as e:  # noqa: BLE001 - one repo failing must not stop the rest
            detail = getattr(e, "stderr", None) or e
            progress.update(
                task_id,
                description=f"[red]✗ {padded} {str(detail).strip()}[/red]",
                completed=1,
            )
            return "failed"

    def update(self, names: list[str] | None = None):
        """Reconcile the cache with the catalogue: fetch declared repos that are
        missing or changed upstream, prune clones nobody declares, and
        regenerate the skill. Idempotent; a fresh machine runs it once."""
        console.print()
        configs = self.config_manager.load()
        self.data_dir.mkdir(parents=True, exist_ok=True)

        if not names:
            for entry in self.data_dir.iterdir():
                if entry == STATE_FILE or entry.name in configs:
                    continue
                _delete(entry)
                console.print(f"[red]✗[/red] {entry.name} pruned (not in catalogue)")

        if names:
            unknown = [n for n in names if n not in configs]
            if unknown:
                raise ValueError(f"Not in the catalogue: {', '.join(unknown)}")
            configs_to_update = {name: configs[name] for name in names}
        else:
            configs_to_update = configs

        if not configs_to_update:
            console.print("[yellow]No repositories to update[/yellow]")
            return

        pad = max(len(n) for n in configs_to_update)
        outcomes: dict[str, str] = {}
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
                outcome = self._update_one(name, config, progress, tasks[name], pad)
                with lock:
                    outcomes[name] = outcome

            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [
                    pool.submit(worker, name, config)
                    for name, config in configs_to_update.items()
                ]
                for future in as_completed(futures):
                    future.result()

        self.config_manager.save(configs)
        updated = sorted(n for n, o in outcomes.items() if o == "updated")
        failed = sorted(n for n, o in outcomes.items() if o == "failed")
        if updated:
            console.print(
                f"\n[bold]Updated:[/bold] [green]{', '.join(updated)}[/green]"
            )
        self.skill_manager.update(configs)
        if failed:
            raise RuntimeError(f"failed to update: {', '.join(failed)}")

    def status(self, names: list[str] | None = None, as_json: bool = False) -> bool:
        """Show status of repositories: table for humans, --json for tools.
        Returns False when a declared repo has no clone (run `dkb update`)."""
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
            return all((self.data_dir / name).exists() for name in configs)

        if not configs:
            console.print("[yellow]No repositories found[/yellow]")
            return True

        rows = []
        missing = []
        for name, config in sorted(configs.items()):
            if not (self.data_dir / name).exists():
                missing.append(name)
                age_str = "MISSING (dkb update)"
            elif config.last_updated:
                age = datetime.now(UTC) - config.last_updated
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

            rows.append(
                (
                    name,
                    config.effective_version or "-",
                    str(self.data_dir / name),
                    config.repository.display_name,
                    source_display,
                    age_str,
                )
            )

        # Piped output (agents, scripts): one greppable line per repo,
        # location included, no box drawing to mangle
        if not sys.stdout.isatty():
            widths = [max(len(row[i]) for row in rows) for i in range(5)]
            for row in rows:
                print(
                    "  ".join(
                        [*(row[i].ljust(widths[i]) for i in range(5)), row[5]]
                    ).rstrip()
                )
            return not missing

        console.print()
        table = Table(
            title="Knowledge Base Status",
            title_style="bold",
            caption=f"docs at {self.data_dir}/<name> · full config: dkb status --json",
        )
        table.add_column("Repository", style="cyan", overflow="fold")
        table.add_column("Version", style="green", overflow="fold")
        table.add_column("Docs", style="blue", overflow="fold")
        table.add_column("Source", style="dim", overflow="fold")
        table.add_column("Last Updated", style="yellow")

        for name, version, _, docs, source, age_str in rows:
            table.add_row(name, version, docs, source, age_str)

        console.print(table)
        return not missing

    def _get_remote_commit(self, url: str, branch: str | None) -> str:
        """Tip of the declared branch, or of the remote's HEAD (its default
        branch, whatever it is called) when none is declared."""
        ref = f"refs/heads/{branch}" if branch else "HEAD"
        result = subprocess.run(
            ["git", "ls-remote", url, ref],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        line = result.stdout.strip()
        if not line:
            raise ValueError(f"{url} has no {ref}")
        return line.split()[0]

    def _clone_repository(self, config: RepositoryConfig):
        """Clone a repository and copy contents to data dir."""
        repo_dir = self.data_dir / config.name
        # No declared branch: git clones the remote's default
        branch_args = ["--branch", config.branch] if config.branch else []

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
                        *branch_args,
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
                        *branch_args,
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

            config.last_updated = datetime.now(UTC)
            config.last_commit = commit


def _repo_url(url: str) -> str:
    """Canonical clone URL: the identity of a repo, whatever path or
    shorthand the user typed."""
    provider = provider_registry.get_provider(url)
    if not provider:
        raise ValueError(f"Unsupported repository URL: {url}")
    return provider.normalize_url(url)


def _refresh_metadata(repository: GitRepository):
    """Re-read description and latest release from the forge."""
    provider = provider_registry.get_provider(repository.url)
    metadata = provider.fetch_metadata(
        repository.url, repository.owner, repository.repo
    )
    repository.description = metadata["description"]
    repository.latest_version = metadata["latest_version"]


def _delete(path: Path):
    """Remove a cache entry: a symlink is unlinked, a tree is removed."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
