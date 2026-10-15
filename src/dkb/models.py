"""Repository data models. Immutable: every step returns a new value.

A catalogue entry is one of three kinds:

  remote, following   {"url", "paths"?, "branch"?, "version_url"?}
      a clone, re-fetched when upstream moves; version = latest release
  remote, pinned      {"url", "paths"?, "pin"}
      a clone of one tag or commit, never re-fetched; version = the pin
  local               {"local": "~/Developer/lib"}
      a symlink to a working copy, always current; version = its latest tag
"""

import re
import subprocess
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from dkb.providers import RepositoryProvider, provider_registry, release_version


@dataclass(frozen=True)
class GitRepository:
    """A repository on a forge: canonical clone URL plus fetched metadata."""

    url: str
    owner: str
    repo: str
    provider: RepositoryProvider = field(repr=False, compare=False)
    description: str | None = None
    latest_version: str | None = None

    @classmethod
    def parse(
        cls,
        url: str,
        description: str | None = None,
        latest_version: str | None = None,
    ) -> "GitRepository":
        """From any accepted URL form, without touching the network."""
        provider, owner, repo, _ = provider_registry.resolve(url)
        return cls(
            url=provider.normalize_url(url),
            owner=owner,
            repo=repo,
            provider=provider,
            description=description,
            latest_version=latest_version,
        )

    def refreshed(self) -> "GitRepository":
        """Description and latest release as the forge reports them now."""
        metadata = self.provider.fetch_metadata(self.url, self.owner, self.repo)
        return replace(
            self,
            description=metadata["description"],
            latest_version=metadata["latest_version"],
        )

    @property
    def display_name(self) -> str:
        return f"{self.owner}/{self.repo}"


REMOTE_KEYS = {"url", "paths", "branch", "pin", "version_url"}
LOCAL_KEYS = {"local"}

# The declared keys that decide what lands on disk; version_url does not
CONTENT_KEYS = ("url", "paths", "branch", "pin", "local")

COMMIT = re.compile(r"[0-9a-f]{40}")


def check_declaration(name: str, declared: dict[str, Any]):
    """The catalogue is hand-editable: say exactly what is wrong with an entry."""
    if ("url" in declared) == ("local" in declared):
        raise ValueError(f"'{name}': needs exactly one of 'url' or 'local'")
    allowed = LOCAL_KEYS if "local" in declared else REMOTE_KEYS
    unknown = set(declared) - allowed
    if unknown:
        raise ValueError(
            f"'{name}': {sorted(unknown)} not allowed here; "
            f"a {'local' if 'local' in declared else 'remote'} entry takes "
            f"{sorted(allowed)}"
        )
    if "pin" in declared and "branch" in declared:
        raise ValueError(
            f"'{name}': 'pin' freezes a ref, 'branch' follows one; pick one"
        )
    if "pin" in declared and "version_url" in declared:
        raise ValueError(
            f"'{name}': a pinned entry's version is its pin; drop 'version_url'"
        )


