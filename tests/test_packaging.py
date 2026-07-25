"""Packaging tests for the wheel frontend bundle (risk R-012).

The setup.py build hook copies the built React client (web/dist) into the
web_api package so an installed wheel's `narrate-web` serves the UI, and
web_api.main prefers that bundled copy over the source-tree build.
"""

import tempfile
import unittest
from pathlib import Path

import setup as build_setup
from web_api.main import REPO_ROOT, _resolve_frontend_dist


class FrontendBundlingTests(unittest.TestCase):
    def test_bundle_frontend_copies_built_assets_and_replaces_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "dist"
            (source / "assets").mkdir(parents=True)
            (source / "index.html").write_text("<html></html>", encoding="utf-8")
            (source / "assets" / "app.js").write_text("//", encoding="utf-8")
            target = Path(tmp) / "pkg" / "frontend"

            self.assertTrue(build_setup.bundle_frontend(source, target))
            self.assertTrue((target / "index.html").is_file())
            self.assertTrue((target / "assets" / "app.js").is_file())

            # Re-bundling wipes stale assets rather than merging over them.
            (source / "assets" / "app.js").unlink()
            (source / "assets" / "new.js").write_text("//", encoding="utf-8")
            self.assertTrue(build_setup.bundle_frontend(source, target))
            self.assertFalse((target / "assets" / "app.js").is_file())
            self.assertTrue((target / "assets" / "new.js").is_file())

    def test_bundle_frontend_skips_when_frontend_not_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "dist"
            source.mkdir()  # no index.html — not a real build
            target = Path(tmp) / "frontend"
            self.assertFalse(build_setup.bundle_frontend(source, target))
            self.assertFalse(target.exists())

    def test_resolve_prefers_bundled_over_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            source = Path(tmp) / "source"
            bundled.mkdir()
            source.mkdir()

            # Without a bundled build, fall back to the source-tree dist.
            self.assertEqual(_resolve_frontend_dist(bundled, source), source)

            # A bundled build wins.
            (bundled / "index.html").write_text("x", encoding="utf-8")
            self.assertEqual(_resolve_frontend_dist(bundled, source), bundled)

    def test_pyproject_declares_frontend_package_data(self):
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("frontend/**/*", text)


if __name__ == "__main__":
    unittest.main()
