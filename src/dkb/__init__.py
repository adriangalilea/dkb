"""Developer Knowledge Base - Fetch and organize documentation locally."""

import importlib.metadata

from rich.console import Console

console = Console()

METADATA = importlib.metadata.metadata("dkb")
NAME = METADATA["Name"]
VERSION = METADATA["Version"]
DESCRIPTION = METADATA["Summary"]
