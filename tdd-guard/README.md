# tdd-guard

Test-integrity mechanisms for agent-driven development. Built from the research in
[reference/agile-tdd-clean-code-for-agents.md](reference/agile-tdd-clean-code-for-agents.md),
whose one-line conclusion this plugin enforces rather than repeats: a practice binds an
agent only if something outside the agent checks it.

What it ships:

- **A weakening guard** on the `tool_call` seam: an edit that removes assertions from a
  test file, adds a skip/xfail marker, or introduces an assertion that can never fail is
  put to the user before it lands, and refused in a headless run.
- **`verify_tests_drive_change`**: materialises the pre-change code (`git worktree add
  --detach HEAD`, never `git stash`), overlays the changed test files, and requires the
  suite to run red there and green in the working tree. A test that passes against the
  code it claims to have driven is one of: asserting nothing, written after the fact,
  tautological, or never run red - and the verdict says so.
- **Skills**: `canon-tdd` (the loop, evidence included), `test-cheat-review` (the nine
  ways a suite is made to lie, as an adversarial review checklist), `definition-of-done`
  (exit criteria written before the work, each one checkable from artifacts).

Configuration (`[plugins.tdd-guard]`): `extra_test_globs` names more files as tests; a
repository may add patterns (watching more is tightening) and can change nothing else.

Stated limits: the guard's heuristics catch the blunt weakenings and deliberately do not
claim the subtle ones (widened tolerance, narrowed input) - those are the review skill's
job. The proof tool needs a git repository with a commit.
