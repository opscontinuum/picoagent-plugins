# sdlc-evidence

The DISA ASD STIG's development-process family, as an evidence probe. Built from
[reference/disa-asd-stig-for-developers.md](reference/disa-asd-stig-for-developers.md),
which measured the STIG (V6R4, 286 rules) and found 212 of its checks name an interview -
so the honest ceiling for a tool is evidence status, never a finding determination, and
this plugin's tests fail if its output ever contains one.

`sdlc_artifacts` surveys a repository for the machine-checkable artifacts:

| Rule | Artifact |
|---|---|
| V-222653 | coding standards document, plus an enforcing linter/formatter config |
| V-222654 | design document |
| V-222655 | threat model containing the five sections the check enumerates |
| V-222657 | vulnerability intake channel (SECURITY.md) |
| V-222649 | code coverage statistics |
| V-222658 | component inventory (lockfile/manifest/SBOM) for support review |
| V-222645 | release artifact hashes |

Each reports present / partial / absent with what was looked for; the five-section check
is structural (the sections V-222655 requires, matched loosely on purpose).

The `sdlc-evidence-review` skill carries the rest: the full 22-artifact checklist, the
freshness arithmetic (21-business-day criticals, quarterly access reviews, annual
training), and the three lines a tool must not cross. For an actual checklist run with
human-gated determinations, use [stig-runner](https://github.com/opscontinuum/stig-runner);
this sweep tells you where that assessment will hit gaps first.
