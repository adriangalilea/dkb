"""Repository data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dkb.providers import provider_registry


@dataclass
class GitRepository:
    """Represents a git repository with all its metadata."""

    url: str
    provider: str
    owner: str
    repo: str
    description: str = "No description available"
    default_branch: str = "main"
    latest_version: str | None = None

    @classmethod
    def from_url(cls, url: str) -> "GitRepository":
        """Create GitRepository from URL by fetching metadata."""
        provider = provider_registry.get_provider(url)
        if not provider:
            raise ValueError(f"Unsupported repository URL: {url}")

        normalized_url = provider.normalize_url(url)

        parsed = provider.parse_url(url)  # Use original URL for path parsing
        if not parsed:
            raise ValueError(f"Invalid repository URL: {url}")

        owner, repo, _ = parsed

        metadata = provider.fetch_metadata(normalized_url, owner, repo)

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


DECLARATION_KEYS = {"url", "paths", "branch", "version_url"}


@dataclass
class RepositoryConfig:
    """Configuration for a repository in dkb."""

    name: str
    repository: GitRepository
    paths: list[str] = field(default_factory=list)
    branch: str | None = None
    version_source: GitRepository | None = None

    # Runtime metadata
    last_updated: datetime | None = None
    last_commit: str | None = None

    @property
    def effective_branch(self) -> str:
        """Get the branch to use (specified or default)."""
        return self.branch or self.repository.default_branch

    @property
    def effective_version(self) -> str | None:
        """Get the version to display."""
        if self.version_source:
            return self.version_source.latest_version
        return self.repository.latest_version

    @property
    def effective_description(self) -> str:
        """Get the description to display."""
        return self.repository.description

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
        data: dict[str, Any] = {"description": self.repository.description}
        if self.effective_version:
            data["version"] = self.effective_version
        if self.last_updated:
            data["last_updated"] = self.last_updated.isoformat()
        if self.last_commit:
            data["commit"] = self.last_commit
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

        repository = _repository_from_stored(
            declared["url"],
            description=state.get("description", "No description available"),
            default_branch=declared.get("branch") or "main",
            latest_version=state.get("version"),
        )

        version_source = None
        if "version_url" in declared and declared["version_url"] != declared["url"]:
            version_source = _repository_from_stored(
                declared["version_url"],
                description="Version source",
                default_branch="main",
                latest_version=state.get("version"),
            )

        config = cls(
            name=name,
            repository=repository,
            paths=declared.get("paths", []),
            branch=declared.get("branch"),
            version_source=version_source,
        )

        if "last_updated" in state:
            config.last_updated = datetime.fromisoformat(state["last_updated"])

        if "commit" in state:
            config.last_commit = state["commit"]

        return config


def _repository_from_stored(
    url: str,
    description: str,
    default_branch: str,
    latest_version: str | None,
) -> GitRepository:
    """Build a GitRepository from stored data, parsing but never fetching."""
    provider = provider_registry.get_provider(url)
    owner, repo, provider_name = "unknown", "unknown", "unknown"
    if provider:
        parsed = provider.parse_url(url)
        if parsed:
            owner, repo, _ = parsed
        provider_name = provider.__class__.__name__.replace("Provider", "").lower()

    return GitRepository(
        url=url,
        provider=provider_name,
        owner=owner,
        repo=repo,
        description=description,
        default_branch=default_branch,
        latest_version=latest_version,
    )
