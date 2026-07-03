"""Repository providers: URL parsing and metadata fetching per forge type."""

import json
import subprocess
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

from dkb import VERSION

GITHUB_HOSTS = {"github.com", "www.github.com"}


class RepositoryProvider(ABC):
    """Abstract base for repository providers."""

    @abstractmethod
    def parse_url(self, url: str) -> Optional[Tuple[str, str, Optional[str]]]:
        """Parse URL and return (owner, repo, path) or None if invalid."""

    @abstractmethod
    def fetch_metadata(self, url: str, owner: str, repo: str) -> Dict[str, Any]:
        """Fetch repository metadata."""

    @abstractmethod
    def normalize_url(self, url: str) -> str:
        """Normalize URL to canonical clone form."""

    @abstractmethod
    def supports_url(self, url: str) -> bool:
        """Check if this provider supports the given URL."""


class GitHubProvider(RepositoryProvider):
    """GitHub repositories, including `owner/repo/path` shorthand."""

    def supports_url(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc in GITHUB_HOSTS:
            return True

        # Shorthand like "owner/repo" or "owner/repo/path"
        if not parsed.scheme and "/" in url:
            parts = url.split("/")
            return len(parts) >= 2

        return False

    def parse_url(self, url: str) -> Optional[Tuple[str, str, Optional[str]]]:
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

        if path.endswith(".git"):
            path = path[:-4]

        parts = path.split("/")
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]

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
        if not self.supports_url(url):
            return url

        parsed = self.parse_url(url)
        if not parsed:
            return url

        owner, repo, _ = parsed
        return f"https://github.com/{owner}/{repo}.git"

    def fetch_metadata(self, url: str, owner: str, repo: str) -> Dict[str, Any]:
        if self._has_gh_cli():
            try:
                return self._fetch_with_gh(owner, repo)
            except Exception:
                pass

        return self._fetch_with_api(owner, repo)

    def _has_gh_cli(self) -> bool:
        try:
            subprocess.run(["gh", "--version"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _fetch_with_gh(self, owner: str, repo: str) -> Dict[str, Any]:
        repo_data = subprocess.check_output(
            ["gh", "api", f"repos/{owner}/{repo}"], text=True
        )
        data = json.loads(repo_data)

        metadata = {
            "description": data.get("description", "No description available"),
            "default_branch": data.get("default_branch", "main"),
            "latest_version": None,
        }

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
        metadata = {
            "description": "No description available",
            "default_branch": "main",
            "latest_version": None,
        }

        data = _get_json(f"https://api.github.com/repos/{owner}/{repo}")
        if data:
            metadata["description"] = data.get(
                "description", "No description available"
            )
            metadata["default_branch"] = data.get("default_branch", "main")

        release = _get_json(
            f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        )
        if release:
            version = release.get("tag_name", "").lstrip("v")
            if version:
                metadata["latest_version"] = version

        return metadata


class GiteaProvider(RepositoryProvider):
    """Gitea/Forgejo forges (codeberg.org, self-hosted instances).

    Catch-all for full URLs on non-GitHub hosts: metadata comes from the
    /api/v1 REST API when the host speaks it, and cloning works on any
    plain git host regardless.
    """

    RESERVED_ROUTES = {
        "src",
        "raw",
        "commit",
        "commits",
        "releases",
        "tags",
        "branches",
        "issues",
        "pulls",
        "wiki",
        "actions",
        "activity",
        "compare",
        "settings",
        "projects",
        "packages",
        "forks",
    }

    def supports_url(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if parsed.netloc in GITHUB_HOSTS:
            return False
        parts = parsed.path.strip("/").split("/")
        return len(parts) >= 2 and all(parts[:2])

    def parse_url(self, url: str) -> Optional[Tuple[str, str, Optional[str]]]:
        if not self.supports_url(url):
            return None

        parsed = urllib.parse.urlparse(url)
        parts = parsed.path.strip("/").split("/")
        owner, repo = parts[0], parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]

        rest = parts[2:]
        if not rest:
            return owner, repo, None

        # Deep link: owner/repo/src/{branch|tag|commit}/{ref}/path...
        if (
            rest[0] == "src"
            and len(rest) >= 3
            and rest[1] in ("branch", "tag", "commit")
        ):
            subpath = "/".join(rest[3:])
            return owner, repo, subpath or None

        if rest[0] in self.RESERVED_ROUTES:
            return owner, repo, None

        # Direct path format: owner/repo/path...
        return owner, repo, "/".join(rest)

    def normalize_url(self, url: str) -> str:
        parsed = self.parse_url(url)
        if not parsed:
            return url

        owner, repo, _ = parsed
        host = urllib.parse.urlparse(url)
        return f"{host.scheme}://{host.netloc}/{owner}/{repo}.git"

    def fetch_metadata(self, url: str, owner: str, repo: str) -> Dict[str, Any]:
        host = urllib.parse.urlparse(url)
        api_base = f"{host.scheme}://{host.netloc}/api/v1/repos/{owner}/{repo}"

        metadata = {
            "description": "No description available",
            "default_branch": "main",
            "latest_version": None,
        }

        data = _get_json(api_base)
        if data is None:
            # Not a Gitea/Forgejo API host; cloning still works with defaults
            return metadata

        if data.get("description"):
            metadata["description"] = data["description"]
        metadata["default_branch"] = data.get("default_branch", "main")

        release = _get_json(f"{api_base}/releases/latest")
        version = (release or {}).get("tag_name", "")
        if not version:
            tags = _get_json(f"{api_base}/tags?limit=1")
            if tags:
                version = tags[0].get("name", "")
        if version:
            metadata["latest_version"] = version.lstrip("v")

        return metadata


def _get_json(url: str) -> Optional[Any]:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", f"dkb/{VERSION}")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode())
    except Exception:
        return None


class ProviderRegistry:
    """Registry for repository providers. Order matters: GitHub owns
    github.com URLs and schemeless shorthand, Gitea catches remaining
    full URLs."""

    def __init__(self):
        self.providers = [
            GitHubProvider(),
            GiteaProvider(),
        ]

    def get_provider(self, url: str) -> Optional[RepositoryProvider]:
        for provider in self.providers:
            if provider.supports_url(url):
                return provider
        return None


provider_registry = ProviderRegistry()
