"""Проверка автономного пакета и синтетических hooks без Python в PATH."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop",
          "PreCompact", "SubagentStart", "SubagentStop"}
WRITER_AGENT = "cc-autohandoff:handoff-writer"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(package):
    package = Path(package).resolve()
    checksums = read_json(package / "package-checksums.json")
    paths = list(package.rglob("*"))
    require(not any(path.is_symlink() or getattr(path, "is_junction", lambda: False)() for path in paths), "В пакете есть ссылки.")
    actual = {path.relative_to(package).as_posix(): path for path in paths if path.is_file()
              and path != package / "package-checksums.json"}
    require(set(actual) == set(checksums), "Список файлов не совпадает с checksum manifest.")
    for name, path in actual.items():
        require(hashlib.sha256(path.read_bytes()).hexdigest() == checksums[name], "Изменён файл: " + name)
        require(not set(path.relative_to(package).parts) & {".git", ".cc-autohandoff", ".env"}
                and path.suffix not in (".log", ".jsonl")
                and path.name not in ("HANDOFF.md", "START-NEXT-SESSION.txt", "handoff.py"),
                "В пакете найден приватный или исходный runtime-файл: " + name)
    for name in (".gitattributes", "bin/cc-autohandoff.exe", "licenses/Python.txt", "licenses/PyInstaller.txt",
                 "agents/handoff-writer.md", "skills/handoff/SKILL.md", ".claude-plugin/plugin.json"):
        require(name in actual, "Нет обязательного файла: " + name)
    require((package / ".gitattributes").read_bytes() == b"* -text\n",
            "Git должен сохранять исходные байты пакета для проверки checksums.")
    require((package / "bin/_internal").is_dir(), "Нет bundled runtime.")
    settings = read_json(package / "handoff-settings.json")
    require(settings["threshold_tokens"] == 150000 and settings["model"] == "claude-haiku-5-5",
            "Нарушен контракт порога или модели.")
    agent = (package / "agents/handoff-writer.md").read_text(encoding="utf-8")
    require("\nmodel: claude-haiku-5-5\n" in agent and "\ntools: Read\n" in agent, "Неверный writer-субагент.")
    catalog = read_json(package / ".claude-plugin/marketplace.json")
    require(len(catalog["plugins"]) == 1 and catalog["plugins"][0]["source"] == "./", "Marketplace не указывает на пакет.")
    hooks = read_json(package / "hooks/hooks.json")["hooks"]
    require(set(hooks) == EVENTS, "Неполный список hooks.")
    for event, entries in hooks.items():
        require(len(entries) == 1 and len(entries[0]["hooks"]) == 1, "Повтор hook: " + event)
        hook = entries[0]["hooks"][0]
        require(hook["type"] == "command" and hook["timeout"] >= 60, "Неверный тип/тайм-аут hook.")
        require(hook["command"] == "${CLAUDE_PLUGIN_ROOT}/bin/cc-autohandoff.exe" and hook["args"] == ["hook"],
                "Неверная exec-команда hook.")
        if event in ("PreToolUse", "UserPromptSubmit", "PreCompact"):
            require(hook.get("onFailure") == "block", "Ошибка hook должна блокировать действие.")
    return len(actual)


@contextlib.contextmanager
def temporary_workspace():
    scratch = ROOT / ".build"
    scratch.mkdir(exist_ok=True)
    work = scratch / ("package-check-" + uuid.uuid4().hex)
    work.mkdir()
    try:
        yield work
    finally:
        target = work.resolve()
        target.relative_to(scratch.resolve())
        shutil.rmtree(target)


def smoke_environment(work):
    env = {key: value for key, value in os.environ.items() if key not in (
        "CC_AUTOHANDOFF_WRITER", "CLAUDE_CODE_ENTRYPOINT", "PYTHONHOME", "PYTHONPATH")}
    # Windows system directories can contain py.exe even on a clean CI runner.
    empty_path = work / "empty-path"
    empty_path.mkdir()
    env["PATH"] = str(empty_path)
    require(not any(shutil.which(name, path=env["PATH"]) for name in ("python", "python3", "py")),
            "В тестовом PATH всё ещё есть Python.")
    return env


def smoke(package):
    require(os.name == "nt", "Запуск Windows-пакета требует Windows.")
    with temporary_workspace() as work:
        installed = work / "плагин с пробелами"
        shutil.copytree(package, installed)
        data = work / "plugin data"
        env = smoke_environment(work)
        env.update(CLAUDE_PLUGIN_ROOT=str(installed), CLAUDE_PLUGIN_DATA=str(data))
        hooks = read_json(installed / "hooks/hooks.json")["hooks"]

        def run(command, payload=None, expected=0):
            result = subprocess.run(command, input=None if payload is None else json.dumps(payload, ensure_ascii=False),
                                    capture_output=True, text=True, encoding="utf-8", timeout=45, cwd=work, env=env)
            require(result.returncode == expected, "Ошибка запуска: " + result.stderr)
            return json.loads(result.stdout)

        def invoke(event):
            hook = hooks[event["hook_event_name"]][0]["hooks"][0]
            return run([hook["command"].replace("${CLAUDE_PLUGIN_ROOT}", str(installed)), *hook["args"]], event)

        transcript = work / "переписка.jsonl"

        def append_usage(tokens, first=False):
            rows = [{"type": "assistant", "message": {"content": "Тест", "usage": {
                "input_tokens": tokens - 30, "output_tokens": 10,
                "cache_creation_input_tokens": 10, "cache_read_input_tokens": 10}}}]
            if first:
                rows.insert(0, {"type": "user", "message": {"content": "Тест упаковки"}})
            with transcript.open("a", encoding="utf-8") as stream:
                for row in rows:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")

        event = {"hook_event_name": "PreToolUse", "cwd": str(work), "transcript_path": str(transcript),
                 "session_id": "package-test", "tool_name": "Bash", "tool_use_id": "toolu_bash",
                 "tool_input": {"command": "echo test"}}
        append_usage(149999, first=True)
        require(invoke(event) == {}, "Порог сработал раньше 150000.")
        append_usage(150000)
        output = invoke(event)
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        require(output["hookSpecificOutput"]["permissionDecision"] == "deny" and WRITER_AGENT in reason
                and "Agent" in reason, "Порог не блокирует работу или не просит запустить writer.")
        states = list((data / "states").glob("*.json"))
        require(len(states) == 1, "Состояние сохранено вне plugin data.")
        state = read_json(states[0])
        require(state["phase"] == "writing" and state["context_tokens"] == 150000, "Источник writer не подготовлен.")
        directory = Path(state["directory"])
        require((directory / "session.json").is_file() and not (directory / "HANDOFF.md").exists(),
                "Handoff выдан до ответа writer.")
        for trigger in ("manual", "auto"):
            require(invoke(dict(event, hook_event_name="PreCompact", trigger=trigger)).get("decision") == "block",
                    "Compact разрешён во время передачи.")
        other = dict(event, agent_id="agent-other", agent_type="Explore")
        require(invoke(other) == {}, "Hook вмешался в чужого субагента.")
        writer = dict(event, agent_id="agent-writer", agent_type=WRITER_AGENT)
        require(invoke(writer)["hookSpecificOutput"]["permissionDecision"] == "deny",
                "Writer получил доступ к инструментам кроме чтения источника.")
        binary = str(installed / "bin/cc-autohandoff.exe")
        require(run([binary, "status", "--session-dir", str(directory)])["phase"] == "writing", "Status не читает состояние.")
        retry = invoke(dict(event, hook_event_name="Stop", stop_hook_active=False))
        require(retry.get("decision") == "block", "Stop не напомнил о запуске writer.")
        failed = invoke(dict(event, hook_event_name="Stop", stop_hook_active=True))
        require(failed.get("continue") is False, "Stop возобновляет работу без writer.")
        require("bin/cc-autohandoff.exe" in json.dumps(failed).replace("\\\\", "/"),
                "Команда восстановления не использует bundled executable.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--static-only", action="store_true", help="Проверить только структуру и checksums.")
    args = parser.parse_args()
    count = verify(args.package)
    if not args.static_only:
        smoke(args.package.resolve())
    print(f"Пакет проверен: {count} файлов. " + ("Без запуска hooks." if args.static_only else
          "Claude Code: синтетические hooks без Python в PATH; живой writer не запускался."))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
