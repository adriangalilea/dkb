"""Repository data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dkb.providers import RepositoryProvider, provider_registry


@dataclass
class GitRepository:
    """A repository on a forge: canonical clone URL plus fetched metadata."""

    url: str
    owner: str
    repo: str
    provider: RepositoryProvider = field(repr=False)
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

    def refresh(self):
        """Re-read description and latest release from the forge."""
        metadata = self.provider.fetch_metadata(self.url, self.owner, self.repo)
        self.description = metadata["description"]
        self.latest_version = metadata["latest_version"]

    @property
    def display_name(self) -> str:
        return f"{self.owner}/{self.repo}"


DECLARATION_KEYS = {"url", "paths", "branch", "version_url"}

# The declared keys that decide what lands on disk; version_url does not
CONTENT_KEYS = ("url", "paths", "branch")


@dataclass
class RepositoryConfig:
    """Configuration for a repository in dkb."""

    name: str
    repository: GitRepository
    paths: list[str] = field(default_factory=list)
    branch: str | None = None
    version_source: GitRepository | None = None

    # What the clone on disk was made from
    last_updated: datetime | None = None
    last_commit: str | None = None
    fetched: dict[str, Any] | None = None

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

    def declaration(self) -> dict[str, Any]:
        """The catalogue entry: what to cache, nothing fetched."""
        data: dict[str, Any] = {"url": self.repository.url, "paths": self.paths}
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
            paths=declared.get("paths", []),
            branch=declared.get("branch"),
            version_source=version_source,
            last_updated=(
                datetime.fromisoformat(state["last_updated"])
                if "last_updated" in state
                else None
            ),
            last_commit=state.get("commit"),
            fetched=state.get("fetched"),
        )
