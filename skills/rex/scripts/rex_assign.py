#!/usr/bin/env python3
"""rex_assign.py: launch, observe, and collect agent assignments in Rex blocks.

Subcommands: launch | wait | status | send | watch | notify | cancel | resume | review | list
Run with: python3 -I rex_assign.py <subcommand> --help

Every mutating rex call carries an explicit session (and block). Nothing here
infers a target from the focused pane. States never say "accepted": only a
reviewer's review.json does that.
"""
import argparse, datetime, hashlib, json, os, pathlib, re, shlex, subprocess, sys, threading, time, uuid

RUN = subprocess.run  # test seam
HELPER = pathlib.Path(__file__).resolve()
RUNS_ROOT = pathlib.Path(os.environ.get("REX_ORCH_RUNS", str(pathlib.Path.home() / ".local/state/rex-orch/runs")))
REX_FALLBACK = "/Applications/Rex Beta.app/Contents/Helpers/Rex Server.app/Contents/MacOS/rex"
AGENTS = ("claude", "codex")
TERMINAL_STATES = ("completed-unreviewed", "completed-with-denials", "failed", "exited-without-result", "timeout", "cancelled")
EXIT_USAGE, EXIT_TARGET, EXIT_SERVER, EXIT_MARKER, EXIT_GUARD = 2, 3, 4, 5, 6


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rex_bin():
    if os.environ.get("REX_BIN_DIR"):
        return str(pathlib.Path(os.environ["REX_BIN_DIR"]) / "rex")
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if (pathlib.Path(d) / "rex").is_file():
            return "rex"
    return REX_FALLBACK


class Policy:
    """Launch-time permission policy. mode: Claude --permission-mode or Codex sandbox; allow: Claude --allowedTools."""

    def __init__(self, mode, allow=()):
        self.mode, self.allow = mode, list(allow)


# ----------------------------------------------------------------- building commands

def brief_pointer(run_dir):
    return f"Read {run_dir}/brief.md and do exactly what it says."


PYTHON = sys.executable  # absolute: hook and notify subprocesses may run with a minimal PATH inside a sandbox


def codex_notify_override(assignment_id, helper=HELPER):
    return "notify=" + json.dumps([PYTHON, "-I", str(helper), "notify", assignment_id])


def interactive_claude_settings(assignment_id, helper=HELPER, osc=False):
    def hook(ev):
        cmd = f"{shlex.quote(PYTHON)} -I {shlex.quote(str(helper))} notify {assignment_id} --ev {ev}" + (" --osc" if osc else "")
        return [{"hooks": [{"type": "command", "command": cmd}]}]
    return {"preferredNotifChannel": "terminal_bell",
            "hooks": {"SessionStart": hook("session_start"), "UserPromptSubmit": hook("prompt"),
                      "Notification": hook("blocked"), "Stop": hook("stop"), "SessionEnd": hook("end")}}


def stage_helper(run_dir):
    """Copy this script into the run dir. Hooks and Codex notify run inside sandboxes that may not read
    the skill's real location (e.g. a TCC-protected ~/Documents); the run dir under ~/.local/state is safe."""
    import shutil
    dst = pathlib.Path(run_dir) / "rex_assign.py"
    shutil.copyfile(HELPER, dst)
    return dst


def build_worker_command(agent, mode, policy, run_dir, agent_session_id, model=None, effort=None,
                         worktree=False, cwd=None, assignment_id=None, resume_of=None, prompt=None, helper=None):
    """Return the `sh -c` payload. Starts with exec so the agent is the block's foreground process."""
    run_dir = pathlib.Path(run_dir)
    aid = assignment_id or run_dir.name
    prompt = prompt or brief_pointer(run_dir)
    q = shlex.quote
    if agent == "claude":
        parts = ["exec", "claude"]
        if mode == "process":
            parts += ["-p"]
        if model:
            parts += ["--model", model]
        if effort:
            parts += ["--effort", effort]
        parts += ["--permission-mode", policy.mode]
        if worktree:
            parts += ["--worktree"]
        if mode == "process":
            parts += ["--permission-prompts", "none", "--add-dir", str(run_dir)]
            if policy.allow:
                parts += ["--allowedTools", *policy.allow]
            parts += ["--output-format", "json"]
            parts += (["--resume", resume_of] if resume_of else ["--session-id", agent_session_id])
            parts += ["--name", aid, prompt]  # --name is not variadic, so the prompt is safe after it
            return " ".join(q(p) for p in parts) + " > " + q(str(run_dir / "result.json"))
        parts += ["--settings", str(run_dir / "claude-settings.json"), "--session-id", agent_session_id, "--name", aid]
        return " ".join(q(p) for p in parts)
    if agent == "codex":
        if mode == "process":
            parts = ["exec", "codex", "exec"]
            if resume_of:
                parts += ["resume", resume_of]
            parts += ["--approve-for-me", "-c", "shell_environment_policy.inherit=all", "--skip-git-repo-check",
                      "--json", "-o", str(run_dir / "last-message.md"), "--add-dir", str(run_dir)]
            if model:
                parts += ["-m", model]
            if cwd:
                parts += ["-C", cwd]
            if worktree:
                parts += ["--worktree"]
            parts += [prompt]
            return " ".join(q(p) for p in parts) + " > " + q(str(run_dir / "events.jsonl"))
        parts = ["exec", "codex", "--no-daemon", "-c", codex_notify_override(aid, helper or HELPER), "-c", "shell_environment_policy.inherit=all",
                 "-c", "sandbox_workspace_write.writable_roots=" + json.dumps([str(run_dir)])]  # notify appends to RUN_DIR/turns.jsonl from inside the sandbox
        if model:
            parts += ["-m", model]
        return " ".join(q(p) for p in parts)
    raise ValueError(f"unknown agent {agent}")


