"""Supervisor shared-core contracts; SIM inputs only, no broker/VM/hardware claim."""
import unittest
from unittest.mock import patch
from breaker import face
from breaker.core import Supervisor, HEARTBEAT_MS, HOLD_MS, FRAME_ERROR_LIMIT
from helpers import Rig


class NoiseContractTests(unittest.TestCase):
    def test_each_budgeted_error_denies_exact_hold_emits_error_and_keeps_heartbeat(self):
        cases = ((b'BRK1|HELLO|00000000\n', 'frame_crc'),
                 (b'bad\x00\n', 'frame_ascii'), (b'bad\n', 'frame_shape'),
                 (b'x'*1024+b'\n', 'frame_size'), (b'BRK1|', 'frame_timeout'))
        for raw, reason in cases:
            with self.subTest(reason=reason):
                r = Rig(); r.arm(); ticket, _ = r.hold(); sha = r.s.pending['sha256']
                r.events.clear(); r.raw(raw)
                if reason == 'frame_timeout': r.step(1000); r.raw(b'\n')
                denied = [e for e in r.events if e[0]=='DENIED']
                self.assertEqual(len(denied), 1)
                self.assertEqual(denied[0][3:], (str(ticket), sha, reason))
                self.assertTrue(any(e[0]=='ERROR' and e[3:]==(reason,) for e in r.events))
                self.assertTrue(any(e[0]=='STATE' and e[3]=='TRIPPED' and e[5]=='1' for e in r.events))
                self.assertTrue(r.s.enabled); self.assertTrue(r.s.latched)
                r.step(4000, heartbeat=True)
                self.assertTrue(r.s.live); self.assertTrue(r.s.enabled)
                r.command('HELLO'); r.command('STATUS'); r.panel(); r.release(); r.press()
                self.assertFalse(r.confirms()); self.assertIsNone(r.s.pending)
                self.assertTrue(r.s.latched)

    def test_threshold_error_events_do_not_wait_for_a_host_cut(self):
        r = Rig(); r.arm(); r.hold(); r.events.clear()
        r.raw(b'bad\n'*FRAME_ERROR_LIMIT)
        self.assertEqual(len([e for e in r.events if e[0]=='ERROR']), FRAME_ERROR_LIMIT)
        self.assertEqual(len([e for e in r.events if e[0]=='DENIED']), 1)
        self.assertFalse(r.s.enabled); self.assertFalse(r.confirms())
        self.assertEqual(r.s._state()[2], 0)


class BrokerContinuityContractTests(unittest.TestCase):
    def test_sole_owner_pings_keep_port_up_through_proxy_downtime(self):
        r = Rig(); r.arm(); r.hold()
        # Host broker cancels the disconnected proxy's ownership without stale DENY.
        r.command('TRIP', 'linux.cut')
        r.step(HEARTBEAT_MS*4, heartbeat=True)
        self.assertTrue(r.s.enabled); self.assertTrue(r.s.live)
        self.assertIsNone(r.s.pending); self.assertFalse(r.confirms())
        before = r.s.reset_epoch
        r.command('HELLO'); r.command('STATUS')
        self.assertEqual(r.s.reset_epoch, before)  # Reconnect is not a physical reset.
        r.panel(); r.release(); r.press()
        self.assertFalse(r.confirms())  # Old ticket cannot survive proxy reconnection.

    def test_proxy_ownership_loss_trip_is_safe_before_or_after_hold_completion(self):
        for completed in ('pending', 'released', 'expired'):
            with self.subTest(completed=completed):
                r = Rig(); r.arm(); r.hold(); r.panel()
                if completed == 'released': r.release(); r.press()
                if completed == 'expired': r.step(HOLD_MS, heartbeat=True)
                prior_releases = len(r.confirms())
                self.assertEqual(prior_releases, int(completed == 'released'))
                # Physical release/expiry can win before the broker's TRIP arrives.
                r.events.clear(); r.command('TRIP', 'linux.cut')
                self.assertFalse(any(e[0]=='ERROR' for e in r.events))
                self.assertTrue(r.s.enabled); self.assertTrue(r.s.latched)
                self.assertIsNone(r.s.pending)
                r.panel(); r.release(); r.press()
                self.assertFalse(r.confirms())  # No new authorization after the barrier.

    def test_physical_reset_during_proxy_downtime_is_in_first_fresh_state(self):
        r = Rig(); r.arm(); r.command('TRIP', 'mail.mass_delete')
        old_epoch = r.s.reset_epoch
        r.step(HEARTBEAT_MS*2, heartbeat=True)
        self.assertTrue(r.s.enabled); self.assertTrue(r.s.latched)
        r.inputs[:2] = [1, 0]; r.step(50, heartbeat=True)
        r.inputs[:2] = [0, 1]; r.step(50, heartbeat=True); r.panel()
        r.step(HEARTBEAT_MS*2, heartbeat=True)
        r.events.clear(); r.command('HELLO')
        state = next(e for e in r.events if e[0]=='STATE')
        self.assertEqual(state[3:10], ('ARMED', 'ON', '1', 'green', '0', '-', str(r.s.face_generation)))
        self.assertEqual(state[10], '1')
        self.assertGreater(int(state[-1]), old_epoch)
        self.assertFalse(r.confirms())

    def test_reconnect_without_physical_reset_keeps_policy_latch(self):
        r = Rig(); r.arm(); r.command('TRIP', 'mail.mass_delete'); epoch = r.s.reset_epoch
        r.step(HEARTBEAT_MS*2, heartbeat=True); r.command('HELLO'); r.command('STATUS')
        self.assertTrue(r.s.latched); self.assertEqual(r.s.reset_epoch, epoch)
        self.assertEqual(r.s.mode, 'TRIPPED')

    def test_new_boot_armed_epoch_requires_physical_rearm(self):
        r = Rig(); r.s = Supervisor('b'*32); r.events.clear()
        r.command('PING', boot='b'*32); r.panel()
        r.inputs[:2] = [0, 1]; r.step(50)
        self.assertEqual(r.s.reset_epoch, 0); self.assertFalse(r.s.enabled)
        r.command('HELLO'); r.command('STATUS', boot='b'*32)
        self.assertFalse(r.s.enabled)
        r.inputs[:2] = [1, 0]; r.step(50); r.inputs[:2] = [0, 1]; r.step(50); r.panel()
        self.assertEqual(r.s.mode, 'ARMED'); self.assertEqual(r.s.reset_epoch, 1)
        self.assertTrue(r.s.enabled); self.assertTrue(r.s.face_ready)
        self.assertIsNone(r.s.pending); self.assertFalse(r.confirms())

    def test_actual_broker_heartbeat_loss_still_cuts_and_ping_never_restores(self):
        r = Rig(); r.arm(); r.hold(); r.step(HEARTBEAT_MS)
        self.assertFalse(r.s.enabled); self.assertIsNone(r.s.pending)
        r.command('HELLO'); r.command('PING'); r.step(50)
        self.assertFalse(r.s.enabled); self.assertFalse(r.confirms())


