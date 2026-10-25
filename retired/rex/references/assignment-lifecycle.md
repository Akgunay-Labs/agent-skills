# Assignment lifecycle (Mode A, process per assignment)

Evidence labels: **live** = observed 2026-10-10 (Rex `0.0.1791515941-gd9a4b5`, Claude Code 2.1.295, Codex 0.162.1); **live 10-11** = observed 2026-10-11 (Rex `0.0.1791573294-gba791b`, Claude Code 2.1.296); **docs** = Rex documentation; **assumption** = not yet verified.

## Shape

One block per assignment. The block runs `sh -c '<banner>; exec <agent> … > <stream file>'` with `--keep-open`, so the screen survives the exit for inspection. The agent's output goes to the file, so the pane shows only the one-line banner (assignment id, run dir, how to check progress) until exit. The brief lives in a file; the prompt is one line pointing at it. Completion is the child's exit (`rex wait`); the outcome is the file the agent wrote; acceptance is a reviewer's `review.json`. Three different facts, three different records.

## Record

`~/.local/state/rex-orch/runs/<assignment_id>/` holds `brief.md`, `assignment.json`, the result (Claude: `stream.jsonl` while it runs, then `result.json`, which `wait` writes from the stream's final event; Codex: `events.jsonl` + `last-message.md`), `REPORT.md` written by the worker, and `review.json` written by a reviewer assignment. The directory is outside every repository; nothing from it is committed.

```json
{"assignment_id": "a-20261010-102318-38fd", "role": "executor",
 "owner":  {"kind": "lead-in-rex", "block_id": "block:…", "controller": "claude-code"},
 "server": {"endpoint": "unix:///…/server.sock", "instance_id": "…", "host_id": "…"},
 "target": {"session_id": "session:…", "window_id": "window:…", "block_id": "block:…", "label": "a-…"},
 "worker": {"agent": "claude", "mode": "process", "model": "sonnet", "effort": null,
            "permission_policy": {"mode": "default", "allowed_tools": ["Read"], "prompts": "none"},
            "cwd": "/repo", "agent_session_id": "uuid", "resume_of": null},
 "paths": {"run_dir": "…", "brief": "…/brief.md", "result": "…/result.json", "stream": "…/stream.jsonl",
           "review": "…/review.json", "deliverable": "…/REPORT.md"},
 "state": "launched", "launched_at": "2026-10-10T06:23:18Z", "exit": null, "review": null, "sends": []}
```

`owner.controller` is `--controller`, else `REX_ORCH_CONTROLLER`, else `claude-code` when `CLAUDECODE` is set, `codex` when `CODEX_THREAD_ID` is set (**live 10-11**), else `unknown`. `target.window_id` comes from `rex block inspect` after a split. `paths.turns` exists only for interactive workers, `paths.stream` only for process ones. `launch` prints a short summary; `--json` prints this record.

States: `launched` → `exited` → one of `completed-unreviewed` | `completed-with-denials` | `completed-without-deliverable` | `failed[:reason]` | `exited-without-result` | `timeout` | `cancelled` | `abandoned:<reason>` (`block-closed`, `server-restarted`, `no-transcript`). After review: `accepted` | `changes-requested`. `cancel --close` on a finished worker keeps the finished state (**live**: a smoke run exposed the opposite bug and it was fixed).

## Launch

`launch` refuses: no `--session`; no `--agent`, `--cwd` or `--brief`; a label instead of `session:…`; neither `--anchor block:…` nor `--new-window`; a `launching` marker without a record (an earlier create may have left an orphan block: `rex block ls -s S --json`, then move the run dir to Trash); a run dir that already holds results. Then it writes the brief, writes the marker, creates the block, writes the record, and removes the marker. Order matters: a marker without a record is the only ambiguous state and it is visible.

Claude worker command (**live 10-11**):
```sh
printf '%s\n' 'rex_assign AID: headless claude worker, no live output here until it exits. …'; \
exec claude -p --model sonnet --permission-mode default --permission-prompts none --add-dir RUN \
  --allowedTools Read 'Edit(//RUN/**)' --output-format stream-json --verbose --session-id UUID --name AID \
  'Read RUN/brief.md and do exactly what it says.' > RUN/stream.jsonl
```
`--allowedTools` and `--add-dir` are variadic: the prompt must follow a non-variadic flag (`--name`) or it is swallowed. `--permission-prompts none` makes every would-be prompt a denial instead of a hang. `stream-json` needs `--verbose` in print mode.

The helper always appends `Edit(//RUN/**)`. An `Edit` rule also covers `Write`, so the worker can create **and correct** its deliverable under any policy without gaining write access to the repo (**live 10-11**: under `--permission-mode default`, writes and edits in the run dir passed, a write in the cwd was denied). The double slash matters: Claude reads `Edit(/abs/…)` as relative to the project and denies the write; `Edit(//abs/…)` is the absolute form. Do not add a bare `--allow Write` or `--allow Edit`: either one opens the whole repo.

Allowlists and shell chains (**live 10-11**): `ls /tmp; ls /var` is denied even when `Bash(ls:*)` covers both parts. Briefs for allowlisted workers ask for one shell command per call and prefer Read/Grep/Glob.

Codex worker command (**live**):
```sh
exec codex exec --approve-for-me -c shell_environment_policy.inherit=all --skip-git-repo-check \
  --json -o RUN/last-message.md [-m MODEL] [-C CWD] [--worktree] 'Read RUN/brief.md …' > RUN/events.jsonl
```
`-s` carries the recorded policy (`read-only` | `workspace-write` | `danger-full-access`; `plan` maps to read-only). Without `--approve-for-me` every command needing approval is rejected in exec mode (**live**). `-c shell_environment_policy.inherit=all` is what lets the worker's own `rex` calls see `REX_*` (**live**). The `--add-dir` form has not completed a live run yet (**assumption**). `codex exec` returned empty turns late in testing while a macOS folder-permission fault was active; the cause is not isolated.

`rex` creation (**live**): `rex split -s S -b ANCHOR --split right --focus=false --keep-open --label AID --cwd CWD --json -- sh -c '<payload>'`, or `rex run -s S …` for a new window. `--focus=false` keeps the lead's focus. The login shell's environment applies (PATH has Homebrew and Rex), not its rc files; `--shell=none` loses that PATH.

## Wait and classify

`rex wait -s S --for 30m --json BLOCK` (**docs/live**): returns the child's exit status; prints `{"exit_code":N[,"error":…]}` on exit; on timeout prints no JSON, writes `Error: wait timed out after …` to stderr and exits 1 (**live 10-11**); the helper calls exit 1 a timeout only when that text is present. The helper classifies the worker only from a payload or a timeout; rex-side outcomes (exit 3 block gone → `abandoned:block-closed`; exit 4 server unreachable; exit 130 the lead's own Ctrl+C) leave the record untouched and say the worker may still be running. `wait` and `status` check `instance_id` first and record `abandoned:server-restarted` before touching the block. A child exit of 2 makes `rex wait` print its own usage text as well (**live**, harmless).

