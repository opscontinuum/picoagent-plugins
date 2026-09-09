"""sdlc-evidence: artifact probes, the five-section threat-model check, and the honesty rule.

The plugin's one hard promise is negative - it never emits a finding determination - and a
promise like that is exactly what drifts unless a test holds it, so the determination
vocabulary is pinned out of the output for both a full and an empty repository.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path

from helpers import ScriptedProvider, make_runtime, run, temp_dir, text
from picoagent.core.tools import ToolContext
from picoagent.plugins import loader

PLUGIN = Path(__file__).resolve().parents[1] / "sdlc-evidence"

_spec = importlib.util.spec_from_file_location("picoagent_plugin_sdlc_evidence",
                                               PLUGIN / "sdlc_evidence.py")
sdlc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sdlc
_spec.loader.exec_module(sdlc)

FIVE_SECTIONS = ("# Threat model\n\n## Identified threats\nT1.\n\n"
                 "## Potential vulnerabilities\nV1.\n\n## Counter measures taken\nC1.\n\n"
                 "## Potential mitigations\nM1.\n\n"
                 "## Mitigations selected based on risk analysis\nS1.\n")


def _tool(tmp: Path):
    rt = make_runtime(tmp, provider=ScriptedProvider([[text("ok")]]))
    loader.load_plugin(PLUGIN, rt, loader.TrustStore(tmp / "home"), allow_untrusted=True)
    return rt.tools.get("sdlc_artifacts")


def _ctx(tmp: Path) -> ToolContext:
    return ToolContext(cwd=tmp, config={"tool_output_max_bytes": 50_000,
                                        "tool_output_max_lines": 2000},
                       tool_call_id="t1", abort=asyncio.Event())


class ThreatModelSectionTests(unittest.TestCase):

    def test_all_five_sections_are_recognised(self):
        present, missing = sdlc.threat_model_sections(FIVE_SECTIONS)
        self.assertEqual(len(present), 5)
        self.assertEqual(missing, [])

    def test_a_missing_section_is_named(self):
        text_without = FIVE_SECTIONS.replace("## Counter measures taken\nC1.\n\n", "")
        present, missing = sdlc.threat_model_sections(text_without)
        self.assertIn("Counter measures taken", missing)
        self.assertEqual(len(present), 4)


class SurveyTests(unittest.TestCase):

    def setUp(self):
        self.tmp = temp_dir()

    def _full_repo(self) -> Path:
        (self.tmp / "CONTRIBUTING.md").write_text("standards\n")
        (self.tmp / ".editorconfig").write_text("root = true\n")
        (self.tmp / "docs" / "security").mkdir(parents=True)
        (self.tmp / "docs" / "architecture.md").write_text("design\n")
        (self.tmp / "docs" / "security" / "threat-model.md").write_text(FIVE_SECTIONS)
        (self.tmp / "SECURITY.md").write_text("report to security@example.invalid\n")
        (self.tmp / "coverage.xml").write_text("<coverage/>\n")
        (self.tmp / "uv.lock").write_text("\n")
        (self.tmp / "SHA256SUMS").write_text("\n")
        return self.tmp

    def test_a_fully_evidenced_repository_reports_seven_of_seven(self):
        result = run(_tool(self._full_repo()).execute({}, _ctx(self.tmp)))
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.details["present"], 7, result.content)
        self.assertEqual(result.details["probed"], 7)

    def test_an_empty_repository_reports_absences_by_rule(self):
        result = run(_tool(self.tmp).execute({}, _ctx(self.tmp)))
        self.assertEqual(result.details["present"], 0, result.content)
        for rule in ("V-222653", "V-222654", "V-222655", "V-222657", "V-222649",
                     "V-222658", "V-222645"):
            self.assertIn(rule, result.content)

    def test_a_threat_model_missing_a_section_reads_partial_and_names_it(self):
        (self.tmp / "docs" / "security").mkdir(parents=True)
        (self.tmp / "docs" / "security" / "threat-model.md").write_text(
            FIVE_SECTIONS.replace("## Potential mitigations\nM1.\n\n", ""))
        result = run(_tool(self.tmp).execute({}, _ctx(self.tmp)))
        self.assertIn("partial", result.content)
        self.assertIn("Potential mitigations", result.content)

    def test_standards_config_without_a_document_is_partial(self):
        (self.tmp / ".editorconfig").write_text("root = true\n")
        result = run(_tool(self.tmp).execute({}, _ctx(self.tmp)))
        line = next(l for l in result.content.splitlines() if l.startswith("V-222653"))
        self.assertIn("partial", line)
        self.assertIn("no standards document", line)

    def test_the_output_never_contains_a_determination(self):
        """The plugin's own honesty rule, held by a test rather than a sentence: the words an
        assessor writes into a checklist must not appear in what this tool reports, on a full
        repository or an empty one."""
        for root in (self._full_repo(), temp_dir()):
            result = run(_tool(root).execute({}, _ctx(root)))
            for verdict in ("Not a Finding", "Not Applicable", "NotAFinding", "Open"):
                self.assertNotIn(verdict, result.content,
                                 f"determination vocabulary {verdict!r} in tool output")

    def test_a_relative_path_leaving_the_project_is_refused_as_a_value(self):
        result = run(_tool(self.tmp).execute({"path": "../.."}, _ctx(self.tmp)))
        self.assertTrue(result.is_error)


if __name__ == "__main__":
    unittest.main()
