---
name: sdlc-evidence-review
description: Assess a repository's SDLC evidence against the ASD STIG process family, honestly about what a tool cannot decide
---
Run `sdlc_artifacts` on the repository and report from its evidence table. Then extend the
picture with what the probe cannot see, using the full artifact list in this plugin's
reference/disa-asd-stig-for-developers.md (section 3.10 - twenty-two retained artifacts):

- Per-release artifacts (threat model review, design document update, test plans, coverage
  statistics) are only evidence if they track releases. Check the release history: a threat
  model last touched three releases ago is stale evidence even though the file is present.
- Evidence-freshness rules are arithmetic once the artifact exists: critical scan findings
  older than 21 business days (V-222624), repository access reviews older than three months
  (V-222631), training records older than a year (V-222673).
- Absence of evidence is itself a finding condition under this STIG ("if test results are
  not available, this is a finding") - so report missing artifacts as missing evidence, not
  as "probably fine".

Three lines you must not cross, from the reference document's section 4:

1. Never emit a determination. "Open", "Not a Finding" and "Not Applicable" are an
   assessor's words; 212 of the 286 checks require an interview. You report evidence
   status and candidates.
2. Never synthesise an approval. V-222655 requires ISSO and ISSM review; a tool can check
   that an approval record exists, and nothing else.
3. Never claim coverage of the whole STIG from repository facts. Say which rules the
   evidence speaks to and which need a human, an interview, a scanner or a running system.

For a full checklist run with human-gated determinations recorded into a CKL, hand over to
the stig-runner plugin - this sweep tells you where that assessment will hit gaps before
anyone sits down for it.
