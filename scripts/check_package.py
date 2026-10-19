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
EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "PreCompact"}


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
        require(not set(path.relative_to(package).parts) & {".git", ".cc-autohandoff", ".codex-handoff", ".env"}
                and path.suffix not in (".log", ".jsonl")
                and path.name not in ("HANDOFF.md", "START-NEXT-SESSION.txt", "handoff.py"),
                "В пакете найден приватный или исходный runtime-файл: " + name)
    for name in ("bin/cc-autohandoff.exe", "scripts/hook.ps1", "licenses/Python.txt", "licenses/PyInstaller.txt",
                 "scripts/handoff-schema.json", "scripts/writer-instructions.md", "skills/handoff/SKILL.md"):
        require(name in actual, "Нет обязательного файла: " + name)
    require((package / "bin/_internal").is_dir(), "Нет bundled runtime.")
    settings = read_json(package / "handoff-settings.json")
    require(settings["threshold_tokens"] == 120000 and settings["models"] == {
        "codex": "gpt-6-luna", "claude": "claude-haiku-5-5"}, "Нарушен контракт порога или моделей.")
    codex = read_json(package / ".codex-plugin/plugin.json")
    claude = read_json(package / ".claude-plugin/plugin.json")
    require(codex["version"] == claude["version"] and codex["hooks"] == "./hooks/codex.json", "Неверный manifest.")
    catalogs = ((".agents/plugins/marketplace.json", {"source": "local", "path": "./"}),
                (".claude-plugin/marketplace.json", "./"))
    for name, source in catalogs:
        catalog = read_json(package / name)
        require(len(catalog["plugins"]) == 1 and catalog["plugins"][0]["source"] == source,
                "Marketplace не указывает на пакет: " + name)
    for host, filename in (("codex", "codex.json"), ("claude", "hooks.json")):
        hooks = read_json(package / "hooks" / filename)["hooks"]
        require(set(hooks) == EVENTS, "Неполный список hooks: " + host)
        for event, entries in hooks.items():
            require(len(entries) == 1 and len(entries[0]["hooks"]) == 1, "Повтор hook: " + event)
            hook = entries[0]["hooks"][0]
            require(hook["type"] == "command" and hook["timeout"] >= 330, "Неверный тип/тайм-аут hook.")
            if host == "codex":
                require(hook["command"] == '"${PLUGIN_ROOT}/bin/cc-autohandoff.exe" hook'
                        and hook["commandWindows"] == ('powershell.exe -NoLogo -NoProfile -NonInteractive '
                            '-ExecutionPolicy Bypass -File "${PLUGIN_ROOT}/scripts/hook.ps1"'), "Неверная команда Codex.")
            else:
                require(hook["command"] == "${CLAUDE_PLUGIN_ROOT}/bin/cc-autohandoff.exe"
                        and hook["args"] == ["--platform", "claude", "hook"], "Неверная exec-команда Claude.")
                if event in ("PreToolUse", "UserPromptSubmit", "PreCompact"):
                    require(hook["onFailure"] == "block", "Ошибка hook должна блокировать действие.")
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