class PolicyStateReasonTests(unittest.TestCase):
    def test_policy_state_trip_cancels_hold_keeps_enable_and_needs_physical_reset(self):
        r = Rig(); r.arm(); r.hold(); r.command('TRIP', 'policy.state_invalid')
        self.assertTrue(r.s.enabled); self.assertTrue(r.s.latched); self.assertIsNone(r.s.pending)
        self.assertEqual(r.s.reason, 'policy.state_invalid')
        epoch = r.s.reset_epoch
        r.command('HELLO'); r.command('PING'); r.panel(); r.release(); r.press()
        self.assertTrue(r.s.latched); self.assertFalse(r.confirms()); self.assertEqual(r.s.reset_epoch, epoch)
        r.inputs[:2] = [1, 0]; r.step(50); r.inputs[:2] = [0, 1]; r.step(50)
        self.assertFalse(r.s.latched); self.assertEqual(r.s.reset_epoch, epoch+1)

    def test_policy_state_trip_never_enables_an_off_port(self):
        r = Rig(); r.arm(); r.command('CUT', 'linux.cut')
        r.command('TRIP', 'policy.state_invalid')
        self.assertFalse(r.s.enabled); self.assertFalse(r.confirms())

    def test_policy_state_face_copy_is_fixed(self):
        r = Rig(); r.arm(); r.command('TRIP', 'policy.state_invalid')
        labels = []
        original = face.Canvas.text_steps
        def capture(canvas, x, baseline, text, style=1, ink=True):
            labels.append(text)
            yield from original(canvas, x, baseline, text, style, ink)
        with patch.object(face.Canvas, 'text_steps', capture):
            self.assertEqual(len(face.render(r.s.face_model())), 4736)
        self.assertIn('POLICY STATE', labels)
        self.assertIn('Blocked until you reset.', labels)
        self.assertIn('Reset: handle OFF, then ON.', labels)
        r.command('TRIP', 'policy.state_invalid.any_host_text')
        self.assertEqual(r.s.reason, 'rule.invalid')


class GlyphPaddingTests(unittest.TestCase):
    def test_checked_in_font_has_zero_row_padding(self):
        canvas, data = face.Canvas(), face.load_font()
        for style in range(face.FONT_STYLES):
            for code in face.GLYPHS:
                w, h, _, _, offset = canvas.glyph(code, style)
                stride = (w+7)//8; unused = stride*8-w
                for y in range(h):
                    self.assertEqual(data[offset+y*stride+stride-1] & ((1<<unused)-1), 0, (style, code, y))

    def test_nonzero_padding_cannot_touch_next_row_or_buffer_edge(self):
        original = face.load_font()
        dirty = bytearray(original)
        canvas = face.Canvas()
        glyphs = []
        for style in range(face.FONT_STYLES):
            for ch in face.GLYPHS:
                w, h, top, _, offset = canvas.glyph(ch, style)
                stride = (w+7)//8; unused = stride*8-w
                if unused:
                    for y in range(h): dirty[offset+y*stride+stride-1] |= (1<<unused)-1
                    glyphs.append((ch, style, w, h, top))
        for ch, style, w, h, top in glyphs:
            for ink in (True, False):
                for bottom in (64, face.HEIGHT):
                    expected, actual = face.Canvas(), face.Canvas()
                    if not ink: expected.buf[:] = actual.buf[:] = b'\x00'*4736
                    baseline = bottom-h-top
                    expected.text(face.WIDTH-w, baseline, ch, style, ink)
                    with patch.object(face, '_font', bytes(dirty)):
                        actual.text(face.WIDTH-w, baseline, ch, style, ink)
                    self.assertEqual(actual.buf, expected.buf, (ch, style, ink, bottom))
