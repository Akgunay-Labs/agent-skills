import io, json, os, pathlib, shlex, sys, tempfile, unittest, contextlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rex_assign as ra  # noqa: E402

RUN = pathlib.Path("/tmp/run dir's/a-1")
S = "session:01a10000-0000-7000-8000-000000000001"
B = "block:01a10000-0000-7000-8000-000000000002"


def no_subprocess(*a, **k):
    raise AssertionError("subprocess must not be called here")


class CommandBuilding(unittest.TestCase):
    def test_command_quoting_preserves_brief_pointer_and_redirect(self):
        payload = ra.build_worker_command("claude", "process", ra.Policy("acceptEdits", ["Read", "Bash(git *)"]), RUN, "u-1", model="sonnet")
        parts = shlex.split(payload.split(" > ")[0])
        self.assertEqual(parts[0:3], ["exec", "claude", "-p"])
        self.assertIn(f"Read {RUN}/brief.md and do exactly what it says.", parts)
        self.assertIn("Bash(git *)", parts)
        self.assertTrue(payload.endswith(shlex.quote(str(RUN / "result.json"))))
        self.assertNotIn("\n", payload)

    def test_claude_process_policy_flags(self):
        p = ra.build_worker_command("claude", "process", ra.Policy("plan", []), RUN, "u-1")
        for flag in ("--permission-mode plan", "--permission-prompts none", "--output-format json", "--session-id u-1"):
            self.assertIn(flag, p)

    def test_codex_process_command(self):
        p = ra.build_worker_command("codex", "process", ra.Policy("workspace-write", []), RUN, None, worktree=True, cwd="/repo")
        parts = shlex.split(p.split(" > ")[0])
        self.assertEqual(parts[0:3], ["exec", "codex", "exec"])
        for flag in ("--approve-for-me", "-c", "shell_environment_policy.inherit=all", "--skip-git-repo-check", "--json", "--worktree", "-o", "-C"):
            self.assertIn(flag, parts)
        self.assertTrue(p.endswith(shlex.quote(str(RUN / "events.jsonl"))))

    def test_interactive_claude_command_uses_settings_and_exec(self):
        p = ra.build_worker_command("claude", "interactive", ra.Policy("default", []), RUN, "u-2", model="sonnet")
        parts = shlex.split(p)
        self.assertEqual(parts[0:2], ["exec", "claude"])
        self.assertNotIn("-p", parts)
        self.assertIn(str(RUN / "claude-settings.json"), parts)

    def test_interactive_codex_command_has_notify_and_no_daemon(self):
        p = ra.build_worker_command("codex", "interactive", ra.Policy("default", []), RUN, None, assignment_id="a-1")
        parts = shlex.split(p)
        self.assertEqual(parts[0:3], ["exec", "codex", "--no-daemon"])
        self.assertTrue(any(x.startswith("notify=[") and "rex_assign.py" in x and "a-1" in x for x in parts))
        self.assertIn("shell_environment_policy.inherit=all", parts)

    def test_interactive_codex_makes_run_dir_writable_for_notify(self):
        p = ra.build_worker_command("codex", "interactive", ra.Policy("default", []), RUN, None, assignment_id="a-1", helper=RUN / "rex_assign.py")
        parts = shlex.split(p)
        self.assertTrue(any(x.startswith("sandbox_workspace_write.writable_roots=") and str(RUN) in x for x in parts))

    def test_split_argv_has_explicit_session_and_anchor_and_no_focus(self):
        argv = ra.build_rex_create_argv(S, anchor=B, split="right", new_window=False, label="a-1", cwd="/repo", payload="exec true")
        self.assertEqual(argv[:2], ["rex", "split"])
        for f in ("-s", S, "-b", B, "--split", "right", "--focus=false", "--keep-open", "--label", "a-1", "--cwd", "/repo", "--json", "--", "sh", "-c", "exec true"):
            self.assertIn(f, argv)

    def test_new_window_argv_uses_run_not_split(self):
        argv = ra.build_rex_create_argv(S, anchor=None, split=None, new_window=True, label="a-1", cwd="/repo", payload="exec true")
        self.assertEqual(argv[:2], ["rex", "run"])
        self.assertNotIn("-b", argv)
        self.assertIn("--focus=false", argv)