Claude `result.json` (**live**; with stream-json it is the stream's last `type=result` line, same fields): `type=result`, `subtype` (`success` | `error_max_turns` | `error_during_execution` …), `is_error`, `result`, `session_id`, `permission_denials[]` (`tool_name`, `tool_input`), `terminal_reason`, `num_turns`, `total_cost_usd`, `duration_ms`.

Codex `events.jsonl` (**live**): `thread.started{thread_id}`, `turn.started`, `item.completed{item.type ∈ command_execution|agent_message|error, command, exit_code, aggregated_output}`, `turn.completed`. `-o` writes the last assistant message.

`wait` prints `state`, `exit`, `summary`, and when present (**live 10-11**): `denials`, one line per denial with tool plus path or command (`Bash: ls /tmp; ls /var`); `warning`, when a denied edit targeted the deliverable, meaning the file exists but lacks a change the worker wanted (its `tool_input` in `result.json` shows the change); and `tools_used`, a tally from the stream.

| exit | result | state |
|---|---|---|
| none (timeout) | — | `timeout`: decide, do not assume failure; `status` first |
| ≠ 0 | any | `failed`: read `capture --trim` tail; auth or login text → `failed:auth`, tell the user, never type credentials |
| 0 | missing/empty | `exited-without-result`: usually a launch or redirect problem |
| 0 | `is_error` or `subtype≠success` | `failed:<subtype>` |
| 0 | `permission_denials` non-empty | `completed-with-denials`: read `denials`; re-run only with a policy the user authorizes, never `bypassPermissions` on your own |
| 0 | clean or with denials, but `--deliverable` file missing | `completed-without-deliverable`: the agent said done, the brief's output is not there |
| 0 | clean | `completed-unreviewed` |

## Review handoff

Executor brief ends with the `REPORT.md` request (what changed, how verified, what not done); launch it with `--deliverable REPORT.md`. Reviewer: `launch --role reviewer --permission default --allow 'Bash(git diff:*)' --deliverable review.json`. The run-dir rule lets it write `review.json` and the repo stays read-only. A plan-mode reviewer cannot write the file (**live**), and `acceptEdits` would let it edit the repo. Give it a brief that says "do not modify anything outside `$RUN_DIR`" and points at the worktree/diff and `REPORT.md`, asking for `review.json`:

```json
{"verdict": "accepted" | "changes-requested", "findings": [{"file": "", "line": 0, "summary": ""}]}
```
Record it with `review <executor-id> --reviewer <reviewer-id>` (or `--from-file review.json`); the helper accepts only the two verdicts and only on `completed-unreviewed` or `completed-with-denials` records (never `completed-without-deliverable`). `changes-requested` → `resume <executor> --brief findings.md` (the resumed run keeps the original agent session id in its record). The resumed run gets a new run dir and only that one is writable, so the findings brief says to write `$RUN_DIR/REPORT.md` anew; the agent otherwise remembers the old path, and that edit is denied. Nothing else moves an assignment to `accepted`. Two workers never share a working tree: PR work uses `--worktree` (`claude --worktree` / `codex exec --worktree`); never `git worktree add` from a shell.

## Checking the report

Before relaying a worker's "what I did", compare its method claims ("I ran X", "I read Y") with `tools_used` and `denials`. A claim with no matching tool use is unverified, so say so. After a `warning`, report the deliverable as stale and name the denied change. This spot-check is cheap; it does not replace a reviewer for executor work.

## Cancel, timeout, exit, recovery (**live** unless noted)

- **Cancel:** `cancel <id>` (owner only; exit 6 otherwise) sends `ctrl+c` only if the foreground process is the agent, waits 15 s, then `--close` closes the block (which kills the child). Every non-live state survives a close: completed, failed, `accepted`, `changes-requested`, abandoned. A `wait` re-run never regresses a reviewed record.
- **Timeout or "is it still running?":** `status <id>` shows the process (`foreground`, `pid`, `child_pid`, `last_exit`), status records with freshness, `progress` from the stream (event count, tool tally, last five actions; **live 10-11**), `deliverable_exists`, the marker tail (interactive only) and the screen tail. Use `pid`, not `pgrep -f`: a name match also hits the lead's own shell when its command line contains the pattern (**live**). On timeout, extend with another `wait`, or `cancel`.
- **Nonzero exit, no result:** launch failure. Common causes: redirect path missing, PATH (use the login shell, not `--shell=none`), auth. Quote the screen tail in your report.
- **Block gone** (`rex` exit 3 on a stored full ID): `abandoned:block-closed`. `resume` works only if the agent's session exists on disk; a Claude worker killed before its first turn completed has no transcript ("No conversation found") → `abandoned:no-transcript`, relaunch with the same brief.
- **Server restart:** `status` compares `instance_id`; a mismatch marks `abandoned:server-restarted`; re-bind every target before doing anything else.
- **Lead restart:** records are on disk. `list`, then `status`/`wait` on the stored IDs; nothing lives only in the lead's memory.

## Evidence rules

Done needs the exit **and** the artifact. The screen is for diagnosis, never for completion. Other agents' transcripts under `~/.claude/projects` or `~/.codex/sessions` are not evidence and not yours to read. A `done` status record proves this assignment finished only if `updated_at` is newer than `launched_at` and the owner pid is the worker (**docs**: done/error survive exit and a new prompt).
