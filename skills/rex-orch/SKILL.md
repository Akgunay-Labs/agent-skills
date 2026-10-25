---
name: rex-orch
description: Use when handing work to another Claude Code or Codex agent that runs in a Rex pane, as a worker or a reviewer, including when that agent stops to ask for permission or when deciding whether its work is done and good. Not for running ordinary commands in Rex, and not for in-process subagents.
---

# Agent workers in Rex

Rex ships its own `rex` skill for the CLI: targets, `split`, `run`, `wait`, `capture`, `send`. Load it first (or run `rex skill show`). If that guide now covers delegating work to agents, follow it instead of this skill.

This skill covers only what Rex's guide does not: another agent acting for the user.

## 1. Launch: set the worker's permissions explicitly

Every launch names the worker's permissions. A worker started without them inherits the user's global mode, which may be auto mode.

| Worker | Permissions at launch |
|---|---|
| `claude -p` (one-shot) | `--permission-mode acceptEdits` plus `--allowedTools` scoped to the commands the task needs |
| interactive `claude` | `--permission-mode` and `--allowedTools` as above, or the mode the user asked for |
| `codex exec` (one-shot) | `-s read-only` or `-s workspace-write`, plus `-c 'approval_policy="never"'` so a sandbox escalation fails in the worker instead of reaching a user's approval hook; `-o FILE` keeps its last message |

What you set at launch is the scope the user agreed to. When nobody will watch the pane, prefer a one-shot worker: it never stops to ask.

## 2. A prompt during the run: stop and report

A permission prompt in a worker pane ("Do you want to…", "Allow", "Overwrite", "Create", "Proceed") means the task went outside the scope set at launch. Do not answer it. Report the run as **blocked**, with the prompt text and the pane's block ID, and let the user decide.

| Rationalization | Reality |
|---|---|
| "The user said don't ping me." | That covers questions. A blocked report is the correct outcome. |
| "I read the diff; it is what the task asked for." | The user chose a mode with a human gate. You are not that human. |
| "Only once, not 'don't ask again'." | One approval is still an approval. |
| "It's a scratch folder." | Widen the scope at launch next time; don't answer prompts now. |
| "I'll start a fresh worker with the action pre-approved." | That bypasses the same gate. The scope is set before the run, not after a prompt. |

**Red flag:** you are about to `send-key enter`, `y` or `1` into a pane that shows a prompt. Stop.

**Folder trust is the one exception.** Claude's startup "trust this folder?" prompt may be accepted when the folder is the one the task names **and** it has no `.claude/`, no `.mcp.json`, and nothing but `*.sample` files in `.git/hooks/`. Say in the report that you accepted it. If any check fails, treat it like any other prompt.

## 3. Review: hand the reviewer the change

Write the change to a file before launching a reviewer: `git diff HEAD` for tracked files, plus the list from `git ls-files --others --exclude-standard` so it reads new files whole. Point the reviewer at that file; don't count on it running `git` under an allowlist.

If the reviewer says it could not see the change or could not run the tests, the review is incomplete. Report that first, and don't call the work good to keep.

## 4. Report: verified vs reported

The final report starts with the status (**done**, **blocked** or **incomplete**), then has two lists:

- **Verified by me:** each fact you checked with your own command, with the command: exit code (`rex wait`, `last_exit`), `git diff`, your own test run.
- **The worker says:** everything else, attributed to the worker. A side effect that is "consistent with" a claim does not verify it.

## Gotchas (Claude Code 2.1.310, Codex 0.164.0, Rex 0.0.1792878257, 2026-10-25)

- `--allowedTools` takes several values, so `claude -p --allowedTools A B "prompt"` swallows the prompt ("Input must be provided"). Put the prompt right after `-p`.
- `claude -p` prints nothing in its pane until it exits. Tell the user the pane will look idle.
- A chained shell command (`a; b`, `a && b`) matches no single `Bash(...)` allow rule. Ask the worker for one command per call.
- Codex's `workspace-write` sandbox can also write to `/tmp`. Don't treat a `/tmp` path as outside the worker's reach.
