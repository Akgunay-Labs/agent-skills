# Interactive workers (Mode B)

Evidence labels: **live** = observed 2026-10-10 (Rex `0.0.1791515941-gd9a4b5`, Claude Code 2.1.295, Codex 0.162.1); **docs** = Rex documentation; **assumption** = not yet verified.

A long-lived `claude` or `codex` TUI in its own block, driven by one-line prompts. Use it when the user wants a conversation with the worker, or a reviewer that stays open across rounds. Everything else prefers Mode A.

## What Rex and the agents actually show (live)

- Claude Code emits no Program Status Protocol records. Rex synthesizes `working`/`idle` records (owner `claude`) only when an interactive `claude` is the block's foreground process; none for `claude -p`, none when the agent sits behind a `sh -c "… && claude"` wrapper (foreground `bash`). `idle` appeared after completions **and** after interrupts (2026-10-08 run).
- Codex's TUI emits real records: `{"state":"done","app":"codex","owner":{"name":"codex"}}` after each turn.
- Claude rings the terminal bell (with `preferredNotifChannel: terminal_bell`) at a permission prompt, not at turn end. Bell means "needs attention".
- Esc at a permission prompt and Ctrl+C mid-generation produce no `Stop` hook; Ctrl+C puts the interrupted prompt text back into the input box.
- Multi-line `rex send` lands in the input box without submitting; `send-key enter` submits all of it.
- Per-launch `--settings` hooks merge with the user's global hooks (debug log showed both running).
- A hook can emit OSC 7501 to Rex by writing to the pty device `/dev/$(ps -o tty= -p $PPID)`; `/dev/tty` from inside the agent's tools reaches nothing.

## Turn channel the helper installs at launch

Claude: `--settings RUN/claude-settings.json` with `SessionStart`, `UserPromptSubmit`, `Notification`, `Stop`, `SessionEnd` hooks that run `rex_assign.py notify <id> --ev <session_start|prompt|blocked|stop|end> [--osc]`. Each appends one line to `RUN/turns.jsonl`: `{"ev","ts","hook_event_name","notification_type","message","session_id"}`. `Notification` carries `notification_type` (`permission_prompt`, `idle_prompt`, `elicitation_dialog`). With `--osc-status` the same hook writes `working`/`done`/`blocked:kind=permission` records with `app=claude-code` into Rex (**live**: `done` record visible after exit).

Codex: `-c 'notify=["python3","-I","<helper>","notify","<id>"]'`. Payloads: `agent-turn-complete` (`thread-id`, `turn-id`, `cwd`, `input-messages`, `last-assistant-message`) → `stop`; `approval-requested` → `blocked`. `codex queue --thread <thread-id> --message '…'` delivers a follow-up without typing into the pty (**live**, with `--no-daemon`).

Marker sequence seen in one session: `session_start prompt stop prompt stop prompt blocked prompt prompt blocked stop end`. The `end` marker can arrive after `child_exited`; treat the exit as authoritative.

## Launch and readiness

`launch --mode interactive --agent claude|codex …` runs `exec claude --model M --permission-mode P --settings RUN/claude-settings.json --session-id UUID --name AID` (or `exec codex --no-daemon -c notify=… -c shell_environment_policy.inherit=all`). Readiness: `process.foreground.name` is the agent, then a `session_start` marker or, after 20 s, the input prompt visible in `capture`; a trust-this-folder or permission dialog on screen → `blocked:startup-dialog` (hooks do not run behind it; a person must answer; **live**: a fresh git directory triggers it, print mode does not); still a shell after 30 s → `failed:launch` with the screen tail recorded (**live**: a prompt appeared ~55 s after launch once under load, so a `failed:launch` can be a slow start; `status` shows the truth).

## Send guards (all must pass, exit 6 names the failing one)

`not-owner` (record's owner block ≠ your `REX_BLOCK` or `--as-owner`), `foreground-not-agent`, `blocked` (newest `blocked` marker newer than newest `stop`/`interrupted`, or a Rex `blocked` record), `dialog-open` (a permission or trust dialog is on the screen, Claude or Codex wording), `input-not-empty`, `not-ready`. A record that never reached `ready` (slow start, dialog answered later) is re-checked by `status` and by `send` itself, which set `ready_at` once the agent is in the foreground at its prompt. `send` then snapshots the screen hash, records `sent_at`, and does `rex send` + `send-key enter`. Prompts are one line pointing at a file. After an interrupt, check `capture` for leftover text in the input box before sending (**live**).

## Watch outcomes

| Outcome | Evidence required | Means |
|---|---|---|
| `blocked:<type>` | `blocked` marker newer than `sent_at` (wins over everything) | a person must answer; never `send-key enter` on a dialog |
| `exited` | `child_exited` event | the worker is gone; see Mode A recovery |
| `turn-ended:hook` | `stop` marker newer than `sent_at` | the agent ended that turn |
| `turn-ended:status` | Rex `done`/`error` record with `app` newer than `sent_at` (Codex native, or Claude with `--osc-status`) | same, from Rex's side |
| `turn-ended:heuristic` | bell **and** `idle` record after `sent_at`, no marker | weak; say so |
| `taken-over:person` | a `prompt` marker after `sent_at` that is not one of this lead's sends | a person is driving the worker; stop, report, do not re-send (**live**: seen when a person rejected a dialog and kept typing) |
| `no-fresh-evidence` | nothing newer than `sent_at` within `--for` | unknown; `status`, then decide |

`record.state` is a cache written by the last command; `status` recomputes `live.outcome` and `live.blocked_open` from the markers and Rex records now, and the two can differ when a person acted in between. No turn end is task completion. Completion still needs the artifact the brief asked for (`REPORT.md`, `review.json`, …). An interrupt yields no `stop`, so `watch` after `cancel` (without `--close`) reports `no-fresh-evidence`, which is correct.

## Cancel vs interrupt

`cancel <id>` sends Ctrl+C: for an interactive worker that interrupts the turn and leaves the agent running (`state: ready`, `interrupted_at` set). `cancel --close` closes the block and ends the worker. Exit cleanly instead with `send --text /exit` (Claude) or `/quit` (Codex) and let `child_exited` arrive.

## Shared panes

The worker's pane is visible in the Rex app, and the person at the keyboard can answer a dialog, type, or close the block at any time (**live**: both happened during testing). A `blocked` outcome that clears without a marker, or a block that is suddenly `not found`, usually means a person acted. Say so in the report instead of guessing.

## Open items

- Codex per-launch `notify` did not fire when launched by the helper (interpreter, run-dir path and writable-root were ruled in and out without a clean result); the manual `sh -c echo` form did fire in experiments. Codex's native `app=codex` status records carry `watch` (`turn-ended:status`) in the meantime; `codex queue` therefore has no thread id from markers yet (read it from `~/.codex/sessions` only if the user allows).

- `turn-ended:heuristic` has not been observed in a real run (**assumption** that bell plus idle is the right pair); hooks were present in every live test.
- Codex Desktop driving a Mode B worker end to end is untested.