def build_rex_create_argv(session_id, anchor, split, new_window, label, cwd, payload, rex="rex"):
    if new_window:
        argv = [rex, "run", "-s", session_id]
    else:
        argv = [rex, "split", "-s", session_id, "-b", anchor, "--split", split or "right"]
    argv += ["--focus=false", "--keep-open", "--label", label, "--cwd", cwd, "--json", "--", "sh", "-c", payload]
    return argv


# ----------------------------------------------------------------- classification

def classify(exit_code, result_path, agent):
    result_path = pathlib.Path(result_path)
    if exit_code is None:
        return "timeout", "wait expired; worker may still be running"
    if exit_code != 0:
        return "failed", f"worker exited {exit_code}"
    if not result_path.exists() or result_path.stat().st_size == 0:
        return "exited-without-result", f"exit 0 but {result_path.name} missing or empty"
    if agent == "claude":
        try:
            r = json.loads(result_path.read_text())
        except Exception as e:  # noqa: BLE001
            return "exited-without-result", f"result.json unreadable: {e}"
        if r.get("is_error") or r.get("subtype") != "success":
            return "failed", f"subtype={r.get('subtype')} is_error={r.get('is_error')}"
        denials = r.get("permission_denials") or []
        if denials:
            tools = sorted({d.get("tool_name", "?") for d in denials})
            return "completed-with-denials", "denied tools: " + ", ".join(tools)
        return "completed-unreviewed", (r.get("result") or "")[:200]
    # codex: result_path is events.jsonl, last message beside it
    lines = [l for l in result_path.read_text().splitlines() if l.strip()]
    types = [json.loads(l).get("type") for l in lines if l.startswith("{")]
    last = result_path.with_name("last-message.md")
    if "turn.completed" in types and last.exists() and last.read_text().strip():
        return "completed-unreviewed", last.read_text()[:200]
    if "turn.completed" in types:
        return "exited-without-result", "turn completed but last-message.md is empty (no deliverable reported)"
    return "exited-without-result", "no turn.completed in events.jsonl"


def resolve_owner(as_owner, whoami, external=False):
    """(kind, block_id): an explicit --as-owner always wins; --external ignores any inherited pane;
    otherwise the caller's own pane from whoami."""
    if as_owner:
        return "external", as_owner
    if external or not whoami.get("block_id"):
        return "external", None
    return "lead-in-rex", whoami.get("block_id")


def render_brief(text, run_dir):
    """Substitute $RUN_DIR / ${RUN_DIR} so a brief can name its own run directory before it exists."""
    return text.replace("${RUN_DIR}", str(run_dir)).replace("$RUN_DIR", str(run_dir))


INPUT_PLACEHOLDERS = ("Ask Codex to do anything", "Try \"")  # empty-input hints the TUIs render


def input_has_text(capture):
    """True when the agent's input box (last line starting with the prompt glyph) holds real text."""
    last = None
    for line in capture.splitlines():
        st = line.strip()
        if st.startswith(("❯", "›")):
            last = st
    if not last:
        return False
    body = last[1:].strip()
    return bool(body) and not body.startswith(INPUT_PLACEHOLDERS)


def session_id_for(agent, resume_of, fresh):
    """Claude: the session id the worker really runs under (resume keeps the original). Codex: none (thread id comes from events)."""
    if agent != "claude":
        return None
    return resume_of or fresh


def apply_deliverable(state, deliverable_path):
    """A completed worker without the file the brief asked for is not complete."""
    if state == "completed-unreviewed" and deliverable_path is not None and not pathlib.Path(deliverable_path).exists():
        return "completed-without-deliverable"
    return state


