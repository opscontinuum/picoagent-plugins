---
name: canon-tdd
description: Run a change test-first the way Beck defines it, with the proof recorded rather than claimed
---
Work test-first, and leave the evidence. The loop (Beck's Canon TDD):

1. Write the list of test scenarios this change must cover, before any code. Put it in the
   task's spec or commit it; expected values in later assertions must trace to this list,
   never to what the implementation happens to produce.
2. Turn exactly one scenario into a concrete runnable test.
3. Run it and watch it fail. A test that passes on first run has demonstrated nothing;
   treat an unexpected pass as seriously as an unexpected failure.
4. Make it pass with the simplest change. Returning a constant is legitimate on the way
   (Fake It) and fraudulent if you stop there.
5. Refactor with the suite green. Structural and behavioural changes never share a commit:
   a commit labelled refactor must leave every test outcome identical.
6. Back to 2 until the list is empty.

Rules that hold throughout:
- Never delete or weaken a failing test to get to green; fix the code. Loosening an
  equality, widening a tolerance, adding a skip marker and narrowing the input are all
  deletion wearing a disguise.
- When a bug is found, the fix starts with a test that reproduces it and fails.
- Before claiming the change is done, run `verify_tests_drive_change` with the suite
  command. If it reports the changed tests pass against pre-change code, the tests do not
  drive the change - strengthen them and rerun until it is proven.

$ARGUMENTS
