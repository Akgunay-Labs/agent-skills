# Repository rules

- Skills live under `skills/<name>/`. Author or edit them with the
  `superpowers:writing-skills` workflow (baseline test → write → re-test) and
  validate with `python3 scripts/check.py`.
- Commit flow: direct commits to `main` while a skill is pre-release (the repository is public, so the no-private-content rule below is load-bearing). From
  a skill's first release tag on, changes go through a pull request opened
  from a Codex-managed worktree.
- Prefer what a tool's vendor ships. Before writing a skill for a tool, check
  whether the tool installs its own (Rex: `rex skill setup`); write ours only
  for the gap, build on theirs, and never reuse its name. Closed-source tools
  get extra suspicion: pin the version next to every observed fact.
- `retired/` holds skills taken out of use; never link them. `retired/rex`
  (retired 2026-10-25) explains why in `RETIRED.md`; read it before any Rex
  orchestration work.
- Nothing machine-specific or private enters the repository: no
  `session:`/`block:` IDs, no personal hook paths, no credentials, no run
  state. Run state belongs under `~/.local/state/`.
- Anything removed is moved to the Trash, never deleted with `rm`.
- Do not duplicate tool manuals into a skill; point at the tool's own
  discovery path (`--help`, `api describe`) and pin the version you tested.
