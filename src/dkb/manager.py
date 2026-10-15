"""Repository operations: declare, add, remove, prune, update, clone.

Work only: nothing here prints. Each operation returns what happened and
`view` draws it; long operations take a callback to report progress.
"""

import difflib
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from py_utils import SourcedError

from dkb.config import STATE_FILE, ConfigManager, write_atomic
from dkb.models import COMMIT, GitRepository, RepositoryConfig
from dkb.providers import provider_registry
from dkb.skill import SkillManager

# Clones are assembled here, inside the data dir, then renamed into place:
# a failed fetch never leaves a repo half-copied or deleted
STAGING_SUFFIX = ".partial"

# ripgrep, and so Claude Code's Grep and Glob, honours every .gitignore
# above a path: a dotfiles repo at ~ that ignores "*" hides the whole cache
# and every search silently finds nothing. An .ignore file outranks
# .gitignore, so the cache re-includes itself, minus dkb's bookkeeping. A
# local entry's own .gitignore is deeper and still applies (.build stays out).
SEARCH_IGNORE = f"!*\n{STATE_FILE.name}\n*{STAGING_SUFFIX}\n"
IGNORE_FILE = ".ignore"

# report(name, state, detail): state is syncing, updated, unchanged or failed;
# detail is the version reached, or the exception a failed repo raised
Report = Callable[[str, str, str | Exception], None]


class RepositoryManager:
    """Manages repository operations."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.config_manager = ConfigManager()
        self.skill_manager = SkillManager(data_dir)

    def known(self, names: list[str]) -> dict[str, RepositoryConfig]:
        """The catalogue, or only `names` from it (a typo suggests the closest)."""
        configs = self.config_manager.load()
        if not names:
            return configs
        _require_known(names, configs)
        return {name: configs[name] for name in names}

    def declare(
        self,
        source: str,
        branch: str | None = None,
        pin: str | None = None,
        version_url: str | None = None,
        name: str | None = None,
    ) -> RepositoryConfig:
        """The entry `dkb add` would create, validated, before any fetch: a
        remote repo (following or pinned) or a local directory."""
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
        return declared

    def add(self, declared: RepositoryConfig) -> RepositoryConfig:
        """Materialize a declared entry, then record it in the catalogue."""
        self._prepare_data_dir()
        _, config = self._sync(declared)
        self.config_manager.add(config)
        self.skill_manager.update(self.config_manager.load())
        return config

    def remove(self, names: list[str]):
        _require_known(names, self.config_manager.load())
        for name in names:
            self.config_manager.remove(name)
            _delete(self.data_dir / name)
        self.skill_manager.update(self.config_manager.load())

    def prune(self) -> list[str]:
        """Delete cache entries the catalogue no longer declares."""
        self._prepare_data_dir()
        configs = self.config_manager.load()
        own = {STATE_FILE.name, IGNORE_FILE}
        stray = sorted(
            entry
            for entry in self.data_dir.iterdir()
            if entry.name not in own and entry.name not in configs
        )
        for entry in stray:
            _delete(entry)
        return [entry.name for entry in stray]

    def update(self, names: list[str], report: Report) -> list[str]:
        """Reconcile the cache with the catalogue (all of it, or `names`) in
        parallel, save, regenerate the skill. Returns the names that failed;
        a failed repo keeps its previous config and cache entry."""
        configs = self.config_manager.load()
        targets = self.known(names)
        self._prepare_data_dir()

        def one(name: str, config: RepositoryConfig) -> RepositoryConfig | None:
            report(name, "syncing", "")
            try:
                outcome, synced = self._sync(config)
            except Exception as e:  # noqa: BLE001 - one repo failing must not stop the rest
                report(name, "failed", e)
                return None
            report(name, outcome, synced.effective_version or "")
            return synced

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {name: pool.submit(one, name, c) for name, c in targets.items()}
            results = {name: future.result() for name, future in futures.items()}

        merged = {name: results.get(name) or config for name, config in configs.items()}
        self.config_manager.save(merged)
        self.skill_manager.update(merged)
        return sorted(name for name, result in results.items() if result is None)

    def missing(self, configs: dict[str, RepositoryConfig]) -> list[str]:
        """Declared entries with nothing on disk (run `dkb update`)."""
        return [name for name in configs if not (self.data_dir / name).exists()]

    def _prepare_data_dir(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        ignore = self.data_dir / IGNORE_FILE
        if not ignore.exists() or ignore.read_text() != SEARCH_IGNORE:
            write_atomic(ignore, SEARCH_IGNORE)

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
    """git's stdout. A failure is a SourcedError carrying git's own message
    (`[git:fetch] fatal: couldn't find remote ref v9`), never its argv, so
    every layer above can print it as it is."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if result.returncode != 0:
        raise SourcedError(
            source="git",
            operation=args[0],
            message=" ".join(result.stderr.split()) or f"exit {result.returncode}",
            status=result.returncode,
        )
    return result.stdout


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


def _require_known(names: list[str], configs: dict[str, RepositoryConfig]):
    unknown = [n for n in names if n not in configs]
    if not unknown:
        return
    hints = []
    for n in unknown:
        close = difflib.get_close_matches(n, configs, n=1)
        hints.append(f"'{n}'" + (f" (did you mean '{close[0]}'?)" if close else ""))
    raise ValueError(f"not in the catalogue: {', '.join(hints)}; `dkb status` lists it")


def _delete(path: Path):
    """Remove a cache entry: a symlink is unlinked, a tree is removed."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
