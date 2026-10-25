# Field notes: `rex` skill, first real-use test

Written 2026-10-10 as `skills/skill-notes-rex/NOTES.md` and moved here when the skill was retired
on 2026-10-25 (see `RETIRED.md`). They record one end-to-end use of the skill (then `skills/rex`)
and the changes it suggested; none were made before retirement.

## Test context

| Item | Value |
|---|---|
| Date | 2026-10-10 |
| Rex | `0.0.1791573294-gba791b` (SKILL.md pins `0.0.1791564903-g7abb0a`; the newer build worked unchanged) |
| Controller | Claude Code `2.1.296`, running **inside a Rex pane** (lead-in-rex) |
| Worker | `claude -p` (Mode A, process per assignment), split right of the lead's block |
| Task | Standalone research: "should qlty.sh join this app repo's quality workflow?" Read-only on the repo, no installs, web research, one deliverable `REPORT.md` |
| Outcome | Exit 0 after 6m40s, 67 turns, about $2.32. `REPORT.md` was written (26 KB, solid content). State `completed-with-denials` (2 denials). Block closed with `cancel --close`. |

## What was run (placeholders, no real IDs)

```sh
rex whoami --json      # -> session_id, block_id, instance_id; store them
rex ls --json          # exactly one session, so it is unambiguous
python3 -I <skill>/scripts/rex_assign.py launch --role executor --agent claude \
  --session <session:…> --anchor <block:… (lead)> --split right --cwd <repo> \
  --brief <brief.md> --deliverable REPORT.md --label <label> --permission default \
  --allow WebSearch --allow WebFetch --allow Read --allow Grep --allow Glob --allow Write \
  --allow 'Bash(ls:*)' --allow 'Bash(git log:*)' --allow 'Bash(git show:*)' --allow 'Bash(rg:*)' \
  --allow 'Bash(swiftlint version)'
python3 -I <skill>/scripts/rex_assign.py wait <assignment-id> --for 30m   # in the background
python3 -I <skill>/scripts/rex_assign.py status <assignment-id>           # mid-run check
python3 -I <skill>/scripts/rex_assign.py cancel <assignment-id> --close   # finish
```

The brief ended with the required line: "Write `REPORT.md` in `$RUN_DIR` with … what you did not do."

## What worked (keep)

- Binding was smooth. `whoami --json` gave every ID and `ls --json` showed one session; with a full ID on
  every call, there was no ambiguity.
- `launch` created and recorded the block in one step and printed the full record. `--deliverable` turned
  "process ended" into a real completion check.
- `wait` returned promptly on exit with `state`, `exit.code`, and a one-line `summary`
  (`denied tools: Bash, Edit`).
- **`completed-with-denials` was the right label.** It surfaced a clean exit that still had permission
  problems instead of calling it success.
- `cancel --close` honored ownership and recorded `closed_at`.
- The server-identity check in `status` (`"server": "same instance"`) is useful.

## Issues and suggested changes

Ordered by impact. Each entry has: observed → evidence → suggested change → where.

### 1. Mode A gives no progress signal at all (medium)

- **Observed:** the worker pane stayed blank for the whole run. The user asked "is it still running?"
  about four minutes in.
- **Evidence:**
  - Mid-run `status` showed `turns_tail: []` and `capture_tail: [""]`.
  - The record's `paths.turns` names a `turns.jsonl` that **was never created**. The run dir held only
    `assignment.json`, `brief.md`, `result.json` and `REPORT.md`.
  - `result.json` existed but was empty (0 bytes) until exit.
  - The only proof of life was the process table.
