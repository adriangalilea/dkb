"""Two files, two owners.

The catalogue (WHAT to cache: url, branch, paths, version source) lives in
XDG config so a dotfiles repo can track it; it changes only on add/remove.
The fetched state (what WAS cached: description, version, commit, when)
lives beside the clones in XDG data and is rewritten on every update.
Every machine that shares the catalogue reaches the same cache through
`dkb update`.
"""

import json
import os
from pathlib import Path

from dkb.models import RepositoryConfig

XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
CONFIG_FILE = XDG_CONFIG_HOME / "dkb" / "config.json"
DATA_DIR = XDG_DATA_HOME / "dkb"
STATE_FILE = DATA_DIR / "state.json"
LEGACY_CONFIG_FILE = DATA_DIR / "config.json"


class ConfigManager:
    """Loads the catalogue merged with its state; saves both."""

    def __init__(self, config_file: Path = CONFIG_FILE, state_file: Path = STATE_FILE):
        self.config_file = config_file
        self.state_file = state_file
        if LEGACY_CONFIG_FILE.exists():
            raise RuntimeError(
                f"{LEGACY_CONFIG_FILE} is the dkb 2 layout. dkb 3 keeps the "
                f"catalogue (url, branch, paths, version_url per repo) in "
                f"{CONFIG_FILE} and the fetched state in {STATE_FILE}. Move the "
                f"declaration by hand, delete the old file, then run `dkb update` "
                f"to rebuild the state."
            )

    def load(self) -> dict[str, RepositoryConfig]:
        declared = _read(self.config_file).get("repositories", {})
        state = _read(self.state_file)
        return {
            name: RepositoryConfig.from_dict(name, data, state.get(name, {}))
            for name, data in declared.items()
        }

    def save(self, configs: dict[str, RepositoryConfig]):
        ordered = sorted(configs.items())
        _write(
            self.config_file,
            {"repositories": {name: c.declaration() for name, c in ordered}},
        )
        _write(self.state_file, {name: c.state() for name, c in ordered})

    def add(self, config: RepositoryConfig):
        configs = self.load()
        if config.name in configs:
            raise ValueError(f"Repository '{config.name}' already exists")
        configs[config.name] = config
        self.save(configs)

    def remove(self, name: str):
        configs = self.load()
        if name not in configs:
            raise ValueError(f"Repository '{name}' not found")
        del configs[name]
        self.save(configs)


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
