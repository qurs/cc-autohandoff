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


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.workspace = ROOT / ".cc-autohandoff" / ("test " + uuid.uuid4().hex)
        self.workspace.mkdir(parents=True)
        self.addCleanup(self.cleanup_workspace)
        self.transcript = self.workspace / "session.jsonl"
        self.transcript.touch()
        env = {key: value for key, value in os.environ.items()
               if key not in ("CODEX_HANDOFF_WRITER", "CC_AUTOHANDOFF_WRITER")}
        env.update(PLUGIN_DATA=str(self.workspace / "plugin data"), CODEX_HOME=str(self.workspace / "codex home"))
        self.env_patch = patch.dict(os.environ, env, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.platform_patch = patch.multiple(handoff, PLATFORM="codex", MODEL="gpt-6-luna")
        self.platform_patch.start()
        self.addCleanup(self.platform_patch.stop)

    def platform(self, name):
        handoff.PLATFORM = name
        handoff.MODEL = handoff.MODELS[name]

    def append(self, *rows):
        with self.transcript.open("a", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    def conversation(self, tokens=120000):
        if handoff.PLATFORM == "claude":
            self.append({"type": "user", "message": {"content": "Сделай плагин"}},
                        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Проверяю"}],
                         "usage": {"input_tokens": tokens - 3000, "cache_creation_input_tokens": 1000,
                                   "cache_read_input_tokens": 1500, "output_tokens": 500}}})
        else:
            self.append({"type": "response_item", "payload": {"type": "message", "role": "user", "content": "Сделай плагин"}},
                        {"type": "event_msg", "payload": {"type": "token_count", "info": {
                            "last_token_usage": {"total_tokens": tokens}, "total_token_usage": {"total_tokens": 900000},
                            "model_context_window": 200000}}})

    def event(self, name, **extra):
        return dict(hook_event_name=name, session_id="session-12345", cwd=str(self.workspace),
                    transcript_path=str(self.transcript), **extra)

    def state(self):
        path, _ = handoff.state_path_for(self.workspace, self.transcript, "session-12345")
        return path, handoff.read_json(path)

    def summary(self):
        return {key: (["Проверено по источнику"] if key in handoff.ARRAY_FIELDS else "Продолжить работу")
                for key in handoff.FIELDS}

    def writer(self, command, prompt, directory, timeout):
        handoff.write_json(directory / "writer-result.json", self.summary())

    def cleanup_workspace(self):
        target = self.workspace.resolve()
        target.relative_to((ROOT / ".cc-autohandoff").resolve())
        shutil.rmtree(target)

    def ready(self):
        self.conversation()
        with patch.object(handoff, "run_writer", side_effect=self.writer) as writer, patch.object(handoff.shutil, "which", return_value="codex"):
            handoff.hook(self.event("PreToolUse", tool_name="Bash", tool_input={"command": "work"}))
        writer.assert_called_once()
        return self.state()[1]

    def tool_event(self, event_name, kind, arguments, call_id, **extra):
        return handoff.hook(self.event(event_name, tool_name=handoff.DESKTOP_TOOLS[kind],
                                       tool_input=arguments, tool_use_id=call_id, **extra))

    def projects(self):
        return {"projects": [{"projectId": "local-project", "projectKind": "local", "hostId": "local",
                              "path": str(self.workspace)}]}

    def select_project(self, result=None):
        self.assertEqual(self.tool_event("PreToolUse", "projects", {}, "projects-call"), {})
        self.tool_event("PostToolUse", "projects", {}, "projects-call", tool_response=self.projects() if result is None else result)
        return self.state()[1]["continuation"]

    def retry_continuation(self, *flags):
        directory = self.state()[1]["directory"]
        with patch.object(sys, "argv", ["handoff.py", "retry-continuation", "--session-dir", directory, *flags]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            handoff.main()

    def test_desktop_delivery_and_receipts_survive_repeated_hooks(self):
        state = self.ready()
        wrapped = {"content": [{"type": "text", "text": json.dumps(self.projects())}]}
        continuation = self.select_project(wrapped)
        startup = (Path(state["directory"]) / "START-NEXT-SESSION.txt").read_text(encoding="utf-8")
        arguments = continuation["create_input"]
        self.assertEqual(arguments, {"target": {"type": "project", "projectId": "local-project", "environment": {"type": "local"}}, "prompt": startup})
        self.assertEqual(self.tool_event("PreToolUse", "create", arguments, "create-call"), {})
        for call_id in ("create-call", "another-call"):
            self.assertEqual(self.tool_event("PreToolUse", "create", arguments, call_id)["hookSpecificOutput"]["permissionDecision"], "deny")
        receipt = {"structuredContent": {"threadId": "new-thread", "hostId": "local"}}
        self.tool_event("PostToolUse", "create", arguments, "stale-call", tool_response=receipt)
        self.assertEqual(self.state()[1]["continuation"]["phase"], "creating")
        self.tool_event("PostToolUse", "create", arguments, "create-call", tool_response=receipt)
        self.tool_event("PostToolUse", "projects", {}, "projects-call", tool_response=self.projects())
        self.assertEqual(self.state()[1]["continuation"]["phase"], "created")
        self.assertEqual(self.tool_event("PreToolUse", "open", {"threadId": "new-thread"}, "open-call"), {})
        self.tool_event("PostToolUse", "open", {"threadId": "new-thread"}, "open-call", tool_response={"success": True})
        self.tool_event("PostToolUse", "create", arguments, "create-call", tool_response=receipt)
        final = handoff.notice(self.state()[1])
        self.assertIn('::created-thread{threadId="new-thread"}', final)
        self.assertFalse(handoff.hook(self.event("Stop", last_assistant_message=final))["continue"])
        self.assertEqual(self.state()[1]["continuation"]["phase"], "opened")
        self.assertTrue((Path(state["directory"]) / "desktop-creating.json").is_file())
        self.assertEqual(self.state()[1]["continuation"]["thread_id"], "new-thread")

    def test_desktop_only_permits_exact_delivery_arguments(self):
        self.ready()
        for arguments, call_id in (({"hostId": "remote"}, "call"), ({}, ""), ([], "call")):
            self.assertEqual(self.tool_event("PreToolUse", "projects", arguments, call_id)["hookSpecificOutput"]["permissionDecision"], "deny")
        continuation = self.select_project()
        expected = continuation["create_input"]
        variants = [dict(expected, model="other-model"), dict(expected, prompt="arbitrary work"),
                    dict(expected, target={"type": "project", "projectId": "wrong-project", "environment": {"type": "local"}})]
        for arguments in variants:
            self.assertEqual(self.tool_event("PreToolUse", "create", arguments, "call")["hookSpecificOutput"]["permissionDecision"], "deny")
        denied = handoff.hook(self.event("PreToolUse", tool_name="Bash", tool_use_id="shell", tool_input={"command": "codex exec task"}))
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.state()[1]["continuation"]["phase"], "project_ready")

    def test_desktop_rejects_missing_ambiguous_remote_or_relative_projects(self):
        self.ready()
        project = self.projects()["projects"][0]
        invalid = [[], [project, project], [dict(project, hostId="remote")],
                   [dict(project, projectKind="chatgpt")], [dict(project, path=".")],
                   [dict(project, path=str(self.workspace.parent))]]
        path, state = self.state()
        for projects in invalid:
            with self.subTest(projects=projects):
                state["continuation"] = {"phase": "pending"}
                handoff.write_json(path, state)
                continuation = self.select_project({"projects": projects})
                self.assertEqual(continuation["phase"], "failed")
                self.assertNotIn("create_input", continuation)

    def test_desktop_failed_create_requires_confirmed_manual_retry(self):
        self.ready()
        arguments = self.select_project()["create_input"]
        self.tool_event("PreToolUse", "create", arguments, "create-call")
        self.tool_event("PostToolUse", "create", arguments, "create-call", tool_response={"isError": True, "content": []})
        self.assertEqual(self.state()[1]["continuation"]["phase"], "uncertain")
        with self.assertRaises(SystemExit):
            self.retry_continuation()
        with patch.object(handoff, "run_writer") as writer:
            self.retry_continuation("--confirm-no-chat")
            writer.assert_not_called()
        continuation = self.state()[1]["continuation"]
        self.assertEqual(continuation["phase"], "pending")
        self.assertEqual(continuation["history"][0]["phase"], "uncertain")

    def test_desktop_lost_response_is_not_retried_after_restart(self):
        self.ready()
        arguments = self.select_project()["create_input"]
        self.tool_event("PreToolUse", "create", arguments, "create-call")
        handoff.hook(self.event("SessionStart"))
        self.assertEqual(self.state()[1]["continuation"]["phase"], "uncertain")
        for _ in range(3):
            result = self.tool_event("PreToolUse", "create", arguments, "new-call")
            self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        result = handoff.hook(self.event("Stop", stop_hook_active=True))
        self.assertEqual(result["decision"], "block")
        self.assertIn("--confirm-no-chat", result["reason"])
        self.assertFalse(handoff.hook(self.event("Stop", stop_hook_active=True))["continue"])

    def test_desktop_create_rejects_unconfirmed_results_without_duplicates(self):
        self.ready()
        arguments = self.select_project()["create_input"]
        path, state = self.state()
        for result in (None, "not json", {}, {"threadId": "session-12345"},
                       {"threadId": "new-thread", "hostId": "remote"}, {"threadId": "bad\nidentifier"},
                       {"content": [{"type": "text", "text": "{}"}, {"type": "text", "text": "{}"}]}):
            with self.subTest(result=result):
                handoff.write_json(path, state)
                self.tool_event("PreToolUse", "create", arguments, "call")
                self.tool_event("PostToolUse", "create", arguments, "call", tool_response=result)
                self.assertEqual(self.state()[1]["continuation"]["phase"], "uncertain")

    def test_desktop_queued_result_is_never_used_to_open_or_create_again(self):
        self.ready()
        arguments = self.select_project()["create_input"]
        self.tool_event("PreToolUse", "create", arguments, "call")
        self.tool_event("PostToolUse", "create", arguments, "call", tool_response={"clientThreadId": "queued-thread"})
        final = handoff.notice(self.state()[1])
        self.assertIn('::created-thread{clientThreadId="queued-thread"}', final)
        for kind, arguments in (("open", {"threadId": "queued-thread"}), ("create", arguments)):
            self.assertEqual(self.tool_event("PreToolUse", kind, arguments, "retry")["hookSpecificOutput"]["permissionDecision"], "deny")
        with self.assertRaises(SystemExit):
            self.retry_continuation("--confirm-no-chat")

    def test_desktop_open_retry_preserves_created_chat(self):
        self.ready()
        arguments = self.select_project()["create_input"]
        self.tool_event("PreToolUse", "create", arguments, "create-call")
        self.tool_event("PostToolUse", "create", arguments, "create-call", tool_response={"threadId": "new-thread"})
        with self.assertRaises(SystemExit):
            self.retry_continuation()
        self.tool_event("PreToolUse", "open", {"threadId": "new-thread"}, "open-call")
        self.tool_event("PostToolUse", "open", {"threadId": "new-thread"}, "open-call", tool_response={"isError": True})
        self.assertEqual(self.state()[1]["continuation"]["phase"], "open_failed")
        notice = handoff.notice(self.state()[1])
        self.assertIn(self.state()[1]["continuation"]["error"], notice)
        self.assertIn("retry-continuation", notice)
        self.retry_continuation()
        self.assertEqual(self.state()[1]["continuation"]["thread_id"], "new-thread")
        self.assertEqual(self.state()[1]["continuation"]["phase"], "created")
        self.assertEqual(self.tool_event("PreToolUse", "create", arguments, "duplicate")["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.tool_event("PreToolUse", "open", {"threadId": "new-thread"}, "open-again"), {})
        with self.assertRaises(SystemExit):
            self.retry_continuation()

    def test_desktop_stop_requests_are_bounded_when_tools_are_unavailable(self):
        self.ready()
        results = [handoff.hook(self.event("Stop", stop_hook_active=True)) for _ in range(5)]
        self.assertEqual(sum(result.get("decision") == "block" for result in results), 2)
        self.assertFalse(results[-1]["continue"])
        self.assertEqual(self.state()[1]["continuation"]["phase"], "failed")

    def test_desktop_stop_can_request_all_delivery_stages(self):
        self.ready()
        self.assertEqual(handoff.hook(self.event("Stop"))["decision"], "block")
        arguments = self.select_project()["create_input"]
        self.assertEqual(handoff.hook(self.event("Stop", stop_hook_active=True))["decision"], "block")
        self.tool_event("PreToolUse", "create", arguments, "create-call")
        self.tool_event("PostToolUse", "create", arguments, "create-call", tool_response={"threadId": "new-thread"})
        self.assertEqual(handoff.hook(self.event("Stop", stop_hook_active=True))["decision"], "block")
        self.tool_event("PreToolUse", "open", {"threadId": "new-thread"}, "open-call")
        self.tool_event("PostToolUse", "open", {"threadId": "new-thread"}, "open-call", tool_response={"success": True})
        # A receipt must still be delivered if the model omitted its final links/card.
        self.assertEqual(handoff.hook(self.event("Stop", stop_hook_active=True))["decision"], "block")
        self.assertFalse(handoff.hook(self.event("Stop", stop_hook_active=True))["continue"])

    def test_disabling_desktop_blocks_new_operations_but_retains_receipts(self):
        self.ready()
        arguments = self.select_project()["create_input"]
        self.tool_event("PreToolUse", "create", arguments, "call")
        manual = dict(handoff.settings(), continuation={"codex": "manual", "claude": "manual"})
        with patch.object(handoff, "settings", return_value=manual):
            self.tool_event("PostToolUse", "create", arguments, "call", tool_response={"threadId": "new-thread"})
            result = self.tool_event("PreToolUse", "open", {"threadId": "new-thread"}, "open-call")
            self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertIn('::created-thread{threadId="new-thread"}', result["hookSpecificOutput"]["permissionDecisionReason"])
            self.assertEqual(handoff.hook(self.event("Stop"))["decision"], "block")
            with self.assertRaises(SystemExit):
                self.retry_continuation()
        self.assertEqual(self.state()[1]["continuation"]["thread_id"], "new-thread")

    def test_manual_and_legacy_sessions_never_enable_desktop(self):
        cfg = dict(handoff.settings(), continuation={})
        with patch.object(handoff, "settings", return_value=cfg):
            self.ready()
            self.assertEqual(self.tool_event("PreToolUse", "projects", {}, "call")["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertEqual(handoff.hook(self.event("Stop"))["decision"], "block")
            self.assertFalse(handoff.hook(self.event("Stop", stop_hook_active=True))["continue"])
        path, state = self.state()
        state.pop("continuation_mode")
        handoff.write_json(path, state)
        self.assertEqual(self.tool_event("PreToolUse", "projects", {}, "call")["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertNotIn("continuation", self.state()[1])

    def test_writer_failure_still_blocks_both_platforms_and_never_delivers_desktop(self):
        for platform in handoff.MODELS:
            with self.subTest(platform=platform):
                self.platform(platform)
                self.transcript.write_bytes(b"")
                self.conversation()
                with patch.object(handoff, "run_writer", side_effect=RuntimeError("offline")) as writer, patch.object(handoff.shutil, "which", return_value="fake"):
                    handoff.hook(self.event("PostToolUse"))
                    self.assertEqual(self.state()[1]["phase"], "failed")
                    denied = self.tool_event("PreToolUse", "projects", {}, "call")
                    self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
                    compact = handoff.hook(self.event("PreCompact"))
                    self.assertTrue(compact.get("decision") == "block" or compact.get("continue") is False)
                    self.assertIn("offline", compact.get("reason", compact.get("stopReason", "")))
                    writer.assert_called_once()
                self.assertNotIn("continuation", self.state()[1])

    def test_recursion_guard_on_both_platforms_and_flags(self):
        for platform in handoff.MODELS:
            self.platform(platform)
            for flag in ("CODEX_HANDOFF_WRITER", "CC_AUTOHANDOFF_WRITER"):
                with self.subTest(platform=platform, flag=flag), patch.dict(os.environ, {flag: "1"}), patch.object(handoff, "generate") as writer:
                    handoff.hook(self.event("PreToolUse"))
                    writer.assert_not_called()

    def test_current_usage_is_not_accumulated(self):
        self.conversation(119999)
        handoff.hook(self.event("PreToolUse"))
        _, state = self.state()
        self.assertEqual(state["context_tokens"], 119999)
        self.assertEqual(state["phase"], "monitoring")

    def test_claude_cache_usage_and_threshold_delivery(self):
        self.platform("claude")
        self.conversation(119999)
        with patch.object(handoff, "run_writer", side_effect=self.writer) as writer, patch.object(handoff.shutil, "which", return_value="claude"):
            self.assertEqual(handoff.hook(self.event("PreToolUse")), {})
            self.conversation(120000)
            result = handoff.hook(self.event("PostToolUse"))
            self.assertIn("claude-haiku-5-5", result["hookSpecificOutput"]["additionalContext"])
            denied = handoff.hook(self.event("PreToolUse"))
            self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertEqual(handoff.hook(self.event("UserPromptSubmit", prompt="Ещё задача"))["decision"], "block")
            stop = handoff.hook(self.event("Stop"))
            self.assertEqual(stop["decision"], "block")
            final_stop = handoff.hook(self.event("Stop", stop_hook_active=True))
            self.assertFalse(final_stop["continue"])
            writer.assert_called_once()
        _, state = self.state()
        directory = Path(state["directory"])
        self.assertEqual(state["phase"], "ready")
        self.assertTrue((directory / "START-NEXT-SESSION.txt").is_file())
        self.assertIn("claude-haiku-5-5", (directory / "HANDOFF.md").read_text(encoding="utf-8"))

    def test_codex_threshold_uses_luna(self):
        self.conversation()
        with patch.object(handoff, "run_writer", side_effect=self.writer) as writer, patch.object(handoff.shutil, "which", return_value="codex"):
            handoff.hook(self.event("PreToolUse"))
            command = writer.call_args.args[0]
            self.assertEqual(command[command.index("--model") + 1], "gpt-6-luna")
            self.assertIn("--ephemeral", command)
            self.assertEqual(self.state()[1]["phase"], "ready")

    def test_compact_always_blocked_without_transcript(self):
        for platform in handoff.MODELS:
            with self.subTest(platform=platform):
                self.platform(platform)
                result = handoff.hook({"hook_event_name": "PreCompact"})
                if platform == "claude":
                    self.assertEqual(result["decision"], "block")
                else:
                    self.assertFalse(result["continue"])

    def test_writer_failure_keeps_tools_and_compact_blocked(self):
        self.platform("claude")
        self.conversation()
        with patch.object(handoff.shutil, "which", return_value="claude"), patch.object(handoff, "run_writer", side_effect=RuntimeError("offline")) as writer:
            result = handoff.hook(self.event("PreCompact"))
            self.assertEqual(result["decision"], "block")
            self.assertIn("offline", result["reason"])
            self.assertEqual(self.state()[1]["phase"], "failed")
            self.assertEqual(handoff.hook(self.event("PreToolUse"))["hookSpecificOutput"]["permissionDecision"], "deny")
            writer.assert_called_once()

    def test_retry_restores_claude_platform_and_recovers(self):
        self.platform("claude")
        self.conversation()
        with patch.object(handoff.shutil, "which", return_value=None):
            handoff.hook(self.event("PreCompact"))
        path, state = self.state()
        directory = Path(state["directory"])
        with patch.object(sys, "argv", ["handoff.py", "retry", "--session-dir", str(directory)]), patch.object(handoff.shutil, "which", return_value="claude"), patch.object(handoff, "run_writer", side_effect=self.writer), contextlib.redirect_stdout(io.StringIO()):
            handoff.main()
        self.assertEqual(handoff.PLATFORM, "claude")
        self.assertEqual(handoff.read_json(path)["phase"], "ready")

    def test_partial_row_is_retried(self):
        self.conversation(119999)
        state = {}
        handoff.scan_usage(self.transcript, state)
        offset = state["offset"]
        row = json.dumps({"type": "token_usage_record", "payload": {"usage": {"total_tokens": 120000}}})
        with self.transcript.open("ab") as stream:
            stream.write(row.encode())
        handoff.scan_usage(self.transcript, state)
        self.assertEqual(state["offset"], offset)
        self.assertEqual(state["context_tokens"], 119999)
        with self.transcript.open("ab") as stream:
            stream.write(b"\n")
        handoff.scan_usage(self.transcript, state)
        self.assertEqual(state["context_tokens"], 120000)

    def test_truncated_transcript_resets_usage(self):
        self.conversation()
        state = {}
        handoff.scan_usage(self.transcript, state)
        self.transcript.write_bytes(b"")
        handoff.scan_usage(self.transcript, state)
        self.assertIsNone(state["context_tokens"])

    def test_export_claude_preserves_tools_and_marks_images(self):
        self.platform("claude")
        self.conversation()
        self.append({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "hidden"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "test"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "content": "z" * 20000},
                {"type": "image", "source": {"data": "secret-pixels"}}]}})
        source, omissions = handoff.project_source(self.transcript, handoff.settings(), self.transcript.stat().st_size)
        records = [json.loads(line) for line in source.splitlines()]
        self.assertIn("tool_call", [record["kind"] for record in records])
        self.assertIn("tool_output", [record["kind"] for record in records])
        self.assertTrue(omissions)
        self.assertIn("Image attachment", source)
        self.assertNotIn("secret-pixels", source)
        self.assertNotIn("hidden", source)

    def test_over_budget_user_text_is_rejected(self):
        self.conversation()
        with self.assertRaisesRegex(ValueError, "budget"):
            handoff.project_source(self.transcript, dict(handoff.settings(), max_source_chars=5), self.transcript.stat().st_size)

    def test_pending_prompt_and_last_stop_message_are_preserved(self):
        for platform in handoff.MODELS:
            for event_name, extra in (("UserPromptSubmit", {"prompt": "Последнее уточнение"}),
                                      ("Stop", {"last_assistant_message": "Последний ответ"})):
                with self.subTest(platform=platform, event=event_name):
                    self.platform(platform)
                    self.transcript.write_bytes(b"")
                    self.conversation()
                    path, _ = self.state()
                    if path.exists():
                        path.unlink()
                    with patch.object(handoff.shutil, "which", return_value="fake"), patch.object(handoff, "run_writer", side_effect=self.writer):
                        handoff.hook(self.event(event_name, **extra))
                    _, state = self.state()
                    source = (Path(state["directory"]) / "SOURCE.jsonl").read_text(encoding="utf-8")
                    self.assertIn(next(iter(extra.values())), source)

    def test_writer_recursion_guard(self):
        with patch.dict(os.environ, {"CC_AUTOHANDOFF_WRITER": "1"}), patch.object(handoff, "settings") as settings:
            self.assertEqual(handoff.hook(self.event("PreToolUse")), {})
            settings.assert_not_called()

    def test_subagent_skips_writer(self):
        self.conversation()
        with patch.object(handoff, "generate") as writer:
            self.assertEqual(handoff.hook(self.event("PreToolUse", agent_id="subagent")), {})
            writer.assert_not_called()

    def test_summary_requires_schema(self):
        for value in (None, {}, dict(self.summary(), goal=""), dict(self.summary(), constraints=[False])):
            with self.subTest(value=value), self.assertRaises(ValueError):
                handoff.validate_summary(value)

    def test_claude_command_has_fixed_model_and_no_tools(self):
        self.platform("claude")
        with patch.object(handoff.shutil, "which", return_value="claude"):
            command = handoff.writer_command(self.workspace / "writer-result.json", handoff.settings(), self.workspace)
        self.assertEqual(command[command.index("--model") + 1], "claude-haiku-5-5")
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertIn("--no-session-persistence", command)
        self.assertNotIn("--fallback-model", command)
        self.assertEqual(json.loads(command[command.index("--settings") + 1])["fallbackModel"], [])

    def test_claude_print_process_and_isolated_environment(self):
        self.platform("claude")
        script = self.workspace / "fake writer.py"
        result = {"subtype": "success", "is_error": False, "structured_output": self.summary()}
        script.write_text("import json, os, sys\n"
                          "assert 'CLAUDECODE' not in os.environ\n"
                          "assert os.environ['CC_AUTOHANDOFF_WRITER'] == '1'\n"
                          "assert os.environ['DISABLE_AUTO_COMPACT'] == '1'\n"
                          "assert sys.stdin.read() == 'quoted source'\n"
                          f"print({json.dumps(result)!r})\n", encoding="utf-8")
        with patch.dict(os.environ, {"CLAUDECODE": "1"}):
            handoff.run_writer([sys.executable, str(script)], "quoted source", self.workspace, 10)
        self.assertEqual(handoff.read_json(self.workspace / "writer-result.json"), self.summary())

    def test_cli_blocks_compact_without_transcript(self):
        script = ROOT / "scripts/handoff.py"
        # A missing transcript exercises the public CLI without invoking a model.
        for platform in handoff.MODELS:
            result = subprocess.run([sys.executable, str(script), "--platform", platform, "hook"],
                                    input=json.dumps({"hook_event_name": "PreCompact"}), text=True,
                                    encoding="utf-8", capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertTrue(output.get("decision") == "block" or output.get("continue") is False)

    def test_plugin_paths_and_hook_platforms(self):
        codex = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(codex["hooks"], "./hooks/codex.json")
        for name, platform in (("codex.json", "codex"), ("hooks.json", "claude")):
            hooks = json.loads((ROOT / "hooks" / name).read_text(encoding="utf-8"))["hooks"]
            self.assertIn("PreCompact", hooks)
            for entries in hooks.values():
                hook = entries[0]["hooks"][0]
                if platform == "claude":
                    self.assertEqual(hook["args"][-3:], ["--platform", "claude", "hook"])
                else:
                    self.assertIn("${PLUGIN_ROOT}", hook["command"])


if __name__ == "__main__":
    unittest.main()
