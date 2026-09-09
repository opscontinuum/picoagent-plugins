"""secure-dev-policy: the SBOM minimum-elements check, against both formats it reads.

The fixtures are synthetic SBOMs, complete and holed, because the check's value is naming
the hole: "absent on 1/2 components" is actionable where a boolean is not.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from helpers import ScriptedProvider, make_runtime, run, temp_dir, text
from picoagent.core.tools import ToolContext
from picoagent.plugins import loader

PLUGIN = Path(__file__).resolve().parents[1] / "secure-dev-policy"

_spec = importlib.util.spec_from_file_location("picoagent_plugin_secure_dev_policy",
                                               PLUGIN / "secure_dev_policy.py")
sdp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sdp
_spec.loader.exec_module(sdp)

COMPLETE_CDX = {
    "bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
    "signature": {"algorithm": "ES256", "value": "..."},
    "metadata": {"timestamp": "2026-09-01T00:00:00Z",
                 "tools": [{"name": "genbom", "version": "2.0"}],
                 "authors": [{"name": "Example Corp"}],
                 "lifecycles": [{"phase": "build"}]},
    "components": [
        {"name": "left-pad", "version": "1.3.0", "supplier": {"name": "npm"},
         "purl": "pkg:npm/left-pad@1.3.0",
         "hashes": [{"alg": "SHA-256", "content": "ab" * 32}],
         "licenses": [{"license": {"id": "MIT"}}]},
    ],
    "dependencies": [{"ref": "left-pad"}],
}


def _tool(tmp: Path):
    rt = make_runtime(tmp, provider=ScriptedProvider([[text("ok")]]))
    loader.load_plugin(PLUGIN, rt, loader.TrustStore(tmp / "home"), allow_untrusted=True)
    return rt.tools.get("sbom_check")


def _ctx(tmp: Path) -> ToolContext:
    return ToolContext(cwd=tmp, config={"tool_output_max_bytes": 50_000,
                                        "tool_output_max_lines": 2000},
                       tool_call_id="t1", abort=asyncio.Event())


class SbomCheckTests(unittest.TestCase):

    def setUp(self):
        self.tmp = temp_dir()

    def _check(self, payload) -> "object":
        (self.tmp / "bom.json").write_text(json.dumps(payload))
        return run(_tool(self.tmp).execute({"path": "bom.json"}, _ctx(self.tmp)))

    def test_a_complete_cyclonedx_sbom_has_no_gaps(self):
        result = self._check(COMPLETE_CDX)
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.details["format"], "cyclonedx")
        self.assertEqual(result.details["gaps"], 0, result.content)

    def test_a_component_missing_its_hash_is_counted_not_hidden(self):
        holed = json.loads(json.dumps(COMPLETE_CDX))
        holed["components"].append({"name": "mystery", "version": "0.1",
                                    "supplier": {"name": "x"}, "purl": "pkg:pypi/mystery@0.1",
                                    "licenses": [{"license": {"id": "MIT"}}]})
        result = self._check(holed)
        self.assertGreater(result.details["gaps"], 0)
        self.assertIn("absent on 1/2 components", result.content)

    def test_spdx_is_detected_and_its_formatless_fields_say_so(self):
        result = self._check({"spdxVersion": "SPDX-2.3",
                              "creationInfo": {"created": "2026-09-01T00:00:00Z",
                                               "creators": ["Tool: genbom-2.0"]},
                              "documentNamespace": "https://example.invalid/bom/1",
                              "packages": [{"name": "left-pad", "versionInfo": "1.3.0",
                                            "supplier": "Organization: npm",
                                            "SPDXID": "SPDXRef-1",
                                            "checksums": [{"algorithm": "SHA256",
                                                           "checksumValue": "ab" * 32}],
                                            "licenseConcluded": "MIT"}],
                              "relationships": [{"spdxElementId": "SPDXRef-1"}]})
        self.assertEqual(result.details["format"], "spdx")
        self.assertIn("no standard field in this format", result.content)

    def test_json_that_is_neither_format_is_an_error_value(self):
        result = self._check({"hello": "world"})
        self.assertTrue(result.is_error)
        self.assertIn("neither SPDX", result.content)

    def test_a_file_that_is_not_json_is_an_error_value(self):
        (self.tmp / "bom.json").write_text("<xml/>")
        result = run(_tool(self.tmp).execute({"path": "bom.json"}, _ctx(self.tmp)))
        self.assertTrue(result.is_error)
        self.assertIn("not JSON", result.content)

    def test_requirement_status_is_framed_as_a_contract_question(self):
        """The category discipline the plugin exists for, held in its own output."""
        result = self._check(COMPLETE_CDX)
        self.assertIn("contract question", result.content)
        self.assertNotIn("required by regulation", result.content)

    def test_a_relative_path_leaving_the_project_is_refused_as_a_value(self):
        result = run(_tool(self.tmp).execute({"path": "../../bom.json"}, _ctx(self.tmp)))
        self.assertTrue(result.is_error)


if __name__ == "__main__":
    unittest.main()