class Classification(unittest.TestCase):
    def write(self, d, name, obj):
        p = pathlib.Path(d) / name
        p.write_text(json.dumps(obj) if not isinstance(obj, str) else obj)
        return p

    def test_classify_success(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, "result.json", {"subtype": "success", "is_error": False, "permission_denials": [], "result": "done"})
            self.assertEqual(ra.classify(0, p, "claude")[0], "completed-unreviewed")

    def test_classify_denials(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, "result.json", {"subtype": "success", "is_error": False, "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "rm x"}}]})
            state, summary = ra.classify(0, p, "claude")
            self.assertEqual(state, "completed-with-denials")
            self.assertIn("Bash", summary)

    def test_classify_exit_without_result(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ra.classify(0, pathlib.Path(d) / "result.json", "claude")[0], "exited-without-result")

    def test_classify_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ra.classify(1, pathlib.Path(d) / "result.json", "claude")[0], "failed")

    def test_classify_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ra.classify(None, pathlib.Path(d) / "result.json", "claude")[0], "timeout")

    def test_classify_codex_success_from_events(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "last-message.md", "OK")
            p = self.write(d, "events.jsonl", '{"type":"thread.started","thread_id":"t1"}\n{"type":"turn.completed"}\n')
            self.assertEqual(ra.classify(0, p, "codex")[0], "completed-unreviewed")

    def test_classify_codex_empty_last_message_is_no_result(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "last-message.md", "")
            p = self.write(d, "events.jsonl", '{"type":"thread.started","thread_id":"t1"}\n{"type":"turn.completed"}\n')
            self.assertEqual(ra.classify(0, p, "codex")[0], "exited-without-result")

    def test_codex_process_command_adds_run_dir_to_sandbox(self):
        p = ra.build_worker_command("codex", "process", ra.Policy("workspace-write", []), RUN, None)
        parts = shlex.split(p.split(" > ")[0])
        self.assertIn("--add-dir", parts)
        self.assertIn(str(RUN), parts)

    def test_interactive_commands_use_staged_helper_path(self):
        p = ra.build_worker_command("codex", "interactive", ra.Policy("default", []), RUN, None, assignment_id="a-1", helper=RUN / "rex_assign.py")
        parts = shlex.split(p)
        self.assertTrue(any(str(RUN / "rex_assign.py") in x for x in parts))
        self.assertFalse(any(str(ra.HELPER) in x for x in parts))
        s = ra.interactive_claude_settings("a-1", RUN / "rex_assign.py")
        self.assertIn(str(RUN / "rex_assign.py"), shlex.split(s["hooks"]["Stop"][0]["hooks"][0]["command"]))

    def test_stage_helper_copies_script_into_run_dir(self):
        with tempfile.TemporaryDirectory() as d:
            staged = ra.stage_helper(pathlib.Path(d))
            self.assertEqual(staged, pathlib.Path(d) / "rex_assign.py")
            self.assertTrue(staged.exists())
            self.assertIn("def stage_helper", staged.read_text())

    def test_server_restart_detected(self):
        self.assertEqual(ra.check_server({"instance_id": "a"}, {"instance_id": "b"}), "abandoned:server-restarted")
        self.assertIsNone(ra.check_server({"instance_id": "a"}, {"instance_id": "a"}))

    def test_cancel_close_keeps_terminal_state(self):
        self.assertEqual(ra.cancel_state("completed-unreviewed", alive=False, close=True), "completed-unreviewed")
        self.assertEqual(ra.cancel_state("failed", alive=False, close=True), "failed")

    def test_cancel_running_worker_is_cancelled(self):
        self.assertEqual(ra.cancel_state("launched", alive=False, close=False), "cancelled")
        self.assertEqual(ra.cancel_state("working", alive=True, close=True), "cancelled")

    def test_interrupt_leaves_interactive_worker_ready(self):
        self.assertEqual(ra.cancel_state("working", alive=True, close=False), "ready")

    def test_explicit_owner_wins_over_whoami(self):
        self.assertEqual(ra.resolve_owner("block:explicit", {"block_id": "block:mine"}), ("external", "block:explicit"))
        self.assertEqual(ra.resolve_owner(None, {"block_id": "block:mine"}), ("lead-in-rex", "block:mine"))
        self.assertEqual(ra.resolve_owner(None, {"block_id": None}), ("external", None))

    def test_external_flag_ignores_whoami_block(self):
        self.assertEqual(ra.resolve_owner(None, {"block_id": "block:mine"}, external=True), ("external", None))
        self.assertEqual(ra.resolve_owner("block:x", {"block_id": "block:mine"}, external=True), ("external", "block:x"))

    def test_brief_run_dir_substitution(self):
        self.assertEqual(ra.render_brief("Write REPORT.md in $RUN_DIR now. ${RUN_DIR}/x", pathlib.Path("/r/a-1")), "Write REPORT.md in /r/a-1 now. /r/a-1/x")

    def test_resume_keeps_original_agent_session_id(self):
        self.assertEqual(ra.session_id_for(agent="claude", resume_of="orig-1", fresh="new-1"), "orig-1")
        self.assertEqual(ra.session_id_for(agent="claude", resume_of=None, fresh="new-1"), "new-1")
        self.assertIsNone(ra.session_id_for(agent="codex", resume_of=None, fresh="new-1"))

    def test_deliverable_missing_downgrades_completion(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ra.apply_deliverable("completed-unreviewed", pathlib.Path(d) / "review.json"), "completed-without-deliverable")
            (pathlib.Path(d) / "review.json").write_text("{}")
            self.assertEqual(ra.apply_deliverable("completed-unreviewed", pathlib.Path(d) / "review.json"), "completed-unreviewed")
            self.assertEqual(ra.apply_deliverable("failed", pathlib.Path(d) / "nope"), "failed")

    def test_apply_review_sets_verdict_state(self):
        rec = {"state": "completed-unreviewed", "review": None}
        self.assertEqual(ra.apply_review(rec, {"verdict": "accepted", "findings": []}, "a-rev")["state"], "accepted")
        rec = {"state": "completed-unreviewed", "review": None}
        self.assertEqual(ra.apply_review(rec, {"verdict": "changes-requested", "findings": [{"summary": "x"}]}, "a-rev")["state"], "changes-requested")
        with self.assertRaises(ValueError):
            ra.apply_review({"state": "launched"}, {"verdict": "accepted"}, "a-rev")
        with self.assertRaises(ValueError):
            ra.apply_review({"state": "completed-unreviewed"}, {"verdict": "maybe"}, "a-rev")

    def test_states_never_say_accepted_without_review(self):
        for st in ra.TERMINAL_STATES:
            self.assertNotEqual(st, "accepted")


