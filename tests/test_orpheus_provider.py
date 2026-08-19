"""Tests for the self-hosted Orpheus provider (Phase 1).

These are offline: every HTTP call is mocked. Live verification against a real
orpheus-tts container is a separate, manual step — see docs/providers/ORPHEUS.md.
"""

import asyncio
import contextlib
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import narrate
import providers


class _FakeResponse:
    """Minimal stand-in for the object urlopen() yields as a context manager."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@contextlib.contextmanager
def _provider(base_url="http://localhost:8013", **env):
    """A fresh provider, with the ORPHEUS_* environment applied for the whole
    block. The environment must stay patched across the calls and not merely
    during construction: the base URL is read live on every call, so that
    setting it later still takes effect on the memoized instance that
    `get_provider` hands out.
    """
    patch = dict(env)
    patch["ORPHEUS_BASE_URL"] = "" if base_url is None else base_url
    with mock.patch.dict(os.environ, patch, clear=False):
        yield providers.OrpheusProvider()


class AvailabilityGateTests(unittest.TestCase):
    """The gate is what keeps Orpheus off machines that do not have it."""

    def test_unset_base_url_means_unavailable(self):
        with _provider(None) as p:
            self.assertFalse(p.is_available())

    def test_missing_key_entirely_means_unavailable(self):
        env = {k: v for k, v in os.environ.items() if k != "ORPHEUS_BASE_URL"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(providers.OrpheusProvider().is_available())

    def test_non_http_value_is_rejected(self):
        for junk in ("localhost:8013", "yes", "/var/run/orpheus.sock"):
            with self.subTest(junk=junk), _provider(junk) as p:
                self.assertFalse(p.is_available())

    def test_http_and_https_are_accepted(self):
        with _provider("http://localhost:8013") as p:
            self.assertTrue(p.is_available())
        with _provider("https://beast.example:8013") as p:
            self.assertTrue(p.is_available())

    def test_trailing_slash_and_whitespace_are_normalized(self):
        with mock.patch.dict(os.environ,
                             {"ORPHEUS_BASE_URL": "  http://localhost:8013/  "},
                             clear=False):
            self.assertEqual(providers._orpheus_base_url(), "http://localhost:8013")

    def test_is_available_makes_no_network_call(self):
        # available_providers() calls this for every provider; a request here
        # would stall every UI that lists providers.
        with mock.patch("urllib.request.urlopen") as urlopen, _provider() as p:
            p.is_available()
        urlopen.assert_not_called()

    def test_unconfigured_provider_is_absent_from_available_providers(self):
        providers._INSTANCES.pop("orpheus", None)
        try:
            with mock.patch.dict(os.environ, {"ORPHEUS_BASE_URL": ""}, clear=False):
                ids = [p.id for p in providers.available_providers()]
            self.assertNotIn("orpheus", ids)
        finally:
            providers._INSTANCES.pop("orpheus", None)


class RegistryTests(unittest.TestCase):
    def test_provider_is_registered(self):
        self.assertIn("orpheus", providers.all_provider_ids())

    def test_edge_is_still_the_default(self):
        self.assertEqual(providers.DEFAULT_PROVIDER_ID, "edge")
        self.assertEqual(providers.default_provider().id, "edge")


class CapabilityTests(unittest.TestCase):
    def test_rate_and_pitch_are_unsupported(self):
        with _provider() as p:
            caps = p.capabilities()
        self.assertFalse(caps.supports_rate)
        self.assertFalse(caps.supports_pitch)

    def test_needs_no_api_key(self):
        with _provider() as p:
            self.assertFalse(p.capabilities().needs_api_key)

    def test_reports_snac_sample_rate(self):
        with _provider() as p:
            self.assertEqual(p.capabilities().output_sample_rate_hz, 24000)

    def test_cache_identity_tracks_the_model(self):
        with _provider(ORPHEUS_TTS_MODEL="orpheus-3b-q4") as a:
            first = a.cache_identity()
        with _provider(ORPHEUS_TTS_MODEL="orpheus-3b-q8") as b:
            second = b.cache_identity()
        self.assertNotEqual(first, second)

    def test_cache_identity_ignores_the_url(self):
        # Where the decoder lives cannot change the audio it returns, so moving
        # the container must not invalidate a half-finished render.
        with _provider("http://localhost:8013") as a:
            first = a.cache_identity()
        with _provider("http://beast.local:8013") as b:
            second = b.cache_identity()
        self.assertEqual(first, second)


class VoiceCatalogTests(unittest.TestCase):
    def test_lists_the_eight_shipped_voices(self):
        with _provider() as p:
            voices = asyncio.run(p.list_voices())
        self.assertEqual(
            sorted(v.id for v in voices),
            ["dan", "jess", "leah", "leo", "mia", "tara", "zac", "zoe"],
        )

    def test_every_voice_carries_a_gender_and_both_are_offered(self):
        # Orpheus ships an unbalanced catalog (5 female, 3 male). Assert the
        # invariant the UI depends on — every voice labelled, both genders
        # selectable — rather than restating a count the constant already fixes.
        with _provider() as p:
            genders = [v.gender for v in asyncio.run(p.list_voices())]
        self.assertTrue(all(genders), "every voice must declare a gender")
        self.assertEqual(set(genders), {"Male", "Female"})

    def test_catalog_matches_the_declared_constant(self):
        with _provider() as p:
            voices = {v.id: v.gender for v in asyncio.run(p.list_voices())}
        self.assertEqual(voices, dict(providers._ORPHEUS_VOICES))

    def test_no_voices_when_unconfigured(self):
        with _provider(None) as p:
            self.assertEqual(asyncio.run(p.list_voices()), [])


class RequestShapeTests(unittest.TestCase):
    def test_posts_to_the_openai_compatible_route(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            return _FakeResponse(b"RIFFfake")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                _provider() as p:
            audio = p._request_audio("Hello.", "tara")

        self.assertEqual(captured["url"], "http://localhost:8013/v1/audio/speech")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(audio, b"RIFFfake")

    def test_requests_wav_so_chunk_encoding_stays_uniform(self):
        # MP3 straight from the container would inherit its encoder settings;
        # chunks are concatenated with an ffmpeg stream copy, which needs every
        # chunk to share identical codec parameters.
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured.update(json.loads(request.data.decode("utf-8")))
            return _FakeResponse(b"RIFFfake")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                _provider() as p:
            p._request_audio("Hello.", "tara")

        self.assertEqual(captured["response_format"], "wav")
        self.assertEqual(captured["input"], "Hello.")
        self.assertEqual(captured["voice"], "tara")

    def test_timeout_is_configurable(self):
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["timeout"] = timeout
            return _FakeResponse(b"RIFFfake")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                _provider(ORPHEUS_TIMEOUT="45") as p:
            p._request_audio("Hi.", "tara")
        self.assertEqual(seen["timeout"], 45.0)

    def test_invalid_timeout_falls_back_to_the_default(self):
        with _provider(ORPHEUS_TIMEOUT="soon") as p:
            self.assertEqual(p.timeout, 300.0)


class ErrorReportingTests(unittest.TestCase):
    def test_unconfigured_synthesis_names_the_variable(self):
        with _provider(None) as p:
            with self.assertRaises(RuntimeError) as ctx:
                p._request_audio("Hello.", "tara")
        self.assertIn("ORPHEUS_BASE_URL", str(ctx.exception))

    def test_unreachable_decoder_suggests_the_container(self):
        err = urllib.error.URLError("Connection refused")
        with mock.patch("urllib.request.urlopen", side_effect=err), _provider() as p:
            with self.assertRaises(RuntimeError) as ctx:
                p._request_audio("Hello.", "tara")
        message = str(ctx.exception)
        self.assertIn("http://localhost:8013", message)
        self.assertIn("container", message)

    def test_http_error_reports_the_status_code(self):
        err = urllib.error.HTTPError(
            "http://localhost:8013/v1/audio/speech", 503, "Service Unavailable",
            {}, None)
        self.addCleanup(err.close)   # HTTPError owns a file object
        with mock.patch("urllib.request.urlopen", side_effect=err), _provider() as p:
            with self.assertRaises(RuntimeError) as ctx:
                p._request_audio("Hello.", "tara")
        self.assertIn("503", str(ctx.exception))


class SynthesisTests(unittest.TestCase):
    @staticmethod
    def _fake_transcode(seen=None):
        def _run(wav_path, out_path, bitrate="64k"):
            if seen is not None:
                seen["wav_bytes"] = wav_path.read_bytes()
                seen["bitrate"] = bitrate
            wav_path.unlink()
            out_path.write_bytes(b"ID3fake-mp3")
        return _run

    def test_writes_audio_then_encodes_to_mp3(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "chunk_0001.mp3"
            seen = {}
            with (
                mock.patch("urllib.request.urlopen",
                           return_value=_FakeResponse(b"RIFFfake-wav")),
                mock.patch.object(providers, "_transcode_wav_to_mp3",
                                  side_effect=self._fake_transcode(seen)),
                _provider() as p,
            ):
                asyncio.run(p.synthesize(
                    "Hello.", "tara", out, rate="+0%", pitch="+0Hz"))

            self.assertEqual(seen["wav_bytes"], b"RIFFfake-wav")
            self.assertEqual(seen["bitrate"], "64k")
            self.assertEqual(out.read_bytes(), b"ID3fake-mp3")

    def test_leaves_no_temporary_wav_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "chunk_0001.mp3"
            with (
                mock.patch("urllib.request.urlopen",
                           return_value=_FakeResponse(b"RIFFfake-wav")),
                mock.patch.object(providers, "_transcode_wav_to_mp3",
                                  side_effect=self._fake_transcode()),
                _provider() as p,
            ):
                asyncio.run(p.synthesize(
                    "Hello.", "tara", out, rate="+0%", pitch="+0Hz"))

            self.assertEqual(list(Path(tmp).glob("*.wav")), [])

    def test_empty_response_is_an_error_not_a_silent_zero_byte_chunk(self):
        # generate_chunks() treats any non-empty file as done and skips it on
        # resume, so a failed chunk must never reach disk at all.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "chunk_0001.mp3"
            with (
                mock.patch("urllib.request.urlopen",
                           return_value=_FakeResponse(b"")),
                _provider() as p,
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    asyncio.run(p.synthesize(
                        "Hello.", "tara", out, rate="+0%", pitch="+0Hz"))
            self.assertIn("empty", str(ctx.exception).lower())
            self.assertFalse(out.exists())


class ChunkSizeNegotiationTests(unittest.TestCase):
    """Phase 2: a provider that needs a different chunk size gets one without
    every caller having to remember a flag."""

    def test_orpheus_asks_for_a_much_smaller_chunk(self):
        with _provider() as p:
            preferred = p.capabilities().preferred_max_chars
        self.assertEqual(preferred, providers.ORPHEUS_MAX_CHUNK_CHARS)
        self.assertLess(preferred, narrate.MAX_CHUNK_CHARS)

    def test_other_providers_express_no_preference(self):
        for pid in ("edge", "piper", "kokoro", "apple", "openai"):
            with self.subTest(provider=pid):
                caps = providers.get_provider(pid).capabilities()
                self.assertIsNone(caps.preferred_max_chars)

    def test_resolver_prefers_an_explicit_request(self):
        with _provider() as p:
            self.assertEqual(narrate.resolve_max_chars(p, 1234), 1234)

    def test_resolver_falls_back_to_the_provider_preference(self):
        with _provider() as p:
            self.assertEqual(
                narrate.resolve_max_chars(p, None),
                providers.ORPHEUS_MAX_CHUNK_CHARS,
            )

    def test_resolver_falls_back_to_the_global_default(self):
        self.assertEqual(
            narrate.resolve_max_chars(providers.get_provider("edge"), None),
            narrate.MAX_CHUNK_CHARS,
        )

    def test_preference_reaches_the_cache_fingerprint(self):
        # The fingerprint must record the size actually used, or a render at one
        # chunk size would reuse chunks generated at another.
        with _provider() as p:
            resolved = narrate.resolve_max_chars(p, None)
            same = narrate.narration_fingerprint(
                ["Hello."], provider=p, voice="tara", rate="+0%", pitch="+0Hz",
                max_chars=resolved)
            other = narrate.narration_fingerprint(
                ["Hello."], provider=p, voice="tara", rate="+0%", pitch="+0Hz",
                max_chars=narrate.MAX_CHUNK_CHARS)
        self.assertNotEqual(same, other)

    def test_build_audiobook_applies_the_preference_end_to_end(self):
        # Drive the real pipeline and assert the chunk size the engine chose.
        long_text = " ".join(f"Sentence number {i} here." for i in range(200))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            source.write_text(long_text, encoding="utf-8")
            output = root / "book.mp3"

            def fake_encode(chunk_paths, out, work_dir, fmt):
                out.write_bytes(b"ID3")

            with (
                mock.patch("urllib.request.urlopen",
                           return_value=_FakeResponse(b"RIFFfake-wav")),
                mock.patch.object(providers, "_transcode_wav_to_mp3",
                                  side_effect=SynthesisTests._fake_transcode()),
                mock.patch.object(narrate, "encode_output", side_effect=fake_encode),
                mock.patch.object(narrate, "probe_duration", return_value=1.0),
                _provider() as p,
                mock.patch.object(providers, "get_provider", return_value=p),
            ):
                result = narrate.build_audiobook(
                    source, output, provider="orpheus", voice="tara",
                    keep_chunks=True)

            # Every chunk must respect the provider's preferred ceiling, and the
            # document must have been split far more finely than the default.
            self.assertGreater(result.n_chunks,
                               len(long_text) // narrate.MAX_CHUNK_CHARS)
            self.assertGreaterEqual(
                result.n_chunks,
                len(long_text) // providers.ORPHEUS_MAX_CHUNK_CHARS)


class EmotionTagTests(unittest.TestCase):
    """Phase 3: tags must reach Orpheus intact and never reach anything else."""

    def test_only_orpheus_advertises_tag_support(self):
        supporting = [pid for pid in providers.all_provider_ids()
                      if providers.get_provider(pid).capabilities().supports_emotion_tags]
        self.assertEqual(supporting, ["orpheus"])

    def test_tags_are_stripped_by_default(self):
        # The default protects every other engine: edge-tts would say "laugh".
        self.assertEqual(
            narrate.clean_for_speech("She paused. <laugh> Then went on.", []),
            "She paused. Then went on.",
        )

    def test_tags_are_kept_when_requested(self):
        self.assertEqual(
            narrate.clean_for_speech("She paused. <laugh> Then went on.", [],
                                     keep_emotion_tags=True),
            "She paused. <laugh> Then went on.",
        )

    def test_stripping_preserves_following_punctuation(self):
        # A naive strip leaves " ," behind and the engine reads a stray pause.
        self.assertEqual(
            narrate.strip_emotion_tags("Well <sigh>, that is that."),
            "Well, that is that.",
        )

    def test_stripping_is_case_insensitive(self):
        self.assertEqual(narrate.strip_emotion_tags("Oh <LAUGH> dear."), "Oh dear.")

    def test_every_documented_tag_is_recognized(self):
        for tag in providers.ORPHEUS_EMOTION_TAGS:
            with self.subTest(tag=tag):
                self.assertEqual(
                    narrate.strip_emotion_tags(f"Before <{tag}> after."),
                    "Before after.",
                )

    def test_unknown_angle_bracket_content_is_left_alone(self):
        # Documents legitimately contain things like <placeholder>; today they
        # are read aloud and this change must not silently swallow them.
        text = "A <placeholder> and an <html> tag stay."
        self.assertEqual(narrate.strip_emotion_tags(text), text)

    def test_square_brackets_are_not_treated_as_tags(self):
        # Orpheus only understands angle brackets; [laugh] is read as a word,
        # so it must not be mistaken for a tag in either direction.
        text = "Nothing [laugh] happens here."
        self.assertEqual(narrate.strip_emotion_tags(text), text)

    def test_paragraph_builder_threads_the_switch(self):
        blocks = [{"type": "paragraph", "text": "Oh <laugh> really."}]
        stripped = narrate.blocks_to_paragraphs(blocks, [])
        kept = narrate.blocks_to_paragraphs(blocks, [], keep_emotion_tags=True)
        self.assertEqual(stripped, ["Oh really."])
        self.assertEqual(kept, ["Oh <laugh> really."])

    def test_user_rules_can_introduce_a_tag(self):
        # Rules run before tag handling, so the shipped preset works.
        rules = [("(laughs)", "<laugh>")]
        self.assertEqual(
            narrate.clean_for_speech("She said (laughs) nothing.", rules,
                                     keep_emotion_tags=True),
            "She said <laugh> nothing.",
        )

    def test_a_rule_introduced_tag_is_still_stripped_for_other_engines(self):
        # The same document, the same rules, a provider that cannot perform
        # them: the tag must vanish rather than be spoken.
        rules = [("(laughs)", "<laugh>")]
        self.assertEqual(
            narrate.clean_for_speech("She said (laughs) nothing.", rules),
            "She said nothing.",
        )

    def test_shipped_preset_maps_only_to_documented_tags(self):
        preset = json.loads(
            Path("rules.orpheus.example.json").read_text(encoding="utf-8"))
        allowed = {f"<{tag}>" for tag in providers.ORPHEUS_EMOTION_TAGS}
        self.assertTrue(preset, "the preset must not be empty")
        self.assertEqual(set(preset.values()) - allowed, set())

    def test_shipped_preset_survives_a_round_trip(self):
        preset = json.loads(
            Path("rules.orpheus.example.json").read_text(encoding="utf-8"))
        rules = list(preset.items())
        for cue, tag in rules:
            with self.subTest(cue=cue):
                kept = narrate.clean_for_speech(f"Then {cue} she left.", rules,
                                                keep_emotion_tags=True)
                self.assertIn(tag, kept)
                stripped = narrate.clean_for_speech(f"Then {cue} she left.", rules)
                self.assertNotIn("<", stripped)
                self.assertEqual(stripped, "Then she left.")

    def test_engine_selects_tag_handling_from_the_provider(self):
        # End-to-end: the same source document, two providers, different text.
        source_text = "She paused. <laugh> Then she went on."
        seen: dict[str, list[str]] = {}

        def capture(pid):
            def _synth(chunks, voice, rate, pitch, work_dir, on_chunk=None,
                       provider=None):
                seen[pid] = list(chunks)
                paths = []
                for i, _ in enumerate(chunks, start=1):
                    path = Path(work_dir) / f"chunk_{i:04d}.mp3"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"ID3")
                    paths.append(path)
                return paths
            return _synth

        for pid in ("edge", "orpheus"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "source.txt"
                source.write_text(source_text, encoding="utf-8")
                with (
                    _provider() as orpheus,
                    mock.patch.object(
                        providers, "get_provider",
                        side_effect=lambda p, _o=orpheus: (
                            _o if p == "orpheus" else providers._REGISTRY[p]())),
                    mock.patch.object(narrate, "generate_chunks",
                                      side_effect=capture(pid)),
                    mock.patch.object(narrate, "encode_output",
                                      side_effect=lambda c, o, w, f: o.write_bytes(b"ID3")),
                    mock.patch.object(narrate, "probe_duration", return_value=1.0),
                ):
                    narrate.build_audiobook(
                        source, root / "book.mp3", provider=pid, voice="tara",
                        keep_chunks=True)

        self.assertIn("<laugh>", " ".join(seen["orpheus"]))
        self.assertNotIn("<laugh>", " ".join(seen["edge"]))


if __name__ == "__main__":
    unittest.main()
