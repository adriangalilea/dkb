"""Configuration storage (XDG data dir + config.json)."""

import json
import os
from pathlib import Path
from typing import Dict

from dkb import console
from dkb.models import RepositoryConfig

XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
DATA_DIR = XDG_DATA_HOME / "dkb"
CONFIG_FILE = DATA_DIR / "config.json"


class ConfigManager:
    """Manages dkb configuration."""

    def __init__(self, config_file: Path):
        self.config_file = config_file
        self._ensure_config()

    def _ensure_config(self):
        """Ensure config file exists."""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.config_file.exists():
            self.config_file.write_text('{"repositories": {}}')

    def load(self) -> Dict[str, RepositoryConfig]:
        """Load all repository configurations."""
        with open(self.config_file) as f:
            data = json.load(f)

        configs = {}
        for name, repo_data in data.get("repositories", {}).items():
            try:
                configs[name] = RepositoryConfig.from_dict(name, repo_data)
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to load {name}: {e}[/yellow]")

        return configs

    def save(self, configs: Dict[str, RepositoryConfig]):
        """Save all repository configurations."""
        data = {
            "repositories": {name: config.to_dict() for name, config in configs.items()}
        }

        with open(self.config_file, "w") as f:
            json.dump(data, f, indent=2)

    def add(self, config: RepositoryConfig):
        """Add a repository configuration."""
        configs = self.load()
        if config.name in configs:
            raise ValueError(f"Repository '{config.name}' already exists")

        configs[config.name] = config
        self.save(configs)

    def remove(self, name: str):
        """Remove a repository configuration."""
        configs = self.load()
        if name not in configs:
            raise ValueError(f"Repository '{name}' not found")

        del configs[name]
        self.save(configs)

    def get(self, name: str) -> RepositoryConfig:
        """Get a specific repository configuration."""
        configs = self.load()
        if name not in configs:
            raise ValueError(f"Repository '{name}' not found")

        return configs[name]
