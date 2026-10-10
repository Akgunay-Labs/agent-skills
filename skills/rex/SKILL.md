---
name: rex
description: Use when a lead agent must create Rex terminal blocks, launch or resume executor or reviewer agents in them, send them input, or collect and verify their results, from inside a Rex pane or from an outside controller such as Codex Desktop. Not for Herdr, tmux, or other terminals, and not for Rex server, hosts, config, or keymap administration.
---

# Rex orchestration

Rex is a terminal multiplexer: sessions hold windows (tabs), windows hold blocks (terminals); the `rex` CLI drives them. Tested against Rex `0.0.1791564903-g7abb0a`, Claude Code 2.1.296, Codex 0.162.1 on 2026-10-10. A newer `rex version` is fine until a command fails; then check `rex help <cmd>` first.

Helper: `python3 -I <skill>/scripts/rex_assign.py <subcommand> --help`.

## 1. Bind the target before anything else

- **Inside a Rex pane:** `rex whoami --json` gives your `session_id`, `window_id`, `block_id`, `instance_id`. Store them; still pass `-s SESSION` and `-b BLOCK` on every command that creates, types, or closes; defaults resolve to the focused block, not necessarily yours.
- **Outside Rex** (Codex Desktop, a plain shell, a daemon): you have no pane. Any `REX_*` you inherited belongs to whatever launched you: `unset REX_SESSION REX_BLOCK` and pass `--external` to the helper. Find `rex` via `"$REX_BIN_DIR/rex"` if set, else `/Applications/Rex Beta.app/Contents/Helpers/Rex Server.app/Contents/MacOS/rex`. Choose the session from `rex ls --json` by the full ID the user named. More than one session and none named: stop and ask, or create your own with `rex new LABEL --cwd DIR --json` and record its `session_id` and initial block as yours. Never pick the first or the most recent.
- **Codex inside a Rex pane** loses `REX_*` in the commands it runs. Launch it with `-c shell_environment_policy.inherit=all` (`codex --no-daemon …` interactive, `codex exec --approve-for-me …` headless), per launch, never by editing its config.
- **IDs only.** Store and script full IDs (`session:…`, `block:…`), never labels or suffixes. Exit 3 = not found or ambiguous, nothing changed; exit 4 = server unreachable.
- **Server identity.** Keep `instance_id`; a different value later means Rex restarted and every stored ID is stale.
- **Three different permissions.** Direct server commands work without Remote Control; app actions (`rex do pane.*`) need the Rex app with Remote Control on; Codex's sandbox approval for the Rex socket is a third thing.

Full recipes and the exit-code table: `references/targets-and-entry-paths.md`.

## 2. Pick a worker mode

| | Mode A: process per assignment (default) | Mode B: interactive worker |
|---|---|---|
| Worker | `claude -p` or `codex exec` in its own block, `--keep-open` | long-lived `claude` or `codex` TUI in its own block |
| Input | brief file + one-line prompt at launch | `send` one line pointing at a file, then `watch` |
| Done | child exit via `rex wait`, then the result file | fresh turn marker written by the agent's own hook, then the result file |
| Approvals | fixed at launch (`--permission-mode`, `--allowedTools`); denials appear in the result | a `blocked` marker; a person answers, never you |

Default to Mode A; `resume` continues it, `send` follows up in B. Details: `references/assignment-lifecycle.md`, `references/interactive-workers.md`.

## 3. Assignment flow

