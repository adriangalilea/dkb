# dkb - Developer Knowledge Base

Local documentation manager for vibe coding.

> local md files > MCP

## Install

```bash
# Install with uv
uv tool install dkb

# Or with pipx
pipx install dkb
```

## Usage

```bash
$ dkb -h
usage: dkb [-h] {add,remove,update,status,cron} ...

dkb v0.0.5

Developer Knowledge Base - Fetch and organize markdown documentation locally for vibe coding

positional arguments:
  {add,remove,update,status,cron}
                        Available commands
    add                 Add a new repository
    remove              Remove a repository
    update              Update all repositories
    status              Show status of all repositories
    cron                Run continuous update loop

options:
  -h, --help            show this help message and exit

Examples:
  dkb add vue https://github.com/vuejs/docs.git src/guide src/api
  dkb remove vue
  dkb update
  dkb status

# Add a repository with specific paths
$ dkb add orpc https://github.com/unnoq/orpc.git apps/content/docs
Fetching orpc from https://github.com/unnoq/orpc.git
Branch: main
Paths: apps/content/docs
✓ orpc updated

# Show status - note the newly added 'orpc' repository
$ dkb status
Knowledge Base Status

drizzle         no-tags              eb8d0dd2  25m ago
nextjs          no-tags              81f0c764  31m ago
orpc            v1.6.4               99032307  0m ago     # <-- just added!
turborepo       no-tags              6c85c5ae  29m ago
uv              no-tags              c3f13d25  19m ago

# Update all repositories
$ dkb update

# Remove a repository
$ dkb remove drizzle
✗ drizzle removed
```

## Configuration

Docs stored in `$XDG_DATA_HOME/dkb/` (defaults to `~/.local/share/dkb/`)

Configuration file: `$XDG_DATA_HOME/dkb/config.json`

## TODO

- [ ] UX should be `dkb add https://github.com/astral-sh/uv/tree/main/docs`
- [ ] Explain how to hook-up Cloude Code with `dkb`
