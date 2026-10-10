# Assignment lifecycle (Mode A, process per assignment)

Evidence labels: **live** = observed 2026-10-10 (Rex `0.0.1791515941-gd9a4b5`, Claude Code 2.1.295, Codex 0.162.1); **docs** = Rex documentation; **assumption** = not yet verified.

## Shape

One block per assignment. The block runs `sh -c 'exec <agent> … > <result file>'` with `--keep-open`, so the screen survives the exit for inspection. The brief lives in a file; the prompt is one line pointing at it. Completion is the child's exit (`rex wait`); the outcome is the file the agent wrote; acceptance is a reviewer's `review.json`. Three different facts, three different records.

## Record

`~/.local/state/rex-orch/runs/<assignment_id>/` holds `brief.md`, `assignment.json`, the result (`result.json` for Claude, `events.jsonl` + `last-message.md` for Codex), `REPORT.md` written by the worker, and `review.json` written by a reviewer assignment. The directory is outside every repository; nothing from it is committed.

```json
{"assignment_id": "a-20261010-102318-38fd", "role": "executor",
 "owner":  {"kind": "lead-in-rex", "block_id": "block:…", "controller": "claude-code"},
 "server": {"endpoint": "unix:///…/server.sock", "instance_id": "…", "host_id": "…"},
 "target": {"session_id": "session:…", "window_id": null, "block_id": "block:…", "label": "a-…"},
 "worker": {"agent": "claude", "mode": "process", "model": "sonnet", "effort": null,
            "permission_policy": {"mode": "acceptEdits", "allowed_tools": ["Write", "Read", "Edit"], "prompts": "none"},
            "cwd": "/repo", "agent_session_id": "uuid", "resume_of": null},
 "paths": {"run_dir": "…", "brief": "…/brief.md", "result": "…/result.json", "review": "…/review.json", "turns": "…/turns.jsonl"},
 "state": "launched", "launched_at": "2026-10-10T06:23:18Z", "exit": null, "review": null, "sends": []}
```

States: `launched` → `exited` → one of `completed-unreviewed` | `completed-with-denials` | `failed[:reason]` | `exited-without-result` | `timeout` | `cancelled` | `abandoned:<reason>` (`block-closed`, `server-restarted`, `no-transcript`). After review: `accepted` | `changes-requested`. `cancel --close` on a finished worker keeps the finished state (**live**: a smoke run exposed the opposite bug and it was fixed).

## Launch

`launch` refuses: no `--session`; a label instead of `session:…`; neither `--anchor block:…` nor `--new-window`; a `launching` marker without a record (an earlier create may have left an orphan block: `rex block ls -s S --json`, then move the run dir to Trash); a run dir that already holds results. Then it writes the brief, writes the marker, creates the block, writes the record, and removes the marker. Order matters: a marker without a record is the only ambiguous state and it is visible.

Claude worker command (**live**):
```sh
exec claude -p --model sonnet --permission-mode acceptEdits --permission-prompts none --add-dir RUN \
  --allowedTools Write Read Edit --output-format json --session-id UUID --name AID \
  'Read RUN/brief.md and do exactly what it says.' > RUN/result.json
```
`--allowedTools` and `--add-dir` are variadic: the prompt must follow a non-variadic flag (`--name`) or it is swallowed. `--permission-prompts none` makes every would-be prompt a denial instead of a hang.

Codex worker command (**live**):
```sh
exec codex exec --approve-for-me -c shell_environment_policy.inherit=all --skip-git-repo-check \
  --json -o RUN/last-message.md [-m MODEL] [-C CWD] [--worktree] 'Read RUN/brief.md …' > RUN/events.jsonl
```
`-s` carries the recorded policy (`read-only` | `workspace-write` | `danger-full-access`; `plan` maps to read-only). Without `--approve-for-me` every command needing approval is rejected in exec mode (**live**). `-c shell_environment_policy.inherit=all` is what lets the worker's own `rex` calls see `REX_*` (**live**). The `--add-dir` form has not completed a live run yet (**assumption**; row 11 deferred).

`rex` creation (**live**): `rex split -s S -b ANCHOR --split right --focus=false --keep-open --label AID --cwd CWD --json -- sh -c '<payload>'`, or `rex run -s S …` for a new window. `--focus=false` keeps the lead's focus. The login shell's environment applies (PATH has Homebrew and Rex), not its rc files; `--shell=none` loses that PATH.

## Wait and classify