1. Write the brief to a file. End it with: "Write `REPORT.md` in `$RUN_DIR` with what changed, how you verified, what you did not do." The helper replaces `$RUN_DIR` with the real run directory at launch.
2. `rex_assign.py launch --agent claude|codex --session S (--anchor B [--split right] | --new-window) --cwd DIR --brief FILE --deliverable REPORT.md [--model M] [--permission P] [--allow TOOL …] [--worktree] [--mode interactive]`. `--deliverable` names the file the brief must produce; without it, `wait` can only say the process ended. It writes a marker, creates the block, records `assignment.json` under `~/.local/state/rex-orch/runs/<id>/`, and prints the record. Every created block is in a record; nothing is created by hand.
3. Mode A: `wait <id> --for 30m`. Mode B: `send <id> --text '…'` then `watch <id> --for 20m`. `send` refuses (exit 6) and names the guard: not owner, agent not in the foreground, dialog open, or text left in the input box.
4. Read `result.json` (or `events.jsonl` + `last-message.md`) and `REPORT.md`. The state is `completed-unreviewed` at best: the worker's deliverable exists and it says it finished. For a standalone reviewer or research task, that plus the deliverable file is the end.
5. Executor work gets a review: launch a second assignment with `--role reviewer --permission acceptEdits --deliverable review.json` (plan mode cannot write the file) and a brief that forbids touching the repo and asks for `review.json` `{"verdict": "accepted"|"changes-requested", "findings": [...]}`. Then `review <executor-id> --reviewer <reviewer-id>` records the verdict; nothing else moves an executor to `accepted`. Changes requested: `resume`/`send` the findings to the executor.
6. Finish: `cancel <id> --close` (owner only); `list` shows every record under the runs root, not only yours.

PR work runs with `--worktree` (agent-managed), never a shell-created worktree.

## 4. What a signal proves

| Signal | Proves | Does not prove |
|---|---|---|
| `send` exit 0 | bytes reached the pty | the agent read or accepted them |
| Rex record `working`/`idle` (owner `claude`/`codex`) | heuristic activity | completion; `idle` also follows an interrupt |
| Rex record `done`/`error` | a finish, if `updated_at` is newer than your send and `app` is set | this assignment, unless the owner pid matches |
| hook marker `stop` newer than `sent_at` | the agent ended that turn | the task succeeded, or that the turn was yours: a `prompt` marker you did not send means a person took over (`taken-over:person`) |
| `rex wait` returned / `child_exited` | the process ended | success: read exit + result |
| `result.json` `subtype:success`, `permission_denials:[]` | the agent reports completing the task | reviewer acceptance |
| `review.json` `verdict:accepted` | acceptance | nothing further |

## 5. Never

- Never target the focused block, the first session, a label, or a suffix. Full IDs, every call.
- Never type into a block you did not create or whose record does not name you as owner.
- Never answer a permission dialog for the worker (`send-key enter` on "Do you want to proceed?"). Report `blocked:<type>` and the dialog text; a person decides. If a person already answered or typed into the worker (`taken-over:person`), stop and report; do not re-send.
- Never call a quiet screen, `idle`, or a successful `send` "done". Done = exit + artifact (A) or fresh marker + artifact (B).
- Never read other agents' transcripts under `~/.claude` or `~/.codex`. Records, result files and `capture` are the evidence.
- Never leave a block or session you created unrecorded. Record first, then close or hand over, and say which. Close only what you created: `block close` for your blocks, `session kill` only for a session you created and recorded.
- Never run `rex server`, `rex config`, `rex hosts`, `rex home` from this skill; never edit `~/.codex/config.toml` or `~/.claude/settings.json` for a worker (per-launch flags only).
- Never replace `rex wait`/`watch` with a sleep loop, and never call a quiet screen evidence of anything.

## 6. Raw commands

When the helper does not fit, the exact `split`/`run`/`wait`/`capture`/`send`/`events`/`close` forms are in `references/targets-and-entry-paths.md` ("Raw command forms"). Always `exec` the agent inside `sh -c` so the foreground process is the agent; the command after `--` is not shell-parsed.

## 7. Discovery

`rex help <cmd>`, `rex api list -q`, `rex api describe <method>`, `rex block inspect -s S BLOCK --json`. `rex api call` needs `-s "$REX_SESSION"`.

## Known limits (2026-10-10)

Claude Code emits no status protocol; Rex synthesizes `idle`/`working` only for an exec'd interactive `claude`. Codex's TUI emits real `app=codex` records. A worker killed before its first turn has no transcript. Direct commands were verified only with Remote Control on; Codex Desktop as controller is untested. `codex exec` returned empty turns late in testing while a macOS folder-permission fault was active; the cause is not isolated.
