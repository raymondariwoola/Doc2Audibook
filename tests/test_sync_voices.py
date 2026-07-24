import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sync_voices


class DownloadIntegrityTests(unittest.TestCase):
    def test_valid_existing_file_is_skipped(self):
        payload = b"voice model"
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.bin"
            path.write_bytes(payload)
            with mock.patch.object(
                sync_voices.urllib.request, "urlopen"
            ) as urlopen:
                self.assertEqual(
                    sync_voices._download("https://example.test/model", path, expected),
                    "skip",
                )
            urlopen.assert_not_called()

    def test_bad_download_is_rejected_without_replacing_existing_file(self):
        expected = hashlib.sha256(b"expected").hexdigest()
        response = io.BytesIO(b"wrong")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.bin"
            path.write_bytes(b"old")
            with mock.patch.object(
                sync_voices.urllib.request, "urlopen", return_value=response
            ):
                with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                    sync_voices._download(
                        "https://example.test/model", path, expected
                    )
            self.assertEqual(path.read_bytes(), b"old")
            self.assertFalse(path.with_suffix(".bin.part").exists())


if __name__ == "__main__":
    unittest.main()
