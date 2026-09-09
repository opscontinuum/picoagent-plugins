---
name: cato-evidence
description: Assemble or audit the evidence package a continuous ATO actually requires, from the DoD CISO's own criteria
---
Working toward or auditing a cATO, use the package the DoD CISO evaluates (reference/
dod-secure-development-practice.md, section 5, from the 29 May 2024 Evaluation Criteria).
The unit of authorisation is the software factory, not the release; the evidence divides
into three kinds, and a tool that produces a report at release time and nothing between
releases meets none of them:

1. **Continuously emitted signals**: dashboard feeds, CMRS compliance reporting, scan
   results, control-gate outcomes, an SBOM per build with an archive queryable when a new
   CVE lands, audit logs per OMB M-21-31 Appendix A.
2. **Periodically refreshed documents**: cATO risk-management strategy, CONMON strategy
   with per-control monitoring timelines, authorization boundary diagram with data flows,
   SSP/SAP/SAR/RAR/POA&Ms, COOP/DRP with test evidence, incident response plan with
   exercise evidence, third-party penetration test within 90 days then annually.
3. **Demonstrable live behaviour**: each control gate shown firing (what opens it, what
   closes it, what alerts), each guardrail's tolerance band and escalation, the dashboard
   in operation, separation of duties in the org chart, per-role training records.

The three competencies those map to (February 2022 OSD memo): continuous monitoring of RMF
controls; active cyber defense ("simply conducting scans and patching does not meet the
threshold"); adoption of an approved DevSecOps reference design.

Honesty rules when reporting status against this list: name the artifacts that exist and
their dates; the unresolved tensions are real and stay named (annual pen-test vs daily
deploy; remediation timelines are whatever the AO accepts - configurable, never
hard-coded); and gates may start human-operated - "control gates are mandatory, but there
is no expectation that they are fully automated" from day one, so a human-in-the-loop gate
is a compliant gate with automation on the backlog.
