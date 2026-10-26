"""Regressions for supervisor safety, with real shared core and SIM physical inputs."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from breaker import face
from breaker.canonical import payout, digest, to_hex
from breaker.core import FRAME_ERROR_LIMIT, FRAME_ERROR_WINDOW_MS
from helpers import Rig


class OffWitnessTests(unittest.TestCase):
    def off(self):
        r = Rig(); r.arm(); r.inputs[:2] = [1, 0]; r.step(50)
        self.assertTrue(r.s.off_seen)
        return r

    def on(self, r):
        r.inputs[:2] = [0, 1]; r.step(50)
        self.assertTrue(r.s.enabled)
        self.assertEqual(r.s.reset_epoch, 2)
        self.assertFalse(r.confirms())

    def deny_by_off(self, delay):
        r = Rig(); r.arm(); r.hold(); r.panel()
        r.inputs[:2] = [1, 0]; r.step(delay)
        r.command('CUT', 'linux.cut'); r.step(50)
        self.on(r)

    def test_deny_off_cut_10ms(self): self.deny_by_off(10)
    def test_deny_off_cut_40ms(self): self.deny_by_off(40)
    def test_deny_off_cut_500ms(self): self.deny_by_off(500)

    def test_off_host_loss_return_on(self):
        r = self.off(); r.step(3100); r.command('PING'); r.step(10)
        self.on(r)

    def test_off_linux_trip_on(self):
        r = self.off(); r.command('TRIP', 'mail.mass_delete'); r.step(10)
        self.on(r)

    def test_off_linux_cut_on(self):
        r = self.off(); r.command('CUT', 'linux.cut'); r.step(10)
        self.on(r)

    def test_off_uart_error_on(self):
        r = self.off(); r.raw(b'BRK1|PING|zz\n'); r.step(10)
        self.on(r)

    def test_remote_events_never_create_off_witness_or_enable(self):
        for verb in ('TRIP', 'CUT', 'ERROR'):
            r = Rig(); r.arm()
            if verb == 'ERROR': r.raw(b'bad\n')
            else: r.command(verb, 'linux.cut')
            self.assertFalse(r.s.off_seen)
            enabled = r.s.enabled
            r.command('HELLO'); r.command('PING'); r.step(50)
            self.assertEqual(r.s.enabled, enabled)
            self.assertTrue(r.s.latched)

    def test_semantic_uart_errors_also_preserve_off(self):
        for kind in ('replay', 'session', 'command'):
            r = self.off()
            if kind == 'replay': r.command('PING', seq=r.seq)
            elif kind == 'session': r.command('PING', boot='b'*32)
            else: r.command('RESTORE')
            self.assertFalse(r.s.enabled)
            self.on(r)

    def test_local_safety_faults_invalidate_off(self):
        for reason in ('cut.fault', 'case.open', 'handle.invalid', 'input.edge',
                       'display.fault', 'loop.stall', 'clock.invalid', 'input.invalid', 'hold.integrity'):
            r = self.off(); r.s.fail(reason); r.collect()
            self.assertFalse(r.s.off_seen, reason)
            r.command('PING'); r.inputs[:2] = [0, 1]; r.step(50)
            self.assertFalse(r.s.enabled, reason)
            r.inputs[:2] = [1, 0]; r.step(50); r.inputs[:2] = [0, 1]; r.step(50)
            self.assertTrue(r.s.enabled, reason)

    def test_actual_fault_input_while_off_needs_another_cycle(self):
        r = self.off(); r.inputs[3] = 0; r.step(10)
        r.inputs[3] = 1; r.step(50); r.inputs[:2] = [0, 1]; r.step(50)
        self.assertFalse(r.s.enabled)

    def test_disconnect_and_backpressure_do_not_erase_off(self):
        for reason in ('uart.disconnect', 'io.backpressure'):
            r = self.off(); r.s.fail(reason); r.collect(); self.on(r)
        r = self.off()
        from breaker.wire import encode
        for _ in range(20): r.s.receive(encode('HELLO'), r.now)
        self.assertTrue(r.s.off_seen)
        r.collect(); self.on(r)


class NoiseTests(unittest.TestCase):
    def test_three_errors_in_window_cut_and_stay_cut(self):
        r = Rig(); r.arm(); r.hold(); r.panel(); r.release(); r.press(1000)
        for count in range(1, FRAME_ERROR_LIMIT+1):
            r.raw(b'bad\n')
            self.assertIsNone(r.s.pending)
            self.assertEqual(r.s.enabled, count < FRAME_ERROR_LIMIT)
            self.assertTrue(r.s.latched); self.assertFalse(r.confirms())
            r.step(10)
        r.command('PING'); r.command('HELLO'); r.step(FRAME_ERROR_WINDOW_MS, heartbeat=True)
        self.assertFalse(r.s.enabled)
        self.assertEqual(len([e for e in r.events if e[0] == 'DENIED']), 1)
        self.assertEqual(len([e for e in r.events if e[0] == 'ERROR']), 3)

    def test_rolling_window_boundary_and_valid_ping_does_not_reset_count(self):
        r = Rig(); r.arm()
        r.raw(b'bad\n'); r.step(500); r.command('PING'); r.raw(b'bad\n')
        r.step(500); r.raw(b'bad\n')  # First error is exactly 1000 ms old, excluded.
        self.assertTrue(r.s.enabled)
        r.step(10); r.raw(b'bad\n')
        self.assertFalse(r.s.enabled)

    def test_separated_errors_do_not_cut_but_cannot_clear_latch(self):
        r = Rig(); r.arm()
        for _ in range(5):
            r.raw(b'bad\n'); r.step(FRAME_ERROR_WINDOW_MS, heartbeat=True)
            self.assertTrue(r.s.enabled); self.assertTrue(r.s.latched)
        self.assertLessEqual(len(r.s.frame_errors), FRAME_ERROR_LIMIT)

    def test_error_budget_survives_handle_reset(self):
        r = Rig(); r.arm(); r.raw(b'bad\n'); r.raw(b'bad\n')
        r.inputs[:2] = [1, 0]; r.step(50); r.inputs[:2] = [0, 1]; r.step(50)
        self.assertTrue(r.s.enabled); self.assertFalse(r.s.latched)
        r.raw(b'bad\n'); self.assertFalse(r.s.enabled)

    def test_threshold_while_off_still_allows_next_physical_on(self):
        r = Rig(); r.arm(); r.inputs[:2] = [1, 0]; r.step(50)
        r.raw(b'bad\n'*FRAME_ERROR_LIMIT)
        self.assertFalse(r.s.enabled); self.assertTrue(r.s.off_seen)
        r.inputs[:2] = [0, 1]; r.step(50)
        self.assertTrue(r.s.enabled)

    def test_corruption_followed_by_hold_in_same_burst_cannot_release(self):
        from breaker.wire import encode
        from helpers import BOOT
        r = Rig(); r.arm(); raw = payout(184000)
        r.raw(b'bad\n'+encode('HOLD', BOOT, r.seq+1, digest(raw), to_hex(raw)))
        self.assertIsNone(r.s.pending); self.assertTrue(r.s.latched)
        r.panel(); r.release(); r.press(); self.assertFalse(r.confirms())


class MismatchTests(unittest.TestCase):
    def test_wrong_claimed_hash_is_not_check_divergence(self):
        for verb in ('HOLD', 'CHECK'):
            r = Rig(); r.arm(); ticket, raw = r.hold()
            if verb == 'HOLD': r.command('HOLD', '0'*64, to_hex(raw))
            else: r.command('CHECK', ticket, '0'*64, to_hex(raw))
            self.assertEqual(r.s.reason, 'hash.invalid')
            self.assertFalse(r.s.enabled); self.assertFalse(r.confirms())

    def test_wrong_or_absent_ticket_is_not_check_divergence(self):
        for present in (True, False):
            r = Rig(); r.arm(); raw = payout(184000)
            if present: r.hold()
            r.command('CHECK', 12345, digest(raw), to_hex(raw))
            self.assertEqual(r.s.reason, 'ticket.invalid')
            self.assertFalse(r.confirms())

    def test_local_binding_corruption_is_not_check_divergence(self):
        r = Rig(); r.arm(); r.hold(); r.panel(); r.release()
        r.s.pending['canonical'] = payout(184001)
        r.press()
        self.assertEqual(r.s.reason, 'hold.integrity')
        self.assertFalse(r.s.enabled); self.assertFalse(r.confirms())

    def test_new_request_after_release_is_held_with_new_hash(self):
        r = Rig(); r.arm(); _, original = r.hold(); r.panel(); r.release(); r.press()
        r.release(); _, altered = r.hold(1840000)
        self.assertEqual(r.s.mode, 'HELD'); self.assertEqual(r.s.reason, 'held')
        self.assertNotEqual(digest(original), digest(altered))
        self.assertEqual(len(r.confirms()), 1)


class RenderingTests(unittest.TestCase):
    def test_text_byte_blit_matches_pixel_reference_at_every_alignment(self):
        data = face.load_font()
        for style in range(face.FONT_STYLES):
            for shift in range(8):
                for ch in 'srgjAM@$/012•·…':
                    for ink in (False, True):
                        actual, expected = face.Canvas(), face.Canvas()
                        if not ink:
                            actual.buf[:] = expected.buf[:] = b'\x00'*4736
                        actual.text(8+shift, 64, ch, style, ink)
                        w, h, top, _, offset = expected.glyph(ch, style)
                        for y in range(h):
                            for x in range(w):
                                if data[offset+y*((w+7)//8)+x//8] & (128>>(x%8)):
                                    expected.pixel(8+shift+x, 64+top+y, ink)
                        self.assertEqual(actual.buf, expected.buf, (style, shift, ch, ink))

    def test_text_yields_per_glyph_and_finishes_identically(self):
        c, sync = face.Canvas(), face.Canvas()
        job = c.text_steps(6, 48, 'ARMED', 4)
        self.assertIsNone(next(job))
        sync.text(6, 48, 'A', 4)
        self.assertEqual(c.buf, sync.buf)  # First scheduling point is after just A.
        self.assertEqual(len(list(job)), 4)
        sync = face.Canvas(); sync.text(6, 48, 'ARMED', 4)
        self.assertEqual(c.buf, sync.buf)

    def test_band_yields_after_four_rows(self):
        c = face.Canvas(); job = c.rect_steps(0, 23, 296, 31)
        next(job)
        self.assertEqual(c.buf[23*37:27*37], b'\x00'*(4*37))
        self.assertEqual(c.buf[27*37:54*37], b'\xff'*(27*37))

    def test_explicit_transport_labels_and_unknown_reason_never_becomes_text(self):
        from breaker.face import REASONS
        codes = ('frame_crc', 'frame_ascii', 'frame_shape', 'frame_size', 'frame_timeout',
                 'session.invalid', 'sequence.invalid', 'sequence.replay', 'rx.burst',
                 'ticket.invalid', 'io.backpressure', 'uart.disconnect', 'host.timeout')
        for code in codes:
            self.assertIn(code, REASONS)
            self.assertNotEqual(REASONS[code], 'INVALID REQUEST')
        original = face.Canvas.text_steps
        labels = []
        def capture(canvas, x, baseline, text, style=1, ink=True):
            labels.append(text)
            yield from original(canvas, x, baseline, text, style, ink)
        r = Rig(); r.arm(); r.command('CUT', 'linux.cut')
        with patch.object(face.Canvas, 'text_steps', capture):
            for code in codes + ('agent says release money now',):
                labels.clear(); model = r.s.face_model(); model['reason'] = code
                self.assertEqual(len(face.render(model)), 4736)
                self.assertIn(REASONS.get(code, 'SUPERVISOR ERROR'), labels)
                self.assertNotIn('INVALID REQUEST', labels)
                self.assertNotIn('agent says release money now', labels)

    def test_small_s_and_r_share_lowercase_baseline_and_height(self):
        c = face.Canvas(); data = face.load_font()
        for style in range(3):
            _, n_height, n_top, _, _ = c.glyph('n', style)
            for ch in 'sr':
                w, h, top, advance, offset = c.glyph(ch, style)
                self.assertEqual((h, top), (n_height, n_top), (style, ch))
                rows = [data[offset+y*((w+7)//8):offset+(y+1)*((w+7)//8)] for y in range(h)]
                self.assertTrue(any(rows[0])); self.assertTrue(any(rows[-1]))
                self.assertEqual(advance, w)
            w, h, _, _, offset = c.glyph('s', style)
            occupied = [x for x in range(w) if any(data[offset+y*((w+7)//8)+x//8] & (128>>(x%8)) for y in range(h))]
            self.assertGreaterEqual(max(occupied)-min(occupied)+1, c.glyph('n', style)[0]-3)
