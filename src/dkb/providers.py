"""Repository providers: URL parsing and metadata fetching per forge type.

Metadata is description + latest release. "The repo has no release" is
None; "could not ask" raises, so a network hiccup never erases a version.
"""

import functools
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from dkb import VERSION

GITHUB_HOSTS = {"github.com", "www.github.com"}
GH = shutil.which("gh")


@functools.cache
def _gh_ready() -> bool:
    """gh installed AND logged in; otherwise the public REST API (60
    requests/hour unauthenticated) is the way in."""
    if not GH:
        return False
    # `gh auth token` is no test: it prints a keychain token even when gh
    # considers itself logged out and every `gh api` call fails
    auth = subprocess.run([GH, "auth", "status"], capture_output=True, check=False)
    return auth.returncode == 0


URL_FORMS = """accepted forms:
  owner/repo[/path]                              GitHub shorthand
  https://github.com/owner/repo[/tree/<ref>]/path
  https://codeberg.org/owner/repo[/src/branch/<ref>]/path   (any Gitea/Forgejo host)"""


class RepositoryProvider(ABC):
    """Abstract base for repository providers."""

    @abstractmethod
    def parse_url(self, url: str) -> tuple[str, str, str | None] | None:
        """Parse URL and return (owner, repo, path) or None if invalid."""

    @abstractmethod
    def fetch_metadata(self, url: str, owner: str, repo: str) -> dict[str, Any]:
        """Fetch {description, latest_version}; raises when the forge can't be asked."""

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
            return len(parts) >= 2 and all(parts[:2])

        return False

    def parse_url(self, url: str) -> tuple[str, str, str | None] | None:
        parsed = urllib.parse.urlparse(url)

        # Handle shorthand notation like "owner/repo/path"
        if not parsed.scheme:
            parts = url.strip("/").split("/")
            if len(parts) >= 2 and all(parts[:2]):
                owner, repo = parts[0], parts[1].removesuffix(".git")
                path = "/".join(parts[2:]) or None
                return owner, repo, path
            return None

        if not self.supports_url(url):
            return None

        parts = parsed.path.strip("/").removesuffix(".git").split("/")
        if len(parts) < 2:
            return None
        owner, repo = parts[0], parts[1]

        if len(parts) > 4 and parts[2] == "tree":
            # Format: owner/repo/tree/branch/path...
            return owner, repo, "/".join(parts[4:])
        if len(parts) > 2 and parts[2] not in [
            "tree",
            "blob",
            "commits",
            "releases",
            "issues",
            "pulls",
        ]:
            # Direct path format: owner/repo/path...
            return owner, repo, "/".join(parts[2:])

        return owner, repo, None

    def normalize_url(self, url: str) -> str:
        owner, repo, _ = self.parse_url(url)
        return f"https://github.com/{owner}/{repo}.git"

    def fetch_metadata(self, url: str, owner: str, repo: str) -> dict[str, Any]:
        if _gh_ready():
            return self._fetch_with_gh(owner, repo)
        return self._fetch_with_api(owner, repo)

    def _fetch_with_gh(self, owner: str, repo: str) -> dict[str, Any]:
        data = _gh(f"repos/{owner}/{repo}")
        if data is None:
            raise ValueError(f"github.com/{owner}/{repo} not found")
        release = _gh(f"repos/{owner}/{repo}/releases/latest")
        return _github_metadata(data, release)

    def _fetch_with_api(self, owner: str, repo: str) -> dict[str, Any]:
        base = f"https://api.github.com/repos/{owner}/{repo}"
        data = _get_json(base)
        if data is None:
            raise ValueError(f"github.com/{owner}/{repo} not found")
        return _github_metadata(data, _get_json(f"{base}/releases/latest"))


def _github_metadata(data: dict, release: dict | None) -> dict[str, Any]:
    # GitHub sends "description": null for repos without one
    return {
        "description": data.get("description"),
        "latest_version": release_version((release or {}).get("tag_name", "")),
    }


