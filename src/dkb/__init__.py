"""Developer Knowledge Base - Fetch and organize documentation locally."""

import importlib.metadata

METADATA = importlib.metadata.metadata("dkb")
NAME = METADATA["Name"]
VERSION = METADATA["Version"]
DESCRIPTION = METADATA["Summary"]
