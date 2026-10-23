"""Сборка автономного Windows x64 пакета плагина для Claude Desktop."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_FILES = (
    ".claude-plugin/plugin.json", "handoff-settings.json", "agents/handoff-writer.md",
    "skills/handoff/SKILL.md", "README.md", "docs/architecture.md",
)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def assemble(runtime, output, licenses):
    """Copy only public, allowlisted files; never package the working directory."""
    runtime, output = Path(runtime).resolve(), Path(output).resolve()
    output.relative_to((ROOT / "dist").resolve())
    if output == (ROOT / "dist").resolve() or output.exists():
        raise ValueError("Нужен новый подкаталог dist; существующий пакет не перезаписывается.")
    if not (runtime / "cc-autohandoff.exe").is_file() or not (runtime / "_internal").is_dir():
        raise ValueError("Не найдена готовая onedir-сборка Windows x64.")
    if not licenses or not all(Path(path).is_file() for path in licenses.values()):
        raise ValueError("Не найдены лицензии bundled runtime.")
    for name in PACKAGE_FILES:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    (output / "bin").mkdir()
    shutil.copy2(runtime / "cc-autohandoff.exe", output / "bin/cc-autohandoff.exe")
    shutil.copytree(runtime / "_internal", output / "bin/_internal")
    for name, path in licenses.items():
        target = output / "licenses" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    # Source hooks remain available for developers. Installed hooks use only the binary.
    config = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
    for event, entries in config["hooks"].items():
        for entry in entries:
            for hook in entry["hooks"]:
                hook["command"] = "${CLAUDE_PLUGIN_ROOT}/bin/cc-autohandoff.exe"
                hook["args"] = ["hook"]
                if event in ("PreToolUse", "UserPromptSubmit", "PreCompact"):
                    hook["onFailure"] = "block"
    write_json(output / "hooks/hooks.json", config)
    catalog = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    catalog["plugins"][0]["source"] = "./"
    write_json(output / ".claude-plugin/marketplace.json", catalog)
    # Git must preserve the checked bytes on Windows and POSIX alike.
    (output / ".gitattributes").write_bytes(b"* -text\n")
    checksums = {path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(output.rglob("*")) if path.is_file()}
    write_json(output / "package-checksums.json", checksums)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/plugin")
    args = parser.parse_args()
    if sys.platform != "win32" or sysconfig.get_platform().lower() != "win-amd64":
        parser.error("Готовый пакет пока поддерживает только Windows x64.")
    output = args.output.resolve()
    output.relative_to((ROOT / "dist").resolve())
    if output == (ROOT / "dist").resolve() or output.exists():
        parser.error("Каталог пакета уже существует; выбери новый --output внутри dist.")
    distribution = importlib.metadata.distribution("pyinstaller")
    license_file = next(path for path in distribution.files
                        if str(path).endswith("licenses/COPYING.txt"))
    licenses = {"Python.txt": Path(sys.base_prefix) / "LICENSE.txt",
                "PyInstaller.txt": distribution.locate_file(license_file)}
    build = ROOT / ".build"
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(build / "pyinstaller-cache"))
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--name", "cc-autohandoff", "--distpath", str(build / "native"),
        "--workpath", str(build / "work"), "--specpath", str(build / "spec"),
        str(ROOT / "scripts/handoff.py"),
    ], cwd=ROOT, env=env, check=True)
    print(assemble(build / "native/cc-autohandoff", output, licenses))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
