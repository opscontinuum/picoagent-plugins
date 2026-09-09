"""tdd-guard - the test-integrity mechanisms an instruction cannot supply.

The research this plugin operationalises (``reference/agile-tdd-clean-code-for-agents.md``)
reaches one conclusion worth restating here: a practice binds an agent only if something
outside the agent checks it. Told to do TDD, an agent writes the code first and then tests
that pass; told not to weaken tests, it loosens a tolerance instead of deleting a line. Both
behaviours are documented (Beck's own account; METR's reward-hacking measurements), so the
two things this plugin ships are mechanisms, not advice:

* **A weakening guard on the ``tool_call`` seam.** An ``edit`` or ``write`` that touches a
  test file and removes assertions, adds a skip marker, or introduces an assertion that can
  never fail is put to the person at the keyboard before it happens, and refused outright in
  a headless run - test integrity is exactly the decision that must not be delegated to the
  party whose work the tests judge. The same failure has subtler forms this guard does not
  see (a widened numeric tolerance, an input narrowed to the case that works); the
  ``test-cheat-review`` skill carries the full list for a human or a second agent.

* **``verify_tests_drive_change``** - the research's "nearly free check that catches the
  largest class of problems": run the changed tests against the *pre-change* code. A test
  that passes there never ran red, so it demonstrably does not drive the change it claims
  to; one of the trivial-test, after-the-fact, tautology or never-red failure modes is
  present. The pre-change tree is materialised with ``git worktree add --detach`` into a
  temporary directory - never ``git stash``, whose stack is shared state a tool must not
  touch - and removed whatever happens.

Configuration (``[plugins.tdd-guard]`` in config.toml)::

    extra_test_globs = ["checks/*.py"]   # more patterns that count as test files
    timeout = 120                        # seconds per suite run inside the tool

A repository's ``.picoagent/config.toml`` may add ``extra_test_globs``: naming more files as
tests only widens what the guard watches. Nothing a repository writes can narrow the
detection or the refusal.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from picoagent.core.tools import (ToolContext, kill_process_tree, resolve_tool_path,
                                  spawn_shell, tool_result)
from picoagent.core.types import ToolResult
from picoagent.plugins.api import minimal_env

#: What counts as a test file with nothing configured. Basename patterns, matched against the
#: resolved path's name and its parent directories, because "tests live under tests/" and
#: "tests are named test_*" are the two conventions this ecosystem actually uses.
DEFAULT_TEST_GLOBS = ["test_*.py", "*_test.py"]
TEST_DIR_NAMES = {"tests", "test"}

#: One line each, because the guard's job is to notice, and the reason shown to the person
#: has to name what was noticed.
_ASSERTION = re.compile(r"\bassert\w*\s*\(|\bassert\s+", re.MULTILINE)
_SKIP_MARKER = re.compile(r"@\w+\.?\w*\.(skip\w*|xfail)|unittest\.skip|pytest\.skip\(|@skip\b")
_VACUOUS = re.compile(r"assertTrue\(\s*True\s*[,)]|\bassert\s+True\b")

PROMPT_NOTE = ("# Test integrity\nEdits that weaken tests (removing assertions, adding skip "
               "markers) need user confirmation and are refused in headless runs. Before "
               "claiming a change is done, prove the tests drive it with "
               "verify_tests_drive_change.")


def weakening_reason(before: str, after: str) -> str | None:
    """Why ``after`` is a weaker test text than ``before``, or ``None`` if it is not.

    Heuristics, not proof, and deliberately biased toward asking: the cost of a false hit is
    one confirmation question, the cost of a miss is a suite that lies. The subtle forms
    (widened tolerance, narrowed input, try/except around an assertion) are not detected
    here - a regex that claimed to catch them would be the overclaiming this plugin's own
    reference document warns about - so the reason names only what was actually seen.
    """
    lost = len(_ASSERTION.findall(before)) - len(_ASSERTION.findall(after))
    if lost > 0:
        return f"removes {lost} assertion{'s' if lost > 1 else ''}"
    added_skips = len(_SKIP_MARKER.findall(after)) - len(_SKIP_MARKER.findall(before))
    if added_skips > 0:
        return "adds a skip/xfail marker"
    if len(_VACUOUS.findall(after)) > len(_VACUOUS.findall(before)):
        return "introduces an assertion that can never fail"
    return None


class TddGuard:
    """Watches test-file edits; one instance per session."""

    def __init__(self, api):
        cfg = api.plugin_config()
        api.warn_about_project_config("extra_test_globs")
        self.api = api
        self.globs: list[str] = (DEFAULT_TEST_GLOBS + list(cfg.get("extra_test_globs", []))
                                 + cfg.from_project("extra_test_globs", []))

    def is_test_file(self, path: Path) -> bool:
        """Is ``path`` a test file: by name pattern, or by living under a tests directory."""
        from fnmatch import fnmatch
        if any(fnmatch(path.name, pattern) for pattern in self.globs):
            return True
        return any(part in TEST_DIR_NAMES for part in path.parts[:-1]) and path.suffix == ".py"

    async def on_tool_call(self, event: dict, rt) -> dict | None:
        """Ask before a weakening edit lands; refuse it when there is nobody to ask."""
        name, args = event["name"], event["args"]
        if name not in ("edit", "write"):
            return None
        raw = args.get("path")
        if not isinstance(raw, str) or not raw:
            return None
        resolved = resolve_tool_path(raw, rt.cfg).path
        if not self.is_test_file(resolved):
            return None
        if name == "write" and not resolved.exists():
            # Creating a test file weakens nothing - there is no before. Whether a brand-new
            # test asserts anything is verify_tests_drive_change's question, not this guard's.
            return None
        before, after = self._texts(name, args, resolved)
        reason = weakening_reason(before, after)
        if reason is None:
            return None
        question = (f"This edit to {resolved.name} {reason}. A weaker test hides exactly the "
                    "failure it existed to show. Allow it?")
        ui = self.api.ui
        allowed = await ui.ask("confirm", question) if ui else False
        if allowed:
            return None
        return {"block": True, "reason": f"edit to test file {resolved.name} {reason} and was "
                                         "not approved (tdd-guard)"}

    @staticmethod
    def _texts(name: str, args: dict, resolved: Path) -> tuple[str, str]:
        """The before/after texts the weakening question is asked about.

        For ``edit`` the arguments carry both sides. For ``write`` the before is whatever is
        on disk - a file that does not exist yet cannot be weakened, and reading it as empty
        makes every heuristic compare against nothing, which is the right answer for a new
        test file.
        """
        if name == "edit":
            return str(args.get("old_text", "")), str(args.get("new_text", ""))
        try:
            before = resolved.read_text(errors="replace")
        except OSError:
            before = ""
        return before, str(args.get("content", ""))


class VerifyTestsDriveChange:
    """Run the changed tests against the pre-change code; a pass there is the finding.

    The four failure modes this catches share one signature - the new test cannot fail
    against the code it claims to have driven - and one operation exposes all of them:
    materialise ``HEAD`` (the pre-change code), overlay only the changed test files, run the
    suite there, and require red. The live tree is then run and required green, because "the
    tests never passed" is a different problem and the verdict has to say which one you have.
    """
    name = "verify_tests_drive_change"
    description = ("Prove that changed tests drive the current change: runs the given test "
                   "command against the pre-change code (HEAD) with only the changed test "
                   "files overlaid, expecting failure there and success in the working tree. "
                   "A test that passes against pre-change code did not drive anything. "
                   "Requires a git repository with at least one commit. Pass the exact test "
                   "command; optionally name the test files, otherwise changed test files "
                   "are detected from git.")
    parameters = {"type": "object",
                  "properties": {"command": {"type": "string"},
                                 "paths": {"type": "array", "items": {"type": "string"}}},
                  "required": ["command"]}

    def __init__(self, guard: TddGuard):
        self.guard = guard

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        root = Path(ctx.cwd)
        if _git(root, "rev-parse", "--verify", "HEAD") is None:
            return tool_result(ctx, "not a git repository with a commit; this check compares "
                                    "against HEAD and has nothing to compare to", is_error=True)
        tests = [Path(p) for p in args.get("paths") or []] or self._changed_tests(root)
        if not tests:
            return tool_result(ctx, "no changed test files found (nothing test-shaped in "
                                    "`git diff HEAD` or untracked); pass paths=[...] to name "
                                    "them", is_error=True)
        timeout = int(ctx.config.get("shell_timeout") or 120)
        scratch = Path(tempfile.mkdtemp(prefix="tdd-guard-")).resolve()
        try:
            if _git(root, "worktree", "add", "--detach", str(scratch / "pre"), "HEAD") is None:
                return tool_result(ctx, "could not materialise HEAD in a temporary worktree",
                                   is_error=True)
            pre = scratch / "pre"
            for test in tests:
                source = (root / test) if not test.is_absolute() else test
                try:
                    relative = source.resolve().relative_to(root.resolve())
                except ValueError:
                    return tool_result(ctx, f"{test} is outside the repository; this check "
                                            "overlays test files onto HEAD and can only place "
                                            "files the repository contains", is_error=True)
                target = pre / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            red_code, red_tail = await _run(args["command"], pre, timeout)
            green_code, green_tail = await _run(args["command"], root, timeout)
        finally:
            _git(root, "worktree", "remove", "--force", str(scratch / "pre"))
            shutil.rmtree(scratch, ignore_errors=True)

        names = ", ".join(str(t) for t in tests)
        if red_code != 0 and green_code == 0:
            verdict, text = "proven", (f"proven: {names} fail against pre-change code and pass "
                                       "in the working tree - the tests drive this change")
        elif red_code == 0:
            verdict, text = "not-driven", (
                f"NOT PROVEN: {names} pass against the pre-change code, so they do not drive "
                "this change. One of: the test asserts nothing, it was written after the code "
                "it restates, its expected values are tautological, or it never ran red. "
                "Strengthen the test until this check goes red on HEAD.")
        else:
            verdict, text = "still-red", (
                f"NOT PROVEN: {names} fail in the working tree too (red on both sides). The "
                f"change is not done yet.\n{green_tail}")
        return tool_result(ctx, text, is_error=False, verdict=verdict,
                           pre_change_exit=red_code, working_tree_exit=green_code)

    def _changed_tests(self, root: Path) -> list[Path]:
        changed = (_git(root, "diff", "--name-only", "HEAD") or "").splitlines()
        untracked = (_git(root, "ls-files", "--others", "--exclude-standard") or "").splitlines()
        return [Path(line) for line in changed + untracked
                if line and self.guard.is_test_file(Path(line))]


def _git(root: Path, *argv: str) -> str | None:
    """One git call, argv discipline, ``None`` on any failure - the callers treat absence of
    an answer and a failing command the same way, and neither is worth a traceback."""
    try:
        done = subprocess.run(["git", "-C", str(root), *argv],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


async def _run(command: str, cwd: Path, timeout: int) -> tuple[int, str]:
    """Run the test command where asked; the exit code is the whole answer, the tail is for
    the still-red message. A run that outlives ``timeout`` is killed as a tree and reported
    as failed, because a hung suite proves nothing either way."""
    proc = await spawn_shell(command, cwd, minimal_env())
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await kill_process_tree(proc)
        return 124, f"timed out after {timeout}s"
    tail = stdout.decode(errors="replace")[-2000:]
    return proc.returncode or 0, tail


def register(api):
    guard = TddGuard(api)
    api.on("tool_call", guard.on_tool_call)
    api.register_tool(VerifyTestsDriveChange(guard))
    api.register_system_prompt_section("tdd-guard", lambda: PROMPT_NOTE)
