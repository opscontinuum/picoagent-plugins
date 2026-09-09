"""tdd-guard: the weakening guard on the tool_call seam, and the pre-change proof tool.

The plugin exists because instruction is not a mechanism (its reference document's phrase),
so these tests drive the mechanisms rather than reading them: the guard is exercised through
a real AgentLoop with a scripted model attempting the edit, and the proof tool is run
against a real git repository built in a temp directory - a fake git would prove nothing
about the worktree overlay the tool's verdict rests on.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import CaptureFrontend, ScriptedProvider, call, make_runtime, run, temp_dir, text
from picoagent.core.loop import AgentLoop
from picoagent.core.tools import ToolContext
from picoagent.plugins import loader

PLUGIN = Path(__file__).resolve().parents[1] / "tdd-guard"

_spec = importlib.util.spec_from_file_location("picoagent_plugin_tdd_guard", PLUGIN / "tdd_guard.py")
tdd_guard = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = tdd_guard
_spec.loader.exec_module(tdd_guard)


class WeakeningHeuristicTests(unittest.TestCase):
    """``weakening_reason`` is the guard's whole judgement; pin each signal separately."""

    def test_removing_an_assertion_is_named(self):
        before = "def test_a(self):\n    assert f(1) == 2\n    assert f(2) == 4\n"
        after = "def test_a(self):\n    assert f(1) == 2\n"
        self.assertIn("assertion", tdd_guard.weakening_reason(before, after))

    def test_adding_a_skip_marker_is_named(self):
        before = "def test_a(self):\n    assert f(1) == 2\n"
        after = "@unittest.skip('later')\ndef test_a(self):\n    assert f(1) == 2\n"
        self.assertIn("skip", tdd_guard.weakening_reason(before, after))

    def test_a_vacuous_assertion_is_named(self):
        before = "def test_a(self):\n    self.assertEqual(f(1), 2)\n"
        after = "def test_a(self):\n    self.assertEqual(f(1), 2)\n    self.assertTrue(True)\n"
        self.assertIn("never fail", tdd_guard.weakening_reason(before, after))

    def test_an_ordinary_strengthening_edit_is_not_flagged(self):
        before = "def test_a(self):\n    assert f(1) == 2\n"
        after = "def test_a(self):\n    assert f(1) == 2\n    assert f(0) == 0\n"
        self.assertIsNone(tdd_guard.weakening_reason(before, after))


class GuardTests(unittest.TestCase):
    """The guard through the real loop: a scripted model tries the edit, a person answers."""

    def setUp(self):
        self.tmp = temp_dir()

    def _edit_result(self, answer: bool, path: str, old: str, new: str):
        (self.tmp / path).parent.mkdir(parents=True, exist_ok=True)
        (self.tmp / path).write_text(old)
        rt = make_runtime(self.tmp, provider=ScriptedProvider(
            [[call("edit", path=path, old_text=old, new_text=new)], [text("ok")]]),
            frontend=CaptureFrontend(answer=answer))
        loader.load_plugin(PLUGIN, rt, loader.TrustStore(self.tmp / "home"), allow_untrusted=True)
        run(AgentLoop(rt).run("edit the test"))
        return rt.frontend.tool_results()[0]

    def test_a_declined_weakening_edit_is_blocked_with_the_reason(self):
        result = self._edit_result(False, "tests/test_x.py",
                                   "def test_a():\n    assert f() == 1\n",
                                   "def test_a():\n    pass\n")
        self.assertTrue(result.is_error)
        self.assertIn("tdd-guard", result.content)
        self.assertEqual((self.tmp / "tests/test_x.py").read_text(),
                         "def test_a():\n    assert f() == 1\n",
                         "the block must reach the file: nothing may be written")

    def test_an_approved_weakening_edit_goes_through(self):
        result = self._edit_result(True, "tests/test_x.py",
                                   "def test_a():\n    assert f() == 1\n",
                                   "def test_a():\n    pass\n")
        self.assertFalse(result.is_error, result.content)

    def test_a_non_test_file_is_not_the_guards_business(self):
        result = self._edit_result(False, "app.py",
                                   "def f():\n    assert x\n    return 1\n",
                                   "def f():\n    return 1\n")
        self.assertFalse(result.is_error, result.content)


