---
name: test-cheat-review
description: Adversarial review of a diff for the nine ways a test suite is made to lie
---
Review the current diff as a skeptic whose only question is: could these tests pass while
the behaviour is wrong? Check for each named cheat explicitly - a general "check quality"
pass misses them. Read the full taxonomy in this plugin's
reference/agile-tdd-clean-code-for-agents.md (Part 3) when a finding needs its mechanism.

1. Trivially-passing test: an assertion that holds for every implementation, including the
   empty one (`assertTrue(True)`, asserting only that something was returned).
2. Test written after the code, presented as before: test and implementation arriving in
   one commit make the ordering claim unfalsifiable - treat it as unmade.
3. Weakened test: removed assertion, added skip/xfail, exact equality loosened to substring
   or type or truthiness, widened numeric tolerance, narrowed input, try/except swallowing
   the failure, retry added around flakiness, expected value changed to the observed value,
   test config edited to exclude a file or lower a threshold.
4. Mocked into meaninglessness: every collaborator a double, so the assertions verify only
   that the mock returned what the mock was configured to return.
5. Tautological assertion: the expected value computed by the same expression as the
   implementation, or a constant copied from the code's output. Expected values must be
   derivable from the spec without reading the implementation.
6. Never ran red: nothing shows the test can fail.
7. Special-cased input: the implementation recognises the test's input. Only a test the
   author never saw catches the general case; triangulate (two or more examples) for any
   non-trivial computation.
8. Code no test demanded: new lines uncovered by new tests are, by construction,
   functionality nothing asked for.
9. Tests discarded during refactor as "redundant": two tests exercising one path stay if
   they speak to different scenarios; deletion is a human decision.

For findings in classes 1, 2, 5 and 6, `verify_tests_drive_change` gives a mechanical
verdict: run it and quote the result rather than arguing from reading. Report what was
checked and what was found, including "checked, clean" - an unstructured approval is not a
review.
