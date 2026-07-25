import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import media_tools


class MediaToolResolutionTests(unittest.TestCase):
    def test_explicit_absolute_tool_wins_over_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            executable.write_bytes(b"tool")
            with (
                mock.patch.dict(
                    os.environ,
                    {"DOC2AUDIOBOOK_FFMPEG": str(executable.resolve())},
                    clear=False,
                ),
                mock.patch("media_tools.shutil.which", return_value="path-ffmpeg"),
            ):
                self.assertEqual(
                    media_tools.media_tool("ffmpeg"), str(executable.resolve())
                )

    def test_invalid_explicit_tool_does_not_fall_back(self):
        missing = Path("missing") / "ffmpeg"
        with (
            mock.patch.dict(
                os.environ,
                {"DOC2AUDIOBOOK_FFMPEG": str(missing.resolve())},
                clear=False,
            ),
            mock.patch("media_tools.shutil.which", return_value="path-ffmpeg"),
        ):
            with self.assertRaisesRegex(RuntimeError, "does not point to a file"):
                media_tools.media_tool("ffmpeg")

    def test_relative_explicit_tool_is_rejected(self):
        with mock.patch.dict(
            os.environ, {"DOC2AUDIOBOOK_FFPROBE": "tools/ffprobe"}, clear=False
        ):
            with self.assertRaisesRegex(RuntimeError, "must be an absolute path"):
                media_tools.media_tool("ffprobe")

    def test_path_fallback_and_missing_required_tool(self):
        with (
            mock.patch.dict(
                os.environ,
                {
                    "DOC2AUDIOBOOK_FFMPEG": "",
                    "DOC2AUDIOBOOK_FFPROBE": "",
                },
                clear=False,
            ),
            mock.patch("media_tools.shutil.which", side_effect=lambda name: name),
        ):
            self.assertEqual(media_tools.require_media_tool("ffmpeg"), "ffmpeg")

        with (
            mock.patch.dict(
                os.environ,
                {
                    "DOC2AUDIOBOOK_FFMPEG": "",
                    "DOC2AUDIOBOOK_FFPROBE": "",
                },
                clear=False,
            ),
            mock.patch("media_tools.shutil.which", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "DOC2AUDIOBOOK_FFMPEG"):
                media_tools.require_media_tool("ffmpeg")

    def test_unknown_tool_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported media tool"):
            media_tools.media_tool("sox")


if __name__ == "__main__":
    unittest.main()
