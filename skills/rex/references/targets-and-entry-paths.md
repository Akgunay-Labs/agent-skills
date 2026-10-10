# Targets and entry paths

Evidence labels: **live** = observed on 2026-10-10 against Rex `0.0.1791515941-gd9a4b5`; **docs** = https://www.superlogical.com/rex/docs; **assumption** = not yet verified.

## Resources and IDs (docs)

Session → window (tab) → block (terminal). Clients are connected programs (the app, each `rex` call). IDs are stable and never reused: `session:01a1…`, `window:01a1…`, `block:01a1…`, `client:01a1…`. Targets resolve in this order and stop at the first kind that matches: full ID, case-insensitive label, window ordinal, 4–32 char hex ID suffix. An ambiguous target exits 3 and changes nothing.

| Exit | Meaning |
|---|---|
| 0 | ok |
| 1 | failed (also `rex wait` timeout, and the child's own status for `wait`/`run --wait`) |
| 2 | bad command line (also printed when a waited child exits 2, a CLI quirk; **live**) |
| 3 | target not found or ambiguous |
| 4 | server unreachable |
| 130 | interrupted |

`rex … --json` is the stable form. `rex new --json` → `.session_id`; `rex split --json` → `.block_ids[0]`; `rex run --json` → `.block_ids[0]`, `.window_id`.

## Path 1: lead inside a Rex pane (live)

```sh
rex whoami --json | jq '{session_id, window_id, block_id, instance_id, host_id}'
```
Rex sets `REX_SESSION`, `REX_BLOCK`, `REX_SERVER`, `REX_BIN_DIR` in every terminal it starts; there is no `REX_CLIENT`. Subshells and `cd` keep them, but a pane restored after a Rex restart has new IDs: bind once per task from `whoami`, not from memory.

## Path 2: outside controller (live)

No `REX_*`. `rex` may not be on PATH: use `"$REX_BIN_DIR/rex"` when set, else the app bundle binary `/Applications/Rex Beta.app/Contents/Helpers/Rex Server.app/Contents/MacOS/rex`. The default server is the local socket, so no `-S` is needed on the same machine.

```sh
REX="/Applications/Rex Beta.app/Contents/Helpers/Rex Server.app/Contents/MacOS/rex"
"$REX" whoami --json                    # session_id/block_id are null here: no implicit pane
"$REX" ls --json                        # choose by full session_id the user named
"$REX" new rex-work --cwd "$DIR" --json # or create your own session
"$REX" block ls -s "$S" --json          # anchor candidates; never the focused block
"$REX" run -s "$S" --focus=false --keep-open --label exec-1 --cwd "$DIR" --json -- sh -c 'exec …'
```
Ambiguity rule: with several sessions and no instruction, stop and list them; or create a session and say so. Creating your own session is always acceptable; guessing is not.

## Path 3: Codex as the lead or controller (live)

Codex's `shell_environment_policy.inherit = "core"` strips `REX_*` from the commands it runs, even inside a Rex pane; `rex` itself stays on PATH there. Fixes, all per launch:

- Interactive in a pane: `codex --no-daemon -c shell_environment_policy.inherit=all`. `--no-daemon` goes before any subcommand.
- Headless: `codex exec --approve-for-me -c shell_environment_policy.inherit=all --skip-git-repo-check …`. Without `--approve-for-me` (or an approval policy), exec mode rejects every command that would need approval ("approval is not supported in exec mode").
- Codex Desktop runs commands through a shared daemon whose environment is whatever started it; treat it as Path 2 and bind explicitly. (Source-backed in planning; end-to-end run still **assumption**.)

## The three permissions (live for the first two)

| Thing | Gate | Symptom when missing |
|---|---|---|
| Direct server ops: `new`, `split`, `run`, `send`, `send-key`, `capture`, `wait`, `block call`, `block close`, `events` | none beyond reaching the socket | exit 4 |
| App actions: `rex do pane.split`, `client.perform` | Rex app running with Remote Control on (Settings) | `not_supported`/refused, or the action silently does not happen; a timed-out `client.perform` may still run later, so inspect the layout before retrying |
| Codex sandbox reaching the Rex socket | Codex host-tool approval | `rex` commands fail inside Codex only |

"Direct ops work with Remote Control off" is **assumption**: verified only with it on.

## Server identity

`rex whoami --json` → `instance_id` (and `host_id`). Store it with every record. A later mismatch means the server restarted: all stored session/block IDs are stale (**live**: observed between 2026-10-09 and 2026-10-10). `rex server status --json` reports the same fields.

## Multiple sessions, labels, suffixes

`rex ls --json` lists sessions. Labels are convenient at a prompt but can change and repeat; suffixes are 6 chars by default and can collide. Resolve a label to a full ID once (`jq -r '.sessions[] | select(.label=="x") | .session_id'`) and use the ID from then on. The helper refuses `--session` values that are not full IDs for this reason.