class ModeB(unittest.TestCase):
    def test_interactive_settings_json_has_all_five_hooks_and_assignment_id(self):
        s = ra.interactive_claude_settings("a-1", pathlib.Path("/x/rex_assign.py"), osc=True)
        self.assertEqual(sorted(s["hooks"]), ["Notification", "SessionEnd", "SessionStart", "Stop", "UserPromptSubmit"])
        for ev, hooks in s["hooks"].items():
            cmd = hooks[0]["hooks"][0]["command"]
            self.assertIn("/x/rex_assign.py notify a-1", cmd)
            self.assertIn("--osc", cmd)
        self.assertEqual(s["preferredNotifChannel"], "terminal_bell")

    def test_codex_notify_override_is_per_launch_argv(self):
        v = ra.codex_notify_override("a-1", pathlib.Path("/x/rex_assign.py"))
        self.assertTrue(v.startswith("notify=["))
        self.assertIn("/x/rex_assign.py", v)
        self.assertIn("a-1", v)

    def test_hook_and_notify_commands_use_absolute_interpreter(self):
        v = json.loads(ra.codex_notify_override("a-1", pathlib.Path("/x/rex_assign.py"))[len("notify="):])
        self.assertEqual(v[0], sys.executable)
        self.assertTrue(os.path.isabs(v[0]))
        cmd = ra.interactive_claude_settings("a-1", pathlib.Path("/x/rex_assign.py"))["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertEqual(shlex.split(cmd)[0], sys.executable)

    def rec(self, owner=B):
        return {"owner": {"block_id": owner}, "target": {"block_id": "block:01a1-w"}, "worker": {"agent": "claude"}, "ready_at": "2026-10-10T00:00:00Z"}

    def test_send_refuses_foreground_shell(self):
        g = ra.send_guards(self.rec(), {"foreground": {"name": "zsh"}}, [], caller_block=B)
        self.assertIn("foreground-not-agent", g)

    def test_send_refuses_blocked_line_newer_than_stop(self):
        turns = [{"ev": "stop", "ts": "2026-10-10T00:01:00Z"}, {"ev": "blocked", "ts": "2026-10-10T00:02:00Z", "notification_type": "permission_prompt"}]
        g = ra.send_guards(self.rec(), {"foreground": {"name": "claude"}}, turns, caller_block=B)
        self.assertIn("blocked", g)

    def test_send_refuses_other_owner(self):
        g = ra.send_guards(self.rec(owner="block:other"), {"foreground": {"name": "claude"}}, [], caller_block=B)
        self.assertIn("not-owner", g)

    def test_send_guards_pass(self):
        turns = [{"ev": "blocked", "ts": "2026-10-10T00:01:00Z"}, {"ev": "stop", "ts": "2026-10-10T00:02:00Z"}]
        self.assertEqual(ra.send_guards(self.rec(), {"foreground": {"name": "claude"}}, turns, caller_block=B), [])

    def test_watch_hook_stop_after_sent_at_is_turn_ended_hook(self):
        turns = [{"ev": "stop", "ts": "2026-10-10T00:05:00Z"}]
        self.assertEqual(ra.watch_outcome(turns, [], sent_at="2026-10-10T00:04:00Z")[0], "turn-ended:hook")

    def test_watch_stop_before_sent_at_is_ignored(self):
        turns = [{"ev": "stop", "ts": "2026-10-10T00:03:00Z"}]
        self.assertEqual(ra.watch_outcome(turns, [], sent_at="2026-10-10T00:04:00Z")[0], "no-fresh-evidence")

    def test_watch_blocked_line_wins_over_heuristic(self):
        turns = [{"ev": "blocked", "ts": "2026-10-10T00:05:00Z", "notification_type": "permission_prompt"}]
        ev = [{"name": "bell", "ts": "2026-10-10T00:05:01Z"}, {"name": "program_status_changed", "ts": "2026-10-10T00:05:02Z", "record": {"state": "idle"}}]
        self.assertEqual(ra.watch_outcome(turns, ev, sent_at="2026-10-10T00:04:00Z")[0], "blocked:permission_prompt")

    def test_watch_bell_plus_idle_after_sent_at_is_heuristic_only(self):
        ev = [{"name": "bell", "ts": "2026-10-10T00:05:01Z"}, {"name": "program_status_changed", "ts": "2026-10-10T00:05:02Z", "record": {"state": "idle"}}]
        out, evidence = ra.watch_outcome([], ev, sent_at="2026-10-10T00:04:00Z")
        self.assertEqual(out, "turn-ended:heuristic")
        self.assertTrue(any("heuristic" in e for e in evidence))

    def test_watch_idle_alone_is_not_turn_end(self):
        ev = [{"name": "program_status_changed", "ts": "2026-10-10T00:05:02Z", "record": {"state": "idle"}}]
        self.assertEqual(ra.watch_outcome([], ev, sent_at="2026-10-10T00:04:00Z")[0], "no-fresh-evidence")

    def test_watch_native_done_record_after_sent_at_is_turn_ended_status(self):
        ev = [{"name": "program_status_changed", "ts": "2026-10-10T00:05:02Z", "record": {"state": "done", "app": "codex", "updated_at": "2026-10-10T00:05:02Z"}}]
        self.assertEqual(ra.watch_outcome([], ev, sent_at="2026-10-10T00:04:00Z")[0], "turn-ended:status")

    def test_watch_child_exited_is_exited(self):
        ev = [{"name": "child_exited", "ts": "2026-10-10T00:05:02Z", "payload": {"exit_code": 0}}]
        self.assertEqual(ra.watch_outcome([], ev, sent_at="2026-10-10T00:04:00Z")[0], "exited")

    def test_watch_foreign_prompt_means_taken_over(self):
        sends = ["2026-10-10T00:04:00Z"]
        turns = [{"ev": "prompt", "ts": "2026-10-10T00:04:01Z"}, {"ev": "stop", "ts": "2026-10-10T00:04:10Z"},
                 {"ev": "prompt", "ts": "2026-10-10T00:06:00Z"}, {"ev": "stop", "ts": "2026-10-10T00:06:05Z"}]
        out, ev = ra.watch_outcome(turns, [], sent_at=sends[-1], sends=sends)
        self.assertEqual(out, "taken-over:person")
        self.assertTrue(any("foreign prompt" in e for e in ev))

    def test_watch_own_prompt_marker_is_not_foreign(self):
        sends = ["2026-10-10T00:04:00Z"]
        turns = [{"ev": "prompt", "ts": "2026-10-10T00:04:02Z"}, {"ev": "stop", "ts": "2026-10-10T00:04:10Z"}]
        self.assertEqual(ra.watch_outcome(turns, [], sent_at=sends[-1], sends=sends)[0], "turn-ended:hook")

    def test_watch_interrupt_marker_is_not_turn_end(self):
        turns = [{"ev": "prompt", "ts": "2026-10-10T00:05:00Z"}, {"ev": "interrupted", "ts": "2026-10-10T00:05:30Z"}]
        self.assertEqual(ra.watch_outcome(turns, [], sent_at="2026-10-10T00:04:00Z")[0], "no-fresh-evidence")

    def test_watch_interrupt_clears_older_blocked(self):
        turns = [{"ev": "blocked", "ts": "2026-10-10T00:05:00Z", "notification_type": "permission_prompt"}, {"ev": "interrupted", "ts": "2026-10-10T00:06:00Z"}]
        self.assertEqual(ra.watch_outcome(turns, [], sent_at="2026-10-10T00:04:00Z")[0], "no-fresh-evidence")

    def test_send_guard_blocked_cleared_by_interrupt(self):
        turns = [{"ev": "blocked", "ts": "2026-10-10T00:05:00Z"}, {"ev": "interrupted", "ts": "2026-10-10T00:06:00Z"}]
        self.assertNotIn("blocked", ra.send_guards(self.rec(), {"foreground": {"name": "claude"}}, turns, caller_block=B))

    def test_input_box_text_detected(self):
        self.assertTrue(ra.input_has_text("⏺ OK\n──── w ─\n❯ Use the Bash tool to run exactly: ls /tmp\n────\n  ⏸ manual mode on"))
        self.assertFalse(ra.input_has_text("⏺ OK\n──── w ─\n❯ \n────\n  ⏸ manual mode on"))
        self.assertFalse(ra.input_has_text("› Ask Codex to do anything\n  GPT-6 xhigh"))
        self.assertTrue(ra.input_has_text("› leftover text here\n  GPT-6 xhigh"))

    def test_send_guard_input_not_empty(self):
        g = ra.send_guards(self.rec(), {"foreground": {"name": "claude"}}, [], caller_block=B, capture="❯ leftover\n  ⏸ manual mode on")
        self.assertIn("input-not-empty", g)

    def test_readiness_requires_agent_foreground_then_marker_or_prompt(self):
        self.assertEqual(ra.readiness({"foreground": {"name": "zsh"}}, [], "", 5, "claude"), "starting")
        self.assertEqual(ra.readiness({"foreground": {"name": "claude"}}, [{"ev": "session_start"}], "", 5, "claude"), "ready")
        self.assertEqual(ra.readiness({"foreground": {"name": "claude"}}, [], "❯ \n ? for shortcuts", 25, "claude"), "ready")
        self.assertEqual(ra.readiness({"foreground": {"name": "claude"}}, [], "", 5, "claude"), "starting")

    def test_readiness_detects_startup_dialog(self):
        cap = "Do you trust the files in this folder?\n ❯ Yes, I trust this folder\n   No, exit"
        self.assertEqual(ra.readiness({"foreground": {"name": "claude"}}, [{"ev": "session_start"}], cap, 5, "claude"), "blocked:startup-dialog")
        self.assertEqual(ra.readiness({"foreground": {"name": "claude"}}, [], "Do you want to proceed?\n ❯ 1. Yes", 25, "claude"), "blocked:startup-dialog")

    def test_readiness_timeout_is_failed_launch(self):
        self.assertEqual(ra.readiness({"foreground": {"name": "zsh"}}, [], "", 31, "claude"), "failed:launch")


class CliGuards(unittest.TestCase):
    def setUp(self):
        self.orig = ra.RUN
        ra.RUN = no_subprocess
        self.tmp = tempfile.TemporaryDirectory()
        ra.RUNS_ROOT = pathlib.Path(self.tmp.name)

    def tearDown(self):
        ra.RUN = self.orig
        self.tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = ra.main(list(args))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue() + err.getvalue()

    def test_refuses_missing_session(self):
        code, msg = self.run_cli("launch", "--agent", "claude", "--anchor", B, "--cwd", "/tmp", "--brief", "/dev/null")
        self.assertEqual(code, 2)
        self.assertIn("--session", msg)

    def test_refuses_label_as_session(self):
        code, msg = self.run_cli("launch", "--agent", "claude", "--session", "lunar-hollow", "--new-window", "--cwd", "/tmp", "--brief", "/dev/null")
        self.assertEqual(code, 2)
        self.assertIn("rex ls --json", msg)

    def test_refuses_anchor_and_new_window_both_missing(self):
        code, msg = self.run_cli("launch", "--agent", "claude", "--session", S, "--cwd", "/tmp", "--brief", "/dev/null")
        self.assertEqual(code, 2)
        self.assertIn("--anchor", msg)

    def test_launch_marker_blocks_second_launch(self):
        d = pathlib.Path(self.tmp.name) / "a-dup"; d.mkdir(); (d / "launching").write_text("x")
        code, msg = self.run_cli("launch", "--agent", "claude", "--session", S, "--new-window", "--cwd", "/tmp", "--brief", "/dev/null", "--assignment-id", "a-dup")
        self.assertEqual(code, 5)
        self.assertIn("orphan", msg)

    def test_stale_result_dir_refused(self):
        d = pathlib.Path(self.tmp.name) / "a-old"; d.mkdir(); (d / "result.json").write_text("{}")
        code, _ = self.run_cli("launch", "--agent", "claude", "--session", S, "--new-window", "--cwd", "/tmp", "--brief", "/dev/null", "--assignment-id", "a-old")
        self.assertEqual(code, 5)


if __name__ == "__main__":
    unittest.main()
