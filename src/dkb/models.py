"""Repository data models. Immutable: every step returns a new value."""

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from dkb.providers import RepositoryProvider, provider_registry


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


DECLARATION_KEYS = {"url", "paths", "branch", "version_url"}

# The declared keys that decide what lands on disk; version_url does not
CONTENT_KEYS = ("url", "paths", "branch")


@dataclass(frozen=True)
class RepositoryConfig:
    """One catalogue entry plus what the cache knows about it."""

    name: str
    repository: GitRepository
    paths: tuple[str, ...] = ()
    branch: str | None = None
    version_source: GitRepository | None = None

    # When the docs on disk last changed, and what they were made from
    last_updated: datetime | None = None
    last_commit: str | None = None
    fetched: dict[str, Any] | None = None
    # When upstream was last asked, changed or not
    last_checked: datetime | None = None

    @property
    def effective_version(self) -> str | None:
        if self.version_source:
            return self.version_source.latest_version
        return self.repository.latest_version

    def content(self) -> dict[str, Any]:
        declared = self.declaration()
        return {k: declared.get(k) for k in CONTENT_KEYS}

    def is_current(self, remote_commit: str) -> bool:
        """The clone on disk matches both upstream and the declaration (a
        catalogue edited elsewhere changes paths without moving the commit)."""
        return remote_commit == self.last_commit and self.fetched == self.content()

    def refreshed(self) -> "RepositoryConfig":
        return replace(
            self,
            repository=self.repository.refreshed(),
            version_source=self.version_source and self.version_source.refreshed(),
        )

    def checked(self, now: datetime) -> "RepositoryConfig":
        return replace(self, last_checked=now)

    def cloned(self, commit: str, now: datetime) -> "RepositoryConfig":
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
        data: dict[str, Any] = {"url": self.repository.url, "paths": list(self.paths)}
        if self.branch:
            data["branch"] = self.branch
        if self.version_source:
            data["version_url"] = self.version_source.url
        return data

    def state(self) -> dict[str, Any]:
        """What was fetched: rewritten on every update, never tracked."""
        data: dict[str, Any] = {}
        if self.repository.description:
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
        unknown = set(declared) - DECLARATION_KEYS
        if unknown:
            raise ValueError(
                f"'{name}': unknown key(s) {sorted(unknown)} in the catalogue; "
                f"allowed: {sorted(DECLARATION_KEYS)}"
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
            version_source=version_source,
            last_updated=_when(state.get("last_updated")),
            last_commit=state.get("commit"),
            fetched=state.get("fetched"),
            last_checked=_when(state.get("last_checked")),
        )


def _when(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp) if stamp else None
