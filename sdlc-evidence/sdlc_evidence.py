"""sdlc-evidence - the ASD STIG's process family, as an evidence probe a repository can answer.

The research this plugin carries (``reference/disa-asd-stig-for-developers.md``) measured the
shape of the DISA Application Security and Development STIG V6R4 and found that 212 of its
286 check procedures name an interview: the STIG is written for a human assessor, and DISA
ships no automation content for it at all. What a repository-inspecting tool can honestly do
is the document's section 4.4: determine applicability, report **evidence status** - does
the artifact exist, is it structurally complete - and flag candidates for a human. What it
must never do is emit a finding determination as if it were an assessor.

That last rule is enforced here, not just stated: the tool's output never contains the
determination vocabulary ("Open", "Not a Finding", "Not Applicable" as verdicts), and the
plugin's tests fail if it ever starts to. Every line it reports is "present", "absent" or
"partial", plus what was looked for - facts an assessor consumes, not conclusions that
pre-empt one.

The rules probed are the development-process family the research calls "the rules teams
miss, because they do not show up in a scanner": coding standards (V-222653), the design
document (V-222654), the threat model with its five required sections (V-222655), the
vulnerability-intake channel (V-222657), per-release coverage statistics (V-222649), the
component inventory a supported-version review needs (V-222658), and release-artifact
hashes (V-222645).

For running an actual CKL checklist against a repository - determinations included, each
one gated on a human - use the stig-runner plugin (opscontinuum/stig-runner). The two
compose: this probe is the quick sweep; stig-runner is the assessment.

This plugin reads nothing from a repository's ``.picoagent/config.toml``.
"""
from __future__ import annotations

import re
from pathlib import Path

from picoagent.core.tools import PathRefused, ToolContext, resolve_path_inside_project, tool_result
from picoagent.core.types import ToolResult

#: The five sections V-222655's check procedure requires a threat model to contain, verbatim
#: from the STIG, each paired with the loosest pattern that still means the section is there.
#: Loose on purpose: the check is "is the section present", and a probe that demanded exact
#: headings would report absent for a document an assessor would accept.
THREAT_MODEL_SECTIONS = [
    ("Identified threats", re.compile(r"\bthreats?\b", re.I)),
    ("Potential vulnerabilities", re.compile(r"\bvulnerabilit", re.I)),
    ("Counter measures taken", re.compile(r"\bcounter\s?measures?\b", re.I)),
    ("Potential mitigations", re.compile(r"\bpotential\s+mitigations?\b|\bmitigation options\b", re.I)),
    ("Mitigations selected based on risk analysis",
     re.compile(r"\bselected\b|\brisk analysis\b", re.I)),
]

#: Filename shapes that count as evidence per artifact. Shapes, not certainties: presence is
#: reported with the matched path so the reader can judge whether the file is what it looks like.
_STANDARDS_FILES = ["CODING_STANDARDS*", "STYLEGUIDE*", "docs/coding-standards*", "docs/conventions*",
                    "docs/testing-and-conventions*", "CONTRIBUTING*"]
_STANDARDS_ENFORCERS = [".flake8", "ruff.toml", ".ruff.toml", ".pylintrc", "setup.cfg",
                        ".editorconfig", ".eslintrc*", ".pre-commit-config.yaml", "pyproject.toml"]
_DESIGN_FILES = ["DESIGN*", "docs/design*", "docs/architecture*", "docs/designs", "ARCHITECTURE*"]
_THREAT_FILES = ["docs/security/threat-model*", "docs/threat-model*", "THREAT_MODEL*",
                 "docs/security/threat_model*", "threat-model*"]
_INTAKE_FILES = ["SECURITY.md", "docs/SECURITY.md", ".github/SECURITY.md"]
_COVERAGE_FILES = ["coverage.xml", ".coverage", "coverage/", "docs/testing-and-conventions*",
                   "tools/coverage_report*"]
_INVENTORY_FILES = ["uv.lock", "poetry.lock", "package-lock.json", "Cargo.lock", "go.sum",
                    "requirements*.txt", "pyproject.toml", "package.json", "*.sbom.json",
                    "sbom.json", "bom.json"]
_HASH_FILES = ["SHA256SUMS*", "*.sha256", "checksums*"]


def _first_match(root: Path, patterns: list[str]) -> Path | None:
    for pattern in patterns:
        hit = next((p for p in sorted(root.glob(pattern))), None)
        if hit is not None:
            return hit
    return None


