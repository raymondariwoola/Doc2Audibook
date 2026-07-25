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
  - PiperProvider    — local offline neural TTS using downloaded voice models.
  - KokoroProvider   — local offline Kokoro-82M through ONNX Runtime.
  - AppleSayProvider — macOS's built-in offline `say` command.
  - OpenAIProvider   — OpenAI TTS, PAID (needs OPENAI_API_KEY). *Experimental:*
    implemented against the documented API but not yet verified end-to-end here.

Add a provider by subclassing `TTSProvider` and registering it in `_REGISTRY`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from media_tools import require_media_tool


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
    supports_word_marks: bool = False   # per-word timings for karaoke highlighting
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

    def cache_identity(self) -> str:
        """Identity of provider state that can change synthesized chunk audio."""
        return self.id

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

    async def synthesize_with_marks(
        self, text: str, voice_id: str, out_path: Path, *, rate: str, pitch: str
    ) -> Optional[list[dict]]:
        """Render `text` and, when the engine exposes them, return per-word timing
        marks as ``[{"text", "start", "end"}]`` (seconds into the rendered audio).

        Default: synthesize normally and return ``None`` (no marks). Providers that
        advertise ``supports_word_marks`` override this. UIs use the marks to
        highlight words in sync with playback; ``None`` means fall back to an
        estimate or no highlighting.
        """
        await self.synthesize(text, voice_id, out_path, rate=rate, pitch=pitch)
        return None


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
            supports_word_marks=True,
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
        await self._with_ssl_fallback(
            self._synthesize_once, text, voice_id, rate, pitch, out_path
        )

    async def _stream_once_with_marks(
        self, text: str, voice_id: str, rate: str, pitch: str, out_path: Path
    ) -> list[dict]:
        """Stream audio to `out_path` while collecting edge-tts WordBoundary marks.

        Offsets/durations arrive in 100-nanosecond units; convert to seconds.
        """
        import edge_tts

        # edge-tts 7.x defaults to sentence-level boundaries; request word-level
        # so the UI can highlight each word as it is spoken.
        communicate = edge_tts.Communicate(
            text, voice=voice_id, rate=rate, pitch=pitch, boundary="WordBoundary"
        )
        marks: list[dict] = []
        tmp = out_path.with_suffix(out_path.suffix + ".part")
        with open(tmp, "wb") as handle:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    handle.write(chunk["data"])
                elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                    start = chunk["offset"] / 1e7
                    marks.append({
                        "text": chunk["text"],
                        "start": round(start, 3),
                        "end": round(start + chunk["duration"] / 1e7, 3),
                    })
        if tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("edge-tts produced an empty audio file")
        tmp.replace(out_path)
        return marks

    async def synthesize_with_marks(
        self, text: str, voice_id: str, out_path: Path, *, rate: str, pitch: str
    ) -> Optional[list[dict]]:
        return await self._with_ssl_fallback(
            self._stream_once_with_marks, text, voice_id, rate, pitch, out_path
        )

    async def _with_ssl_fallback(self, fn, text, voice_id, rate, pitch, out_path):
        """Run an edge-tts coroutine, retrying once via the system trust store on a
        certificate-verification error (shared by synthesize and the marks path)."""
        global _ssl_fallback_logged
        try:
            return await fn(text, voice_id, rate, pitch, out_path)
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
                    return await fn(text, voice_id, rate, pitch, out_path)
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

    def cache_identity(self) -> str:
        return f"{self.id}:{self.model}"

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
# Shared helper: encode a WAV file to MP3 so every local provider produces the
# same chunk codec (keeps the resumable-chunk + ffmpeg stream-copy assembly
# uniform across providers).
# ----------------------------------------------------------------------------


def _transcode_wav_to_mp3(wav_path: Path, out_path: Path, bitrate: str = "64k") -> None:
    try:
        ffmpeg = require_media_tool("ffmpeg")
    except RuntimeError as exc:
        raise RuntimeError(
            "ffmpeg is required to encode local-provider audio to MP3."
        ) from exc
    tmp = out_path.with_suffix(out_path.suffix + ".part")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(wav_path), "-c:a", "libmp3lame", "-b:a", bitrate,
           "-f", "mp3", str(tmp)]   # .part extension needs an explicit format
    result = subprocess.run(cmd, capture_output=True, text=True)
    wav_path.unlink(missing_ok=True)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed encoding audio:\n{result.stderr.strip()}")
    if tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Local provider produced an empty audio file")
    tmp.replace(out_path)


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

    def cache_identity(self) -> str:
        files: list[tuple[str, int, int]] = []
        for model in self._models():
            for path in (model, model.with_suffix(model.suffix + ".json")):
                if path.exists():
                    stat = path.stat()
                    files.append((path.name, stat.st_size, stat.st_mtime_ns))
        return json.dumps([self.id, files], separators=(",", ":"))

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

        length_scale = _rate_str_to_length_scale(rate)

        def _run() -> None:
            from piper import SynthesisConfig

            voice = self._load_voice(voice_id)
            wav_tmp = out_path.with_suffix(".piper.wav")
            with wave.open(str(wav_tmp), "wb") as wav_file:
                voice.synthesize_wav(
                    text, wav_file, syn_config=SynthesisConfig(length_scale=length_scale)
                )
            _transcode_wav_to_mp3(wav_tmp, out_path)

        await asyncio.to_thread(_run)


