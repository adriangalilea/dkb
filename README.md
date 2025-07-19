# dkb - Developer Knowledge Base

Local documentation manager for vibe coding with Claude Code.


> [!NOTE]
> ✨ **Perfect for Claude Code**
> 
> `dkb` automatically generates a `CLAUDE.md` file that provides context about your local documentation cache and `dkb` usage instructions.
> 
> ```diff
> # ~/CLAUDE.md
> + @~/.local/share/dkb/CLAUDE.md
> ```
> Now on your next Claude Code session it will know how to use it.

![Claude integration](claude.png)

> Local .md files > MCP

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

dkb v0.2.3

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
  dkb add deno https://github.com/denoland/docs.git
  dkb add tailwind https://github.com/tailwindlabs/tailwindcss.com.git src/docs
  dkb remove tailwind
  dkb update
  dkb status

# Add a repository (entire repo)
$ dkb add deno https://github.com/denoland/docs.git

Fetching deno from https://github.com/denoland/docs.git
Branch: main
Paths: <entire repository>
✓ deno fetched
✓ Updated /Users/you/.local/share/dkb/CLAUDE.md

# Show status with rich formatting
$ dkb status

                                    Knowledge Base Status
┏━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ Repository   ┃ Version ┃ Docs                     ┃ Source                  ┃ Last Updated ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ better-auth  │ 1.2.12  │ better-auth/better-auth  │ -                       │ 25m ago      │
│ deno         │ 2.4.2   │ denoland/docs            │ denoland/deno           │ 25m ago      │
│ nextjs       │ 15.4.2  │ vercel/next.js           │ -                       │ 24m ago      │
│ tailwind     │ 4.1.11  │ tailwindlabs/tailwindcss.com │ tailwindlabs/tailwindcss │ 12m ago      │
│ uv           │ 0.8.0   │ astral-sh/uv             │ -                       │ 33m ago      │
└──────────────┴─────────┴──────────────────────────┴─────────────────────────┴──────────────┘

# Update all repositories
$ dkb update

Updating deno... ✓ updated
Updating nextjs... - unchanged
Updating tailwind... - unchanged
Updating uv... - unchanged

Updated: deno
✓ Updated /Users/you/.local/share/dkb/CLAUDE.md
```

## Configuration

Docs stored in `$XDG_DATA_HOME/dkb/` (defaults to `~/.local/share/dkb/`)

Configuration file: `$XDG_DATA_HOME/dkb/config.json`

## TODO

- [ ] UX should be `dkb add https://github.com/astral-sh/uv/tree/main/docs`
