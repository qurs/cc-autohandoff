import importlib.util
import json
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load_module("build_plugin")
checker = load_module("check_package")
handoff = load_module("handoff")


class PackageTests(unittest.TestCase):
    def test_source_marketplaces_resolve_remote_package_for_both_hosts(self):
        for name in (".agents/plugins/marketplace.json", ".claude-plugin/marketplace.json"):
            catalog = checker.read_json(ROOT / name)
            self.assertEqual(catalog["name"], "cc-autohandoff")
            self.assertEqual(len(catalog["plugins"]), 1)
            entry = catalog["plugins"][0]
            self.assertEqual(entry["name"], "cc-autohandoff")
            self.assertEqual(entry["source"], {
                "source": "url", "url": "https://github.com/qurs/cc-autohandoff.git", "ref": "windows-x64",
            })

    def setUp(self):
        scratch = ROOT / ".build"
        scratch.mkdir(exist_ok=True)
        self.root = scratch / ("package-unit-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(self.cleanup_workspace)
        self.runtime = self.root / "runtime"
        (self.runtime / "_internal").mkdir(parents=True)
        (self.runtime / "cc-autohandoff.exe").write_bytes(b"synthetic runtime")
        (self.runtime / "_internal/python.dll").write_bytes(b"synthetic library")
        license_path = self.root / "license.txt"
        license_path.write_text("Synthetic license fixture", encoding="utf-8")
        self.licenses = {"Python.txt": license_path, "PyInstaller.txt": license_path}
        # Package tests copy public sources into a disposable root, never installed plugins.
        for name in (*builder.PACKAGE_FILES, "hooks/codex.json", "hooks/hooks.json",
                     ".agents/plugins/marketplace.json", ".claude-plugin/marketplace.json"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        self.root_patch = patch.object(builder, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.output = self.root / "dist/plugin"

    def assemble(self):
        return builder.assemble(self.runtime, self.output, self.licenses)

    def test_smoke_path_excludes_python_launcher_in_windows_directory(self):
        system = self.root / "Windows"
        system.mkdir()
        (system / "py.exe").write_bytes(b"system Python launcher fixture")
        self.assertIsNotNone(shutil.which("py.exe", path=str(system)))
        with patch.dict(checker.os.environ, {"SystemRoot": str(system), "PATH": str(system),
                                           "PYTHONPATH": "ambient Python", "CC_AUTOHANDOFF_WRITER": "1"}):
            env = checker.smoke_environment(self.root)
        self.assertEqual(list(Path(env["PATH"]).iterdir()), [])
        self.assertIsNone(shutil.which("py.exe", path=env["PATH"]))
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("CC_AUTOHANDOFF_WRITER", env)

    def cleanup_workspace(self):
        target = self.root.resolve()
        target.relative_to((ROOT / ".build").resolve())
        shutil.rmtree(target)

    def test_allowlist_and_both_marketplaces(self):
        for directory in (self.root, self.runtime):
            (directory / "SOURCE.jsonl").write_text("private transcript", encoding="utf-8")
        package = self.assemble()
        self.assertGreater(checker.verify(package), 10)
        self.assertFalse(list(package.rglob("SOURCE.jsonl")))
        self.assertFalse((package / "scripts/handoff.py").exists())
        self.assertFalse((package / "bin/LICENSE.txt").exists())

    def test_checksum_detects_changed_missing_and_extra_files(self):
        self.assemble()
        target = self.output / "handoff-settings.json"
        original = target.read_bytes()
        for replacement in (b"{}", None):
            if replacement is None:
                target.unlink()
            else:
                target.write_bytes(replacement)
            with self.assertRaises(ValueError):
                checker.verify(self.output)
            target.write_bytes(original)
        (self.output / "SOURCE.jsonl").write_text("private", encoding="utf-8")
        with self.assertRaises(ValueError):
            checker.verify(self.output)

    def test_invalid_hooks_are_rejected_even_with_matching_checksum(self):
        self.assemble()
        path = self.output / "hooks/hooks.json"
        value = checker.read_json(path)
        value["hooks"]["PreCompact"][0]["hooks"][0].pop("onFailure")
        builder.write_json(path, value)
        checksums = checker.read_json(self.output / "package-checksums.json")
        checksums["hooks/hooks.json"] = checker.hashlib.sha256(path.read_bytes()).hexdigest()
        builder.write_json(self.output / "package-checksums.json", checksums)
        with self.assertRaises((ValueError, KeyError)):
            checker.verify(self.output)

    def test_output_cannot_escape_dist_or_overwrite_existing_directory(self):
        self.assemble()
        for path in (self.output, self.root / "dist", self.root / "outside", self.root / "dist/../../outside"):
            with self.assertRaises(ValueError):
                builder.assemble(self.runtime, path, self.licenses)
        self.assertFalse((self.root / "outside").exists())

    def test_incomplete_runtime_is_rejected_before_copy(self):
        (self.runtime / "cc-autohandoff.exe").unlink()
        with self.assertRaises(ValueError):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_missing_licenses_are_rejected_before_copy(self):
        with self.assertRaises(ValueError):
            builder.assemble(self.runtime, self.output, {"Python.txt": self.root / "missing"})
        self.assertFalse(self.output.exists())

    def test_frozen_recovery_command_uses_executable_with_powershell_quoting(self):
        with patch.object(handoff.sys, "frozen", True, create=True), \
             patch.object(handoff.sys, "executable", "C:/Плагин с пробелами/bin/cc-autohandoff.exe"), \
             patch.object(handoff.os, "name", "nt"):
            command = handoff.cli_command("retry", "--session-dir", "C:/O'Brien/сеанс")
        self.assertEqual(command, "& 'C:/Плагин с пробелами/bin/cc-autohandoff.exe' 'retry' '--session-dir' 'C:/O''Brien/сеанс'")
        self.assertNotIn("python", command.lower())


if __name__ == "__main__":
    unittest.main()
