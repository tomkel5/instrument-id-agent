import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import call, patch

from src.main import discovery_config, discovery_instructions, prompt, run_once


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        maker_patch = patch("src.main.discovery_makers", return_value=[])
        maker_patch.start()
        self.addCleanup(maker_patch.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = {"state_dir": Path(directory.name), "workspace": Path(directory.name),
                         "codex_command": ["codex"], "api_url": "https://api.test/api/ingest",
                         "api_key": "secret"}

    def test_configured_prompt_preserves_discovery_requirements(self):
        instructions = "Search French dealers.\nPrefer violins made before 1900."
        value = prompt({"sources": ["https://example.test/seen"], "runs": []}, instructions)
        for text in (instructions, "Do not invent facts", "complete image gallery",
                     "every unique image", "Avoid every source URL", "https://example.test/seen",
                     "evidence-based description", "DIRECT_IMPORT_JSON: null", '"imageUrls"'):
            self.assertIn(text, value)

    @patch("src.main.subprocess.run")
    @patch("src.main.api_request")
    def test_each_discovery_fetches_current_instructions(self, api, codex):
        api.side_effect = [[], [{"name": "AI_SEARCH_INSTRUCTIONS", "value": "Search French dealers"}],
                           [], [{"name": "AI_SEARCH_INSTRUCTIONS", "value": "Search Italian auctions"}]]
        codex.return_value = subprocess.CompletedProcess([], 0, "DIRECT_IMPORT_JSON: null", "")
        run_once(self.settings)
        run_once(self.settings)
        self.assertEqual(api.call_args_list, [call(self.settings, "/reparation-requests"),
                                             call(self.settings, "/config")] * 2)
        self.assertIn("Search French dealers", codex.call_args_list[0].kwargs["input"])
        self.assertIn("Search Italian auctions", codex.call_args_list[1].kwargs["input"])
        self.assertNotIn("Search French dealers", codex.call_args_list[1].kwargs["input"])

    @patch("src.main.api_request")
    def test_empty_or_missing_setting_uses_default_prompt_and_logs(self, api):
        for response, level in (([], "WARNING"),
                                ([{"name": "OTHER", "value": "ignore me"}], "WARNING"),
                                ([{"name": "AI_SEARCH_INSTRUCTIONS", "value": ""}], "INFO"),
                                ([{"name": "AI_SEARCH_INSTRUCTIONS", "value": " \n "}], "INFO")):
            with self.subTest(response=response):
                api.return_value = response
                with self.assertLogs("src.main", level=level) as logs:
                    instructions = discovery_instructions(self.settings)
                self.assertIn("using default discovery instructions", logs.output[0])
                self.assertEqual(prompt({}, instructions), prompt({}))

    @patch("src.main.import_direct")
    @patch("src.main.subprocess.run")
    @patch("src.main.api_request")
    def test_unavailable_or_malformed_config_skips_discovery(self, api, codex, import_direct):
        for response in (urllib.error.URLError("offline"), RuntimeError("HTTP failure"),
                         json.JSONDecodeError("invalid", "", 0), None, {},
                         [{"name": "AI_SEARCH_INSTRUCTIONS", "value": None}],
                         [{"name": "AI_SEARCH_INSTRUCTIONS", "value": 42}]):
            with self.subTest(response=response):
                api.side_effect = [[], response]
                with self.assertRaisesRegex(RuntimeError, "skipping discovery this cycle"):
                    run_once(self.settings)
                codex.assert_not_called()
                import_direct.assert_not_called()
                self.assertFalse((self.settings["state_dir"] / "discovery-state.json").exists())

    @patch("src.main.urllib.request.urlopen")
    def test_config_api_uses_authenticated_get(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = (
            b'[{"name":"AI_SEARCH_INSTRUCTIONS","value":"Search dealers"}]')
        self.assertEqual(discovery_instructions(self.settings), "Search dealers")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.test/api/config")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")

    @patch("src.main.api_request")
    def test_batch_configuration_defaults_and_validation(self, api):
        for settings, expected in (([], 1), ([{"name": "AI_SEARCH_BATCH_SIZE", "value": "1"}], 1),
                                   ([{"name": "AI_SEARCH_BATCH_SIZE", "value": "12"}], 12)):
            api.return_value = settings
            self.assertEqual(discovery_config(self.settings)[1], expected)
        for value in (None, 2, "0", "-1", "1.5", "bad", ""):
            api.return_value = [{"name": "AI_SEARCH_BATCH_SIZE", "value": value}]
            with self.assertRaisesRegex(RuntimeError, "positive integer"):
                discovery_config(self.settings)

    def listing(self, number):
        return {"uri": f"https://example.test/{number}", "title": "V", "description": "D",
                "imageUrls": ["https://example.test/1.jpg", "https://example.test/2.jpg"]}

    @patch("src.main.import_direct")
    @patch("src.main.subprocess.run")
    @patch("src.main.api_request")
    def test_batch_persists_success_failure_and_duplicate_results(self, api, codex, importer):
        api.side_effect = [[], [{"name": "AI_SEARCH_BATCH_SIZE", "value": "6"}]]
        invalid = {**self.listing(2), "imageUrls": []}
        values = [self.listing(1), invalid, self.listing(3), self.listing(4), self.listing(1), self.listing(5)]
        codex.return_value = subprocess.CompletedProcess([], 0, "DIRECT_IMPORT_JSON: " + json.dumps(values), "")
        def importing(settings, value):
            # Earlier successful imports are durable before the next request.
            if value["uri"].endswith("/3"):
                state = json.loads((self.settings["state_dir"] / "discovery-state.json").read_text())
                self.assertEqual(state["sources"], [self.listing(1)["uri"]])
                raise RuntimeError("download failed")
            return {"duplicate": True} if value["uri"].endswith("/4") else {"instrumentId": value["uri"]}
        importer.side_effect = importing
        run_once(self.settings)
        self.assertIn("Find up to 6", codex.call_args.kwargs["input"])
        self.assertIn("JSON array", codex.call_args.kwargs["input"])
        state = json.loads((self.settings["state_dir"] / "discovery-state.json").read_text())
        outcomes = state["runs"][0]["results"]
        self.assertEqual([v["result"] for v in outcomes],
                         ["imported", "validation-failed", "import-failed", "duplicate", "duplicate-source", "imported"])
        self.assertEqual(len(state["discoveredSources"]), 5)
        self.assertEqual(state["sources"], [self.listing(i)["uri"] for i in (1, 4, 5)])
        self.assertEqual(importer.call_count, 4)
        self.assertEqual(outcomes[0]["response"], {"instrumentId": self.listing(1)["uri"]})
        self.assertEqual(outcomes[-1]["candidate"]["imageUrls"], self.listing(5)["imageUrls"])
        # A later cycle can retry a failed source; imported sources stay deduplicated.
        api.side_effect = [[], [{"name": "AI_SEARCH_BATCH_SIZE", "value": "2"}]]
        codex.return_value = subprocess.CompletedProcess([], 0, "DIRECT_IMPORT_JSON: " + json.dumps(
            [self.listing(1), self.listing(3)]), "")
        importer.side_effect = None
        importer.return_value = {"instrumentId": 3}
        importer.reset_mock()
        run_once(self.settings)
        importer.assert_called_once_with(self.settings, self.listing(3))
        state = json.loads((self.settings["state_dir"] / "discovery-state.json").read_text())
        self.assertEqual(len(state["runs"]), 2)
        self.assertIn(self.listing(3)["uri"], state["sources"])


    @patch("src.main.import_direct")
    @patch("src.main.subprocess.run")
    @patch("src.main.api_request")
    def test_batch_limit_and_previous_sources(self, api, codex, importer):
        path = self.settings["state_dir"] / "discovery-state.json"
        path.write_text(json.dumps({"sources": [self.listing(1)["uri"]], "runs": []}))
        for size in (None, "1", "2"):
            api.side_effect = [[], [] if size is None else [{"name": "AI_SEARCH_BATCH_SIZE", "value": size}]]
            codex.return_value = subprocess.CompletedProcess([], 0, "DIRECT_IMPORT_JSON: " + json.dumps(
                [self.listing(1), self.listing(2), self.listing(3)]), "")
            importer.return_value = {"instrumentId": 2}
            importer.reset_mock()
            run_once(self.settings)
            self.assertEqual(importer.call_count, 1 if size == "2" else 0)
            self.assertIn(f"Find up to {size or '1'}", codex.call_args.kwargs["input"])


if __name__ == "__main__":
    unittest.main()