def threat_model_sections(text: str) -> tuple[list[str], list[str]]:
    """Which of V-222655's five sections the document covers, and which it does not."""
    present = [name for name, pattern in THREAT_MODEL_SECTIONS if pattern.search(text)]
    missing = [name for name, _ in THREAT_MODEL_SECTIONS if name not in present]
    return present, missing


def survey(root: Path) -> list[tuple[str, str, str, str]]:
    """Every probe as ``(rule, artifact, status, note)``. Status is a fact, never a verdict."""
    rows: list[tuple[str, str, str, str]] = []

    doc = _first_match(root, _STANDARDS_FILES)
    enforcer = _first_match(root, _STANDARDS_ENFORCERS)
    if doc and enforcer:
        status, note = "present", f"{doc.name}, enforced by {enforcer.name}"
    elif doc or enforcer:
        found, half = (doc, "no enforcing linter/formatter config found") if doc else \
                      (enforcer, "config exists but no standards document found")
        status, note = "partial", f"{found.name}; {half}"
    else:
        status, note = "absent", "no standards document and no linter/formatter config"
    rows.append(("V-222653", "coding standards, followed", status, note))

    design = _first_match(root, _DESIGN_FILES)
    rows.append(("V-222654", "design document per release",
                 "present" if design else "absent",
                 design.name if design else "no design/architecture document found"))

    threat = _first_match(root, _THREAT_FILES)
    if threat and threat.is_file():
        present, missing = threat_model_sections(threat.read_text(errors="replace"))
        if missing:
            rows.append(("V-222655", "threat model, five required sections", "partial",
                         f"{threat.name} lacks: {', '.join(missing)}"))
        else:
            rows.append(("V-222655", "threat model, five required sections", "present",
                         f"{threat.name} covers all five required sections; ISSO/ISSM review "
                         "and per-release currency are for a human to confirm"))
    else:
        rows.append(("V-222655", "threat model, five required sections", "absent",
                     "no threat-model document found"))

    intake = _first_match(root, _INTAKE_FILES)
    rows.append(("V-222657", "vulnerability intake channel", "present" if intake else "absent",
                 intake.name if intake else "no SECURITY.md naming how to report a vulnerability"))

    coverage = _first_match(root, _COVERAGE_FILES)
    rows.append(("V-222649", "code coverage statistics per release",
                 "present" if coverage else "absent",
                 str(coverage.relative_to(root)) if coverage else
                 "no coverage report, config or recorded statistics found"))

    inventory = _first_match(root, _INVENTORY_FILES)
    rows.append(("V-222658", "component inventory for support review",
                 "present" if inventory else "absent",
                 inventory.name if inventory else
                 "no lockfile, manifest or SBOM to review versions against support windows"))

    hashes = _first_match(root, _HASH_FILES)
    rows.append(("V-222645", "release artifact hashes (SHA-256)",
                 "present" if hashes else "absent",
                 hashes.name if hashes else "no checksum files; acceptable when releases are "
                                            "hashed elsewhere - say where"))
    return rows


class SdlcArtifacts:
    """The probe as a tool: one call, one evidence table, zero determinations."""
    name = "sdlc_artifacts"
    description = ("Survey a repository for the DISA ASD STIG development-process artifacts "
                   "that are machine-checkable: coding standards (V-222653), design document "
                   "(V-222654), threat model with its five required sections (V-222655), "
                   "vulnerability intake (V-222657), coverage statistics (V-222649), component "
                   "inventory (V-222658), release hashes (V-222645). Reports evidence status "
                   "only - present, partial or absent, with what was looked for. It never "
                   "makes a finding determination; that belongs to an assessor. Optional "
                   "'path' names a repository other than the project root.")
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": []}

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            root = resolve_path_inside_project(ctx, args.get("path") or ".")
        except PathRefused as exc:
            return tool_result(ctx, str(exc), is_error=True)
        if not root.is_dir():
            return tool_result(ctx, f"{root} is not a directory", is_error=True)
        rows = survey(root)
        width = max(len(r[1]) for r in rows)
        lines = [f"{rule}  {artifact.ljust(width)}  {status:<8} {note}"
                 for rule, artifact, status, note in rows]
        counted = sum(1 for r in rows if r[2] == "present")
        lines.append("")
        lines.append(f"{counted}/{len(rows)} artifacts present. Evidence status only: whether "
                     "each is current, correct and approved is an assessor's judgement, and "
                     "212 of the STIG's 286 checks require an interview no repository can "
                     "answer. See this plugin's reference document, section 4.")
        return tool_result(ctx, "\n".join(lines), is_error=False,
                           present=counted, probed=len(rows))


def register(api):
    api.register_tool(SdlcArtifacts())
