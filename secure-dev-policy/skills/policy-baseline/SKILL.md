---
name: policy-baseline
description: State secure-development obligations in the right category - regulation, contract, or recommendation - as of the 2026 landscape
---
Whenever you write or review anything asserting a secure-development obligation, classify
it before stating it, using this plugin's reference/dod-secure-development-practice.md:

- **Required by regulation or policy**: binds by its own force (a statute, EO, OMB
  memorandum, DoD issuance). Example: DoDI 8510.01 binds DoD components to RMF.
- **Required by contract**: binds only because an agency, programme office or Authorizing
  Official chose to impose it. Example: SBOM delivery; a specific control gate.
- **Recommended practice**: describes outcomes without compelling them. Example: NIST
  SP 800-218 (SSDF), in its own words ("not... a checklist to follow").

Facts that must not be misstated (each verified in the reference document, dated there):

1. OMB M-26-05 (23 January 2026) **rescinded** M-22-18 and M-23-16. SSDF attestation and
   the CISA Common Form are no longer a government-wide requirement; they are options an
   agency "may choose". Never assert the attestation regime as current law.
2. What survived as "shall": agencies maintain a software and hardware inventory - an
   agency obligation, not a producer one.
3. SSDF task IDs stop at RV.3. Documents citing RV.8 (the DoD Activities & Tools Guidebook
   does) carry a source defect - surface it, never invent a mapping for it.
4. DoDI 8510.01 defines seven RMF steps and never uses the words "DevSecOps" or "cATO";
   cATO comes from the February 2022 OSD memo and the 2024 evaluation criteria. Attribute
   requirements to the instrument that actually states them.
5. Do not hard-code Iron Bank as the sole artifact source (softened in Fundamentals v2.5),
   and treat the approved reference-design list as a lookup, not a constant.

When a claim you need is in the reference document's **Not verified** section (the SWFT
framework, FAR/DFARS SSDF clauses, SP 800-218 Rev.1 content), say it is unverified or go
verify it against the primary source - never cite it as settled. Build tooling and prose
so SSDF/SBOM gates are per-programme configuration, not universal assumptions.
