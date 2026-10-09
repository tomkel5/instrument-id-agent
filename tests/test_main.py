import json
import unittest
from src.main import _codex_usage, candidate, prompt

class MainTests(unittest.TestCase):
    def test_candidate_deduplicates_images(self):
        value = candidate('DIRECT_IMPORT_JSON: ' + json.dumps({"uri":"https://example.test/v","title":"V","description":"D","imageUrls":["https://example.test/1.jpg","https://example.test/1.jpg","https://example.test/2.jpg"]}))
        self.assertEqual(value["imageUrls"], ["https://example.test/1.jpg", "https://example.test/2.jpg"])

    def test_prompt_requires_complete_gallery(self):
        value = prompt({"sources": [], "runs": []})
        self.assertIn("complete image gallery", value)
        self.assertIn("every unique image", value)

    def test_codex_usage_reads_completed_turn(self):
        self.assertEqual(
            _codex_usage([{"type": "turn.completed", "usage": {
                "input_tokens": 1200,
                "cached_input_tokens": 900,
                "output_tokens": 80,
            }}]),
            {
                "input_tokens": 1200,
                "cached_input_tokens": 900,
                "output_tokens": 80,
                "total_tokens": 1280,
            },
        )

    def test_prompt_compacts_old_run_payloads(self):
        state = {
            "sources": ["https://example.test/seen"],
            "runs": [{"completedAt": "now", "result": "imported", "candidate": {
                "uri": "https://example.test/old",
                "description": "This large description should remain on disk only." * 100,
            }}],
        }
        value = prompt(state)
        self.assertIn("https://example.test/seen", value)
        self.assertNotIn("This large description should remain on disk only", value)
        self.assertIn("full state remains on disk", value)

if __name__ == "__main__":
    unittest.main()
