---
name: definition-of-done
description: Write the exit condition before the work, so "is it done" is a comparison and not a judgement
---
Before starting the task, write its Definition of Done and get it agreed:

1. List the acceptance criteria as checkable statements - each one something a reviewer
   who was not present could verify from the repository and the test run alone. "Works
   correctly" is not checkable; "`parse()` returns X for input Y, and the suite passes both
   TMPDIR shapes" is.
2. Include the non-functional floor that already binds this repository: suite green, no
   test weakened or removed without approval, new behaviour covered by tests proven with
   `verify_tests_drive_change`, docs updated where behaviour they describe changed.
3. Commit the list (or put it in the tracked spec) before implementation starts, so it
   cannot be quietly revised to match what got built. Scope drift then shows up as a diff
   to this file, which is a decision someone can see, instead of a silent renegotiation.

At the end, walk the list item by item and state pass or fail with the evidence for each.
Work that fails an item is not done and does not get reported as done; say which item and
what is missing. The one-sentence test for every criterion you write: if you cannot
describe what a violation would look like in the artefacts, it is an aspiration, not a
criterion - rewrite it or drop it.
