import unittest
from breaker.canonical import payout, digest, to_hex
from breaker.core import Supervisor, HOLD_MS
from breaker.wire import encode
from helpers import Rig, BOOT


class SupervisorTests(unittest.TestCase):
    def test_boot_disabled_ring_off(self):
        s = Supervisor(BOOT)
        self.assertFalse(s.enabled)
        self.assertEqual(s.ring, "off")
        self.assertFalse(s.face_ready)

    def test_boot_on_cannot_arm(self):
        r = Rig(); r.inputs[:2] = [0, 1]; r.command("PING"); r.panel(); r.step(100)
        self.assertFalse(r.s.enabled)

    def test_arm_requires_off_on_and_live_panel(self):
        r = Rig(); r.arm()
        self.assertTrue(r.s.enabled)
        self.assertEqual((r.s.mode, r.s.ring, r.s.reset_epoch), ("ARMED", "green", 1))
        for missing in ("heartbeat", "panel"):
            q = Rig()
            if missing != "heartbeat": q.command("PING")
            q.step(50)
            if missing != "panel": q.panel()
            q.inputs[:2] = [0, 1]; q.step(50)
            self.assertFalse(q.s.enabled)
            q.command("PING"); q.panel(); q.step(50)
            self.assertFalse(q.s.enabled)

    def test_hold_release_exactly_once(self):
        r = Rig(); r.arm(); ticket, raw = r.hold()
        self.assertEqual((r.s.mode, r.s.ring), ("HELD", "amber"))
        r.panel(); r.release(); r.press(1030)
        self.assertFalse(r.confirms())
        r.step(10); r.collect()
        self.assertEqual([(e[3], e[4]) for e in r.confirms()], [(str(ticket), digest(raw))])
        r.step(1500, heartbeat=True)
        self.assertEqual(len(r.confirms()), 1)
        self.assertIsNone(r.s.pending)

    def test_hold_before_refresh_cannot_confirm(self):
        r = Rig(); r.arm(); r.hold(); r.press(1500)
        self.assertFalse(r.confirms())
        r.panel(); r.press(1500)
        self.assertFalse(r.confirms())
        r.release(); r.press()
        self.assertEqual(len(r.confirms()), 1)

    def test_stale_panel_generation(self):
        r = Rig(); r.arm(); old = r.s.face_generation; r.hold()
        r.s.panel_done(old, r.now)
        self.assertFalse(r.s.face_ready)
        r.release(); r.press(); self.assertFalse(r.confirms())

    def test_already_pressed_before_hold(self):
        r = Rig(); r.arm(); r.press(100); r.hold(); r.panel(); r.press(1500)
        self.assertFalse(r.confirms())
        r.release(); r.press(); self.assertEqual(len(r.confirms()), 1)

    def test_button_bounce_restarts_timing(self):
        r = Rig(); r.arm(); r.hold(); r.panel(); r.release(); r.press(900)
        r.inputs[2] = 1; r.step(10); r.inputs[2] = 0; r.step(900, heartbeat=True)
        self.assertFalse(r.confirms())
        r.step(140); self.assertEqual(len(r.confirms()), 1)

    def test_every_byte_change_cannot_release_original_hash(self):
        raw = payout(184000)
        for i in range(len(raw)):
            r = Rig(); r.arm()
            mutated = raw[:i] + bytes([raw[i] ^ 1]) + raw[i+1:]
            r.hold(raw=mutated, claimed=digest(raw)); r.panel(); r.release(); r.press()
            self.assertFalse(r.confirms(), i)
            self.assertFalse(r.s.enabled, i)

    def test_check_changed_valid_request_denies_original(self):
        r = Rig(); r.arm(); ticket, raw = r.hold(); r.panel(); r.release(); r.press(900)
        new = payout(184001)
        r.command("CHECK", ticket, digest(new), to_hex(new)); r.press(500)
        self.assertFalse(r.confirms())
        self.assertTrue(any(e[0] == "DENIED" and e[4] == digest(raw) for e in r.events))
        self.assertEqual(r.s.reason, "hash.mismatch")

    def test_check_cannot_release(self):
        r = Rig(); r.arm(); ticket, raw = r.hold()
        r.command("CHECK", ticket, digest(raw), to_hex(raw))
        self.assertFalse(r.confirms())
        self.assertIsNotNone(r.s.pending)

    def test_rehash_at_release_last_decision(self):
        r = Rig(); r.arm(); r.hold(); r.panel(); r.release()
        r.s.pending["canonical"] = payout(184001)
        r.press(); self.assertFalse(r.confirms()); self.assertFalse(r.s.enabled)

    def test_deny(self):
        r = Rig(); r.arm(); ticket, _ = r.hold(); r.command("DENY", ticket)
        r.panel(); r.release(); r.press()
        self.assertFalse(r.confirms()); self.assertIsNone(r.s.pending)
        self.assertEqual(r.s.mode, "ARMED")

    def test_hold_replacement_denies_both(self):
        r = Rig(); r.arm(); r.hold(); r.hold(184001)
        self.assertIsNone(r.s.pending); self.assertFalse(r.s.enabled)

    def test_hold_timeout_with_live_host(self):
        r = Rig(); r.arm(); r.hold(); r.panel(); r.step(HOLD_MS, heartbeat=True)
        self.assertIsNone(r.s.pending); self.assertFalse(r.confirms())
        self.assertTrue(any(e[0] == "DENIED" and e[-1] == "hold.timeout" for e in r.events))

    def test_timeout_wins_over_confirm(self):
        r = Rig(); r.arm(); r.hold(); r.panel(); r.release()
        r.step(HOLD_MS - 1040, heartbeat=True); r.press()
        self.assertFalse(r.confirms())

    def test_policy_trip_preserves_already_enabled_port(self):
        r = Rig(); r.arm(); r.hold(); r.command("TRIP", "mail.mass_delete")
        self.assertEqual((r.s.mode, r.s.ring), ("TRIPPED", "red"))
        self.assertTrue(r.s.enabled); self.assertIsNone(r.s.pending)
        r.command("PING"); r.panel(); r.release(); r.press()
        self.assertFalse(r.confirms()); self.assertTrue(r.s.latched)

    def test_policy_trip_never_restores(self):
        r = Rig(); r.arm(); r.command("CUT", "linux.cut"); r.command("TRIP", "mail.mass_delete")
        self.assertFalse(r.s.enabled)

    def test_trip_reset_only_off_then_on(self):
        r = Rig(); r.arm(); r.command("CUT", "linux.cut"); r.panel()
        r.command("HELLO"); r.command("PING"); r.command("STATUS"); r.step(50)
        self.assertFalse(r.s.enabled)
        r.inputs[:2] = [1, 0]; r.step(50)
        self.assertEqual(r.s.ring, "off"); self.assertFalse(r.s.enabled)
        r.inputs[:2] = [0, 1]; r.step(50)
        self.assertTrue(r.s.enabled); self.assertEqual(r.s.reset_epoch, 2)

    def test_off_bounce_does_not_authorize_reset(self):
        r = Rig(); r.arm(); r.command("CUT", "linux.cut")
        r.inputs[:2] = [1, 0]; r.step(10); r.inputs[:2] = [0, 1]; r.step(100)
        self.assertFalse(r.s.enabled)

    def test_transit_immediately_cuts_and_cancels(self):
        r = Rig(); r.arm(); r.hold(); r.inputs[:2] = [1, 1]; r.step(10)
        self.assertFalse(r.s.enabled); self.assertIsNone(r.s.pending); self.assertEqual(r.s.ring, "off")
        r.inputs[:2] = [0, 1]; r.step(50)
        self.assertFalse(r.s.enabled)

    def test_valid_off_transit_on_rearms(self):
        r = Rig(); r.arm(); r.command("CUT", "linux.cut")
        r.inputs[:2] = [1, 0]; r.step(50); r.inputs[:2] = [1, 1]; r.step(50)
        r.inputs[:2] = [0, 1]; r.step(50)
        self.assertTrue(r.s.enabled)

    def test_invalid_contacts(self):
        r = Rig(); r.arm(); r.inputs[:2] = [0, 0]; r.step(10)
        self.assertFalse(r.s.enabled); self.assertEqual(r.s.reason, "handle.invalid")

    def test_fault_latched(self):
        r = Rig(); r.arm(); r.hold(); r.inputs[3] = 0; r.step(10)
        self.assertFalse(r.s.enabled); self.assertIsNone(r.s.pending)
        r.inputs[3] = 1; r.step(50); self.assertFalse(r.s.enabled)

    def test_linux_restore_release_reset_forbidden(self):
        for verb in ("RELEASE", "RESTORE", "RESET", "ENABLE", "BUTTON", "HANDLE", "GPIO", "STATE", "READY"):
            r = Rig(); r.arm(); r.hold(); r.command(verb)
            self.assertFalse(r.s.enabled, verb); self.assertFalse(r.confirms(), verb)

    def test_wrong_session_and_replay(self):
        for kind in ("session", "replay"):
            r = Rig(); r.arm(); r.hold()
            if kind == "session": r.command("PING", boot="b"*32)
            else: r.command("PING", seq=r.seq)
            self.assertFalse(r.s.enabled); self.assertIsNone(r.s.pending)

    def test_single_frame_error_cancels_and_latches_without_cut(self):
        for bad in (b"\n", b"x\x00\n", b"x"*2048+b"\n", b"BRK1|HELLO|00000000\n"):
            r = Rig(); r.arm(); r.hold(); r.raw(bad)
            self.assertTrue(r.s.enabled); self.assertIsNone(r.s.pending)
            self.assertTrue(r.s.latched); self.assertFalse(r.confirms())

    def test_partial_frame_timeout(self):
        r = Rig(); r.arm(); r.hold(); r.raw(b"BRK1|"); r.step(1000)
        self.assertTrue(r.s.enabled); self.assertIsNone(r.s.pending)
        self.assertTrue(r.s.latched); self.assertFalse(r.confirms())

    def test_heartbeat_loss_and_ping_cannot_restore(self):
        r = Rig(); r.arm(); r.hold(); r.step(3000)
        self.assertFalse(r.s.enabled); self.assertIsNone(r.s.pending)
        r.command("PING"); self.assertFalse(r.s.enabled)

    def test_no_release_across_stalled_loop(self):
        r = Rig(); r.arm(); r.hold(); r.panel(); r.release(); r.press(100)
        r.now += 1500; r.s.sample(r.now, *r.inputs); r.collect()
        self.assertFalse(r.s.enabled); self.assertFalse(r.confirms())

    def test_panel_fault_cancels(self):
        r = Rig(); r.arm(); r.hold(); r.s.panel_failed(); r.collect()
        self.assertFalse(r.s.enabled); self.assertIsNone(r.s.pending)

    def test_backpressure_discards_release_and_cuts(self):
        s = Supervisor(BOOT)
        for _ in range(100): s.receive(encode("HELLO"), 0)
        self.assertLessEqual(len(s.outbox), 32); self.assertFalse(s.enabled)

    def test_tamper_enforced_outside_bench(self):
        r = Rig(bench=False); r.arm(); r.inputs[4] = 1; r.step(10)
        self.assertFalse(r.s.enabled); self.assertEqual(r.s.reason, "case.open")

    def test_reset_events_not_lost_between_pings(self):
        r = Rig(); r.arm(); r.events = []
        r.inputs[:2] = [1, 0]; r.step(50); r.inputs[:2] = [0, 1]; r.step(50)
        states = [e for e in r.events if e[0] == "STATE"]
        self.assertTrue(any(e[3] == "OFF" for e in states))
        self.assertTrue(any(e[3] == "ARMED" and e[-1] == "2" for e in states))
        numbers = [int(e[2]) for e in states]
        self.assertEqual(numbers, sorted(set(numbers)))
