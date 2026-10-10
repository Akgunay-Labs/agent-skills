# Akgunay Labs Agent Skills

Hand-authored skills for coding agents (Claude Code, Codex), kept as plain
`SKILL.md` folders that the agents read live through symlinks. This is the
sibling of `agent-plugins`: that repository packages installable, versioned
plugins with host manifests; this one holds skills that stay editable in
place, with no packaging step.

## Layout

```text
agent-skills/
├── skills/<name>/          one skill per folder: SKILL.md, references/, scripts/
├── scripts/link-skills.sh  create or check the symlinks in ~/.agents/skills and ~/.claude/skills
├── scripts/check.py        validate frontmatter, word counts, and run each skill's tests
└── .github/workflows/      runs scripts/check.py on every push
```

## Install

```sh
git clone git@github.com:Akgunay-Labs/agent-skills.git
cd agent-skills
bash scripts/link-skills.sh            # link every skill
bash scripts/link-skills.sh rex        # link one skill
bash scripts/link-skills.sh --check    # exit 1 on drift, change nothing
```

Each skill becomes `~/.agents/skills/<name>` (absolute link into this
checkout, read by Codex and Claude Code) and `~/.claude/skills/<name>`
(relative link to the first). The script never replaces a real directory.
`--prune` moves links that point into this repository but whose skill no
longer exists to the Trash.

## Conventions

- Author skills with the `superpowers:writing-skills` workflow: baseline
  without the skill first, then write, then re-test.
- `python3 scripts/check.py` must pass before a commit.
- Nothing machine-specific or private goes in: no session or block IDs,
  no personal hook paths, no run state.
- Removing anything means moving it to the Trash, not `rm`.
