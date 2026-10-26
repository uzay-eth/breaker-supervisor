"""v0.2 faces: copy/tiers, display timing and local-only authority; SIM inputs only."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from breaker import config, face
from breaker.core import APPROVED_MS, NOT_SENT_MS, HOLD_MS
from breaker.canonical import payout, digest
from face_host import render as render_host
from helpers import Rig
from tools.render_faces_v02 import models


def labels(model):
    result = []
    original = face.Canvas.text_steps
    def capture(canvas, x, baseline, text, style=1, ink=True):
        result.append(text)
        yield from original(canvas, x, baseline, text, style, ink)
    with patch.object(face.Canvas, 'text_steps', capture): face.render(model)
    return result


class FacesV02RendererTests(unittest.TestCase):
    def test_tiers_sizes_and_character_budgets(self):
        self.assertEqual({k: v[1:] for k, v in face.TIERS.items()},
                         {'XL': (40, 12), 'L': (24, 21), 'S': (16, 30)})
        for tier, (_, _, budget) in face.TIERS.items():
            c = face.Canvas()
            list(c.line_steps(8, 64, 'i'*budget, tier))
            with self.assertRaisesRegex(ValueError, 'face_char_budget'):
                list(c.line_steps(8, 64, 'i'*(budget+1), tier))
            self.assertEqual(c.width('i'*budget, face.TIERS[tier][0]),
                             sum(c.glyph('i', face.TIERS[tier][0])[3] for _ in range(budget)))

    def test_pixel_overflow_is_rejected_even_within_char_budget(self):
        with self.assertRaisesRegex(ValueError, 'face_line_width'):
            list(face.Canvas().line_steps(8, 64, 'W'*12, 'XL'))

    def test_all_v02_faces_and_every_fixed_error_fit(self):
        expected = {'armed','held_1840','approved_1840','held_18400','not_sent_18400',
                    'tripped','off','starting','setup'}
        frames = models()
        self.assertEqual(set(frames), expected)
        for model in frames.values():
            self.assertEqual(len(face.render(model)), 4736)
            self.assertEqual(len(render_host(model)), 4736)
        for reason in face.REASONS:
            self.assertEqual(len(face.render(dict(frames['tripped'], reason=reason))), 4736)

    def test_fixed_copy_payee_and_log_tag(self):
        frames = models()
        self.assertIn('Hold the button 1 s to send.', labels(frames['held_1840']))
        self.assertIn('to acct ••4471', labels(frames['held_1840']))
        self.assertIn('Log #'+digest(payout(184000))[:4], labels(frames['held_1840']))
        self.assertIn('to acct ••4471 · released once', labels(frames['approved_1840']))
        self.assertIn('to acct ••4471 · not sent', labels(frames['not_sent_18400']))
        self.assertIn('Mail: mass delete', labels(frames['tripped']))
        for model in frames.values():
            text = ' '.join(labels(model))
            self.assertNotIn('OFF to deny', text)
            self.assertNotIn('receipt', text.lower())

    def test_no_caller_label_or_linux_count_reaches_face(self):
        frames = models()
        model = dict(frames['tripped'], count=470, text='agent says send 999', reason='send 999')
        text = labels(model)
        self.assertIn('SUPERVISOR ERROR', text)
        self.assertNotIn('470', ' '.join(text)); self.assertNotIn('999', ' '.join(text))
        for key, value in (('destination', 'agent says send'), ('amount', '184000'),
                           ('amount', True), ('sha256', 'short')):
            with self.assertRaises(ValueError): face.render(dict(frames['held_1840'], **{key:value}))

    def test_hardware_header_cannot_be_overridden_by_model_or_sim_flag(self):
        for model in models().values():
            self.assertIn('OPEN BENCH', labels(dict(model, bench=False, header='DEMO', sim=True)))
            self.assertNotIn('DEMO', labels(model))
            with self.assertRaises(TypeError): face.render(model, sim=True)

    def test_host_overlay_changes_only_reserved_header_pixels(self):
        for model in models().values():
            hardware, host = face.render(model), render_host(model)
            self.assertNotEqual(hardware, host)
            for y in range(face.HEIGHT):
                for byte in range(face.STRIDE):
                    if y >= 24 or byte < 20:
                        self.assertEqual(hardware[y*37+byte], host[y*37+byte])

    def test_design_setup_code_is_fixed_and_never_a_uart_mode(self):
        r = Rig(); r.arm()
        model = dict(r.s.face_model(), mode='SETUP', code='evil', text='pair now')
        self.assertIn('Code 4817', labels(model)); self.assertNotIn('evil', labels(model))
        for verb in ('SETUP', 'PAIR', 'APPROVED', 'RELEASE', 'RESTORE'):
            r.command(verb)
            self.assertFalse(r.s.enabled); self.assertFalse(r.confirms())
            self.assertNotEqual(r.s.face_model()['mode'], 'SETUP')

    def test_demo_overlay_is_absent_from_exact_flash_payload(self):
        path = Path(__file__).resolve().parents[1]/'provision/flash.py'
        spec = importlib.util.spec_from_file_location('faces_v02_flash', path)
        flash = importlib.util.module_from_spec(spec); spec.loader.exec_module(flash)
        with tempfile.TemporaryDirectory() as directory:
            manifest = flash.stage(Path(directory))
            self.assertFalse(any('face_host' in name or 'render_faces' in name for name in manifest))
            for name in manifest:
                if name.endswith('.py'):
                    data = (Path(directory)/name).read_text()
                    self.assertNotIn('DEMO', data, name)
                    self.assertNotIn('face_host', data, name)
            # A reused stage cannot smuggle a host overlay into the recursive upload.
            (Path(directory)/'breaker/face_host.py').write_text('HEADER = "DEMO"\n')
            with self.assertRaisesRegex(ValueError, 'unexpected file in flash package'):
                flash.stage(Path(directory))


class FacesV02TimingTests(unittest.TestCase):
    def approved(self):
        r = Rig(); r.arm(); r.hold(); r.panel(); r.release(); r.press()
        self.assertEqual(len(r.confirms()), 1)
        self.assertEqual(r.s.face_model()['mode'], 'APPROVED')
        return r

    def expired(self):
        r = Rig(); r.arm(); r.hold(1840000); r.panel(); r.step(HOLD_MS-10, heartbeat=True)
        self.assertEqual(r.s.face_model()['mode'], 'HELD')
        r.step(10)
        self.assertEqual(r.s.face_model()['mode'], 'NOT SENT')
        return r

    def test_approved_dwell_starts_only_when_matching_face_is_visible(self):
        r = self.approved(); r.step(4000, heartbeat=True)
        self.assertEqual(r.s.face_model()['mode'], 'APPROVED')
        r.s.panel_done(r.s.face_generation-1, r.now)
        self.assertIsNone(r.s.notice_until)
        r.panel(); started = r.now; r.step(APPROVED_MS-10, heartbeat=True)
        self.assertEqual(r.s.face_model()['mode'], 'APPROVED')
        r.s.panel_done(r.s.face_generation, r.now)
        self.assertEqual(r.s.notice_until, started+APPROVED_MS)  # No extension.
        r.step(10)
        self.assertEqual(r.s.face_model()['mode'], 'ARMED')
        self.assertEqual(len(r.confirms()), 1)

    def test_sixty_second_expiry_then_five_second_not_sent(self):
        r = self.expired()
        self.assertIsNone(r.s.pending); self.assertFalse(r.confirms())
        self.assertEqual(r.s.face_model()['amount'], 1840000)
        self.assertTrue(any(e[0]=='DENIED' and e[-1]=='hold.timeout' for e in r.events))
        r.panel(); r.step(NOT_SENT_MS-10, heartbeat=True)
        self.assertEqual(r.s.face_model()['mode'], 'NOT SENT')
        r.step(10)
        self.assertEqual(r.s.face_model()['mode'], 'ARMED')
        self.assertIsNone(r.s.face_model()['amount'])

    def test_deny_does_not_invent_expiry_notice(self):
        r = Rig(); r.arm(); ticket, _ = r.hold(); r.command('DENY', ticket)
        self.assertEqual(r.s.face_model()['mode'], 'ARMED')
        self.assertIsNone(r.s.notice)

    def test_new_hold_preempts_notice_and_needs_fresh_confirmation(self):
        r = self.approved(); r.hold(1840000); r.panel(); r.press(1200)
        self.assertEqual(r.s.face_model()['mode'], 'HELD')
        self.assertIsNone(r.s.notice); self.assertEqual(len(r.confirms()), 1)
        r.release(); r.press()
        self.assertEqual(len(r.confirms()), 2)
        self.assertEqual(r.confirms()[-1][4], digest(payout(1840000)))

    def test_safety_events_preempt_both_notices(self):
        for make in (self.approved, self.expired):
            for action in ('OFF', 'INVALID', 'CUT', 'TRIP', 'host.timeout'):
                r = make(); before = len(r.confirms())
                if action in ('OFF','INVALID'):
                    r.inputs[:2] = [1,0] if action=='OFF' else [0,0]; r.step(10)
                elif action=='host.timeout': r.step(3000)
                else: r.command(action, 'mail.mass_delete')
                self.assertIsNone(r.s.notice, action)
                self.assertEqual(r.s.face_model()['mode'], 'OFF' if action=='OFF' else 'TRIPPED')
                self.assertEqual(len(r.confirms()), before)

    def test_starting_never_arms_without_live_session_and_handle_cycle(self):
        r = Rig()
        self.assertEqual(r.s.face_model()['mode'], 'STARTING')
        r.inputs[:2] = [0,1]; r.step(50)
        self.assertEqual(r.s.face_model()['mode'], 'STARTING')
        r.command('PING'); r.panel(); r.step(50)
        self.assertEqual(r.s.face_model()['mode'], 'TRIPPED')
        self.assertFalse(r.s.enabled)
        r.arm(); self.assertEqual(r.s.face_model()['mode'], 'ARMED')

    def test_notices_do_not_change_uart_mode_or_replay_authority(self):
        for r in (self.approved(), self.expired()):
            before = len(r.confirms())
            self.assertEqual(r.s._state()[0], 'ARMED')
            self.assertEqual(r.s._state()[4:6], (0, '-'))
            r.command('HELLO'); r.command('STATUS'); r.release(); r.press()
            self.assertEqual(len(r.confirms()), before)


class RingTests(unittest.TestCase):
    def test_dim_steady_armed_and_pulsing_amber(self):
        r = Rig(); self.assertEqual(r.s.ring_levels(), (0,0,0))
        r.arm(); self.assertEqual(r.s.ring_levels(), (0, config.RING_ARMED, 0))
        r.step(1000, heartbeat=True); self.assertEqual(r.s.ring_levels(), (0, config.RING_ARMED, 0))
        r.hold(); low = r.s.ring_levels(); r.step(config.RING_PULSE_MS//2, heartbeat=True)
        high = r.s.ring_levels()
        self.assertGreater(high[0], low[0]); self.assertGreater(high[1], low[1])
        self.assertEqual((low[2], high[2]), (0,0))
        r.step(config.RING_PULSE_MS//2, heartbeat=True); self.assertEqual(r.s.ring_levels(), low)

    def test_approved_flash_red_trip_and_off_override(self):
        r = Rig(); r.arm(); r.hold(); r.panel(); r.release(); r.press()
        self.assertEqual(r.s.ring_levels(), (0, config.RING_FLASH, 0))
        r.step(config.RING_FLASH_MS//2); self.assertEqual(r.s.ring_levels(), (0,0,0))
        r.command('TRIP','mail.mass_delete'); self.assertEqual(r.s.ring_levels(), (config.RING_RED,0,0))
        r.inputs[:2] = [1,0]; r.step(10); self.assertEqual(r.s.ring_levels(), (0,0,0))
