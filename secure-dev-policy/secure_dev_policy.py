"""secure-dev-policy - the federal secure-development landscape, made hard to misstate.

The research this plugin carries (``reference/dod-secure-development-practice.md``) found
that the single most common error in this area is a category error: treating recommended
practice as regulation. Its sharpest instance is dated: OMB M-26-05 (23 January 2026)
rescinded the SSDF attestation memoranda (M-22-18, M-23-16) that most secure-development
tooling was built around, leaving SSDF attestation and SBOM delivery as things an agency
*may choose* to require by contract - and agency software/hardware inventory as the one
"shall" that survived. Tooling and prose that assert the old regime carry a false premise.
The ``policy-baseline`` skill exists to keep that from being written here.

The tool half is ``sbom_check``: an SBOM either carries the CISA *2026 Minimum Elements for
a Software Bill of Materials* data fields or it does not, and that is a mechanical question.
It reads SPDX JSON and CycloneDX JSON (the safe format pair - the 2026 elements dropped SWID)
and reports each minimum element as present or absent. It does not judge whether an SBOM is
*required* - that is a contract question, which is the whole point of the classification
discipline.

This plugin reads nothing from a repository's ``.picoagent/config.toml``. The research is a
dated snapshot (September 2026); the reference document's own header says what was verified
and its Not-verified section what was not.
"""
from __future__ import annotations

import json
from pathlib import Path

from picoagent.core.tools import PathRefused, ToolContext, resolve_path_inside_project, tool_result
from picoagent.core.types import ToolResult

#: The CISA 2026 minimum-element data fields (Appendix A), each mapped to how the two JSON
#: formats spell it. ``None`` means the format has no standard home for the field; that is
#: reported as absent-with-a-note rather than silently skipped, because "the format cannot
#: say it" and "the producer left it out" call for different fixes.
_METADATA_FIELDS: list[tuple[str, str | None, str | None]] = [
    # (element, SPDX JSON location, CycloneDX JSON location) - locations are dotted paths
    ("SBOM Author", "creationInfo.creators", "metadata.authors|metadata.tools"),
    ("SBOM Author Signature", None, "signature"),
    ("SBOM Data Format Name", "spdxVersion", "bomFormat"),
    ("SBOM Data Format Version", "spdxVersion", "specVersion"),
    ("SBOM Generation Context", None, "metadata.lifecycles"),
    ("SBOM Timestamp", "creationInfo.created", "metadata.timestamp"),
    ("SBOM Tool Name", "creationInfo.creators", "metadata.tools"),
    ("SBOM Tool Version", "creationInfo.creators", "metadata.tools"),
    ("SBOM Version", "documentNamespace", "version"),
]

_COMPONENT_FIELDS: list[tuple[str, str, str]] = [
    ("Component Name", "name", "name"),
    ("Component Producer", "supplier|originator", "supplier|publisher|group"),
    ("Component Version", "versionInfo", "version"),
    ("Component Identifiers", "externalRefs|SPDXID", "purl|cpe|bom-ref"),
    ("Component Hash Value", "checksums", "hashes"),
    ("Component Hash Algorithm", "checksums", "hashes"),
    ("Component License", "licenseConcluded|licenseDeclared", "licenses"),
    ("Component Dependency Relationship", "@relationships", "@dependencies"),
]


def _dig(payload: dict, dotted: str):
    """The value at a ``a.b.c`` path, or ``None``; ``|`` tries alternatives in order."""
    for candidate in dotted.split("|"):
        value: object = payload
        for key in candidate.split("."):
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]
        if value not in (None, "", [], {}):
            return value
    return None


def detect_format(payload: dict) -> str | None:
    if isinstance(payload.get("spdxVersion"), str):
        return "spdx"
    if payload.get("bomFormat") == "CycloneDX":
        return "cyclonedx"
    return None


def check_metadata(payload: dict, fmt: str) -> list[tuple[str, str]]:
    """Each metadata element as ``(element, status)``; status is present / absent / a note."""
    rows = []
    for element, spdx_path, cdx_path in _METADATA_FIELDS:
        path = spdx_path if fmt == "spdx" else cdx_path
        if path is None:
            rows.append((element, "absent (no standard field in this format; supply out of band)"))
        else:
            rows.append((element, "present" if _dig(payload, path) is not None else "absent"))
    return rows


def check_components(payload: dict, fmt: str) -> list[tuple[str, str]]:
    """Component-data elements over the whole component list: an element is present only when
    every component carries it, because a minimum element met by half the inventory is not
    met - and the count of components missing it is the actionable part."""
    components = payload.get("packages" if fmt == "spdx" else "components") or []
    rows: list[tuple[str, str]] = []
    if not isinstance(components, list) or not components:
        return [(element, "absent (no components listed)") for element, _, _ in _COMPONENT_FIELDS]
    for element, spdx_path, cdx_path in _COMPONENT_FIELDS:
        path = spdx_path if fmt == "spdx" else cdx_path
        if path.startswith("@"):
            # A document-level list (relationships/dependencies), not a per-component field.
            rows.append((element, "present" if payload.get(path[1:]) else "absent"))
            continue
        missing = sum(1 for c in components if not isinstance(c, dict) or _dig(c, path) is None)
        rows.append((element, "present" if missing == 0
                     else f"absent on {missing}/{len(components)} components"))
    return rows


class SbomCheck:
    """One SBOM file against the CISA 2026 minimum elements."""
    name = "sbom_check"
    description = ("Check an SBOM file (SPDX JSON or CycloneDX JSON) against the CISA 2026 "
                   "Minimum Elements data fields: the nine SBOM-metadata elements and eight "
                   "component-data elements, reported present/absent per element, with "
                   "component-level gaps counted. Does not decide whether an SBOM is required "
                   "- as of OMB M-26-05 that is a per-contract question. XML SPDX and SWID "
                   "are not read; SWID was dropped from the 2026 elements.")
    parameters = {"type": "object", "properties": {"path": {"type": "string"}},
                  "required": ["path"]}

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            path = resolve_path_inside_project(ctx, args["path"])
        except PathRefused as exc:
            return tool_result(ctx, str(exc), is_error=True)
        try:
            payload = json.loads(Path(path).read_text(errors="replace"))
        except OSError as exc:
            return tool_result(ctx, f"cannot read {path}: {exc}", is_error=True)
        except json.JSONDecodeError as exc:
            return tool_result(ctx, f"{path} is not JSON ({exc}); this check reads SPDX JSON "
                                    "and CycloneDX JSON only", is_error=True)
        if not isinstance(payload, dict) or (fmt := detect_format(payload)) is None:
            return tool_result(ctx, f"{path} is JSON but neither SPDX (no spdxVersion) nor "
                                    "CycloneDX (no bomFormat)", is_error=True)

        metadata = check_metadata(payload, fmt)
        components = check_components(payload, fmt)
        gaps = sum(1 for _, status in metadata + components if status != "present")
        lines = [f"format: {fmt}", "", "SBOM metadata (CISA 2026 minimum elements):"]
        lines += [f"  {element:<34} {status}" for element, status in metadata]
        lines += ["", "Component data:"]
        lines += [f"  {element:<34} {status}" for element, status in components]
        lines += ["", (f"{gaps} element(s) not fully met. Whether this SBOM is *required* is "
                       "a contract question (OMB M-26-05: agencies may choose); what it must "
                       "contain when one is, is the list above.")]
        return tool_result(ctx, "\n".join(lines), is_error=False, format=fmt, gaps=gaps)


def register(api):
    api.register_tool(SbomCheck())