class _GitRepoCase(unittest.TestCase):
    """A real repository with one committed state, for the pre-change proof."""

    def setUp(self):
        self.tmp = temp_dir()
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "t@example.invalid")
        self._git("config", "user.name", "t")
        (self.tmp / "app.py").write_text("def double(x):\n    return x + x\n")
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "test_app.py").write_text(
            "import sys, unittest\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
            "import app\n\nclass T(unittest.TestCase):\n"
            "    def test_double(self):\n        self.assertEqual(app.double(2), 4)\n")
        self._git("add", "app.py", "tests/test_app.py")
        self._git("commit", "-q", "-m", "v0")

    def _git(self, *argv):
        subprocess.run(["git", "-C", str(self.tmp), *argv], check=True, capture_output=True)

    def _tool(self):
        rt = make_runtime(self.tmp, provider=ScriptedProvider([[text("ok")]]))
        loader.load_plugin(PLUGIN, rt, loader.TrustStore(self.tmp / "home"), allow_untrusted=True)
        return rt.tools.get("verify_tests_drive_change")

    def _ctx(self):
        import asyncio
        return ToolContext(cwd=self.tmp, config={"shell_timeout": 60,
                                                 "tool_output_max_bytes": 50_000,
                                                 "tool_output_max_lines": 2000},
                           tool_call_id="t1", abort=asyncio.Event())

    COMMAND = f"{sys.executable} -m unittest discover -q -s tests"


class VerifyTestsDriveChangeTests(_GitRepoCase):

    def test_a_test_that_drives_the_change_is_proven(self):
        """New behaviour, a test that fails on HEAD and passes now: the honest case."""
        (self.tmp / "app.py").write_text(
            "def double(x):\n    return x + x\n\ndef triple(x):\n    return 3 * x\n")
        (self.tmp / "tests" / "test_triple.py").write_text(
            "import sys, unittest\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
            "import app\n\nclass T(unittest.TestCase):\n"
            "    def test_triple(self):\n        self.assertEqual(app.triple(2), 6)\n")
        result = run(self._tool().execute({"command": self.COMMAND}, self._ctx()))
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.details["verdict"], "proven", result.content)

    def test_a_test_that_passes_against_head_is_called_out(self):
        """The cheat signature: the 'new' test also passes on the pre-change code."""
        (self.tmp / "tests" / "test_hollow.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n"
            "    def test_anything(self):\n        self.assertTrue(True)\n")
        result = run(self._tool().execute({"command": self.COMMAND}, self._ctx()))
        self.assertEqual(result.details["verdict"], "not-driven", result.content)
        self.assertIn("NOT PROVEN", result.content)

    def test_red_on_both_sides_means_the_change_is_not_done(self):
        (self.tmp / "tests" / "test_wish.py").write_text(
            "import sys, unittest\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
            "import app\n\nclass T(unittest.TestCase):\n"
            "    def test_quadruple(self):\n        self.assertEqual(app.quadruple(2), 8)\n")
        result = run(self._tool().execute({"command": self.COMMAND}, self._ctx()))
        self.assertEqual(result.details["verdict"], "still-red", result.content)

    def test_no_changed_tests_is_an_error_value_not_a_guess(self):
        result = run(self._tool().execute({"command": self.COMMAND}, self._ctx()))
        self.assertTrue(result.is_error)
        self.assertIn("no changed test files", result.content)

    def test_outside_a_git_repository_the_answer_is_a_value(self):
        bare = temp_dir()
        rt = make_runtime(bare, provider=ScriptedProvider([[text("ok")]]))
        loader.load_plugin(PLUGIN, rt, loader.TrustStore(bare / "home"), allow_untrusted=True)
        import asyncio
        ctx = ToolContext(cwd=bare, config={"shell_timeout": 60,
                                            "tool_output_max_bytes": 50_000,
                                            "tool_output_max_lines": 2000},
                          tool_call_id="t1", abort=asyncio.Event())
        result = run(rt.tools.get("verify_tests_drive_change").execute(
            {"command": "true"}, ctx))
        self.assertTrue(result.is_error)
        self.assertIn("git", result.content)

    def test_the_temporary_worktree_is_removed_either_way(self):
        (self.tmp / "tests" / "test_hollow.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n"
            "    def test_anything(self):\n        self.assertTrue(True)\n")
        run(self._tool().execute({"command": self.COMMAND}, self._ctx()))
        listed = subprocess.run(["git", "-C", str(self.tmp), "worktree", "list"],
                                capture_output=True, text=True).stdout
        self.assertEqual(len(listed.strip().splitlines()), 1,
                         f"a scratch worktree survived:\n{listed}")


if __name__ == "__main__":
    unittest.main()
