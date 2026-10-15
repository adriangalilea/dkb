"""Developer Knowledge Base - Fetch and organize documentation locally."""

import importlib.metadata

from rich.console import Console

# No hard wrapping: paths must stay copyable and greppable in logs and pipes
console = Console(soft_wrap=True)

METADATA = importlib.metadata.metadata("dkb")
NAME = METADATA["Name"]
VERSION = METADATA["Version"]
DESCRIPTION = METADATA["Summary"]