def apply_review(rec, review, reviewer_id):
    """Record a reviewer's verdict on an executor record. Only this moves a record to accepted."""
    if not str(rec.get("state", "")).startswith("completed"):
        raise ValueError(f"cannot review an assignment in state {rec.get('state')!r}")
    verdict = review.get("verdict")
    if verdict not in ("accepted", "changes-requested"):
        raise ValueError(f"review.json verdict must be accepted or changes-requested, got {verdict!r}")
    rec["review"] = {"reviewer_assignment_id": reviewer_id, "verdict": verdict, "findings": review.get("findings", []), "at": now_iso()}
    rec["state"] = verdict
    return rec


def check_server(stored_server, whoami):
    if stored_server.get("instance_id") and stored_server.get("instance_id") != whoami.get("instance_id"):
        return "abandoned:server-restarted"
    return None


# ----------------------------------------------------------------- mode B logic

def _latest(turns, ev):
    ts = [t.get("ts", "") for t in turns if t.get("ev") == ev]
    return max(ts) if ts else ""


def _blocked_open(turns):
    """A blocked marker is open until a newer stop or interrupted marker."""
    return _latest(turns, "blocked") > max(_latest(turns, "stop"), _latest(turns, "interrupted"))


def send_guards(record, process, turns, caller_block, capture=None):
    failing = []
    if record.get("owner", {}).get("block_id") != caller_block:
        failing.append("not-owner")
    fg = (process.get("foreground") or {}).get("name")
    if fg not in AGENTS:
        failing.append("foreground-not-agent")
    if _blocked_open(turns):
        failing.append("blocked")
    if not record.get("ready_at"):
        failing.append("not-ready")
    if capture is not None and input_has_text(capture):
        failing.append("input-not-empty")
    return failing