`rex wait -s S --for 30m --json BLOCK` (**docs/live**): returns the child's exit status; prints `{"exit_code":N[,"error":…]}` on exit; on timeout prints no JSON, writes "wait timed out" to stderr and exits 1. The helper classifies the worker only from a payload or a timeout; rex-side outcomes (exit 3 block gone → `abandoned:block-closed`; exit 4 server unreachable; exit 130 the lead's own Ctrl+C) leave the record untouched and say the worker may still be running. `wait` and `status` check `instance_id` first and record `abandoned:server-restarted` before touching the block. A child exit of 2 makes `rex wait` print its own usage text as well (**live**, harmless).

Claude `result.json` (**live**): `type=result`, `subtype` (`success` | `error_max_turns` | `error_during_execution` …), `is_error`, `result`, `session_id`, `permission_denials[]` (`tool_name`, `tool_input`), `terminal_reason`, `num_turns`, `total_cost_usd`, `duration_ms`.

Codex `events.jsonl` (**live**): `thread.started{thread_id}`, `turn.started`, `item.completed{item.type ∈ command_execution|agent_message|error, command, exit_code, aggregated_output}`, `turn.completed`. `-o` writes the last assistant message.

| exit | result | state |
|---|---|---|
| none (timeout) | — | `timeout`: decide, do not assume failure; `status` first |
| ≠ 0 | any | `failed`: read `capture --trim` tail; auth or login text → `failed:auth`, tell the user, never type credentials |
| 0 | missing/empty | `exited-without-result`: usually a launch or redirect problem |
| 0 | `is_error` or `subtype≠success` | `failed:<subtype>` |
| 0 | `permission_denials` non-empty | `completed-with-denials`: list the tools; re-run only with a policy the user authorizes, never `bypassPermissions` on your own |
| 0 | clean but `--deliverable` file missing | `completed-without-deliverable`: the agent said done, the brief's output is not there |
| 0 | clean | `completed-unreviewed` |

## Review handoff

Executor brief ends with the `REPORT.md` request (what changed, how verified, what not done); launch it with `--deliverable REPORT.md`. Reviewer: `launch --role reviewer --permission acceptEdits --deliverable review.json` (**live**: a plan-mode reviewer cannot write the file, and path-scoped `Write(...)` allow rules did not match; tool-level `--allow Write` or `acceptEdits` does) with a brief that says "do not modify anything outside `$RUN_DIR`" and points at the worktree/diff and `REPORT.md`, asking for `review.json`:

```json
{"verdict": "accepted" | "changes-requested", "findings": [{"file": "", "line": 0, "summary": ""}]}
```
Record it with `review <executor-id> --reviewer <reviewer-id>` (or `--from-file review.json`); the helper accepts only the two verdicts and only on `completed-unreviewed` or `completed-with-denials` records (never `completed-without-deliverable`). `changes-requested` → `resume <executor> --brief findings.md` (the resumed run keeps the original agent session id in its record). Nothing else moves an assignment to `accepted`. Two workers never share a working tree: PR work uses `--worktree` (`claude --worktree` / `codex exec --worktree`); never `git worktree add` from a shell.

## Cancel, timeout, exit, recovery (**live** unless noted)

- **Cancel:** `cancel <id>` (owner only; exit 6 otherwise) sends `ctrl+c` only if the foreground process is the agent, waits 15 s, then `--close` closes the block (which kills the child). Every non-live state survives a close: completed, failed, `accepted`, `changes-requested`, abandoned. A `wait` re-run never regresses a reviewed record.
- **Timeout:** a decision point. `status <id>` shows process, status records with freshness, marker tail, screen tail. Extend with another `wait`, or `cancel`.
- **Nonzero exit, no result:** launch failure. Common causes: redirect path missing, PATH (use the login shell, not `--shell=none`), auth. Quote the screen tail in your report.
- **Block gone** (`rex` exit 3 on a stored full ID): `abandoned:block-closed`. `resume` works only if the agent's session exists on disk; a Claude worker killed before its first turn completed has no transcript ("No conversation found") → `abandoned:no-transcript`, relaunch with the same brief.
- **Server restart:** `status` compares `instance_id`; a mismatch marks `abandoned:server-restarted`; re-bind every target before doing anything else.
- **Lead restart:** records are on disk. `list`, then `status`/`wait` on the stored IDs; nothing lives only in the lead's memory.

## Evidence rules

Done needs the exit **and** the artifact. The screen is for diagnosis, never for completion. Other agents' transcripts under `~/.claude/projects` or `~/.codex/sessions` are not evidence and not yours to read. A `done` status record proves this assignment finished only if `updated_at` is newer than `launched_at` and the owner pid is the worker (**docs**: done/error survive exit and a new prompt).
