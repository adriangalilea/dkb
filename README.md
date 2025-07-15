# dkb - Developer Knowledge Base

Local documentation manager for vibe coding with Claude Code.

> local md files > MCP

## 🎯 Perfect for Claude Code

`dkb` automatically generates a `CLAUDE.md` file that provides context about your local documentation cache. This integrates seamlessly with Claude Code by:

1. **Auto-generated context**: The `CLAUDE.md` file includes repository descriptions and paths
2. **Easy referencing**: Simply add `@~/.local/share/dkb/CLAUDE.md` to your Claude instructions
3. **Always up-to-date**: Regenerates whenever you add, remove, or update documentation

Example CLAUDE.md reference in your instructions:
```
@/Users/you/.local/share/dkb/CLAUDE.md
```

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
usage: dkb [-h] {add,remove,update,status,claude,cron} ...

dkb v0.1.0

Developer Knowledge Base - Fetch and organize documentation locally for vibe coding with Claude Code

positional arguments:
  {add,remove,update,status,claude,cron}
                        Available commands
    add                 Add a new repository
    remove              Remove a repository
    update              Update all repositories
    status              Show status of all repositories
    claude              Regenerate CLAUDE.md file
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
- [x] Explain how to hook-up Claude Code with `dkb`
