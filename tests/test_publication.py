import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import stat
import subprocess
import textwrap
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def publication_workspace(scratch):
    workspace = scratch / ("publication-unit-" + uuid.uuid4().hex)
    workspace.mkdir()
    try:
        yield workspace
    finally:
        workspace.resolve().relative_to(scratch.resolve())

        def remove_readonly(function, path, error):
            os.chmod(path, stat.S_IWRITE)
            function(path)

        shutil.rmtree(workspace, onerror=remove_readonly)


class PublicationTests(unittest.TestCase):
    def test_first_publication_update_and_unchanged_package(self):
        git = shutil.which("git")
        self.assertIsNotNone(git, "Для проверки публикации нужен Git")
        bash = (Path(git).resolve().parents[1] / "bin/bash.exe"
                if os.name == "nt" else shutil.which("bash"))
        self.assertTrue(bash and Path(bash).is_file(), "Для проверки workflow нужен Bash")
        scratch = ROOT / ".build"
        scratch.mkdir(exist_ok=True)
        with publication_workspace(scratch) as workspace:
            remote = workspace / "remote.git"
            publication = workspace / "publication"
            package = workspace / "package"
            package.mkdir()

            def run_git(*args, cwd=publication):
                return subprocess.run(
                    [git, *args], cwd=cwd, check=True, capture_output=True, text=True,
                    encoding="utf-8",
                ).stdout.strip()

            run_git("init", "--bare", str(remote), cwd=workspace)
            run_git("init", str(publication), cwd=workspace)
            run_git("config", "user.name", "Publication Test")
            run_git("config", "user.email", "test@example.invalid")
            run_git("config", "core.autocrlf", "false")
            run_git("remote", "add", "origin", str(remote))
            (publication / "source-only.txt").write_text("source\n", encoding="utf-8")
            run_git("add", "--all")
            run_git("commit", "-m", "Source fixture")
            source_commit = run_git("rev-parse", "HEAD")
            (package / ".gitattributes").write_bytes(b"* -text\n")
            (package / ".codex-plugin").mkdir()
            (package / ".codex-plugin/plugin.json").write_bytes(b'{}\n')
            runtime = package / "runtime.bin"
            runtime.write_bytes(b"\x00\xff\r\n")
            workflow = (ROOT / ".github/workflows/package.yml").read_text(encoding="utf-8")
            script = textwrap.dedent(workflow[workflow.index("          set -euo pipefail"):])
            env = dict(os.environ, GITHUB_SHA=source_commit)

            def publish():
                # Reproduce actions/checkout: every run starts at the source commit.
                run_git("checkout", "--detach", source_commit)
                result = subprocess.run(
                    [str(bash), "-c", script], cwd=publication, env=env,
                    capture_output=True, text=True, encoding="utf-8",
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(run_git("status", "--porcelain"), "")
                return run_git("rev-parse", "refs/heads/windows-x64", cwd=remote)

            first = publish()
            self.assertEqual(
                run_git("ls-tree", "-r", "--name-only", first, cwd=remote).splitlines(),
                [".codex-plugin/plugin.json", ".gitattributes", "runtime.bin"],
            )
            self.assertEqual(run_git("rev-list", "--parents", "-n", "1", first, cwd=remote), first)
            self.assertEqual(publish(), first)
            runtime.unlink()
            (package / "replacement.bin").write_bytes(b"updated\x00\xff\n")
            updated = publish()
            self.assertNotEqual(updated, first)
            self.assertEqual(run_git("rev-parse", updated + "^", cwd=remote), first)
            self.assertEqual(
                run_git("ls-tree", "-r", "--name-only", updated, cwd=remote).splitlines(),
                [".codex-plugin/plugin.json", ".gitattributes", "replacement.bin"],
            )
            self.assertEqual((publication / "replacement.bin").read_bytes(), b"updated\x00\xff\n")
            self.assertEqual(publish(), updated)


if __name__ == "__main__":
    unittest.main()
