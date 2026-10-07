import json
import unittest
from src.main import candidate, prompt

class MainTests(unittest.TestCase):
    def test_candidate_deduplicates_images(self):
        value = candidate('DIRECT_IMPORT_JSON: ' + json.dumps({"uri":"https://example.test/v","title":"V","description":"D","imageUrls":["https://example.test/1.jpg","https://example.test/1.jpg","https://example.test/2.jpg"]}))
        self.assertEqual(value["imageUrls"], ["https://example.test/1.jpg", "https://example.test/2.jpg"])

    def test_prompt_requires_complete_gallery(self):
        value = prompt({"sources": [], "runs": []})
        self.assertIn("complete image gallery", value)
        self.assertIn("every unique image", value)

if __name__ == "__main__":
    unittest.main()
