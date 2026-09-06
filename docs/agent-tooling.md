# Agent rules and skills

Install [mise](https://mise.jdx.dev/), review `mise.toml`, then run:

```sh
mise trust
mise install
mise run agents:preview
mise run agents:generate
```

Mise pins Node and [Ruler](https://github.com/intellectronica/ruler) for agent tooling;
it does not replace the project's existing Python/uv or device build setup.

Edit shared rules in `.ruler/*.md` and skills in `.ruler/skills/<name>/SKILL.md`.
The inherited codebase guide lives in `.ruler/AGENTS.md`. Generated files are checked
in so agents can use them without first installing tooling:

| Agent | Rules | Skills |
| --- | --- | --- |
| Codex | `AGENTS.md` | `.agents/skills/` |
| Claude | `CLAUDE.md` | `.claude/skills/` |
| OpenCode | `AGENTS.md` | `.opencode/skills/` |
| Cursor | `AGENTS.md` | `.cursor/skills/` |
| Pi | `AGENTS.md` | `.pi/skills/` |

Run generation after source edits and commit sources and generated files together.
The legacy Cursor rule files only point to the shared instructions. MCP propagation,
nested discovery, and backup files are disabled; existing personal agent settings and
submodule rules are not managed. `update-readme` maintains the branch README's credits,
overview, collapsed installation instructions, and attributed additions.
