import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import call, patch

from src.main import discovery_instructions, prompt, run_once


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
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


if __name__ == "__main__":
    unittest.main()