def _is_own_prompt(ts, sends, window_s=8):
    """A prompt marker within a few seconds after one of our sends is ours; anything else was typed by someone."""
    for s in sends or []:
        try:
            d = (datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")) - datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))).total_seconds()
        except ValueError:
            continue
        if 0 <= d <= window_s:
            return True
    return False


def watch_outcome(turns, events, sent_at, sends=None):
    fresh_t = [t for t in turns if t.get("ts", "") > sent_at]
    fresh_e = [e for e in events if e.get("ts", "") > sent_at]
    evidence = [f"turn:{t.get('ev')}@{t.get('ts')}" for t in fresh_t] + [f"event:{e.get('name')}@{e.get('ts')}" for e in fresh_e]
    if sends is not None:
        foreign = [t for t in fresh_t if t.get("ev") == "prompt" and not _is_own_prompt(t.get("ts", ""), sends)]
        if foreign:
            return "taken-over:person", evidence + [f"foreign prompt at {t.get('ts')} (not one of this lead's sends)" for t in foreign]
    if _blocked_open(fresh_t):
        t = [x for x in fresh_t if x.get("ev") == "blocked"][-1]
        return f"blocked:{t.get('notification_type') or 'unknown'}", evidence
    if any(e.get("name") == "child_exited" for e in fresh_e):
        return "exited", evidence
    if any(t.get("ev") == "stop" for t in fresh_t):
        return "turn-ended:hook", evidence
    for e in fresh_e:
        rec = e.get("record") or {}
        if e.get("name") == "program_status_changed" and rec.get("state") in ("done", "error") and rec.get("app"):
            return "turn-ended:status", evidence + [f"native status app={rec.get('app')} state={rec.get('state')}"]
    bell = any(e.get("name") == "bell" for e in fresh_e)
    idle = any(e.get("name") == "program_status_changed" and (e.get("record") or {}).get("state") == "idle" for e in fresh_e)
    if bell and idle:
        return "turn-ended:heuristic", evidence + ["heuristic: bell and idle after sent_at, no hook marker"]
    return "no-fresh-evidence", evidence


STARTUP_DIALOGS = ("trust the files in this folder", "Yes, I trust", "Do you want to proceed", "Esc to cancel")


def readiness(process, turns, capture, elapsed, agent):
    fg = (process.get("foreground") or {}).get("name")
    if fg != agent:
        return "failed:launch" if elapsed > 30 else "starting"
    if any(m in capture for m in STARTUP_DIALOGS):
        return "blocked:startup-dialog"
    if any(t.get("ev") == "session_start" for t in turns):
        return "ready"
    if elapsed >= 20 and any(m in capture for m in ("❯", "›", "shortcuts")):
        return "ready"
    return "starting"


# ----------------------------------------------------------------- rex plumbing

def rex(*args, json_out=True, timeout=None):
    argv = [rex_bin(), *args]
    r = RUN(argv, capture_output=True, text=True, timeout=timeout)
    data = None
    if json_out and r.stdout.strip().startswith(("{", "[")):
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            data = None
    return r.returncode, data, r.stdout, r.stderr


def block_call(session_id, block_id, method):
    rc, data, out, err = rex("block", "call", method, "-s", session_id, "-b", block_id)
    return rc, data if data is not None else {}


def record_path(aid):
    return RUNS_ROOT / aid / "assignment.json"


def load_record(aid):
    p = record_path(aid)
    if not p.exists():
        sys.exit(f"no assignment {aid} under {RUNS_ROOT}")
    return json.loads(p.read_text())


def save_record(rec):
    p = record_path(rec["assignment_id"])
    p.write_text(json.dumps(rec, indent=2) + "\n")


def read_turns(run_dir):
    p = pathlib.Path(run_dir) / "turns.jsonl"
    if not p.exists():
        return []
    out = []
    for l in p.read_text().splitlines():
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            pass
    return out


# ----------------------------------------------------------------- subcommands

def cmd_launch(a):
    if not a.session:
        print("launch needs --session session:<id> (resolve labels first with: rex ls --json)", file=sys.stderr)
        return EXIT_USAGE
    if not a.session.startswith("session:"):
        print(f"--session must be the full session:<uuid> ID, not {a.session!r}; resolve labels with rex ls --json so ambiguity is visible", file=sys.stderr)
        return EXIT_USAGE
    if not a.new_window and not a.anchor:
        print("launch needs --anchor block:<id> (split beside it) or --new-window; it never uses the focused block", file=sys.stderr)
        return EXIT_USAGE
    if a.anchor and not a.anchor.startswith("block:"):
        print("--anchor must be a full block:<uuid> ID", file=sys.stderr)
        return EXIT_USAGE
    aid = a.assignment_id or f"a-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    run_dir = RUNS_ROOT / aid
    if (run_dir / "launching").exists() and not (run_dir / "assignment.json").exists():
        print(f"launch marker exists for {aid} without a record: inspect the session layout for an orphan block before retrying (rex block ls -s {a.session} --json), then move {run_dir} to Trash", file=sys.stderr)
        return EXIT_MARKER
    if run_dir.exists() and any((run_dir / f).exists() for f in ("result.json", "assignment.json", "events.jsonl")):
        print(f"run dir {run_dir} already holds results; use a fresh --assignment-id", file=sys.stderr)
        return EXIT_MARKER
    rc, who, out, err = rex("whoami", "--json")
    if rc != 0 or not who:
        print(f"rex whoami failed ({rc}): {err.strip()}", file=sys.stderr)
        return EXIT_SERVER
    run_dir.mkdir(parents=True, exist_ok=True)
    brief_text = pathlib.Path(a.brief).read_text() if a.brief != "-" else sys.stdin.read()
    (run_dir / "brief.md").write_text(render_brief(brief_text, run_dir))
    policy = Policy(a.permission or ("acceptEdits" if a.agent == "claude" else "workspace-write"), a.allow or [])
    agent_session_id = session_id_for(a.agent, a.resume_of, str(uuid.uuid4()))
    helper = stage_helper(run_dir) if a.mode == "interactive" else HELPER
    if a.mode == "interactive" and a.agent == "claude":
        (run_dir / "claude-settings.json").write_text(json.dumps(interactive_claude_settings(aid, helper, osc=a.osc_status), indent=2))
    payload = build_worker_command(a.agent, a.mode, policy, run_dir, agent_session_id, model=a.model, effort=a.effort,
                                   worktree=a.worktree, cwd=a.cwd, assignment_id=aid, resume_of=a.resume_of, prompt=a.prompt, helper=helper)
    argv = build_rex_create_argv(a.session, a.anchor, a.split, a.new_window, a.label or aid, a.cwd, payload, rex=rex_bin())
    (run_dir / "launching").write_text(now_iso() + "\n" + " ".join(shlex.quote(x) for x in argv) + "\n")
    r = RUN(argv, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"rex create failed ({r.returncode}): {r.stderr.strip()}\nmarker left at {run_dir / 'launching'}", file=sys.stderr)
        return EXIT_TARGET if r.returncode == 3 else (EXIT_SERVER if r.returncode == 4 else 1)
    created = json.loads(r.stdout)
    block_id = created["block_ids"][0]
    rec = {"assignment_id": aid, "role": a.role,
           "owner": dict(zip(("kind", "block_id"), resolve_owner(a.as_owner, who, external=a.external)), controller=a.controller),
           "server": {"endpoint": who.get("endpoint"), "instance_id": who.get("instance_id"), "host_id": who.get("host_id")},
           "target": {"session_id": a.session, "window_id": created.get("window_id"), "block_id": block_id, "label": a.label or aid},
           "worker": {"agent": a.agent, "mode": a.mode, "model": a.model, "effort": a.effort,
                      "permission_policy": {"mode": policy.mode, "allowed_tools": policy.allow, "prompts": "none" if a.mode == "process" else "interactive"},
                      "cwd": a.cwd, "agent_session_id": agent_session_id, "resume_of": a.resume_of},
           "paths": {"run_dir": str(run_dir), "brief": str(run_dir / "brief.md"),
                     "result": str(run_dir / ("result.json" if a.agent == "claude" else "events.jsonl")),
                     "review": str(run_dir / "review.json"), "turns": str(run_dir / "turns.jsonl"),
                     "deliverable": str(run_dir / a.deliverable) if a.deliverable else None},
           "state": "launched", "launched_at": now_iso(), "exit": None, "review": None, "sends": []}
    save_record(rec)
    (run_dir / "launching").unlink(missing_ok=True)
    if a.mode == "interactive":
        rec["state"] = wait_ready(rec)
        save_record(rec)
    print(json.dumps(rec, indent=2))
    return 0


def wait_ready(rec, limit=35):
    t0 = time.time()
    sid, b = rec["target"]["session_id"], rec["target"]["block_id"]
    agent = rec["worker"]["agent"]
    while True:
        _, proc = block_call(sid, b, "process")
        _, _, cap, _ = rex("capture", "-s", sid, "-b", b, "--trim", json_out=False)
        st = readiness(proc, read_turns(rec["paths"]["run_dir"]), cap, time.time() - t0, agent)
        if st == "ready":
            rec["ready_at"] = now_iso()
            return "ready"
        if st.startswith("blocked"):
            rec["last_capture"] = cap[-800:]
            return st  # a person must answer the dialog; send stays refused (not-ready)
        if st.startswith("failed") or time.time() - t0 > limit:
            rec["last_capture"] = cap[-800:]
            return "failed:launch"
        time.sleep(1.5)


def parse_wait(rc, out):
    payload = None
    for line in out.splitlines():
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                pass
    if payload is None:
        return None if rc == 1 else rc  # timeout prints no payload
    return payload.get("exit_code", rc)


def cmd_wait(a):
    rec = load_record(a.assignment_id)
    sid, b = rec["target"]["session_id"], rec["target"]["block_id"]
    rc, _, out, err = rex("wait", "-s", sid, "--for", a.for_, "--json", b, json_out=False)
    if rc == 3:
        rec["state"] = "abandoned:block-closed"; save_record(rec)
        print(json.dumps({"state": rec["state"], "detail": err.strip()})); return 1
    exit_code = parse_wait(rc, out)
    state, summary = classify(exit_code, rec["paths"]["result"], rec["worker"]["agent"])
    state = apply_deliverable(state, rec["paths"].get("deliverable"))
    if state == "completed-without-deliverable":
        summary = f"worker reported success but {rec['paths']['deliverable']} is missing; " + summary
    if state == "timeout" and a.cancel_on_timeout:
        do_cancel(rec, close=False); state = "cancelled"
    rec["exit"] = None if exit_code is None else {"code": exit_code, "at": now_iso()}
    rec["state"] = state; save_record(rec)
    out_obj = {"assignment_id": rec["assignment_id"], "state": state, "exit": rec["exit"], "summary": summary, "result_path": rec["paths"]["result"]}
    print(json.dumps(out_obj, indent=2))
    return 0 if state in ("completed-unreviewed", "completed-with-denials") else 1


def cmd_review(a):
    ex = load_record(a.assignment_id)
    src = pathlib.Path(a.from_file) if a.from_file else pathlib.Path(load_record(a.reviewer)["paths"]["run_dir"]) / "review.json"
    if not src.exists():
        print(f"no review.json at {src}", file=sys.stderr); return 1
    try:
        apply_review(ex, json.loads(src.read_text()), a.reviewer or str(src))
    except (ValueError, json.JSONDecodeError) as e:
        print(f"review not applied: {e}", file=sys.stderr); return 1
    save_record(ex)
    print(json.dumps({"assignment_id": ex["assignment_id"], "state": ex["state"], "review": ex["review"]}, indent=2)); return 0


def cmd_status(a):
    rec = load_record(a.assignment_id)
    sid, b = rec["target"]["session_id"], rec["target"]["block_id"]
    rc, who, _, _ = rex("whoami", "--json")
    stale = check_server(rec.get("server", {}), who or {}) if who else "server-unreachable"
    info = {"assignment_id": rec["assignment_id"], "state": rec["state"], "server": stale or "same instance"}
    rc, proc = block_call(sid, b, "process")
    if rc == 3:
        rec["state"] = "abandoned:block-closed"; save_record(rec); info["state"] = rec["state"]
        print(json.dumps(info, indent=2)); return 1
    info["process"] = {"foreground": (proc.get("foreground") or {}).get("name"), "last_exit": proc.get("last_exit")}
    _, st = block_call(sid, b, "program_status")
    recs = st.get("records", [])
    info["program_status"] = [{**r, "fresh": r.get("updated_at", "") > rec.get("launched_at", "")} for r in recs]
    turns = read_turns(rec["paths"]["run_dir"])
    info["turns_tail"] = turns[-5:]
    if rec["worker"]["mode"] == "interactive":
        last_sent = (rec.get("sends") or [{}])[-1].get("sent_at") or rec.get("launched_at", "")
        live, ev = watch_outcome(turns, [{"name": "program_status_changed", "ts": r.get("updated_at", ""), "record": r} for r in recs], last_sent, sends=[x["sent_at"] for x in rec.get("sends", [])])
        info["live"] = {"outcome": live, "blocked_open": _blocked_open(turns), "note": "record.state is a cache; live is recomputed from markers and status now"}
    _, _, cap, _ = rex("capture", "-s", sid, "-b", b, "--trim", json_out=False)
    info["capture_tail"] = cap.splitlines()[-20:]
    print(json.dumps(info, indent=2)); return 0


def cmd_send(a):
    rec = load_record(a.assignment_id)
    sid, b = rec["target"]["session_id"], rec["target"]["block_id"]
    caller = a.as_owner or os.environ.get("REX_BLOCK")
    rc, proc = block_call(sid, b, "process")
    if rc == 3:
        print("target block not found (closed?)", file=sys.stderr); return EXIT_TARGET
    turns = read_turns(rec["paths"]["run_dir"])
    _, st = block_call(sid, b, "program_status")
    if any(r.get("state") == "blocked" for r in st.get("records", [])):
        turns = turns + [{"ev": "blocked", "ts": "9999", "notification_type": "rex-status"}]
    _, _, cap, _ = rex("capture", "-s", sid, "-b", b, "--trim", json_out=False)
    failing = send_guards(rec, proc, turns, caller, capture=cap)
    if failing:
        hint = " (input box holds text: clear it first, e.g. rex send-key -s S -b B ctrl+u, or wait for the person)" if "input-not-empty" in failing else ""
        print("send refused; failing guards: " + ", ".join(failing) + hint, file=sys.stderr); return EXIT_GUARD
    text = a.text if a.text is not None else f"Read {a.brief} and do exactly what it says."
    sent = {"seq": len(rec.get("sends", [])) + 1, "sent_at": now_iso(), "capture_sha": hashlib.sha1(cap.encode()).hexdigest()[:12], "text": text[:200]}
    rc1, _, _, e1 = rex("send", "-s", sid, "-b", b, text, json_out=False)
    rc2, _, _, e2 = rex("send-key", "-s", sid, "-b", b, "enter", json_out=False)
    if rc1 or rc2:
        print(f"send failed: {e1.strip()} {e2.strip()}", file=sys.stderr); return 1
    rec.setdefault("sends", []).append(sent); rec["state"] = "working"; save_record(rec)
    print(json.dumps(sent)); return 0


def cmd_watch(a):
    rec = load_record(a.assignment_id)
    sid, b = rec["target"]["session_id"], rec["target"]["block_id"]
    sent_at = (rec.get("sends") or [{}])[-1].get("sent_at") or rec.get("launched_at", "")
    events, stop = [], threading.Event()

    def pump():
        p = subprocess.Popen([rex_bin(), "events", "-s", sid, "--json"], stdout=subprocess.PIPE, text=True)
        try:
            for line in p.stdout:
                if stop.is_set():
                    break
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pl = ((ev.get("payload") or {}).get("event") or {}).get("payload") or {}
                if pl.get("block_id") == b:
                    inner = pl.get("payload") or {}
                    events.append({"name": pl.get("name"), "ts": now_iso(), "record": inner.get("record"), "payload": inner})
        finally:
            p.kill()

    th = threading.Thread(target=pump, daemon=True); th.start()
    limit = parse_duration(a.for_); t0 = time.time(); outcome, evidence = "no-fresh-evidence", []
    while time.time() - t0 < limit:
        outcome, evidence = watch_outcome(read_turns(rec["paths"]["run_dir"]), list(events), sent_at, sends=[x["sent_at"] for x in rec.get("sends", [])])
        if outcome != "no-fresh-evidence":
            break
        time.sleep(2)
    stop.set()
    rec["last_watch"] = {"outcome": outcome, "evidence": evidence, "at": now_iso()}
    if outcome.startswith("blocked") or outcome.startswith("taken-over"):
        rec["state"] = outcome
    elif outcome == "exited":
        rec["state"] = "exited"
    elif outcome.startswith("turn-ended"):
        rec["state"] = "ready"
    save_record(rec)
    print(json.dumps({"assignment_id": rec["assignment_id"], "outcome": outcome, "evidence": evidence}, indent=2))
    return 0 if outcome.startswith("turn-ended") else 1


def cmd_notify(a):
    run_dir = RUNS_ROOT / a.assignment_id
    run_dir.mkdir(parents=True, exist_ok=True)
    raw = a.payload if a.payload and a.payload.startswith("{") else (sys.stdin.read() if not sys.stdin.isatty() else "")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {"raw": raw[:500]}
    ev = a.ev
    if not ev:  # codex notify payloads
        ev = {"agent-turn-complete": "stop", "approval-requested": "blocked"}.get(data.get("type"), data.get("type", "unknown"))
    line = {"ev": ev, "ts": now_iso(), "hook_event_name": data.get("hook_event_name"), "notification_type": data.get("notification_type"),
            "message": (data.get("message") or data.get("last-assistant-message") or "")[:300],
            "session_id": data.get("session_id") or data.get("thread-id")}
    with (run_dir / "turns.jsonl").open("a") as f:
        f.write(json.dumps({k: v for k, v in line.items() if v is not None}) + "\n")
    if a.osc:
        state = {"prompt": "working", "stop": "done", "blocked": "blocked:kind=permission", "end": "clear", "session_start": "idle"}.get(ev)
        if state:
            try:
                tty = subprocess.run(["ps", "-o", "tty=", "-p", str(os.getppid())], capture_output=True, text=True).stdout.strip()
                if tty and tty != "??":
                    with open(f"/dev/{tty}", "w") as t:
                        t.write(f"\033]7501;state={state}:app=claude-code\033\\")
            except OSError:
                pass
    return 0


def cancel_state(prev_state, alive, close):
    """State after a cancel: terminal states are kept (closing a finished block changes nothing);
    a live interactive worker that was only interrupted stays ready; otherwise cancelled."""
    if prev_state in TERMINAL_STATES or prev_state.startswith("abandoned"):
        return prev_state
    if alive and not close:
        return "ready"
    return "cancelled"


def do_cancel(rec, close):
    sid, b = rec["target"]["session_id"], rec["target"]["block_id"]
    rc, proc = block_call(sid, b, "process")
    if rc == 3:
        rec["state"] = "abandoned:block-closed"; save_record(rec); return rec["state"]
    if (proc.get("foreground") or {}).get("name") in AGENTS:
        rex("send-key", "-s", sid, "-b", b, "ctrl+c", json_out=False)
        with (pathlib.Path(rec["paths"]["run_dir"]) / "turns.jsonl").open("a") as f:
            f.write(json.dumps({"ev": "interrupted", "ts": now_iso(), "by": "rex_assign cancel"}) + "\n")
        rex("wait", "-s", sid, "--for", "15s", b, json_out=False)
    rc, proc = block_call(sid, b, "process")
    alive = rc == 0 and (proc.get("foreground") or {}).get("name") in AGENTS and not proc.get("last_exit")
    if close:
        rex("block", "close", "-s", sid, b, json_out=False); rec["closed_at"] = now_iso()
    new = cancel_state(rec["state"], alive, close)
    if new == "ready":
        rec["interrupted_at"] = now_iso()
    rec["state"] = new
    save_record(rec); return rec["state"]


def cmd_cancel(a):
    rec = load_record(a.assignment_id)
    print(json.dumps({"assignment_id": rec["assignment_id"], "state": do_cancel(rec, a.close)})); return 0


def cmd_resume(a):
    old = load_record(a.assignment_id)
    w = old["worker"]
    if w["mode"] != "process":
        print("resume is for process-mode workers; use send for interactive ones", file=sys.stderr); return EXIT_USAGE
    sid_old = w.get("agent_session_id") if w["agent"] == "claude" else codex_thread_id(old)
    if not sid_old:
        old["state"] = "abandoned:no-transcript"; save_record(old)
        print("no agent session/thread id to resume; relaunch with the same brief", file=sys.stderr); return 1
    ns = argparse.Namespace(**vars(a)); ns.resume_of = sid_old; ns.agent = w["agent"]; ns.mode = "process"; ns.role = old["role"]
    ns.session = a.session or old["target"]["session_id"]; ns.anchor = a.anchor or (None if a.new_window else old["owner"].get("block_id"))
    ns.cwd = a.cwd or w.get("cwd"); ns.model = a.model or w.get("model"); ns.effort = a.effort or w.get("effort")
    ns.permission = a.permission or w["permission_policy"]["mode"]; ns.allow = a.allow or w["permission_policy"].get("allowed_tools")
    ns.worktree = False; ns.label = None; ns.assignment_id = None; ns.prompt = None; ns.osc_status = False
    ns.deliverable = pathlib.Path(old["paths"]["deliverable"]).name if old["paths"].get("deliverable") else None
    ns.as_owner = old["owner"].get("block_id"); ns.controller = old["owner"].get("controller"); ns.external = old["owner"].get("kind") == "external"
    return cmd_launch(ns)


def codex_thread_id(rec):
    p = pathlib.Path(rec["paths"]["result"])
    if not p.exists():
        return None
    for l in p.read_text().splitlines():
        if '"thread.started"' in l:
            return json.loads(l).get("thread_id")
    return None


def cmd_list(a):
    if not RUNS_ROOT.exists():
        print("no assignments"); return 0
    for d in sorted(RUNS_ROOT.iterdir()):
        p = d / "assignment.json"
        if p.exists():
            r = json.loads(p.read_text())
            if a.state and r.get("state") != a.state:
                continue
            print(f"{r['assignment_id']}\t{r['state']}\t{r['worker']['agent']}/{r['worker']['mode']}\t{r['target']['block_id']}\t{r['launched_at']}")
        elif (d / "launching").exists():
            print(f"{d.name}\tLAUNCHING-MARKER (orphan?)")
    return 0


def parse_duration(s):
    m = re.fullmatch(r"(\d+)([smh]?)", s)
    if not m:
        sys.exit(f"bad duration {s!r}; use 30s, 10m, 1h")
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


def main(argv=None):
    global RUNS_ROOT
    p = argparse.ArgumentParser(prog="rex_assign.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs-root", help=f"assignment records root (default {RUNS_ROOT})")
    sub = p.add_subparsers(dest="cmd", required=True)

    def launch_args(sp):
        sp.add_argument("--role", default="executor", choices=["executor", "reviewer"])
        sp.add_argument("--agent", choices=AGENTS)
        sp.add_argument("--mode", default="process", choices=["process", "interactive"])
        sp.add_argument("--session"); sp.add_argument("--anchor"); sp.add_argument("--split", default="right", choices=["right", "left", "above", "below"])
        sp.add_argument("--new-window", action="store_true"); sp.add_argument("--cwd"); sp.add_argument("--brief", help="brief file ('-' = stdin)")
        sp.add_argument("--model"); sp.add_argument("--effort"); sp.add_argument("--permission", help="claude --permission-mode or codex sandbox")
        sp.add_argument("--allow", action="append", help="claude --allowedTools entry (repeatable)")
        sp.add_argument("--worktree", action="store_true"); sp.add_argument("--label"); sp.add_argument("--osc-status", action="store_true")
        sp.add_argument("--assignment-id"); sp.add_argument("--prompt", help="override the one-line prompt (default points at brief.md)")
        sp.add_argument("--as-owner", help="owner block id when running as an external controller"); sp.add_argument("--controller", default=os.environ.get("REX_ORCH_CONTROLLER", "unknown"))
        sp.add_argument("--external", action="store_true", help="outside controller: ignore any inherited REX_* pane identity")
        sp.add_argument("--deliverable", help="file the brief must produce, relative to the run dir (e.g. REPORT.md, review.json); wait downgrades completion when it is missing")
        sp.add_argument("--resume-of", help=argparse.SUPPRESS)

    sp = sub.add_parser("launch", help="create a block and start a worker"); launch_args(sp); sp.set_defaults(fn=cmd_launch)
    sp = sub.add_parser("wait", help="wait for a process-mode worker to exit and classify"); sp.add_argument("assignment_id"); sp.add_argument("--for", dest="for_", default="30m"); sp.add_argument("--cancel-on-timeout", action="store_true"); sp.set_defaults(fn=cmd_wait)
    sp = sub.add_parser("status", help="process, status records, markers, screen tail"); sp.add_argument("assignment_id"); sp.set_defaults(fn=cmd_status)
    sp = sub.add_parser("send", help="guarded prompt delivery to an interactive worker"); sp.add_argument("assignment_id"); g = sp.add_mutually_exclusive_group(required=True); g.add_argument("--text"); g.add_argument("--brief"); sp.add_argument("--as-owner"); sp.set_defaults(fn=cmd_send)
    sp = sub.add_parser("watch", help="wait for fresh turn evidence"); sp.add_argument("assignment_id"); sp.add_argument("--for", dest="for_", default="20m"); sp.set_defaults(fn=cmd_watch)
    sp = sub.add_parser("notify", help="(hooks) append a turn marker"); sp.add_argument("assignment_id"); sp.add_argument("payload", nargs="?"); sp.add_argument("--ev"); sp.add_argument("--osc", action="store_true"); sp.set_defaults(fn=cmd_notify)
    sp = sub.add_parser("cancel", help="interrupt, optionally close"); sp.add_argument("assignment_id"); sp.add_argument("--close", action="store_true"); sp.set_defaults(fn=cmd_cancel)
    sp = sub.add_parser("resume", help="new block continuing a process-mode worker's session"); sp.add_argument("assignment_id"); launch_args(sp); sp.set_defaults(fn=cmd_resume)
    sp = sub.add_parser("review", help="record a reviewer's review.json verdict on an executor assignment"); sp.add_argument("assignment_id", help="executor assignment"); g = sp.add_mutually_exclusive_group(required=True); g.add_argument("--reviewer", help="reviewer assignment id (its run dir holds review.json)"); g.add_argument("--from-file", help="path to a review.json"); sp.set_defaults(fn=cmd_review)
    sp = sub.add_parser("list", help="list assignments"); sp.add_argument("--state"); sp.set_defaults(fn=cmd_list)
    a = p.parse_args(argv)
    if a.runs_root:
        RUNS_ROOT = pathlib.Path(a.runs_root)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
