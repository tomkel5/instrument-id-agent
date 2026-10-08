import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src.main import run_once, candidate, repair_prompt


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = {"state_dir": Path(self.directory.name), "workspace": Path(self.directory.name),
                         "codex_command": ["codex"], "api_url": "https://api.test/api/ingest", "api_key": "secret"}
        self.request = {"id": 3, "listingId": 12, "notes": "Find missing images", "updatedAt": "2026-10-07T00:00:00Z"}
        self.listing = {"id": 12, "sourceUrl": "https://example.test/v", "title": "V", "description": "D"}
        self.value = {"uri": self.listing["sourceUrl"], "title": "V", "description": "D", "imageUrls": []}

    @patch("src.main.import_direct")
    @patch("src.main.subprocess.run")
    @patch("src.main.api_request")
    def test_repair_has_priority_and_archives_after_update(self, api, codex, import_direct):
        api.side_effect = [[self.request], self.listing, {"storedImageCount": 0}, None]
        codex.return_value = subprocess.CompletedProcess([], 0, "DIRECT_IMPORT_JSON: " + json.dumps(self.value), "")
        run_once(self.settings)
        self.assertEqual([call.args[2] if len(call.args) > 2 else "GET" for call in api.call_args_list],
                         ["GET", "GET", "PATCH", "DELETE"])
        self.assertIn("expectedUpdatedAt=", api.call_args_list[-1].args[1])
        self.assertIn("Find missing images", codex.call_args.kwargs["input"])
        import_direct.assert_not_called()
        state = json.loads((Path(self.directory.name) / "discovery-state.json").read_text())
        self.assertEqual(state["runs"][0]["result"], "repaired")
        self.assertEqual(state["sources"], [])

    @patch("src.main.import_direct")
    @patch("src.main.subprocess.run")
    @patch("src.main.api_request")
    def test_incomplete_repair_remains_active_without_discovery(self, api, codex, import_direct):
        api.side_effect = [[self.request], self.listing]
        codex.return_value = subprocess.CompletedProcess([], 0, "DIRECT_IMPORT_JSON: null", "")
        run_once(self.settings)
        self.assertEqual(api.call_count, 2)
        import_direct.assert_not_called()

    @patch("src.main.import_direct")
    @patch("src.main.subprocess.run")
    @patch("src.main.api_request")
    def test_failed_update_does_not_archive_or_discover(self, api, codex, import_direct):
        api.side_effect = [[self.request], self.listing, RuntimeError("image download failed")]
        codex.return_value = subprocess.CompletedProcess([], 0, "DIRECT_IMPORT_JSON: " + json.dumps(self.value), "")
        with self.assertRaises(RuntimeError):
            run_once(self.settings)
        self.assertEqual(api.call_count, 3)
        import_direct.assert_not_called()

    @patch("src.main.import_direct")
    @patch("src.main.subprocess.run")
    @patch("src.main.api_request", return_value=[])
    def test_no_requests_permits_one_discovery_import(self, api, codex, import_direct):
        self.value["imageUrls"] = ["https://example.test/1.jpg"]
        codex.return_value = subprocess.CompletedProcess([], 0, "DIRECT_IMPORT_JSON: " + json.dumps(self.value), "")
        import_direct.return_value = {"instrumentId": 1}
        run_once(self.settings)
        import_direct.assert_called_once()
        self.assertEqual([call.args[1] for call in api.call_args_list],
                         ["/reparation-requests", "/config", "/makers?page=0&size=100&sort=id,asc"])

    @patch("src.main.import_direct")
    @patch("src.main.subprocess.run")
    @patch("src.main.api_request", side_effect=RuntimeError("API unavailable"))
    def test_request_api_failure_does_not_fall_back_to_discovery(self, api, codex, import_direct):
        with self.assertRaises(RuntimeError):
            run_once(self.settings)
        codex.assert_not_called()
        import_direct.assert_not_called()

class RepairApiTests(unittest.TestCase):
    @patch("src.main.urllib.request.urlopen")
    def test_api_requests_use_ingester_url_and_bearer_credential(self, urlopen):
        from src.main import api_request
        urlopen.return_value.__enter__.return_value.read.return_value = b'{"storedImageCount":1}'
        response = api_request({"api_url": "https://api.test/api/ingest", "api_key": "secret"},
                               "/listing/12", "PATCH", {"imageUrls": ["https://images.test/1"]})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.test/api/listing/12")
        self.assertEqual(request.get_method(), "PATCH")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(json.loads(request.data), {"imageUrls": ["https://images.test/1"]})
        self.assertEqual(response["storedImageCount"], 1)

    @patch("src.main.urllib.request.urlopen")
    def test_archive_accepts_empty_no_content_response(self, urlopen):
        from src.main import api_request
        urlopen.return_value.__enter__.return_value.read.return_value = b""
        self.assertIsNone(api_request({"api_url": "https://api.test/api/ingest", "api_key": "secret"},
                                      "/reparation-request/3", "DELETE"))
