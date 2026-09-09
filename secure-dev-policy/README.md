# secure-dev-policy

The federal secure-development landscape, made hard to misstate. Built from
[reference/dod-secure-development-practice.md](reference/dod-secure-development-practice.md),
a dated (September 2026) primary-source survey whose core discipline is the three-way
classification: required by regulation, required by contract, recommended practice.

The load-bearing dated fact: OMB M-26-05 (23 January 2026) rescinded the SSDF attestation
memoranda. Attestation and SBOM delivery are now per-contract choices; the agency
software/hardware inventory is the "shall" that survived. The `policy-baseline` skill keeps
that from being written wrongly; `cato-evidence` carries the DoD CISO's actual cATO package
(signals emitted continuously, documents refreshed periodically, behaviour demonstrated
live - the unit of authorisation is the software factory, not the release).

`sbom_check` reads an SPDX JSON or CycloneDX JSON SBOM and reports each CISA *2026 Minimum
Elements* data field - nine SBOM-metadata elements, eight component-data elements - as
present or absent, counting the components that miss one. It does not decide whether an
SBOM is required; that is the contract question the classification exists for. XML SPDX
and SWID are not read (SWID was dropped from the 2026 elements).