# A number (dotted or not), optionally with a pre-release/build suffix, at
# the end of the tag: "v2", "shadcn@4.21.5", "release-2.0.0-rc.1"
VERSION_IN_TAG = re.compile(r"\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.-]+)?$")


def release_version(tag: str) -> str | None:
    """The version a release tag names, without its prefix. A tag with no
    number (a codename) is kept whole rather than guessed at."""
    if not tag:
        return None
    match = VERSION_IN_TAG.search(tag)
    return match.group() if match else tag


def _gh(endpoint: str) -> Any | None:
    """`gh api` (authenticated, no rate limit worries). None on 404."""
    result = subprocess.run(
        [GH, "api", endpoint], capture_output=True, text=True, check=False
    )
    if result.returncode == 0:
        return json.loads(result.stdout)
    if "HTTP 404" in result.stderr:
        return None
    raise RuntimeError(f"gh api {endpoint}: {result.stderr.strip()}")


class GiteaProvider(RepositoryProvider):
    """Gitea/Forgejo forges (codeberg.org, self-hosted instances).

    Catch-all for full URLs on non-GitHub hosts: metadata comes from the
    /api/v1 REST API when the host speaks it, and cloning works on any
    plain git host regardless.
    """

    RESERVED_ROUTES = frozenset(
        {
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
    )

    def supports_url(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if parsed.netloc in GITHUB_HOSTS:
            return False
        parts = parsed.path.strip("/").split("/")
        return len(parts) >= 2 and all(parts[:2])

    def parse_url(self, url: str) -> tuple[str, str, str | None] | None:
        if not self.supports_url(url):
            return None

        parsed = urllib.parse.urlparse(url)
        parts = parsed.path.strip("/").split("/")
        owner, repo = parts[0], parts[1]
        repo = repo.removesuffix(".git")

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
        owner, repo, _ = self.parse_url(url)
        host = urllib.parse.urlparse(url)
        return f"{host.scheme}://{host.netloc}/{owner}/{repo}.git"

    def fetch_metadata(self, url: str, owner: str, repo: str) -> dict[str, Any]:
        host = urllib.parse.urlparse(url)
        api_base = f"{host.scheme}://{host.netloc}/api/v1/repos/{owner}/{repo}"

        data = _get_json(api_base)
        if data is None:
            # Not a Gitea/Forgejo API host; cloning still works without metadata
            return {"description": None, "latest_version": None}

        release = _get_json(f"{api_base}/releases/latest")
        version = (release or {}).get("tag_name", "")
        if not version:
            tags = _get_json(f"{api_base}/tags?limit=1")
            if tags:
                version = tags[0].get("name", "")

        return {
            # Gitea sends "" for repos without one
            "description": data.get("description") or None,
            "latest_version": release_version(version),
        }


def _get_json(url: str) -> Any | None:
    """GET a JSON API. None when the resource or the API isn't there (404,
    or a non-JSON page from a host without one); any other failure raises."""
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", f"dkb/{VERSION}")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"{url}: HTTP {e.code} {e.reason}") from e
    except json.JSONDecodeError:
        return None
    except urllib.error.URLError as e:
        raise RuntimeError(f"{url}: {e.reason}") from e


class ProviderRegistry:
    """Registry for repository providers. Order matters: GitHub owns
    github.com URLs and schemeless shorthand, Gitea catches remaining
    full URLs."""

    def __init__(self):
        self.providers = [
            GitHubProvider(),
            GiteaProvider(),
        ]

    def resolve(self, url: str) -> tuple[RepositoryProvider, str, str, str | None]:
        """(provider, owner, repo, path) for any accepted URL form, or a
        ValueError that teaches the forms."""
        for provider in self.providers:
            if provider.supports_url(url):
                parsed = provider.parse_url(url)
                if parsed:
                    return (provider, *parsed)
        raise ValueError(f"not a repository URL: {url}\n{URL_FORMS}")


provider_registry = ProviderRegistry()