- **Suggest (pick one or both):**
  - **(a) Stream turns.** Launch `claude -p --output-format stream-json --verbose` and tee it to
    `turns.jsonl`, then build `result.json` from the final `result` event. `status` can then show the last
    tool calls, and you get a tool-usage log (see #4).
  - **(b) Show a banner.** Have the worker's `sh -c` print one line before `exec`, e.g.
    `headless claude -p — no live output; result on exit; run dir: …`, so a blank pane is explained.
- **Where:** `scripts/rex_assign.py` (launch command construction, `status`), and
  `references/assignment-lifecycle.md`.

### 2. `Write` without `Edit` blocks the worker from correcting its own deliverable (medium)

- **Observed:** the worker tried to fix a factual error in `REPORT.md` with `Edit`. It was denied, so
  the error shipped. The report says "860-plus-file Swift app"; the real count is about 520 (348 app +
  168 test files).
- **Evidence:** `result.json` `permission_denials[]` holds an `Edit` on `<run_dir>/REPORT.md`, with the
  intended old and new strings.
- **Suggest:** when `--deliverable` is given, have `launch` add scoped rules automatically, e.g.
  `Write(<run_dir>/**)` and `Edit(<run_dir>/**)` (check the exact path-rule syntax for the pinned Claude
  Code version), so the deliverable is always writable and editable without widening repo access. In
  the meantime, document "allow both Write and Edit".
- **Where:** `scripts/rex_assign.py` (permission policy assembly); the SKILL.md §3 step 2 note.

### 3. Compound shell commands are denied under per-command allowlists (low–medium)

- **Observed:** one survey command chaining `ls; find; python3 …` was auto-denied, because it matches
  no single `Bash(prefix:*)` rule. The worker recovered with Glob, Grep and Read.
- **Evidence:** `permission_denials[]` holds that `Bash` entry.
- **Suggest:** add one sentence to the brief guidance: "Under an allowlist, use one command per call and
  prefer Read/Grep/Glob; chained shell commands are denied." Optionally `launch --brief-footer` could
  append it.
- **Where:** `references/assignment-lifecycle.md` (brief template), SKILL.md §3 step 1.

### 4. The worker's method claims can't be checked, and one was false (medium)

- **Observed:** `REPORT.md`'s "what I checked" section says it read Qlty's source "through read-only
  `gh api` / `gh search code` calls". `gh` was not in its allowlist, and `permission_denials` shows no
  `gh` attempt, so it never ran `gh`. It most likely used `WebFetch` on GitHub URLs. The content was
  still good; the self-report was not.
- **Evidence:** the allowlist in `assignment.json` vs `permission_denials`. `result.json` has no
  per-tool usage list (keys include `num_turns`, `usage`, `modelUsage`, `subagent_stats`, and nothing
  per tool).
- **Suggest:**
  - Capture tool usage, via the stream-json from #1, and have `wait` print a short "tools used" tally
    next to "denied tools".
  - Add "list the tools and commands you actually ran" to the deliverable footer.
  - SKILL.md says a standalone research task ends at deliverable + `completed-unreviewed`. Consider
    "spot-check method claims against the tool log" as a cheap optional step, short of a full reviewer.
- **Where:** `scripts/rex_assign.py` (`wait`/`status` summary), SKILL.md §3 step 4, §4 table.

### 5. Name-based process checks give false positives; add to "what a signal proves" (low; a controller error)

- **Observed:** the lead checked liveness with `pgrep -fl 'claude.*-p'`. It matched the real worker
  **and the lead's own shell**, whose command line contained the pattern. The lead then wrongly
  reported "the worker has a live child shell, mid tool call".
- **Evidence:** a later `pgrep -P <worker-pid>` showed no children, and the "child" was gone with
  the lead's command.
- **Suggest:** add a row to the SKILL.md §4 table: "`pgrep -f`/`ps | grep` match | that some process
  has that text in its command line | that it's the worker (it can match the controller's own shell);
  use the recorded pid or `pgrep -P <pid>`." Optionally record the worker pid in `assignment.json` so
  `status` can report it directly.
- **Where:** SKILL.md §4; `scripts/rex_assign.py` (record `worker.pid`).

### 6. Small record gaps (low)

- `target.window_id` is `null` even though the worker block lives in a known window. Record it at
  launch.
- `owner.controller` is `unknown` for a lead running inside Rex as Claude Code. Fill it in where it can
  be detected.
- `launch` prints the whole record, which is noisy for a lead. Consider a short summary
  (`assignment_id`, `state`, `run_dir`, `block_id`) with `--json` for the full record.
- **Where:** `scripts/rex_assign.py`.

### 7. Version pin is behind the tested build (low)

- SKILL.md pins `0.0.1791564903-g7abb0a`; this run used `0.0.1791573294-gba791b` with no command
  failures. Bump the pin (and the "Tested against" line) when the next change lands.

## Lead-side lessons (for anyone driving the skill)

- **Tell the user which mode you picked and what they'll see.** Mode A means a blank pane until exit.
  If the user wants to watch, use `--mode interactive` and expect permission prompts to need a person.
- **Treat a blank pane as no signal.** Evidence, in order: `status` (`process.foreground`,
  `last_exit`), then the recorded pid, then `wait`'s exit and the deliverable.
- **Compare the worker's "what I did" against the permissions and denials** before relaying it.

## Status of these notes

- Nothing above has been applied to `skills/rex` yet. The suggestions are proposals.
- Suggested PR grouping:
  1. Permissions and briefing: #2 and #3.
  2. Observability: #1, #4, and the pid part of #5.
  3. Docs, record and pin: the rest of #5, #6 and #7.
