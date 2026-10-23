import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("handoff", ROOT / "scripts/handoff.py")
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)

SESSION = "session-12345"
NEW_SESSION = "local_1234abcd-0000-4000-8000-000000000001"


class HandoffTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".build"
        scratch.mkdir(exist_ok=True)
        self.workspace = scratch / ("handoff unit " + uuid.uuid4().hex)
        self.workspace.mkdir()
        self.addCleanup(self.cleanup_workspace)
        self.transcript = self.workspace / "session.jsonl"
        self.transcript.touch()
        env = {key: value for key, value in os.environ.items()
               if key not in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT")}
        env["CLAUDE_PLUGIN_DATA"] = str(self.workspace / "plugin data")
        self.env_patch = patch.dict(os.environ, env, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def cleanup_workspace(self):
        target = self.workspace.resolve()
        target.relative_to((ROOT / ".build").resolve())
        shutil.rmtree(target)

    def append(self, *rows, path=None):
        with (path or self.transcript).open("a", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    def conversation(self, tokens=150000):
        self.append({"type": "user", "message": {"content": "Сделай плагин"}},
                    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Проверяю"}],
                     "usage": {"input_tokens": tokens - 3000, "cache_creation_input_tokens": 1000,
                               "cache_read_input_tokens": 1500, "output_tokens": 500}}})

    def event(self, name, **extra):
        return {"hook_event_name": name, "session_id": SESSION, "cwd": str(self.workspace),
                "transcript_path": str(self.transcript), **extra}

    def writer_event(self, name, **extra):
        return self.event(name, agent_id="agent-writer", agent_type=handoff.WRITER_AGENT, **extra)

    def bash(self):
        return self.event("PreToolUse", tool_name="Bash", tool_use_id="toolu_bash", tool_input={"command": "echo test"})

    def state(self):
        path, _ = handoff.state_path_for(self.workspace, self.transcript, SESSION)
        return handoff.read_json(path)

    def save_state(self, state):
        path, _ = handoff.state_path_for(self.workspace, self.transcript, SESSION)
        handoff.write_json(path, state)

    def summary(self, **changes):
        value = {key: (["Checked against the source"] if key in handoff.ARRAY_FIELDS else "Continue the work")
                 for key in handoff.FIELDS}
        value.update(changes)
        return value

    def trigger(self):
        self.conversation()
        output = handoff.hook(self.bash())
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        return self.state()

    def agent_call(self, state, call_id="toolu_writer", **changes):
        tool_input = dict(state["agent_input"], **changes)
        return self.event("PreToolUse", tool_name="Agent", tool_use_id=call_id, tool_input=tool_input)

    def launch(self):
        state = self.trigger()
        self.assertEqual(handoff.hook(self.agent_call(state)), {})
        state = self.state()
        self.assertEqual(state["phase"], "writer_running")
        return state

    def finish(self):
        self.launch()
        output = handoff.hook(self.writer_event("SubagentStop", last_assistant_message=json.dumps(self.summary())))
        self.assertIn("HANDOFF.md", output["systemMessage"])
        state = self.state()
        self.assertEqual(state["phase"], "ready")
        return state

    def cli(self, *arguments, expected=0):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/handoff.py"), *arguments], capture_output=True,
                                text=True, encoding="utf-8", env=dict(os.environ), timeout=60)
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def test_below_threshold_is_silent(self):
        self.conversation(149999)
        self.assertEqual(handoff.hook(self.bash()), {})
        self.assertEqual(self.state()["phase"], "monitoring")
        self.assertFalse((self.workspace / ".cc-autohandoff").exists())

    def test_missing_usage_is_reported(self):
        self.append({"type": "user", "message": {"content": "Привет"}})
        self.assertIn("токенах", handoff.hook(self.bash())["systemMessage"])

    def test_session_start_describes_the_writer(self):
        context = handoff.hook(self.event("SessionStart"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn(handoff.WRITER_AGENT, context)
        self.assertIn("150000", context)

    def test_threshold_requests_writer_agent(self):
        self.conversation()
        output = handoff.hook(self.bash())
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        state = self.state()
        self.assertEqual(state["phase"], "writing")
        self.assertEqual(state["context_tokens"], 150000)
        self.assertIn(handoff.WRITER_AGENT, reason)
        self.assertIn(json.dumps(state["agent_input"], ensure_ascii=False, separators=(",", ":")), reason)
        directory = Path(state["directory"])
        self.assertTrue((directory / "session.json").is_file())
        self.assertEqual((directory.parent / ".gitignore").read_text(encoding="utf-8"), "*\n")
        self.assertFalse((directory / "HANDOFF.md").exists())
        self.assertTrue(state["source_parts"])
        for part in state["source_parts"]:
            self.assertIn(part, state["agent_input"]["prompt"])
            self.assertTrue(handoff.source_part(part))
        self.assertIn("Сделай плагин", Path(state["source_parts"][0]).read_text(encoding="utf-8"))
        self.assertTrue(list((self.workspace / "plugin data/states").glob("*.json")))

    def test_user_prompt_at_threshold_is_exported(self):
        self.conversation()
        handoff.hook(self.event("UserPromptSubmit", prompt="Новое требование пользователя"))
        text = Path(self.state()["source_parts"][-1]).read_text(encoding="utf-8")
        self.assertIn("Новое требование пользователя", text)

    def test_precompact_triggers_handoff_and_is_blocked(self):
        self.conversation(1000)
        output = handoff.hook(self.event("PreCompact", trigger="manual"))
        self.assertEqual(output["decision"], "block")
        self.assertEqual(self.state()["phase"], "writing")
        self.assertEqual(self.state()["trigger"], "PreCompact")

    def test_early_auto_compact_is_blocked_without_handoff(self):
        self.conversation(120000)
        output = handoff.hook(self.event("PreCompact", trigger="auto"))
        self.assertEqual(output["decision"], "block")
        self.assertEqual(self.state()["phase"], "monitoring")
        self.assertFalse((self.workspace / ".cc-autohandoff").exists())

    def test_auto_compact_at_threshold_starts_handoff(self):
        self.conversation(150000)
        self.assertEqual(handoff.hook(self.event("PreCompact", trigger="auto"))["decision"], "block")
        self.assertEqual(self.state()["phase"], "writing")

    def test_missing_transcript_is_silent_at_session_start(self):
        missing = {"hook_event_name": "SessionStart", "session_id": SESSION, "cwd": str(self.workspace),
                   "transcript_path": str(self.workspace / "absent.jsonl")}
        self.assertIn("150000", handoff.hook(missing)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(handoff.hook(dict(missing, hook_event_name="UserPromptSubmit", prompt="Привет")), {})
        self.assertIn("transcript", handoff.hook(dict(missing, hook_event_name="PreToolUse", tool_name="Bash"))["systemMessage"])

    def test_compact_is_blocked_in_every_phase(self):
        compact = self.event("PreCompact", trigger="auto")
        self.trigger()
        self.assertEqual(handoff.hook(compact)["decision"], "block")
        handoff.hook(self.agent_call(self.state()))
        self.assertEqual(handoff.hook(compact)["decision"], "block")
        handoff.hook(self.writer_event("SubagentStop", last_assistant_message=json.dumps(self.summary())))
        self.assertEqual(self.state()["phase"], "ready")
        self.assertEqual(handoff.hook(compact)["decision"], "block")
        state = self.state()
        handoff.fail(state, "test failure")
        self.save_state(state)
        self.assertEqual(handoff.hook(compact)["decision"], "block")

    def test_mismatched_agent_calls_are_denied(self):
        state = self.trigger()
        for event in (self.agent_call(state, prompt=state["agent_input"]["prompt"] + " and continue the task"),
                      self.agent_call(state, model="sonnet"),
                      self.agent_call(state, subagent_type="general-purpose"),
                      self.agent_call(state, isolation="worktree"),
                      self.agent_call(state, call_id=None),
                      dict(self.agent_call(state), tool_name="Bash")):
            output = handoff.hook(event)
            self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertEqual(self.state()["phase"], "writing")
        prompt = state["agent_input"]["prompt"].replace("\n", "\n  ")
        self.assertEqual(handoff.hook(self.agent_call(state, model="haiku", run_in_background=True, prompt=prompt)), {})
        self.assertEqual(self.state()["phase"], "writer_running")

    def test_writer_may_only_read_its_source_parts(self):
        state = self.launch()
        part = state["source_parts"][0]
        allowed = handoff.hook(self.writer_event("PreToolUse", tool_name="Read", tool_input={"file_path": part}))
        self.assertEqual(allowed["hookSpecificOutput"]["permissionDecision"], "allow")
        relative = os.path.relpath(part, self.workspace)
        for tool, arguments in (("Read", {"file_path": str(self.transcript)}), ("Read", {"file_path": relative}),
                                ("Bash", {"command": "cat " + part}), ("Write", {"file_path": part, "content": "x"})):
            output = handoff.hook(self.writer_event("PreToolUse", tool_name=tool, tool_input=arguments))
            self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        # A host that omits agent_id still lets the writer read its source.
        self.assertEqual(handoff.hook(self.event("PreToolUse", tool_name="Read", tool_input={"file_path": part})), {})
        self.assertEqual(handoff.hook(self.bash())["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_other_subagents_are_untouched(self):
        self.launch()
        for name in ("PreToolUse", "SubagentStart", "SubagentStop"):
            event = self.event(name, agent_id="agent-other", agent_type="Explore", tool_name="Bash",
                               tool_input={"command": "ls"}, last_assistant_message="done")
            self.assertEqual(handoff.hook(event), {})
        self.assertEqual(self.state()["phase"], "writer_running")

    def test_writer_hooks_never_start_another_handoff(self):
        self.conversation()
        start = handoff.hook(self.writer_event("SubagentStart"))
        self.assertIn("handoff writer", start["hookSpecificOutput"]["additionalContext"])
        output = handoff.hook(self.writer_event("PreToolUse", tool_name="Bash", tool_input={"command": "ls"}))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(handoff.hook(self.writer_event("SubagentStop", last_assistant_message="{}")), {})
        self.assertFalse((self.workspace / "plugin data").exists())
        self.assertFalse((self.workspace / ".cc-autohandoff").exists())

    def test_valid_reply_finalizes_handoff(self):
        self.launch()
        reply = "Here is the handoff:\n```json\n" + json.dumps(self.summary(goal="Ship the plugin")) + "\n```"
        output = handoff.hook(self.writer_event("SubagentStop", last_assistant_message=reply))
        state = self.state()
        directory = Path(state["directory"])
        self.assertEqual(state["phase"], "ready")
        self.assertIn(str(directory / "HANDOFF.md"), output["systemMessage"])
        text = (directory / "HANDOFF.md").read_text(encoding="utf-8")
        self.assertIn("Ship the plugin", text)
        self.assertIn(handoff.HISTORY_BOUNDARY, text)
        self.assertIn(handoff.MODEL, text)
        self.assertNotIn(str(self.transcript), text)
        startup = (directory / "START-NEXT-SESSION.txt").read_text(encoding="utf-8")
        self.assertIn("Read the handoff at this absolute path", startup)
        self.assertTrue(startup.isascii() or str(directory) in startup)
        self.assertFalse((directory / "source").exists())

    def test_manual_delivery_stops_after_one_reminder(self):
        state = self.finish()
        self.assertEqual(state["continuation_mode"], "manual")
        self.assertEqual(handoff.hook(self.bash())["hookSpecificOutput"]["permissionDecision"], "deny")
        create = self.event("PreToolUse", tool_name=handoff.DESKTOP_TOOLS["create"], tool_use_id="toolu_create",
                            tool_input={"prompt": "x"})
        self.assertEqual(handoff.hook(create)["hookSpecificOutput"]["permissionDecision"], "deny")
        first = handoff.hook(self.event("Stop", stop_hook_active=False, last_assistant_message="Готово"))
        self.assertEqual(first["decision"], "block")
        self.assertIn(handoff.DELIVERY_MARKER, first["reason"])
        self.assertIn("HANDOFF.md", first["reason"])
        second = handoff.hook(self.event("Stop", stop_hook_active=True, last_assistant_message="Готово"))
        self.assertIs(second["continue"], False)

    def test_invalid_reply_bounces_twice_then_fails(self):
        self.launch()
        stop = self.writer_event("SubagentStop", last_assistant_message="Секретный ответ без JSON")
        for _ in range(2):
            output = handoff.hook(stop)
            self.assertEqual(output["decision"], "block")
            self.assertIn("Invalid handoff reply", output["reason"])
        output = handoff.hook(stop)
        state = self.state()
        directory = Path(state["directory"])
        self.assertEqual(state["phase"], "failed")
        self.assertIn(handoff.MODEL, output["systemMessage"])
        log = (directory / "writer-errors.log").read_text(encoding="utf-8")
        self.assertNotIn("Секретный", log)
        self.assertFalse((directory / "source").exists())
        self.assertFalse((directory / "HANDOFF.md").exists())

    def test_reply_with_transcript_path_is_rejected(self):
        self.launch()
        reply = json.dumps(self.summary(workspace="See " + str(self.transcript)))
        self.assertEqual(handoff.hook(self.writer_event("SubagentStop", last_assistant_message=reply))["decision"], "block")
        self.assertEqual(self.state()["phase"], "writer_running")

    def test_reply_is_read_from_agent_transcript_and_state_from_session_json(self):
        self.launch()
        agent_transcript = self.workspace / "agent.jsonl"
        self.append({"type": "assistant", "message": {"content": [{"type": "text", "text": json.dumps(self.summary())}]}},
                    path=agent_transcript)
        event = self.writer_event("SubagentStop", last_assistant_message="", agent_transcript_path=str(agent_transcript),
                                  transcript_path=str(agent_transcript))
        handoff.hook(event)
        self.assertEqual(self.state()["phase"], "ready")

    def test_background_writer_keeps_the_session_waiting(self):
        self.launch()
        post = self.event("PostToolUse", tool_name="Agent", tool_use_id="toolu_writer",
                          tool_response={"status": "async_launched", "agentId": "agent-writer"})
        self.assertIn("уже запущен", handoff.hook(post)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(handoff.hook(self.event("Stop", stop_hook_active=False)), {})
        self.assertEqual(self.state()["phase"], "writer_running")

    def test_synchronous_agent_result_finalizes(self):
        self.launch()
        post = self.event("PostToolUse", tool_name="Agent", tool_use_id="toolu_writer",
                          tool_response={"content": [{"type": "text", "text": json.dumps(self.summary())}]})
        handoff.hook(post)
        self.assertEqual(self.state()["phase"], "ready")

    def test_agent_tool_failure_fails_handoff(self):
        self.launch()
        handoff.hook(self.event("PostToolUseFailure", tool_name="Agent", tool_use_id="toolu_writer", error="boom"))
        state = self.state()
        self.assertEqual(state["phase"], "failed")
        reason = handoff.hook(self.bash())["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn(handoff.RETRY_MARKER, reason)

    def test_stale_writer_and_missing_files_fail(self):
        state = self.launch()
        state["launched_at"] = 1
        self.save_state(state)
        handoff.hook(self.bash())
        self.assertEqual(self.state()["phase"], "failed")
        self.assertIn("секунд", self.state()["error"])

        state = dict(self.state(), phase="ready")
        self.save_state(state)
        handoff.hook(self.bash())
        self.assertEqual(self.state()["phase"], "failed")

    def test_stop_without_writer_blocks_once_then_halts(self):
        self.trigger()
        first = handoff.hook(self.event("Stop", stop_hook_active=False))
        self.assertEqual(first["decision"], "block")
        self.assertIn(handoff.WRITER_AGENT, first["reason"])
        second = handoff.hook(self.event("Stop", stop_hook_active=True))
        self.assertIs(second["continue"], False)
        self.assertIn(handoff.RETRY_MARKER, second["stopReason"])
        self.assertEqual(self.state()["phase"], "failed")
        self.assertIs(handoff.hook(self.event("Stop", stop_hook_active=True))["continue"], False)

    def test_chat_retry_marker_restarts_writer(self):
        self.trigger()
        state = self.state()
        handoff.fail(state, "test failure")
        self.save_state(state)
        handoff.hook(self.event("UserPromptSubmit", prompt="продолжай"))
        self.assertEqual(self.state()["phase"], "failed")
        output = handoff.hook(self.event("UserPromptSubmit", prompt=" " + handoff.RETRY_MARKER + "\n"))
        state = self.state()
        self.assertEqual(state["phase"], "writing")
        self.assertIn(handoff.WRITER_AGENT, output["hookSpecificOutput"]["additionalContext"])
        self.assertTrue(all(Path(part).is_file() for part in state["source_parts"]))

    def test_old_state_migrates_to_failed(self):
        self.conversation(1000)
        handoff.hook(self.bash())
        self.save_state(dict(self.state(), phase="running"))
        output = handoff.hook(self.bash())
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.state()["phase"], "failed")

    def test_cli_status_and_retry(self):
        state = self.launch()
        directory = state["directory"]
        self.assertEqual(json.loads(self.cli("status", "--session-dir", directory).stdout)["phase"], "writer_running")
        self.cli("retry", "--session-dir", directory, expected=2)
        state = self.state()
        handoff.fail(state, "test failure")
        self.save_state(state)
        result = self.cli("retry", "--session-dir", directory)
        self.assertEqual(json.loads(result.stdout)["phase"], "writing")
        self.assertIn("Источник подготовлен", result.stderr)

    def test_hook_errors_still_block_compact_and_tools(self):
        for name, check in (("PreCompact", lambda o: o["decision"] == "block"),
                            ("PreToolUse", lambda o: o["hookSpecificOutput"]["permissionDecision"] == "deny"),
                            ("UserPromptSubmit", lambda o: o["decision"] == "block"),
                            ("Stop", lambda o: "boom" in o["systemMessage"])):
            stdout = io.StringIO()
            with patch.object(handoff, "hook", side_effect=RuntimeError("boom")), \
                 patch.object(sys, "argv", ["handoff.py", "hook"]), \
                 patch.object(sys, "stdin", io.StringIO(json.dumps(self.event(name)))), \
                 contextlib.redirect_stdout(stdout):
                handoff.main()
            self.assertTrue(check(json.loads(stdout.getvalue())), name)

    def desktop_ready(self):
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "claude-desktop"
        state = self.finish()
        self.assertEqual(state["continuation_mode"], "claude-desktop")
        return state

    def create_event(self, state, call_id="toolu_create", **changes):
        return self.event("PreToolUse", tool_name=handoff.DESKTOP_TOOLS["create"], tool_use_id=call_id,
                          tool_input=dict(handoff.claude_create_input(state), **changes))

    def test_desktop_continuation_reserves_one_session(self):
        state = self.desktop_ready()
        arguments = handoff.claude_create_input(state)
        self.assertEqual(arguments["context"], "fresh")
        self.assertIs(arguments["use_worktree"], False)
        self.assertEqual(arguments["prompt"],
                         (Path(state["directory"]) / "START-NEXT-SESSION.txt").read_text(encoding="utf-8"))
        search = self.event("PreToolUse", tool_name="ToolSearch", tool_use_id="toolu_search",
                            tool_input={"query": "select:" + handoff.DESKTOP_TOOLS["create"], "max_results": 1})
        self.assertEqual(handoff.hook(search), {})
        changed = handoff.hook(self.create_event(state, title="Other"))
        self.assertEqual(changed["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(handoff.hook(self.create_event(state)), {})
        self.assertEqual(self.state()["continuation"]["phase"], "creating")
        again = handoff.hook(self.create_event(state, call_id="toolu_again"))
        self.assertEqual(again["hookSpecificOutput"]["permissionDecision"], "deny")
        post = self.event("PostToolUse", tool_name=handoff.DESKTOP_TOOLS["create"], tool_use_id="toolu_create",
                          tool_response={"content": [{"type": "text", "text": "Started " + NEW_SESSION}]})
        context = handoff.hook(post)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("#" + NEW_SESSION, context)
        self.assertEqual(self.state()["continuation"]["session_ref"], NEW_SESSION)
        first = handoff.hook(self.event("Stop", stop_hook_active=False, last_assistant_message="Готово"))
        self.assertEqual(first["decision"], "block")
        self.assertIs(handoff.hook(self.event("Stop", stop_hook_active=True))["continue"], False)

    def test_unused_desktop_tool_fails_after_one_request(self):
        self.desktop_ready()
        first = handoff.hook(self.event("Stop", stop_hook_active=False))
        self.assertIn(handoff.DESKTOP_TOOLS["create"], first["reason"])
        second = handoff.hook(self.event("Stop", stop_hook_active=True))
        self.assertEqual(second["decision"], "block")
        self.assertIn("retry-continuation", second["reason"])
        self.assertEqual(self.state()["continuation"]["phase"], "failed")
        self.assertIs(handoff.hook(self.event("Stop", stop_hook_active=True))["continue"], False)

    def test_uncertain_desktop_result_requires_confirmation(self):
        state = self.desktop_ready()
        self.assertEqual(handoff.hook(self.create_event(state)), {})
        handoff.hook(self.event("Stop", stop_hook_active=False))
        self.assertEqual(self.state()["continuation"]["phase"], "uncertain")
        directory = state["directory"]
        self.cli("retry-continuation", "--session-dir", directory, expected=2)
        self.cli("retry-continuation", "--session-dir", directory, "--confirm-no-chat")
        continuation = self.state()["continuation"]
        self.assertEqual(continuation["phase"], "pending")
        self.assertEqual(continuation["history"][0]["phase"], "uncertain")

    def test_fit_source_drops_logs_before_user_messages(self):
        records = ([{"kind": "user", "text": "u" * 100}]
                   + [{"kind": "tool_output", "text": "t" * 10000} for _ in range(5)]
                   + [{"kind": "assistant", "text": "a" * 5000} for _ in range(5)]
                   + [{"kind": "user", "text": "v" * 100}])
        cfg = dict(handoff.settings(), max_source_chars=3000)
        omissions = handoff.fit_source(records, cfg)
        self.assertEqual(omissions["tool_dropped"], 5)
        self.assertGreaterEqual(omissions["assistant_dropped"], 1)
        self.assertEqual([x["text"] for x in records if x["kind"] == "user"], ["u" * 100, "v" * 100])
        with self.assertRaises(ValueError):
            handoff.fit_source([{"kind": "user", "text": "x" * 50000} for _ in range(10)], cfg)

    def test_parts_wrap_long_lines_and_split(self):
        directory = self.workspace / ".cc-autohandoff" / ("0" * 20)
        records = [{"kind": "user", "text": "x" * 2500}, {"kind": "assistant", "text": "\n".join(["y" * 900] * 20)}]
        parts = handoff.write_parts(records, directory, dict(handoff.settings(), part_chars=5000))
        self.assertGreater(len(parts), 1)
        for part in parts:
            text = Path(part).read_text(encoding="utf-8")
            self.assertLessEqual(len(text), 5200)
            self.assertTrue(all(len(line) <= handoff.WRAP_CHARS for line in text.splitlines()))
        self.assertEqual(sum(Path(p).read_text(encoding="utf-8").count("x") for p in parts), 2500)

    def test_settings_reject_other_models_and_continuations(self):
        original = json.loads((ROOT / "handoff-settings.json").read_text(encoding="utf-8"))
        for change in ({"model": "claude-sonnet-5-5"}, {"continuation": "codex-desktop"},
                       {"threshold_tokens": 500}, {"threshold_tokens": "150000"}):
            (self.workspace / "handoff-settings.json").write_text(json.dumps(dict(original, **change)), encoding="utf-8")
            with patch.object(handoff, "ROOT", self.workspace), self.assertRaises(ValueError):
                handoff.settings()

    def test_plugin_manifest_hooks_and_writer_agent(self):
        settings = json.loads((ROOT / "handoff-settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["threshold_tokens"], 150000)
        self.assertEqual(settings["model"], "claude-haiku-5-5")
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(set(hooks), {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                                      "PostToolUseFailure", "Stop", "PreCompact", "SubagentStart", "SubagentStop"})
        for entries in hooks.values():
            hook = entries[0]["hooks"][0]
            self.assertEqual(hook["args"], ["${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py", "hook"])
        agent = (ROOT / "agents/handoff-writer.md").read_text(encoding="utf-8")
        self.assertIn("\nname: handoff-writer\n", agent)
        self.assertIn("\ntools: Read\n", agent)
        self.assertIn("\nmodel: claude-haiku-5-5\n", agent)
        self.assertFalse((ROOT / ".codex-plugin").exists())
        self.assertFalse((ROOT / "hooks/codex.json").exists())


if __name__ == "__main__":
    unittest.main()
