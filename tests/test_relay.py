"""Relay contract and HIL recorder tests. All GPIO/UART/measurements here are SIM."""
import importlib.util
import io
import json
from pathlib import Path
import runpy
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

from breaker import config
from helpers import Rig

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('relay_hil', ROOT/'provision/hil.py')
hil = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hil)


class RelayFirmwareTests(unittest.TestCase):
    def test_shipped_relay_contract(self):
        self.assertEqual(config.CUT_DRIVER, 'HILETGO_H_2XNPN')
        self.assertEqual(config.PINS['cut_enable'], 18)
        self.assertEqual(config.PINS['cut_fault_n'], 14)
        self.assertFalse(config.CUT_FAULT_PRESENT)
        self.assertEqual(config.RELAY_SETTLE_MS, 100)

    def test_actual_boot_script_first_gpio_is_cut_low_before_handle_reads(self):
        events = []
        class Pin:
            OUT, IN, PULL_UP = 1, 2, 3
            def __init__(self, number, mode, pull=None, value=None):
                self.number = number
                events.append(('init', number, mode, value))
            def value(self):
                events.append(('read', self.number))
                return 1
        with patch.dict(sys.modules, machine=types.SimpleNamespace(Pin=Pin)), \
             patch.object(hil.time, 'sleep_ms', create=True), \
             patch.object(config, 'MAINTENANCE_AT_BOOT', False, create=True):
            runpy.run_path(str(ROOT/'firmware/boot.py'))
        self.assertEqual(events[0], ('init', 18, Pin.OUT, 0))
        self.assertTrue(all(e[3] == 0 for e in events if e[0]=='init' and e[2]==Pin.OUT))
        self.assertTrue(any(e[0]=='read' for e in events))

    def test_cut_never_waits_for_observation_settle_and_uart_cannot_reenable(self):
        r = Rig(); r.arm(); r.hold(); now = r.now
        r.command('CUT', 'linux.cut')
        self.assertEqual(r.now, now)
        self.assertFalse(r.s.enabled); self.assertIsNone(r.s.pending)
        r.command('HELLO'); r.command('PING'); r.step(config.RELAY_SETTLE_MS)
        self.assertFalse(r.s.enabled); self.assertFalse(r.confirms())

    def test_unsafe_input_cuts_without_debounce_bounce_cannot_chatter_rearm(self):
        for unsafe in ([1, 0], [1, 1], [0, 0]):
            with self.subTest(unsafe=unsafe):
                r = Rig(); r.arm(); r.inputs[:2] = unsafe; r.step(10)
                self.assertFalse(r.s.enabled)
                for _ in range(3):
                    r.inputs[:2] = [0, 1]; r.step(20)
                    self.assertFalse(r.s.enabled)
                    r.inputs[:2] = [1, 0]; r.step(20)
                    self.assertFalse(r.s.enabled)
                r.step(30)  # Stable OFF witness.
                r.inputs[:2] = [0, 1]; r.step(10); r.step(20)
                self.assertFalse(r.s.enabled)  # Only 20 ms since ON was observed.
                r.step(10)
                self.assertTrue(r.s.enabled)


