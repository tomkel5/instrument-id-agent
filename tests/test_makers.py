import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.main import discovery_makers, import_direct, prompt, run_once


class MakerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = {"state_dir": Path(directory.name), "workspace": Path(directory.name),
                         "codex_command": ["codex"], "api_url": "https://api.test/api/ingest",
                         "api_key": "credential-token"}
        self.catalog = [{"id": 7, "name": "Jean Test", "searchTerms": "J. Test"}]
        self.value = {"uri": "https://dealer.test/violin", "title": "Violin", "description": "Description",
                      "imageUrls": ["https://dealer.test/image.jpg"], "makerId": 7,
                      "makerAssessment": {"confidence": "high", "attribution": "verified",
                          "instrumentSpecific": True, "makerName": "Jean Test", "alternativeMakers": [],
                          "evidence": [{"url": "https://dealer.test/violin", "kind": "source-page",
                                        "quote": "This violin was made by Jean Test; certified by the maker."}],
                          "reasoning": "Dealer describes this instrument and its maker certificate."}}

    def discover(self, catalog=None):
        (self.settings["state_dir"] / "discovery-state.json").unlink(missing_ok=True)
        with patch("src.main.api_request", side_effect=[[], [], {"content": self.catalog if catalog is None else catalog, "last": True}]), \
                patch("src.main.subprocess.run", return_value=subprocess.CompletedProcess(
                    [], 0, "DIRECT_IMPORT_JSON: " + json.dumps(self.value), "")) as codex, \
                patch("src.main.import_direct", return_value={"instrumentId": 1}) as importer:
            run_once(self.settings)
        state = json.loads((self.settings["state_dir"] / "discovery-state.json").read_text())
        return importer.call_args.args[1], state["runs"][-1]["makerDecision"], codex.call_args.kwargs["input"]

    def test_high_confidence_assignment_and_persistent_evidence(self):
        imported, decision, task = self.discover()
        self.assertEqual(imported["makerId"], 7)
        self.assertNotIn("makerAssessment", imported)
        self.assertTrue(decision["assigned"])
        self.assertEqual(decision["assessment"]["evidence"], self.value["makerAssessment"]["evidence"])
        self.assertIn('"id": 7', task)
        self.assertNotIn(self.settings["api_key"], task)

    def test_ambiguous_attribution_preserves_import(self):
        self.value["makerAssessment"].update(attribution="ambiguous", alternativeMakers=["Other maker"],
                                              reasoning="Attributed to Jean Test, possibly another maker")
        imported, decision, _ = self.discover()
        self.assertNotIn("makerId", imported)
        self.assertFalse(decision["assigned"])
        self.assertIn("possibly", decision["assessment"]["reasoning"])
        self.assertEqual(imported["imageUrls"], self.value["imageUrls"])

    def test_empty_catalog_keeps_discovery_import_working(self):
        imported, decision, _ = self.discover([])
        self.assertNotIn("makerId", imported)
        self.assertEqual(decision["reason"], "Maker catalog unavailable or empty")

    def test_no_matching_maker(self):
        imported, decision, _ = self.discover([{"id": 8, "name": "Other maker"}])
        self.assertNotIn("makerId", imported)
        self.assertEqual(decision["reason"], "No unique matching maker in catalog")

    def test_duplicate_names_wrong_ids_and_weak_evidence_are_rejected(self):
        cases = [({"confidence": "low"}, self.catalog),
                 ({"evidence": [{"kind": "source-page", "url": self.value["uri"], "quote": "Attributed to Jean Test"}]}, self.catalog),
                 ({"instrumentSpecific": False}, self.catalog),
                 ({"evidence": [{"kind": "search-snippet", "url": self.value["uri"], "quote": "Jean Test"}]}, self.catalog),
                 ({"evidence": [{"kind": "source-page", "url": "https://dealer.test/other", "quote": "Jean Test"}]}, self.catalog),
                 ({}, self.catalog + [{"id": 9, "name": "Jean Test"}]),
                 ({"makerName": "Other maker"}, self.catalog),
                 ({}, [{"id": 8, "name": "Jean Test"}])]
        original = self.value["makerAssessment"].copy()
        for updates, catalog in cases:
            with self.subTest(updates=updates, catalog=catalog):
                self.value["makerAssessment"] = {**original, **updates}
                imported, decision, _ = self.discover(catalog)
                self.assertNotIn("makerId", imported)
                self.assertFalse(decision["assigned"])

    def test_missing_assessment_and_credentials(self):
        self.value.pop("makerAssessment")
        imported, decision, _ = self.discover()
        self.assertNotIn("makerId", imported)
        self.assertFalse(decision["assigned"])
        self.value["makerAssessment"] = {"reasoning": "unknown " + self.settings["api_key"]}
        self.discover()
        self.assertNotIn(self.settings["api_key"], (self.settings["state_dir"] / "discovery-state.json").read_text())

    @patch("src.main.api_request")
    def test_catalog_pagination_and_failure_discard_partial_catalog(self, api):
        api.side_effect = [{"content": self.catalog, "last": False},
                           {"content": [{"id": 9, "name": "Another"}], "last": True}]
        self.assertEqual(len(discovery_makers(self.settings)), 2)
        self.assertEqual(api.call_args.args[1], "/makers?page=1&size=100&sort=id,asc")
        for failure in (RuntimeError("credential-token"), {}, {"content": [], "last": False}):
            api.side_effect = [{"content": self.catalog, "last": False}, failure]
            with self.assertLogs("src.main", level="WARNING") as logs:
                self.assertEqual(discovery_makers(self.settings), [])
            self.assertNotIn("credential-token", str(logs.output))

    @patch("src.main.urllib.request.urlopen")
    def test_direct_request_sends_maker_id(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = b'{"instrumentId":1}'
        value = {key: val for key, val in self.value.items() if key != "makerAssessment"}
        import_direct(self.settings, value)
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["makerId"], 7)
        self.assertEqual(payload["type"], "DIRECT")

    def test_prompt_conservative_rules(self):
        task = prompt({}, makers=self.catalog)
        for rule in ("seller name", "search-result snippet", "image appearance", "unverified attribution",
                     "multiple plausible makers", "specific instrument", "authoritative", "makerId null"):
            self.assertIn(rule, task)
