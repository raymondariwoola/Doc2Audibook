#!/usr/bin/env python3
"""
TTS provider abstraction (Phase 2 of ROADMAP.md).

A `TTSProvider` hides the differences between speech engines behind one small
interface so the engine (`narrate.py`) and every UI stay identical no matter
which voice service is used:

    list_voices()   -> what voices exist
    synthesize()    -> render one utterance to a file
    capabilities()  -> what this provider supports (rate? pitch? needs a key?)
    is_available()  -> can we actually use it right now?

Implementations:
  - EdgeTTSProvider  — Microsoft edge-tts, FREE, no API key. The default and the
    reference implementation. Owns the corporate-proxy SSL fallback.
  - OpenAIProvider   — OpenAI TTS, PAID (needs OPENAI_API_KEY). *Experimental:*
    implemented against the documented API but not yet verified end-to-end here.

Add a provider by subclassing `TTSProvider` and registering it in `_REGISTRY`.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import ssl
import subprocess
import sys
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ----------------------------------------------------------------------------
# Data types shared across providers
# ----------------------------------------------------------------------------


@dataclass
class VoiceInfo:
    """A single selectable voice, normalized across providers."""
    id: str                       # what synthesize() expects (e.g. "en-US-AndrewNeural")
    label: str                    # human-friendly display name
    gender: str = ""
    locale: str = ""
    provider: str = ""
    raw: Optional[dict] = None    # original provider payload (edge-tts dict, etc.)


@dataclass
class Capabilities:
    """What a provider can do — lets a UI render the right controls."""
    provider_id: str
    needs_api_key: bool = False
    supports_rate: bool = True
    supports_pitch: bool = True
    supports_voice_list: bool = True
    supports_cloning: bool = False
    output_sample_rate_hz: Optional[int] = None
    notes: str = ""


# ----------------------------------------------------------------------------
# SSL fallback — shared by any HTTPS-based provider. Some corporate/proxy
# environments intercept TLS with a self-signed cert trusted by the OS but not
# in certifi's bundle; edge-tts hardcodes certifi, so we fall back to the system
# trust store on a certificate-verification error.
# ----------------------------------------------------------------------------

_ssl_fallback_logged = False


def is_ssl_cert_error(exc: BaseException) -> bool:
    seen = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        msg = str(current).lower()
        if "self-signed certificate" in msg or "certificate verify failed" in msg:
            return True
        current = current.__cause__ or current.__context__
    return False


def apply_ssl_fallback() -> bool:
    """Swap edge-tts's certifi-backed SSL context for one from the system trust
    store. Returns True if any context was patched."""
    system_ctx = ssl.create_default_context()
    patched = False
    for module_name in ("edge_tts.communicate", "edge_tts.voices"):
        try:
            module = sys.modules.get(module_name) or __import__(module_name, fromlist=["*"])
        except Exception:
            continue
        if hasattr(module, "_SSL_CTX"):
            module._SSL_CTX = system_ctx
            patched = True
    return patched


# ----------------------------------------------------------------------------
# Provider interface
# ----------------------------------------------------------------------------


class TTSProvider(ABC):
    id: str = ""
    label: str = ""

    def is_available(self) -> bool:
        """Whether the provider can be used right now (package present, key set)."""
        return True

    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abstractmethod
    async def list_voices(self) -> list[VoiceInfo]: ...

    @abstractmethod
    async def synthesize(
        self, text: str, voice_id: str, out_path: Path, *, rate: str, pitch: str
    ) -> None:
        """Render `text` to `out_path` (MP3). `rate`/`pitch` use edge-tts string
        conventions ('-4%', '+5Hz'); providers map them as best they can."""


# ----------------------------------------------------------------------------
# EdgeTTSProvider — free, default, reference implementation
# ----------------------------------------------------------------------------


class EdgeTTSProvider(TTSProvider):
    id = "edge"
    label = "Microsoft Edge TTS (free)"

    def capabilities(self) -> Capabilities:
        return Capabilities(
            provider_id=self.id,
            needs_api_key=False,
            supports_rate=True,
            supports_pitch=True,
            supports_voice_list=True,
            supports_cloning=False,
            output_sample_rate_hz=24000,
            notes="Free, no key. Output is 24 kHz mono ~48 kbps MP3.",
        )

    async def list_voices(self) -> list[VoiceInfo]:
        import edge_tts

        try:
            rows = await edge_tts.list_voices()
        except Exception as exc:  # noqa: BLE001
            if is_ssl_cert_error(exc) and apply_ssl_fallback():
                print("  ! SSL fallback engaged for voice listing.", file=sys.stderr)
                rows = await edge_tts.list_voices()
            else:
                raise
        return [
            VoiceInfo(
                id=v.get("ShortName", ""),
                label=v.get("ShortName", ""),
                gender=v.get("Gender", ""),
                locale=v.get("Locale", ""),
                provider=self.id,
                raw=v,
            )
            for v in rows
        ]

    async def _synthesize_once(
        self, text: str, voice_id: str, rate: str, pitch: str, out_path: Path
    ) -> None:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice=voice_id, rate=rate, pitch=pitch)
        tmp = out_path.with_suffix(out_path.suffix + ".part")
        await communicate.save(str(tmp))
        if tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("edge-tts produced an empty audio file")
        tmp.replace(out_path)

    async def synthesize(
        self, text: str, voice_id: str, out_path: Path, *, rate: str, pitch: str
    ) -> None:
        global _ssl_fallback_logged
        try:
            await self._synthesize_once(text, voice_id, rate, pitch, out_path)
        except Exception as exc:  # noqa: BLE001
            if is_ssl_cert_error(exc):
                if not _ssl_fallback_logged:
                    print(
                        "  ! SSL certificate verification failed against certifi's bundle.\n"
                        "  ! Falling back to the system trust store "
                        "(edge_tts._SSL_CTX -> ssl.create_default_context()).",
                        file=sys.stderr,
                    )
                    _ssl_fallback_logged = True
                if apply_ssl_fallback():
                    await self._synthesize_once(text, voice_id, rate, pitch, out_path)
                    return
            raise


# ----------------------------------------------------------------------------
# OpenAIProvider — paid, key-gated. EXPERIMENTAL: coded to the documented API,
# not yet verified end-to-end in this project (needs a real OPENAI_API_KEY).
# ----------------------------------------------------------------------------

# https://platform.openai.com/docs/guides/text-to-speech
_OPENAI_VOICES = [
    "alloy", "ash", "ballad", "coral", "echo",
    "fable", "nova", "onyx", "sage", "shimmer", "verse",
]


def _rate_str_to_speed(rate: str) -> float:
    """Map an edge-style rate ('-4%') to an OpenAI speed multiplier (0.25–4.0)."""
    try:
        pct = float(rate.strip().rstrip("%"))
    except ValueError:
        pct = 0.0
    return max(0.25, min(4.0, 1.0 + pct / 100.0))


class OpenAIProvider(TTSProvider):
    id = "openai"
    label = "OpenAI TTS (paid, experimental)"

    def __init__(self) -> None:
        # Overridable via env: OPENAI_TTS_MODEL (e.g. "tts-1", "gpt-4o-mini-tts").
        self.model = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")

    def _has_sdk(self) -> bool:
        try:
            import openai  # noqa: F401
            return True
        except Exception:
            return False

    def is_available(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY")) and self._has_sdk()

    def capabilities(self) -> Capabilities:
        return Capabilities(
            provider_id=self.id,
            needs_api_key=True,
            supports_rate=True,           # via `speed` multiplier
            supports_pitch=False,         # OpenAI TTS has no pitch control
            supports_voice_list=True,     # a fixed catalog
            supports_cloning=False,
            output_sample_rate_hz=24000,
            notes="Needs OPENAI_API_KEY. Rate maps to `speed`; pitch unsupported. "
                  "Experimental — not yet verified end-to-end here.",
        )

    async def list_voices(self) -> list[VoiceInfo]:
        return [
            VoiceInfo(id=name, label=name.capitalize(), gender="", locale="en-US",
                      provider=self.id, raw={"ShortName": name, "Locale": "en-US"})
            for name in _OPENAI_VOICES
        ]

    async def synthesize(
        self, text: str, voice_id: str, out_path: Path, *, rate: str, pitch: str
    ) -> None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set — cannot use the OpenAI provider.")
        if not self._has_sdk():
            raise RuntimeError("The `openai` package is not installed. `pip install openai`.")

        speed = _rate_str_to_speed(rate)

        def _run() -> None:
            from openai import OpenAI

            client = OpenAI()
            tmp = out_path.with_suffix(out_path.suffix + ".part")
            with client.audio.speech.with_streaming_response.create(
                model=self.model,
                voice=voice_id,
                input=text,
                response_format="mp3",
                speed=speed,
            ) as response:
                response.stream_to_file(str(tmp))
            if tmp.stat().st_size == 0:
                tmp.unlink(missing_ok=True)
                raise RuntimeError("OpenAI TTS produced an empty audio file")
            tmp.replace(out_path)

        # The OpenAI SDK call is synchronous; keep the event loop responsive.
        await asyncio.to_thread(_run)


# ----------------------------------------------------------------------------
# PiperProvider — free, fully offline, cross-platform neural TTS.
# Needs the `piper-tts` package and at least one downloaded voice model
# (an .onnx + .onnx.json pair) in the voice directory. No network at runtime.
# ----------------------------------------------------------------------------

# Where Piper looks for downloaded voice models. Override with PIPER_VOICE_DIR.
#   python -m piper.download_voices en_US-lessac-medium --download-dir piper_voices
PIPER_VOICE_DIR = Path(os.environ.get("PIPER_VOICE_DIR", "piper_voices"))


def _rate_str_to_length_scale(rate: str) -> float:
    """Map an edge-style rate ('-4%') to a Piper length_scale (>1 = slower)."""
    try:
        pct = float(rate.strip().rstrip("%"))
    except ValueError:
        pct = 0.0
    return max(0.25, min(4.0, 1.0 - pct / 100.0))


class PiperProvider(TTSProvider):
    id = "piper"
    label = "Piper (local, offline)"

    def __init__(self, voice_dir: Optional[Path] = None) -> None:
        self.voice_dir = Path(voice_dir) if voice_dir else PIPER_VOICE_DIR
        self._loaded: dict[str, object] = {}   # model path -> PiperVoice (cached)

    def _has_pkg(self) -> bool:
        try:
            import piper  # noqa: F401
            return True
        except Exception:
            return False

    def _models(self) -> list[Path]:
        if not self.voice_dir.exists():
            return []
        return sorted(self.voice_dir.glob("*.onnx"))

    def is_available(self) -> bool:
        return self._has_pkg() and bool(self._models())

    def capabilities(self) -> Capabilities:
        return Capabilities(
            provider_id=self.id,
            needs_api_key=False,
            supports_rate=True,           # via length_scale
            supports_pitch=False,         # Piper has no pitch control
            supports_voice_list=True,     # scanned from the model directory
            supports_cloning=False,
            output_sample_rate_hz=None,   # depends on the chosen model
            notes="Fully offline after a one-time model download. Rate maps to "
                  "length_scale; pitch unsupported. Put .onnx models in "
                  f"'{self.voice_dir}' (or set PIPER_VOICE_DIR).",
        )

    async def list_voices(self) -> list[VoiceInfo]:
        voices: list[VoiceInfo] = []
        for model in self._models():
            locale = ""
            config = model.with_suffix(model.suffix + ".json")
            if config.exists():
                try:
                    data = json.loads(config.read_text(encoding="utf-8"))
                    locale = data.get("language", {}).get("code", "") or ""
                except Exception:
                    pass
            vid = model.stem  # e.g. "en_US-lessac-low"
            voices.append(VoiceInfo(
                id=vid, label=vid, gender="", locale=locale, provider=self.id,
                raw={"ShortName": vid, "Locale": locale, "Gender": ""},
            ))
        return voices

    def _load_voice(self, voice_id: str):
        from piper import PiperVoice

        model = self.voice_dir / f"{voice_id}.onnx"
        if not model.exists():
            raise RuntimeError(
                f"Piper model '{model}' not found. Download one, e.g.:\n"
                f"  python -m piper.download_voices en_US-lessac-medium "
                f"--download-dir {self.voice_dir}"
            )
        key = str(model)
        if key not in self._loaded:
            self._loaded[key] = PiperVoice.load(str(model))
        return self._loaded[key]

    async def synthesize(
        self, text: str, voice_id: str, out_path: Path, *, rate: str, pitch: str
    ) -> None:
        if not self._has_pkg():
            raise RuntimeError("The `piper-tts` package is not installed. "
                               "`pip install piper-tts`.")
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg is required to encode Piper audio to MP3.")

        length_scale = _rate_str_to_length_scale(rate)

        def _run() -> None:
            from piper import SynthesisConfig

            voice = self._load_voice(voice_id)
            wav_tmp = out_path.with_suffix(".piper.wav")
            with wave.open(str(wav_tmp), "wb") as wav_file:
                voice.synthesize_wav(
                    text, wav_file, syn_config=SynthesisConfig(length_scale=length_scale)
                )
            # Transcode to MP3 so chunks share a codec for ffmpeg stream-copy.
            tmp = out_path.with_suffix(out_path.suffix + ".part")
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                   "-i", str(wav_tmp), "-c:a", "libmp3lame", "-b:a", "64k",
                   "-f", "mp3", str(tmp)]   # .part extension needs explicit format
            result = subprocess.run(cmd, capture_output=True, text=True)
            wav_tmp.unlink(missing_ok=True)
            if result.returncode != 0:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"ffmpeg failed encoding Piper audio:\n{result.stderr.strip()}")
            if tmp.stat().st_size == 0:
                tmp.unlink(missing_ok=True)
                raise RuntimeError("Piper produced an empty audio file")
            tmp.replace(out_path)

        await asyncio.to_thread(_run)


# ----------------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------------

_REGISTRY: dict[str, type[TTSProvider]] = {
    EdgeTTSProvider.id: EdgeTTSProvider,
    PiperProvider.id: PiperProvider,
    OpenAIProvider.id: OpenAIProvider,
}
_INSTANCES: dict[str, TTSProvider] = {}

DEFAULT_PROVIDER_ID = "edge"


def get_provider(provider_id: str = DEFAULT_PROVIDER_ID) -> TTSProvider:
    if provider_id not in _REGISTRY:
        raise ValueError(f"Unknown provider '{provider_id}'. "
                         f"Available: {', '.join(_REGISTRY)}")
    if provider_id not in _INSTANCES:
        _INSTANCES[provider_id] = _REGISTRY[provider_id]()
    return _INSTANCES[provider_id]


def default_provider() -> TTSProvider:
    return get_provider(DEFAULT_PROVIDER_ID)


def all_provider_ids() -> list[str]:
    return list(_REGISTRY)


def available_providers() -> list[TTSProvider]:
    """Providers usable right now (package present + key set)."""
    return [get_provider(pid) for pid in _REGISTRY if get_provider(pid).is_available()]
