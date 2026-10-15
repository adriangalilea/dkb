# dkb - Developer Knowledge Base

Local documentation manager for vibe coding with Claude Code.


> [!NOTE]
> ✨ **Perfect for Claude Code**
>
> `dkb` maintains a skill at `~/.claude/skills/dkb/SKILL.md` (`$CLAUDE_CONFIG_DIR/skills/` when set). The skill description (always visible to the agent) carries the catalogue of cached repos, so Claude always knows what docs are available locally without running a command; the skill body loads on demand with versions, locations, and usage.

> Local .md files > MCP

## Install

```bash
# Install with uv
uv tool install dkb

# Or with pipx
pipx install dkb
```

## Usage

```
$ dkb add tailwindlabs/tailwindcss.com/src/docs --version-url tailwindlabs/tailwindcss
✓ tailwindcss 4.3.3 → ~/.local/share/dkb/tailwindcss

$ dkb add colinhacks/zod/packages/docs/content
✓ zod 4.6.7 → ~/.local/share/dkb/zod

$ dkb                      # alone: the status
┏━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Name        ┃ Version ┃ Source                       ┃ Version from             ┃ Docs changed ┃ Checked ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ tailwindcss │ 4.3.3   │ tailwindlabs/tailwindcss.com │ tailwindlabs/tailwindcss │ 35d ago      │ 2m ago  │
│ zod         │ 4.6.7   │ colinhacks/zod               │ -                        │ 1d ago       │ 2m ago  │
└─────────────┴─────────┴──────────────────────────────┴──────────────────────────┴──────────────┴─────────┘

$ dkb update               # parallel, skips what did not move upstream
  - tailwindcss unchanged
  ✓ zod         updated

$ dkb update zdo
Error: not in the catalogue: 'zdo' (did you mean 'zod'?); `dkb status` lists it

$ dkb add n0-computer/iroh-ffi/IrohLib/Docs
Error: path 'IrohLib/Docs' not found; 'IrohLib/' has: .swiftpm/, Sources/, Tests/, .gitignore, README.md
```

Piped (`dkb status | grep zod`, an agent's shell), `status` prints one plain line per repo with a header and the docs location; `dkb status --json` prints everything. `dkb -h` lists the files dkb owns and more `add` examples.

## Configuration

Two files, two owners:

| File | Holds | Changes on |
|---|---|---|
| `$XDG_CONFIG_HOME/dkb/config.json` (`~/.config/dkb/config.json`) | the catalogue: one entry per repo, of the kinds below | `dkb add` / `dkb remove` |
| `$XDG_DATA_HOME/dkb/` (`~/.local/share/dkb/`) | the clones, one directory per repo, plus `state.json` (description, version, commit, what the clone was made from, when docs last changed and when upstream was last checked) | `dkb update` |

The catalogue is the declaration and is meant to be tracked in a dotfiles repo. `dkb update` reconciles the cache with it: clones anything declared that is missing, re-clones anything whose upstream commit or declaration changed, re-reads every version and description, prunes clones nobody declares, and regenerates the skill; it exits 1 if any repo failed. A failed fetch keeps the previous clone and version: new content is assembled beside the old and swapped in only when complete. Without a declared `branch`, a repo follows its remote's default branch, whatever it is called. Metadata comes from `gh` when it is logged in, else from GitHub's public API (60 requests/hour). A fresh machine is one `dkb update` away from the same knowledge base. `dkb status` exits 1 while a declared repo has no clone, so it doubles as a drift check.

Three kinds of entry:

| Kind | Added with | Catalogue keys | On disk | Version |
|---|---|---|---|---|
| following | `dkb add owner/repo/path` | `url`, `paths`, `branch`?, `version_url`? | a clone, re-fetched when the branch moves | latest release (of `version_url` if set) |
| pinned | `dkb add owner/repo --pin v3.24.0` | `url`, `paths`, `pin` | a clone of that tag or full commit, never re-fetched | the pin |
| local | `dkb add ~/Developer/my-lib` | `local` | a symlink to the working copy, always current | its latest git tag |

A pin is for the docs of the version a project uses: `zod` (following) and `zod-3` (pinned) can sit side by side. A local entry is stored as `~/...`, so one catalogue serves every machine; on a machine without that directory, `dkb update` says so and exits 1.

```jsonc
// ~/.config/dkb/config.json
{
  "repositories": {
    "next.js": { "url": "https://github.com/vercel/next.js.git", "paths": ["docs"] },
    "zod-3": { "url": "https://github.com/colinhacks/zod.git", "paths": [], "pin": "v3.24.0" },
    "my-lib": { "local": "~/Developer/my-lib" }
  }
}
```

Upgrading from dkb 2: the catalogue used to live in the data dir as one file with the state mixed in. dkb 3 refuses to run while that file exists and prints where each half goes.

## Features

- ✨ **Auto-naming** - No need to specify names, automatically derived from repositories
- 🎯 **Path-specific URLs** - Add only the docs you need: `dkb add owner/repo/path`
- 🔗 **Multiple URL formats**:
  - Full URLs: `https://github.com/astral-sh/uv/tree/main/docs`
  - Shorthand: `tailwindlabs/tailwindcss.com/src/docs`
  - Classic: `https://github.com/denoland/docs.git`
- 🏠 **Gitea/Forgejo support** - Works with codeberg.org and self-hosted forges: `dkb add https://code.haverbeke.berlin/wordgard/website/src/branch/main/site/docs`
- 📦 **Version tracking** - Track versions from a different repository with `--version-url`
- 📌 **Pinned versions** - `--pin <tag|commit>` caches the docs of the version you actually use
- 🔗 **Local repos** - `dkb add ~/Developer/my-lib` links your working copy, so your own libraries are in the skill and never stale
- 🚀 **Parallel updates** - All repos update concurrently with live progress spinners
- ⚡ **Smart skip** - Unchanged repos detected via `git ls-remote` without cloning
- 📂 **Sparse checkout** - Repos with paths only fetch the files they need
- 🧭 **Errors that say what to do** - typo suggestions, the real contents of a mistyped path, git's own message instead of its command line
- 🤖 **Claude Code skill** - Maintains `~/.claude/skills/dkb/SKILL.md`; the always-visible description indexes the catalogue so the agent knows what's cached without running anything. The description stays within the Agent Skills spec's 1024 characters (names that don't fit become "+N more, all listed in the skill"); the body, loaded in full on invocation, carries every entry's version, source, location and layout
