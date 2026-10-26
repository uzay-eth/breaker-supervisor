import json
from pathlib import Path
import tempfile
import unittest
from breaker.panel_arrival import pattern, qualify
from breaker.panel_record import CHECKS, FILES, PROFILES, fingerprint, invalidate, load, save, valid


class RecordTests(unittest.TestCase):
    def record(self):
        return {"schema": 1, "profile": PROFILES[0], "identity": "V1 module / FPC marking recorded",
                "checks": CHECKS[:], "fingerprint": "a"*64, "verified": True}

    def test_both_explicit_profiles(self):
        for profile in PROFILES:
            record = self.record(); record["profile"] = profile
            self.assertTrue(valid(record, "a"*64))

    def test_unknown_and_unverified_rejected(self):
        for key, value in (("profile", "UNKNOWN"), ("profile", "V3"), ("verified", False),
                           ("verified", 1), ("schema", True), ("identity", ""),
                           ("identity", "x\ny"), ("identity", "x"*161), ("checks", [])):
            record = self.record(); record[key] = value
            self.assertFalse(valid(record, "a"*64), (key, value))

    def test_stale_driver_fingerprint_rejected(self):
        self.assertFalse(valid(self.record(), "b"*64))

    def test_missing_or_malformed_fingerprint_rejected(self):
        for value in (None, "", "A"*64, "g"*64, "a"*63):
            record = self.record(); record["fingerprint"] = value
            self.assertFalse(valid(record, value))

    def test_extra_fields_rejected(self):
        record = self.record(); record["enable"] = True
        self.assertFalse(valid(record, "a"*64))

    def test_missing_malformed_oversized_records_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/"panel.json")
            self.assertIsNone(load(path, "a"*64))
            for text in ("{", "null", "[]", "x"*1025):
                Path(path).write_text(text)
                self.assertIsNone(load(path, "a"*64))

    def test_save_load_invalidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/"panel.json")
            expected = save(path, PROFILES[1], "V2 sticker; FPC identifier", "a"*64)
            self.assertEqual(load(path, "a"*64), expected)
            invalidate(path)
            self.assertFalse(Path(path).exists())
            invalidate(path)  # Missing record is a safe initial state.

    def test_fingerprint_covers_every_qualified_file(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in FILES:
                (Path(directory)/name).write_bytes(b"original")
            original = fingerprint(directory)
            for name in FILES:
                path = Path(directory)/name
                path.write_bytes(b"changed")
                self.assertNotEqual(fingerprint(directory), original, name)
                path.write_bytes(b"original")


class FakePanel:
    def __init__(self, complete=True):
        self.frames = []
        self.complete = complete

    def display(self, buffer, now):
        self.frames.append(buffer)
        yield None
        if self.complete:
            yield True


class ArrivalTests(unittest.TestCase):
    def test_test_patterns_are_full_native_frames(self):
        self.assertNotEqual(pattern(PROFILES[0]), pattern(PROFILES[1]))
        for profile in PROFILES:
            self.assertEqual(len(pattern(profile)), 4736)
        with self.assertRaises(ValueError): pattern("UNKNOWN")

    def test_complete_sequence_needs_fresh_physical_hold(self):
        for profile in PROFILES:
            panel = FakePanel()
            now = [0]
            finished_at = [None]
            def service():
                now[0] += 5
                if now[0] > 10000: raise TimeoutError()
                return now[0]
            def button():
                if len(panel.frames) < 4: return 1
                if finished_at[0] is None: finished_at[0] = now[0]
                return int(now[0]-finished_at[0] < 650)
            for _ in qualify(panel, service, button, profile): pass
            self.assertGreaterEqual(now[0]-finished_at[0], 1650)
            self.assertEqual(panel.frames[0], b"\xff"*4736)
            self.assertEqual(panel.frames[1], pattern(profile))
            self.assertEqual(panel.frames[2], bytes(255-b for b in pattern(profile)))
            self.assertEqual(panel.frames[3], pattern(profile))

    def test_preheld_button_never_qualifies(self):
        panel = FakePanel(); now = [0]
        def service():
            now[0] += 5
            if now[0] > 2000: raise TimeoutError()
            return now[0]
        with self.assertRaises(TimeoutError):
            for _ in qualify(panel, service, lambda: 0, PROFILES[0]): pass
        self.assertEqual(panel.frames, [])

    def test_missing_busy_completion_fails(self):
        panel = FakePanel(complete=False); now = [0]
        def service(): now[0] += 5; return now[0]
        with self.assertRaises(OSError):
            for _ in qualify(panel, service, lambda: 1, PROFILES[1]): pass

    def test_handle_or_deadline_veto_interrupts_test(self):
        panel = FakePanel(); now = [0]
        def service():
            now[0] += 5
            if now[0] > 100: raise ValueError("physical handle no longer OFF")
            return now[0]
        with self.assertRaises(ValueError):
            for _ in qualify(panel, service, lambda: 1, PROFILES[1]): pass
