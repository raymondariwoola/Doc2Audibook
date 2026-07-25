import tempfile
import unittest
from pathlib import Path

import app


class AppPathTests(unittest.TestCase):
    def test_modern_ui_has_branded_responsive_styles(self):
        self.assertIn(".app-hero", app.APP_CSS)
        self.assertIn(".surface-card", app.APP_CSS)
        self.assertIn(".result-card", app.APP_CSS)
        self.assertIn("@media (max-width: 600px)", app.APP_CSS)

    def test_rules_change_render_digest_and_cache_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "same.txt"
            path.write_text("Document", encoding="utf-8")
            first = app._render_digest(
                path, "edge", "voice", "-4%", "+0Hz", 3000, [("A", "B")]
            )
            second = app._render_digest(
                path, "edge", "voice", "-4%", "+0Hz", 3000, [("A", "C")]
            )
            self.assertNotEqual(first, second)

    def test_outputs_with_same_stem_use_distinct_render_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_doc = Path(tmp) / "one" / "report.txt"
            second_doc = Path(tmp) / "two" / "report.txt"
            first_doc.parent.mkdir()
            second_doc.parent.mkdir()
            first_doc.write_text("First document", encoding="utf-8")
            second_doc.write_text("Second document", encoding="utf-8")
            args = ("edge", "voice", "-4%", "+0Hz", 3000, [])
            first_digest = app._render_digest(first_doc, *args)
            second_digest = app._render_digest(second_doc, *args)
            first = app._output_path_for(first_doc, "mp3", first_digest)
            second = app._output_path_for(second_doc, "mp3", second_digest)
            self.assertNotEqual(first, second)
            self.assertTrue(first.name.startswith("report-"))


if __name__ == "__main__":
    unittest.main()
