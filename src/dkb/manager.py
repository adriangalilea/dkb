"""Repository operations: add, remove, update, status, clone."""

import difflib
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from dkb import console
from dkb.config import STATE_FILE, ConfigManager
from dkb.models import COMMIT, GitRepository, RepositoryConfig
from dkb.providers import provider_registry
from dkb.skill import SkillManager

# Clones are assembled here, inside the data dir, then renamed into place:
# a failed fetch never leaves a repo half-copied or deleted
STAGING_SUFFIX = ".partial"


class RepositoryManager:
    """Manages repository operations."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.config_manager = ConfigManager()
        self.skill_manager = SkillManager(data_dir)

    def add(
        self,
        source: str,
        branch: str | None = None,
        pin: str | None = None,
        version_url: str | None = None,
        name: str | None = None,
    ):
        """Add a catalogue entry: a remote repo (following or pinned) or a
        local directory, then materialize it."""
        if source.startswith(("/", "~", ".")):
            declared = _local_entry(source, name, branch, pin, version_url)
        else:
            declared = _remote_entry(source, name, branch, pin, version_url)

        configs = self.config_manager.load()
        if declared.name in configs:
            raise ValueError(
                f"'{declared.name}' is already cached "
                f"({configs[declared.name].source}); pick another with --name, "
                "or `dkb remove` it first"
            )

        with console.status(f"Fetching [cyan]{declared.name}[/cyan]..."):
            _, config = self._sync(declared)

        self.config_manager.add(config)
        self.skill_manager.update(self.config_manager.load())

        console.print(
            f"[green]✓[/green] [cyan]{config.name}[/cyan] "
            f"{config.effective_version or ''} → {self.data_dir / config.name}"
        )

    def _sync(self, config: RepositoryConfig) -> tuple[str, RepositoryConfig]:
        """Bring one entry's cache in line with its declaration: 'updated' or
        'unchanged', plus its new config. Raises on failure, leaving the old
        cache entry as it was. Metadata refreshes on every call, since a
        release can land without the docs changing."""
        now = datetime.now(UTC)
        target = self.data_dir / config.name

        if config.local:
            path = config.local_path
            if not path.is_dir():
                raise ValueError(f"{config.local} is not a directory on this machine")
            refreshed = config.refreshed()
            linked = target.is_symlink() and target.resolve() == path.resolve()
            if linked and refreshed.is_current(None):
                return "unchanged", refreshed.checked(now)
            _delete(target)
            target.symlink_to(path)
            return "updated", refreshed.cloned(None, now)

        # A pin never moves: nothing to ask upstream once it is on disk
        if config.pin and target.exists() and config.is_current(None):
            return "unchanged", config.checked(now)

        remote_commit = (
            None
            if config.pin
            else self._get_remote_commit(config.repository.url, config.branch)
        )
        refreshed = config.refreshed()
        if target.exists() and refreshed.is_current(remote_commit):
            return "unchanged", refreshed.checked(now)
        return "updated", refreshed.cloned(self._clone_repository(refreshed), now)

    def remove(self, names: list[str]):
        """Remove repositories."""
        configs = self.config_manager.load()
        _require_known(names, configs)

        for name in names:
            self.config_manager.remove(name)
            _delete(self.data_dir / name)
            console.print(f"[red]✗[/red] {name} removed")

        self.skill_manager.update(self.config_manager.load())

    def _update_one(
        self,
        name: str,
        config: RepositoryConfig,
        progress: Progress,
        task_id,
        pad: int,
    ) -> RepositoryConfig | None:
        """Worker for parallel update: the repo's new config, or None when it
        failed (its old config and cache entry stay as they were)."""
        padded = name.ljust(pad)
        try:
            progress.update(task_id, description=f"[cyan]{padded}[/cyan] syncing...")
            outcome, synced = self._sync(config)
            line = (
                f"[green]✓ {padded} updated[/green]"
                if outcome == "updated"
                else f"[dim]- {padded} unchanged[/dim]"
            )
            progress.update(task_id, description=line, completed=1)
            return synced
        except Exception as e:  # noqa: BLE001 - one repo failing must not stop the rest
            progress.update(
                task_id,
                description=f"[red]✗ {padded} {escape(explain(e))}[/red]",
                completed=1,
            )
            return None

    def update(self, names: list[str] | None = None):
        """Reconcile the cache with the catalogue: fetch declared repos that are
        missing or changed upstream, prune clones nobody declares, and
        regenerate the skill. Idempotent; a fresh machine runs it once."""
        configs = self.config_manager.load()
        self.data_dir.mkdir(parents=True, exist_ok=True)

        if names:
            _require_known(names, configs)
            configs_to_update = {name: configs[name] for name in names}
        else:
            configs_to_update = configs
            for entry in self.data_dir.iterdir():
                if entry == STATE_FILE or entry.name in configs:
                    continue
                _delete(entry)
                console.print(f"[red]✗[/red] {entry.name} pruned (not in catalogue)")

        if not configs_to_update:
            console.print(
                "[yellow]The catalogue is empty.[/yellow] Add docs with "
                "`dkb add owner/repo/path`"
            )
            return

        pad = max(len(n) for n in configs_to_update)

        progress = Progress(
            SpinnerColumn(finished_text=" "),
            TextColumn("{task.description}"),
            console=console,
        )

        with progress:
            tasks = {
                name: progress.add_task(
                    f"[cyan]{name.ljust(pad)}[/cyan] waiting...", total=1
                )
                for name in configs_to_update
            }

            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {
                    name: pool.submit(
                        self._update_one, name, config, progress, tasks[name], pad
                    )
                    for name, config in configs_to_update.items()
                }
                results = {name: future.result() for name, future in futures.items()}

        # A failed repo keeps its previous config: its clone was not touched
        merged = {name: results.get(name) or config for name, config in configs.items()}
        self.config_manager.save(merged)
        self.skill_manager.update(merged)

        failed = sorted(name for name, result in results.items() if result is None)
        if failed:
            raise RuntimeError(f"failed to update: {', '.join(failed)}")

    def status(self, names: list[str] | None = None, as_json: bool = False) -> bool:
        """Show status of repositories: table for humans, --json for tools.
        Returns False when a declared repo has no clone (run `dkb update`)."""
        configs = self.config_manager.load()

        if names:
            _require_known(names, configs)
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
            console.print(
                "[yellow]Nothing cached yet.[/yellow] Add docs with "
                "`dkb add owner/repo/path` (see `dkb add -h`)"
            )
            return True

        header = (
            "NAME",
            "VERSION",
            "LOCATION",
            "SOURCE",
            "VERSION FROM",
            "DOCS CHANGED",
            "CHECKED",
        )
        rows = []
        missing = []
        for name, config in sorted(configs.items()):
            present = (self.data_dir / name).exists()
            if not present:
                missing.append(name)
            rows.append(
                (
                    name,
                    config.effective_version or "-",
                    str(self.data_dir / name),
                    config.source,
                    config.version_source.display_name
                    if config.version_source
                    else "-",
                    ("live" if config.local else _age(config.last_updated))
                    if present
                    else "MISSING",
                    _age(config.last_checked) if present else "run dkb update",
                )
            )

        # Piped output (agents, scripts): one greppable line per repo,
        # location included, no box drawing to mangle
        if not sys.stdout.isatty():
            last = len(header) - 1
            widths = [max(len(row[i]) for row in [header, *rows]) for i in range(last)]
            for row in [header, *rows]:
                cells = [row[i].ljust(widths[i]) for i in range(last)]
                print("  ".join([*cells, row[last]]).rstrip())
            return not missing

        table = Table(
            caption=f"docs at {self.data_dir}/<name> · full config: dkb status --json",
        )
        table.add_column("Name", style="cyan", overflow="fold")
        table.add_column("Version", style="green", overflow="fold")
        table.add_column("Source", style="blue", overflow="fold")
        table.add_column("Version from", style="dim", overflow="fold")
        table.add_column("Docs changed", style="yellow")
        table.add_column("Checked", style="dim")

        for name, version, _, *rest in rows:
            table.add_row(name, version, *rest)

        console.print(table)
        return not missing

    def _get_remote_commit(self, url: str, branch: str | None) -> str:
        """Tip of the declared branch, or of the remote's HEAD (its default
        branch, whatever it is called) when none is declared."""
        ref = f"refs/heads/{branch}" if branch else "HEAD"
        line = _git("ls-remote", url, ref).strip()
        if not line:
            raise ValueError(f"{url} has no branch '{branch}'")
        return line.split()[0]

    def _clone_repository(self, config: RepositoryConfig) -> str:
        """Fetch the declared content into a staging dir beside the cache
        entry, then swap it in; returns the commit it was made from. The
        old clone survives any failure.

        One shape for every remote kind: fetch exactly one ref (a pinned tag
        or commit, the followed branch, or the remote's HEAD), shallow and
        blobless, then check out only the declared paths."""
        target = self.data_dir / config.name
        staging = self.data_dir / f"{config.name}{STAGING_SUFFIX}"
        _delete(staging)

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "repo"
            _git("init", "--quiet", str(repo_path))
            _git("remote", "add", "origin", config.repository.url, cwd=repo_path)
            _git(
                "fetch",
                "--quiet",
                "--depth=1",
                "--filter=blob:none",
                "origin",
                config.ref,
                cwd=repo_path,
            )
            if config.paths:
                _git("sparse-checkout", "set", *config.paths, cwd=repo_path)
            _git(
                "-c",
                "advice.detachedHead=false",
                "switch",
                "--quiet",
                "--detach",
                "FETCH_HEAD",
                cwd=repo_path,
            )

            commit = _git("rev-parse", "HEAD", cwd=repo_path).strip()
            assert (
                config.pin is None
                or not COMMIT.fullmatch(config.pin)
                or (commit == config.pin)
            ), f"pinned {config.pin}, fetched {commit}"

            # A declared path's contents land at the top level; a whole repo
            # is one implicit path, minus .git
            sources = []
            for path in config.paths or [""]:
                src = repo_path / path
                if not src.exists():
                    raise ValueError(_missing_path(repo_path, path))
                sources.extend(
                    [src]
                    if src.is_file()
                    else [p for p in src.iterdir() if p.name != ".git"]
                )

            staging.mkdir(parents=True)
            for src in sources:
                dest = staging / src.name
                if dest.exists():
                    raise ValueError(
                        f"'{src.name}' exists in more than one declared path "
                        f"({', '.join(config.paths)}): cache them as separate repos"
                    )
                if src.is_dir():
                    shutil.copytree(src, dest)
                else:
                    shutil.copy2(src, dest)

        _delete(target)
        staging.rename(target)
        assert target.is_dir() and not staging.exists()
        return commit


def _remote_entry(
    url: str,
    name: str | None,
    branch: str | None,
    pin: str | None,
    version_url: str | None,
) -> RepositoryConfig:
    _, _, repo, path = provider_registry.resolve(url)
    repository = GitRepository.parse(url)

    version_source = None
    if version_url:
        version_source = GitRepository.parse(version_url)
        # The docs repo already carries its own releases
        if version_source.url == repository.url:
            version_source = None

    return RepositoryConfig(
        # Explicit name wins, then the version source repo, then the docs repo
        name=name or (version_source.repo if version_source else repo),
        repository=repository,
        paths=(path,) if path else (),
        branch=branch,
        pin=pin,
        version_source=version_source,
    )


def _local_entry(
    source: str,
    name: str | None,
    branch: str | None,
    pin: str | None,
    version_url: str | None,
) -> RepositoryConfig:
    if branch or pin or version_url:
        raise ValueError(
            "a local entry is your working copy as it is: "
            "--branch, --pin and --version-url do not apply"
        )
    path = Path(source).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"{source} is not a directory")
    # Stored relative to home, so the catalogue reads the same on every machine
    home = Path.home()
    local = f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)
    return RepositoryConfig(name=name or path.name, local=local)


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    ).stdout


def _missing_path(repo_path: Path, path: str) -> str:
    """Name what IS there at the deepest existing ancestor of the missing
    path. Read from git's tree, not the disk: a sparse checkout only
    materializes the files along the declared path."""
    parent = Path(path).parent
    while True:
        spec = "HEAD" if parent == Path(".") else f"HEAD:{parent.as_posix()}"
        listing = subprocess.run(
            ["git", "ls-tree", spec],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if listing.returncode == 0:
            break
        parent = parent.parent
    # ls-tree lines: "<mode> <type> <sha>\t<name>"; dirs first, they are
    # where docs live
    entries = sorted(
        (
            name + ("/" if meta.split()[1] == "tree" else "")
            for meta, name in (
                line.split("\t", 1) for line in listing.stdout.splitlines()
            )
        ),
        key=lambda entry: (not entry.endswith("/"), entry),
    )
    where = "the repo root" if parent == Path(".") else f"'{parent.as_posix()}/'"
    return f"path '{path}' not found; {where} has: {', '.join(entries) or 'nothing'}"


def explain(e: Exception) -> str:
    """One line a person can act on: git's own message, not its argv."""
    if isinstance(e, subprocess.CalledProcessError) and e.stderr:
        return " ".join(e.stderr.split())
    return str(e)


def _require_known(names: list[str], configs: dict[str, RepositoryConfig]):
    unknown = [n for n in names if n not in configs]
    if not unknown:
        return
    hints = []
    for n in unknown:
        close = difflib.get_close_matches(n, configs, n=1)
        hints.append(f"'{n}'" + (f" (did you mean '{close[0]}'?)" if close else ""))
    raise ValueError(f"not in the catalogue: {', '.join(hints)}; `dkb status` lists it")


def _age(when: datetime | None) -> str:
    if when is None:
        return "never"
    seconds = (datetime.now(UTC) - when).total_seconds()
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    return f"{int(seconds / 86400)}d ago"


def _delete(path: Path):
    """Remove a cache entry: a symlink is unlinked, a tree is removed."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