def smoke(package):
    require(os.name == "nt", "Запуск Windows-пакета требует Windows.")
    with temporary_workspace() as work:
        installed = work / "плагин с пробелами"
        shutil.copytree(package, installed)
        env = {key: value for key, value in os.environ.items() if key not in (
            "CODEX_HANDOFF_WRITER", "CC_AUTOHANDOFF_WRITER", "PYTHONHOME", "PYTHONPATH")}
        system = Path(os.environ["SystemRoot"])
        env.update(PATH=os.pathsep.join(map(str, (system / "System32", system,
                   system / "System32/WindowsPowerShell/v1.0"))), CODEX_HOME=str(work / "codex home"),
                   CODEX_HANDOFF_CODEX=str(work / "missing-codex.exe"),
                   CC_AUTOHANDOFF_CLAUDE=str(work / "missing-claude.exe"))
        require(not any(shutil.which(name, path=env["PATH"]) for name in ("python", "python3", "py")),
                "В тестовом PATH всё ещё есть Python.")

        def run(command, payload=None, expected=0, flags=None):
            result = subprocess.run(command, input=None if payload is None else json.dumps(payload, ensure_ascii=False),
                                    capture_output=True, text=True, encoding="utf-8", timeout=45, cwd=work,
                                    env=dict(env, **(flags or {})))
            require(result.returncode == expected, "Ошибка запуска: " + result.stderr)
            return json.loads(result.stdout)

        for host, filename in (("codex", "codex.json"), ("claude", "hooks.json")):
            data = work / (host + " data")
            env.update(PLUGIN_ROOT=str(installed), CLAUDE_PLUGIN_ROOT=str(installed),
                       PLUGIN_DATA=str(data), CLAUDE_PLUGIN_DATA=str(data))
            hooks = read_json(installed / "hooks" / filename)["hooks"]

            def invoke(event, flags=None):
                hook = hooks[event["hook_event_name"]][0]["hooks"][0]
                command = (hook["commandWindows"].replace("${PLUGIN_ROOT}", str(installed)) if host == "codex" else
                           [hook["command"].replace("${CLAUDE_PLUGIN_ROOT}", str(installed)), *hook["args"]])
                return run(command, event, flags=flags)

            def append_usage(tokens, first=False):
                if host == "codex":
                    rows = [{"type": "event_msg", "payload": {"type": "token_count", "info": {
                        "last_token_usage": {"total_tokens": tokens}, "total_token_usage": {"total_tokens": 900000}}}}]
                    if first:
                        rows.insert(0, {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "Тест упаковки"}})
                else:
                    rows = [{"type": "assistant", "message": {"content": "Тест", "usage": {
                        "input_tokens": tokens - 30, "output_tokens": 10,
                        "cache_creation_input_tokens": 10, "cache_read_input_tokens": 10}}}]
                    if first:
                        rows.insert(0, {"type": "user", "message": {"content": "Тест упаковки"}})
                with transcript.open("a", encoding="utf-8") as stream:
                    for row in rows:
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")

            transcript = work / (host + " переписка.jsonl")
            event = {"hook_event_name": "PreToolUse", "cwd": str(work), "transcript_path": str(transcript),
                     "session_id": "package-test", "tool_name": "Bash", "tool_input": {"command": "echo test"}}
            append_usage(119999, first=True)
            require(invoke(event) == {}, "Порог сработал раньше 120000.")
            append_usage(120000)
            output = invoke(event)
            require(output["hookSpecificOutput"]["permissionDecision"] == "deny", "Порог не блокирует работу.")
            states = list((data / "states").glob("*.json"))
            require(len(states) == 1, "Состояние сохранено вне plugin data.")
            state = read_json(states[0])
            require(state["phase"] == "failed" and state["context_tokens"] == 120000,
                    "Нет диагностики недоступного writer.")
            directory = Path(state["directory"])
            require((directory / "session.json").is_file() and not (directory / "HANDOFF.md").exists(),
                    "Сбой writer выдан за готовый handoff.")
            started = state["started_at"]
            for trigger in ("manual", "auto"):
                blocked = invoke(dict(event, hook_event_name="PreCompact", trigger=trigger))
                require(blocked.get("decision") == "block" if host == "claude" else blocked.get("continue") is False,
                        "Compact разрешён после ошибки writer.")
            require(invoke(dict(event, hook_event_name="UserPromptSubmit", prompt="Работай"))["decision"] == "block",
                    "Новый prompt не заблокирован.")
            for _ in range(2):
                require(invoke(dict(event, hook_event_name="Stop", stop_hook_active=True))["continue"] is False,
                        "Stop возобновляет работу после ошибки writer.")
            require(read_json(states[0])["started_at"] == started, "Hook автоматически повторил writer.")
            for marker in ("CODEX_HANDOFF_WRITER", "CC_AUTOHANDOFF_WRITER"):
                require(invoke(event, {marker: "1"}) == {}, "Нет защиты от рекурсии writer.")
            binary = str(installed / "bin/cc-autohandoff.exe")
            require(run([binary, "status", "--session-dir", str(directory)])["platform"] == host,
                    "Status не восстановил платформу.")
            retried = run([binary, "retry", "--session-dir", str(directory)], expected=1)
            require(retried["phase"] == "failed" and retried["platform"] == host, "Retry потерял платформу/ошибку.")
            require("bin/cc-autohandoff.exe" in json.dumps(output).replace("\\\\", "/"),
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
          "Обе платформы: синтетические hooks без Python в PATH; живой writer не запускался."))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
