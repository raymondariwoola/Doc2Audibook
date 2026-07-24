#!/usr/bin/env python3
"""
Download and SHA-256 verify the offline voice models in `voices.manifest.json`.

Model files are too large for git, so they are gitignored and fetched per machine
by this script. It is idempotent: existing files are skipped only after their
recorded digest matches, so it also detects partial or altered model files.

Run inside the project venv (so `certifi` is available):
    python sync_voices.py
or via the all-in-one setup:
    python bootstrap.py

Uses certifi's CA bundle explicitly, which fixes the python.org macOS build where
urllib otherwise fails TLS verification ("unable to get local issuer certificate").
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import ssl
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "voices.manifest.json"


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        # Fall back to the system trust store (works when certifi is absent).
        return ssl.create_default_context()


CTX = _ssl_context()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, dst: Path, expected_sha256: str) -> str:
    if dst.exists() and dst.stat().st_size > 0 and _sha256(dst) == expected_sha256:
        return "skip"
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "doc2audiobook-sync"})
    with urllib.request.urlopen(req, context=CTX) as resp, open(tmp, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    actual = _sha256(tmp)
    if actual != expected_sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"SHA-256 mismatch for {dst.name}: expected {expected_sha256}, got {actual}"
        )
    tmp.replace(dst)
    return "downloaded"


def _checksums(manifest: dict) -> dict[str, str]:
    checksums = manifest.get("sha256")
    if not isinstance(checksums, dict):
        raise ValueError("voices.manifest.json must contain a 'sha256' object")
    for name, digest in checksums.items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError(f"Invalid SHA-256 for '{name}'")
    return {str(name): str(digest).lower() for name, digest in checksums.items()}


def _piper_file_urls(hf_base: str, name: str) -> dict[str, str]:
    """Build the HuggingFace URLs for a Piper voice from its name.

    'en_US-lessac-medium' -> region 'en_US', name 'lessac', quality 'medium',
    lang 'en' -> {hf_base}/en/en_US/lessac/medium/en_US-lessac-medium.{onnx,onnx.json}
    """
    parts = name.split("-")
    if len(parts) < 3:
        raise ValueError(f"Unexpected Piper voice name '{name}' (want lang_REGION-name-quality)")
    region, vname, quality = parts[0], parts[1], parts[2]
    lang = region.split("_")[0]
    base = f"{hf_base}/{lang}/{region}/{vname}/{quality}/{name}"
    return {f"{name}.onnx": f"{base}.onnx", f"{name}.onnx.json": f"{base}.onnx.json"}


def sync(manifest_path: Path = MANIFEST) -> int:
    if not manifest_path.exists():
        print(f"error: {manifest_path} not found", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        checksums = _checksums(manifest)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    total_downloaded = total_skipped = 0

    # ---- Piper voices ----
    piper = manifest.get("piper")
    if piper and piper.get("voices"):
        voice_dir = ROOT / piper.get("voice_dir", "piper_voices")
        hf_base = piper["hf_base"].rstrip("/")
        print(f"Piper voices -> {voice_dir}")
        for name in piper["voices"]:
            for fname, url in _piper_file_urls(hf_base, name).items():
                if fname not in checksums:
                    print(f"error: no SHA-256 recorded for {fname}", file=sys.stderr)
                    return 1
                try:
                    status = _download(url, voice_dir / fname, checksums[fname])
                except Exception as exc:  # noqa: BLE001
                    print(f"  [error] {fname}: {exc}", file=sys.stderr)
                    return 2
                print(f"  {'-' if status == 'skip' else 'download'} {fname} ({status})")
                total_downloaded += status == "downloaded"
                total_skipped += status == "skip"

    # ---- Kokoro model files ----
    kokoro = manifest.get("kokoro")
    if kokoro and kokoro.get("files"):
        model_dir = ROOT / kokoro.get("model_dir", "kokoro_models")
        release_base = kokoro["release_base"].rstrip("/")
        print(f"Kokoro model -> {model_dir}")
        for fname in kokoro["files"]:
            url = f"{release_base}/{fname}"
            if fname not in checksums:
                print(f"error: no SHA-256 recorded for {fname}", file=sys.stderr)
                return 1
            try:
                status = _download(url, model_dir / fname, checksums[fname])
            except Exception as exc:  # noqa: BLE001
                print(f"  [error] {fname}: {exc}", file=sys.stderr)
                return 2
            print(f"  {'-' if status == 'skip' else 'download'} {fname} ({status})")
            total_downloaded += status == "downloaded"
            total_skipped += status == "skip"

    print(f"Done. {total_downloaded} downloaded, {total_skipped} already present.")
    return 0


if __name__ == "__main__":
    sys.exit(sync())
