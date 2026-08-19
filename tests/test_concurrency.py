"""Tests for concurrent chunk synthesis.

The risky properties here are not "does it go faster" but: does work actually
overlap, does it stay within the provider's limit, and does progress still get
reported in chunk order? Both shipped UIs use `i / total` as a progress
fraction, so out-of-order callbacks would make their progress bars go backwards.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

import narrate
import providers


class _TrackingProvider(providers.TTSProvider):
    """Records how many synthesize() calls are in flight at once.

    `delays` maps a chunk's text to how long its synthesis should take, which
    lets a test force completions to finish out of order.
    """

    id = "tracking"
    label = "Tracking"

    def __init__(self, limit=1, delays=None, fail_on=None):
        self.limit = limit
        self.delays = delays or {}
        self.fail_on = fail_on
        self.in_flight = 0
        self.peak_in_flight = 0
        self.started: list[str] = []
        self.completed: list[str] = []

    def capabilities(self):
        return providers.Capabilities(
            provider_id=self.id, max_concurrent_chunks=self.limit
        )

    async def list_voices(self):
        return []

    async def synthesize(self, text, voice_id, out_path, *, rate, pitch):
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        self.started.append(text)
        try:
            await asyncio.sleep(self.delays.get(text, 0.01))
            if self.fail_on is not None and text == self.fail_on:
                raise RuntimeError(f"synthesis failed for {text!r}")
            out_path.write_bytes(b"ID3" + text.encode("utf-8"))
            self.completed.append(text)
        finally:
            self.in_flight -= 1


def _run(provider, chunks, concurrency=None, on_chunk=None, work_dir=None):
    async def go(tmp):
        return await narrate.generate_chunks(
            chunks, "voice", "+0%", "+0Hz", Path(work_dir or tmp),
            on_chunk=on_chunk, provider=provider, concurrency=concurrency,
        )

    if work_dir is not None:
        return asyncio.run(go(work_dir))
    with tempfile.TemporaryDirectory() as tmp:
        return asyncio.run(go(tmp))


class ResolutionTests(unittest.TestCase):
    def test_providers_declare_their_own_limits(self):
        expected = {"edge": 4, "piper": 1, "kokoro": 1, "apple": 1, "openai": 4}
        for pid, want in expected.items():
            with self.subTest(provider=pid):
                caps = providers.get_provider(pid).capabilities()
                self.assertEqual(caps.max_concurrent_chunks, want)

    def test_local_onnx_providers_stay_serial(self):
        # R-004: Piper and Kokoro hold process-singleton engines that are not
        # documented as thread-safe. They must never opt into concurrency.
        for pid in ("piper", "kokoro"):
            with self.subTest(provider=pid):
                self.assertEqual(
                    providers.get_provider(pid).capabilities().max_concurrent_chunks,
                    1,
                )

    def test_explicit_request_wins(self):
        prov = _TrackingProvider(limit=4)
        self.assertEqual(narrate.resolve_concurrency(prov, 2), 2)

    def test_provider_limit_is_the_default(self):
        self.assertEqual(narrate.resolve_concurrency(_TrackingProvider(limit=3)), 3)

    def test_never_below_one(self):
        prov = _TrackingProvider(limit=4)
        for request in (0, -5):
            with self.subTest(request=request):
                self.assertEqual(narrate.resolve_concurrency(prov, request), 1)

    def test_concurrency_is_absent_from_the_cache_fingerprint(self):
        # How many chunks are in flight cannot change the audio any one of them
        # produces, so it must not invalidate a half-finished render.
        prov = _TrackingProvider(limit=4)
        args = dict(provider=prov, voice="v", rate="+0%", pitch="+0Hz",
                    max_chars=3000)
        self.assertEqual(
            narrate.narration_fingerprint(["a", "b"], **args),
            narrate.narration_fingerprint(["a", "b"], **args),
        )


class OverlapTests(unittest.TestCase):
    def test_work_actually_overlaps(self):
        prov = _TrackingProvider(limit=3)
        _run(prov, [f"chunk {i}" for i in range(6)])
        self.assertGreater(prov.peak_in_flight, 1,
                           "expected concurrent synthesis, saw strictly serial")

    def test_never_exceeds_the_limit(self):
        prov = _TrackingProvider(limit=3)
        _run(prov, [f"chunk {i}" for i in range(12)])
        self.assertLessEqual(prov.peak_in_flight, 3)

    def test_explicit_concurrency_overrides_the_provider(self):
        prov = _TrackingProvider(limit=8)
        _run(prov, [f"chunk {i}" for i in range(10)], concurrency=2)
        self.assertLessEqual(prov.peak_in_flight, 2)

    def test_limit_of_one_stays_strictly_serial(self):
        prov = _TrackingProvider(limit=1)
        _run(prov, [f"chunk {i}" for i in range(6)])
        self.assertEqual(prov.peak_in_flight, 1)

    def test_concurrency_one_matches_serial_output(self):
        chunks = [f"chunk {i}" for i in range(4)]
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            serial = _run(_TrackingProvider(limit=1), chunks, work_dir=a)
            parallel = _run(_TrackingProvider(limit=4), chunks, work_dir=b)
            self.assertEqual([p.name for p in serial], [p.name for p in parallel])
            self.assertEqual([p.read_bytes() for p in serial],
                             [p.read_bytes() for p in parallel])


class OrderedReportingTests(unittest.TestCase):
    def test_progress_is_reported_in_chunk_order(self):
        # Make the first chunk by far the slowest so it completes last. The
        # callback must still report 1, 2, 3, 4.
        chunks = ["first", "second", "third", "fourth"]
        prov = _TrackingProvider(
            limit=4,
            delays={"first": 0.20, "second": 0.01, "third": 0.01, "fourth": 0.01},
        )
        seen = []
        _run(prov, chunks, on_chunk=lambda i, total, chars, status: seen.append(i))

        self.assertEqual(seen, [1, 2, 3, 4])
        # Sanity: the slow chunk really did finish last, so ordering was earned
        # rather than accidental.
        self.assertEqual(prov.completed[-1], "first")

    def test_progress_fraction_never_decreases(self):
        chunks = [f"chunk {i}" for i in range(8)]
        delays = {"chunk 0": 0.15, "chunk 1": 0.12}
        prov = _TrackingProvider(limit=4, delays=delays)
        fractions = []
        _run(prov, chunks,
             on_chunk=lambda i, total, chars, s: fractions.append(i / total))
        self.assertEqual(fractions, sorted(fractions))
        self.assertEqual(fractions[-1], 1.0)

    def test_callback_receives_the_matching_chunk_length(self):
        chunks = ["a", "bbb", "ccccc"]
        seen = {}
        _run(_TrackingProvider(limit=3), chunks,
             on_chunk=lambda i, total, chars, s: seen.__setitem__(i, chars))
        self.assertEqual(seen, {1: 1, 2: 3, 3: 5})

    def test_paths_are_returned_in_chunk_order(self):
        # Slowest chunk first, so completion order is the reverse of index order.
        chunks = ["slow", "fast"]
        prov = _TrackingProvider(limit=2, delays={"slow": 0.15, "fast": 0.01})
        with tempfile.TemporaryDirectory() as tmp:
            paths = _run(prov, chunks, work_dir=tmp)
            self.assertEqual([p.name for p in paths],
                             ["chunk_0001.mp3", "chunk_0002.mp3"])
            self.assertEqual(paths[0].read_bytes(), b"ID3slow")
            self.assertEqual(paths[1].read_bytes(), b"ID3fast")
        self.assertEqual(prov.completed, ["fast", "slow"])


class ResumeTests(unittest.TestCase):
    def test_existing_chunks_are_skipped_under_concurrency(self):
        chunks = ["one", "two", "three"]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "chunk_0002.mp3").write_bytes(b"ID3already")

            prov = _TrackingProvider(limit=3)
            statuses = {}
            _run(prov, chunks, work_dir=work,
                 on_chunk=lambda i, t, c, s: statuses.__setitem__(i, s))

            self.assertEqual(statuses, {1: "done", 2: "skipped", 3: "done"})
            self.assertNotIn("two", prov.started)
            self.assertEqual((work / "chunk_0002.mp3").read_bytes(), b"ID3already")

    def test_a_skipped_chunk_does_not_consume_a_slot(self):
        # All but one chunk already exist; the remaining one should still run.
        chunks = [f"chunk {i}" for i in range(6)]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for i in range(1, 6):
                (work / f"chunk_{i:04d}.mp3").write_bytes(b"ID3old")
            prov = _TrackingProvider(limit=2)
            _run(prov, chunks, work_dir=work)
            self.assertEqual(prov.started, ["chunk 5"])

    def test_empty_chunk_file_is_regenerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "chunk_0001.mp3").write_bytes(b"")
            prov = _TrackingProvider(limit=2)
            _run(prov, ["one"], work_dir=work)
            self.assertEqual(prov.started, ["one"])


class FailureTests(unittest.TestCase):
    def test_failure_propagates(self):
        prov = _TrackingProvider(limit=3, fail_on="chunk 1")
        with self.assertRaises(RuntimeError):
            _run(prov, [f"chunk {i}" for i in range(6)])

    def test_completed_chunks_survive_a_failure_so_a_rerun_resumes(self):
        chunks = [f"chunk {i}" for i in range(6)]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            prov = _TrackingProvider(
                limit=2, fail_on="chunk 3",
                delays={"chunk 0": 0.01, "chunk 1": 0.01},
            )
            with self.assertRaises(RuntimeError):
                _run(prov, chunks, work_dir=work)

            # The chunks that finished before the failure stay on disk.
            self.assertTrue((work / "chunk_0001.mp3").exists())
            self.assertTrue((work / "chunk_0002.mp3").exists())
            # The failed one did not leave a partial file behind.
            self.assertFalse((work / "chunk_0004.mp3").exists())

    def test_no_tasks_are_left_pending_after_a_failure(self):
        # A leaked task would keep synthesizing behind the user's back and, in
        # asyncio.run(), surface as "Task was destroyed but it is pending".
        chunks = [f"chunk {i}" for i in range(8)]
        prov = _TrackingProvider(
            limit=2, fail_on="chunk 0",
            delays={f"chunk {i}": 0.05 for i in range(1, 8)},
        )
        with self.assertRaises(RuntimeError):
            _run(prov, chunks)
        self.assertEqual(prov.in_flight, 0)


if __name__ == "__main__":
    unittest.main()
