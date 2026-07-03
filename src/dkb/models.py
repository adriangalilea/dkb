"""Repository data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

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
    latest_version: Optional[str] = None

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
        repository = _repository_from_stored(
            data["url"],
            description=data.get("description", "No description available"),
            default_branch=data.get("branch") or "main",
            latest_version=data.get("version"),
        )

        version_source = None
        if "version_url" in data and data["version_url"] != data["url"]:
            version_source = _repository_from_stored(
                data["version_url"],
                description="Version source",
                default_branch="main",
                latest_version=data.get("version"),
            )

        config = cls(
            name=name,
            repository=repository,
            paths=data.get("paths", []),
            branch=data.get("branch"),
            version_source=version_source,
        )

        if "last_updated" in data:
            config.last_updated = datetime.fromisoformat(data["last_updated"])

        if "commit" in data:
            config.last_commit = data["commit"]

        return config


def _repository_from_stored(
    url: str,
    description: str,
    default_branch: str,
    latest_version: Optional[str],
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