@dataclass(frozen=True)
class RepositoryConfig:
    """One catalogue entry plus what the cache knows about it."""

    name: str
    repository: GitRepository | None = None
    paths: tuple[str, ...] = ()
    branch: str | None = None
    pin: str | None = None
    version_source: GitRepository | None = None
    # As declared ("~/..."), so one catalogue serves every machine
    local: str | None = None
    local_version: str | None = None

    # When the docs on disk last changed, and what they were made from
    last_updated: datetime | None = None
    last_commit: str | None = None
    fetched: dict[str, Any] | None = None
    # When upstream was last asked, changed or not
    last_checked: datetime | None = None

    def __post_init__(self):
        assert (self.repository is None) == (self.local is not None), self.name
        check_declaration(self.name, self.declaration())

    @property
    def local_path(self) -> Path:
        assert self.local
        return Path(self.local).expanduser()

    @property
    def ref(self) -> str:
        """What to fetch: the pin, the followed branch, or the remote's HEAD."""
        return self.pin or self.branch or "HEAD"

    @property
    def effective_version(self) -> str | None:
        if self.local:
            return self.local_version
        if self.pin:
            return (
                self.pin[:12]
                if COMMIT.fullmatch(self.pin)
                else release_version(self.pin)
            )
        if self.version_source:
            return self.version_source.latest_version
        return self.repository.latest_version

    @property
    def description(self) -> str | None:
        if self.local:
            return f"local checkout of {self.local}, always current"
        return self.repository.description

    @property
    def source(self) -> str:
        """Where the docs come from, as a person would write it."""
        if self.local:
            return self.local
        ref = self.ref[:12] if COMMIT.fullmatch(self.ref) else self.ref
        at = f"@{ref}" if ref != "HEAD" else ""
        return f"{self.repository.display_name}{at}"

    def content(self) -> dict[str, Any]:
        declared = self.declaration()
        return {k: declared.get(k) for k in CONTENT_KEYS}

    def is_current(self, remote_commit: str | None) -> bool:
        """The clone on disk matches the declaration (a catalogue edited
        elsewhere changes paths without moving the commit) and, unless
        pinned, upstream."""
        if self.fetched != self.content():
            return False
        return self.pin is not None or remote_commit == self.last_commit

    def refreshed(self) -> "RepositoryConfig":
        if self.local:
            return replace(self, local_version=_latest_tag(self.local_path))
        return replace(
            self,
            repository=self.repository.refreshed(),
            version_source=self.version_source and self.version_source.refreshed(),
        )

    def checked(self, now: datetime) -> "RepositoryConfig":
        return replace(self, last_checked=now)

    def cloned(self, commit: str | None, now: datetime) -> "RepositoryConfig":
        result = replace(
            self,
            last_updated=now,
            last_commit=commit,
            fetched=self.content(),
            last_checked=now,
        )
        assert result.is_current(commit)
        return result

    def declaration(self) -> dict[str, Any]:
        """The catalogue entry: what to cache, nothing fetched."""
        if self.local:
            return {"local": self.local}
        data: dict[str, Any] = {"url": self.repository.url, "paths": list(self.paths)}
        if self.branch:
            data["branch"] = self.branch
        if self.pin:
            data["pin"] = self.pin
        if self.version_source:
            data["version_url"] = self.version_source.url
        return data

    def state(self) -> dict[str, Any]:
        """What was fetched: rewritten on every update, never tracked."""
        data: dict[str, Any] = {}
        if self.repository and self.repository.description:
            data["description"] = self.repository.description
        if self.effective_version:
            data["version"] = self.effective_version
        if self.last_updated:
            data["last_updated"] = self.last_updated.isoformat()
        if self.last_checked:
            data["last_checked"] = self.last_checked.isoformat()
        if self.last_commit:
            data["commit"] = self.last_commit
        if self.fetched:
            data["fetched"] = self.fetched
        return data

    def to_dict(self) -> dict[str, Any]:
        """Declaration + state, the shape `dkb status --json` prints."""
        return {**self.declaration(), **self.state()}

    @classmethod
    def from_dict(
        cls, name: str, declared: dict[str, Any], state: dict[str, Any]
    ) -> "RepositoryConfig":
        """Rebuild from the two stored halves WITHOUT fetching metadata."""
        check_declaration(name, declared)
        times = {
            "last_updated": _when(state.get("last_updated")),
            "last_commit": state.get("commit"),
            "fetched": state.get("fetched"),
            "last_checked": _when(state.get("last_checked")),
        }

        if "local" in declared:
            return cls(
                name=name,
                local=declared["local"],
                local_version=state.get("version"),
                **times,
            )

        repository = GitRepository.parse(
            declared["url"],
            description=state.get("description"),
            latest_version=state.get("version"),
        )

        version_source = None
        if "version_url" in declared:
            version_source = GitRepository.parse(
                declared["version_url"], latest_version=state.get("version")
            )
            if version_source.url == repository.url:
                version_source = None

        return cls(
            name=name,
            repository=repository,
            paths=tuple(declared.get("paths", [])),
            branch=declared.get("branch"),
            pin=declared.get("pin"),
            version_source=version_source,
            **times,
        )


def _latest_tag(path: Path) -> str | None:
    """The newest tag reachable from a local checkout's HEAD; None for an
    untagged repo or a plain directory."""
    result = subprocess.run(
        ["git", "-C", str(path), "describe", "--tags", "--abbrev=0"],
        capture_output=True,
        text=True,
        check=False,
    )
    return release_version(result.stdout.strip()) if result.returncode == 0 else None


def _when(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp) if stamp else None