class RelayHilTests(unittest.TestCase):
    def bench(self):
        b = hil.Bench.__new__(hil.Bench)
        b.file = io.StringIO(); b.lock = threading.Lock()
        return b

    def test_settle_records_design_wait_not_measured_latency(self):
        b = self.bench()
        with patch.object(hil.time, 'sleep') as sleep:
            b.settle()
        sleep.assert_called_once_with(0.1)
        record = json.loads(b.file.getvalue())
        self.assertEqual(record['kind'], 'observation settle')
        self.assertEqual(record['design_ms'], 100)
        self.assertNotIn('milliseconds', record)

    def test_timing_requires_actual_number_and_capture_and_preserves_endpoints(self):
        b = self.bench()
        with patch('builtins.input', side_effect=['17.25', 'SIM-scope-fixture trace A/B']):
            b.timing('SIM rail cut', 'GP18 falling', 'AGENT_5V below 0.4 V')
        record = json.loads(b.file.getvalue())
        self.assertEqual(record['milliseconds'], 17.25)
        self.assertEqual(record['start'], 'GP18 falling')
        self.assertEqual(record['end'], 'AGENT_5V below 0.4 V')
        self.assertEqual(record['capture'], 'SIM-scope-fixture trace A/B')

    def test_unmeasured_nonfinite_negative_rail_and_missing_capture_rejected(self):
        for answers in ([''], ['nan'], ['inf'], ['-1'], ['10', '']):
            b = self.bench()
            with self.subTest(answers=answers), patch('builtins.input', side_effect=answers):
                with self.assertRaises(ValueError): b.timing('SIM', 'start', 'end')
            self.assertEqual(b.file.getvalue(), '')

    def test_signed_packet_offset_is_not_clipped_or_substituted(self):
        b = self.bench()
        with patch('builtins.input', side_effect=['-0.5', 'SIM-packet-fixture']):
            b.timing('SIM packet', 'pole A opening', 'last packet', allow_negative=True)
        self.assertEqual(json.loads(b.file.getvalue())['milliseconds'], -0.5)

    def test_disconnected_phase_keeps_meter_recording_without_uart(self):
        b = self.bench(); b.client = Mock(); client = b.client
        b.stop = False; b.error = None; b.thread = Mock()
        b.thread.is_alive.return_value = False
        b.disconnect()
        self.assertTrue(b.stop); self.assertIsNone(b.client)
        b.thread.join.assert_called_once_with(timeout=2)
        client.close.assert_called_once_with()
        with patch('builtins.input', return_value='0.02'):
            b.voltage('SIM absent rail', 'DESIGN <0.4 V', 0.4)
        self.assertEqual(json.loads(b.file.getvalue())['volts'], 0.02)
        b.close()  # No UART access or fabricated handshake with Pico absent.
        self.assertTrue(b.file.closed)

    def test_absent_phase_cuts_then_disconnects_before_removal_and_checks_loaded_rail(self):
        b = Mock(); b.command.return_value = 42
        # A heartbeat may already have advanced last_seq beyond the CUT reply.
        state = ['']*13; state[5] = '0'; state[11] = '43'
        b.wait.side_effect = lambda predicate: self.assertTrue(predicate(types.SimpleNamespace(state=state)))
        b.observe.side_effect = lambda *args: self.assertTrue(b.disconnect.called)
        hil.relay_absent_checks(b)
        self.assertEqual(b.mock_calls[0].args, ('CUT', 'linux.cut'))
        names = [c[0] for c in b.mock_calls]
        self.assertLess(names.index('settle'), names.index('disconnect'))
        self.assertLess(names.index('disconnect'), names.index('observe'))
        b.voltage.assert_any_call('AGENT_5V (Pico absent, handle ON)',
                                 'after discharge, host/peer powered; <0.4 V DESIGN', 0.4)
        self.assertEqual(b.observe.call_args.args[0], 'finish isolated')

    def test_off_rail_failing_design_screen_is_recorded_and_rejected(self):
        b = self.bench()
        with patch('builtins.input', return_value='1.2'):
            with self.assertRaises(RuntimeError): b.voltage('SIM absent rail', 'DESIGN <0.4 V', 0.4)
        self.assertEqual(json.loads(b.file.getvalue())['volts'], 1.2)

    def test_cleanup_stops_uart_even_if_final_cut_write_fails(self):
        b = self.bench(); b.client = Mock(); client = b.client
        b.stop = False; b.error = None; b.thread = Mock()
        b.thread.is_alive.return_value = False
        client.command.side_effect = OSError('SIM failed write')
        with self.assertRaises(OSError): b.close()
        self.assertTrue(b.stop); self.assertIsNone(b.client)
        client.close.assert_called_once_with()
        self.assertTrue(b.file.closed)