# ----------------------------------------------------------------------------
# AppleSayProvider — free, fully offline, built into macOS (no packages, no
# downloads). macOS only; is_available() is False elsewhere.
# ----------------------------------------------------------------------------


def _rate_str_to_wpm(rate: str, base: int = 180) -> int:
    """Map an edge-style rate ('-4%') to an Apple `say` words-per-minute value."""
    try:
        pct = float(rate.strip().rstrip("%"))
    except ValueError:
        pct = 0.0
    return int(max(80, min(500, base * (1.0 + pct / 100.0))))


class AppleSayProvider(TTSProvider):
    id = "apple"
    label = "Apple say (macOS, offline)"

    def is_available(self) -> bool:
        return sys.platform == "darwin" and bool(shutil.which("say"))

    def capabilities(self) -> Capabilities:
        return Capabilities(
            provider_id=self.id,
            needs_api_key=False,
            supports_rate=True,           # via `say -r` (words per minute)
            supports_pitch=False,
            supports_voice_list=True,
            supports_cloning=False,
            output_sample_rate_hz=22050,
            notes="Built into macOS, fully offline, no downloads. macOS only.",
        )

    async def list_voices(self) -> list[VoiceInfo]:
        if not self.is_available():
            return []
        proc = await asyncio.to_thread(
            subprocess.run, ["say", "-v", "?"], capture_output=True, text=True
        )
        voices: list[VoiceInfo] = []
        # Lines look like: "Bad News            en_US    # Hello! ..."
        pattern = re.compile(r"^(.+?)\s{2,}([a-z]{2}_[A-Z]{2})\b")
        for line in proc.stdout.splitlines():
            m = pattern.match(line)
            if not m:
                continue
            name, locale = m.group(1).strip(), m.group(2)
            voices.append(VoiceInfo(
                id=name, label=name, gender="", locale=locale, provider=self.id,
                raw={"ShortName": name, "Locale": locale, "Gender": ""},
            ))
        return voices

    async def synthesize(
        self, text: str, voice_id: str, out_path: Path, *, rate: str, pitch: str
    ) -> None:
        if not self.is_available():
            raise RuntimeError("Apple `say` is only available on macOS.")
        wpm = _rate_str_to_wpm(rate)

        def _run() -> None:
            # Feed text via a temp file to avoid shell-quoting / arg-length issues.
            txt = out_path.with_suffix(".say.txt")
            txt.write_text(text, encoding="utf-8")
            aiff = out_path.with_suffix(".say.aiff")
            cmd = ["say", "-v", voice_id, "-r", str(wpm), "-o", str(aiff), "-f", str(txt)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            txt.unlink(missing_ok=True)
            if result.returncode != 0:
                aiff.unlink(missing_ok=True)
                raise RuntimeError(f"`say` failed:\n{result.stderr.strip()}")
            _transcode_wav_to_mp3(aiff, out_path)   # ffmpeg reads AIFF fine

        await asyncio.to_thread(_run)


# ----------------------------------------------------------------------------
# KokoroProvider — free, fully offline, cross-platform neural TTS (Kokoro-82M
# via onnxruntime). Needs the `kokoro-onnx` package plus two model files:
#   kokoro-v1.0.onnx  and  voices-v1.0.bin
# in the model directory (default 'kokoro_models', override with KOKORO_MODEL_DIR).
# Download once from:
#   https://github.com/thewh1teagle/kokoro-onnx/releases
# ----------------------------------------------------------------------------

KOKORO_MODEL_DIR = Path(os.environ.get("KOKORO_MODEL_DIR", "kokoro_models"))
_KOKORO_MODEL = "kokoro-v1.0.onnx"
_KOKORO_VOICES = "voices-v1.0.bin"

# Kokoro voice names start with a language letter + a gender letter (e.g.
# "af_heart" = American-English female). Map the leading letter to lang/locale.
_KOKORO_LANG = {
    "a": ("en-us", "en-US"), "b": ("en-gb", "en-GB"), "e": ("es", "es-ES"),
    "f": ("fr-fr", "fr-FR"), "h": ("hi", "hi-IN"), "i": ("it", "it-IT"),
    "j": ("ja", "ja-JP"), "p": ("pt-br", "pt-BR"), "z": ("cmn", "zh-CN"),
}


def _rate_str_to_speed_kokoro(rate: str) -> float:
    try:
        pct = float(rate.strip().rstrip("%"))
    except ValueError:
        pct = 0.0
    return max(0.5, min(2.0, 1.0 + pct / 100.0))


class KokoroProvider(TTSProvider):
    id = "kokoro"
    label = "Kokoro (local, offline)"

    def __init__(self, model_dir: Optional[Path] = None) -> None:
        self.model_dir = Path(model_dir) if model_dir else KOKORO_MODEL_DIR
        self._kokoro = None   # cached Kokoro instance (model load is expensive)

    def _has_pkg(self) -> bool:
        try:
            import kokoro_onnx  # noqa: F401
            import soundfile    # noqa: F401
            return True
        except Exception:
            return False

    def _paths(self) -> tuple[Path, Path]:
        return self.model_dir / _KOKORO_MODEL, self.model_dir / _KOKORO_VOICES

    def is_available(self) -> bool:
        model, voices = self._paths()
        return self._has_pkg() and model.exists() and voices.exists()

    def cache_identity(self) -> str:
        files: list[tuple[str, int, int]] = []
        for path in self._paths():
            if path.exists():
                stat = path.stat()
                files.append((path.name, stat.st_size, stat.st_mtime_ns))
        return json.dumps([self.id, files], separators=(",", ":"))

    def capabilities(self) -> Capabilities:
        return Capabilities(
            provider_id=self.id,
            needs_api_key=False,
            supports_rate=True,           # via `speed`
            supports_pitch=False,
            supports_voice_list=True,
            supports_cloning=False,
            output_sample_rate_hz=24000,
            notes="Fully offline after a one-time ~340 MB model download. Rate "
                  f"maps to speed; pitch unsupported. Models in '{self.model_dir}' "
                  "(or set KOKORO_MODEL_DIR).",
        )

    def _engine(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro
            model, voices = self._paths()
            self._kokoro = Kokoro(str(model), str(voices))
        return self._kokoro

    async def list_voices(self) -> list[VoiceInfo]:
        if not self.is_available():
            return []
        names = await asyncio.to_thread(lambda: sorted(self._engine().get_voices()))
        voices: list[VoiceInfo] = []
        for name in names:
            _lang, locale = _KOKORO_LANG.get(name[:1], ("en-us", ""))
            voices.append(VoiceInfo(
                id=name, label=name, gender="", locale=locale, provider=self.id,
                raw={"ShortName": name, "Locale": locale, "Gender": ""},
            ))
        return voices

    async def synthesize(
        self, text: str, voice_id: str, out_path: Path, *, rate: str, pitch: str
    ) -> None:
        if not self._has_pkg():
            raise RuntimeError("The `kokoro-onnx` (and `soundfile`) packages are not "
                               "installed. `pip install kokoro-onnx soundfile`.")
        model, voices = self._paths()
        if not (model.exists() and voices.exists()):
            raise RuntimeError(
                f"Kokoro model files not found in '{self.model_dir}'. Download "
                f"'{_KOKORO_MODEL}' and '{_KOKORO_VOICES}' from "
                "https://github.com/thewh1teagle/kokoro-onnx/releases")
        speed = _rate_str_to_speed_kokoro(rate)
        lang = _KOKORO_LANG.get(voice_id[:1], ("en-us", ""))[0]

        def _run() -> None:
            import soundfile as sf

            samples, sample_rate = self._engine().create(
                text, voice=voice_id, speed=speed, lang=lang)
            wav_tmp = out_path.with_suffix(".kokoro.wav")
            sf.write(str(wav_tmp), samples, sample_rate)
            _transcode_wav_to_mp3(wav_tmp, out_path)

        await asyncio.to_thread(_run)


# ----------------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------------

_REGISTRY: dict[str, type[TTSProvider]] = {
    EdgeTTSProvider.id: EdgeTTSProvider,
    PiperProvider.id: PiperProvider,
    KokoroProvider.id: KokoroProvider,
    AppleSayProvider.id: AppleSayProvider,
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
