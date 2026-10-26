"""SIM GPIO/SPI tests: controller transcripts and boot qualification gate."""
from contextlib import redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch
from breaker import config
from breaker.epaper import IL3820, SSD1680, V1_LUT, V2_LUT, make_panel
from breaker.panel_record import fingerprint, save
from test_face_panel import FakePin, FakeSPI


class DriverTests(unittest.TestCase):
    def panel(self, profile):
        self.now = 0
        self.spi = FakeSPI()
        self.busy = FakePin(0)
        return make_panel(profile, self.spi, FakePin(), FakePin(), FakePin(), self.busy, timeout=100)

    def drain_init(self, panel):
        for _ in panel.initialize(lambda: self.now):
            self.now += 5
            self.assertLess(self.now, 2000)

    def test_unknown_profile_has_no_spi_effect(self):
        with self.assertRaises(ValueError): self.panel('V3')
        self.assertEqual(self.spi.writes, [])

    def test_v1_initialization_uses_legacy_waveform_and_registers(self):
        p = self.panel('V1_IL3820'); self.drain_init(p)
        self.assertIsInstance(p, IL3820)
        self.assertTrue(p.ready)
        self.assertEqual(self.spi.writes, [b'\x01', b'\x27\x01\x00', b'\x0c', b'\xd7\xd6\x9d',
                         b'\x2c', b'\xa8', b'\x3a', b'\x1a', b'\x3b', b'\x08', b'\x11', b'\x03',
                         b'\x32', V1_LUT, b'\x44', b'\x00\x0f', b'\x45', b'\x00\x00\x27\x01'])
        self.assertEqual(len(V1_LUT), 30)
        self.assertGreaterEqual(self.now, 405)

    def test_v2_initialization_loads_full_waveform_before_voltage_settings(self):
        p = self.panel('V2_SSD1680'); self.drain_init(p)
        self.assertEqual(type(p), SSD1680)
        self.assertTrue(p.ready)
        i = self.spi.writes.index(b'\x32')
        self.assertEqual(b''.join(self.spi.writes[i+1:i+4]), V2_LUT[:153])
        self.assertEqual(self.spi.writes[i+4:], [b'\x3f', b'\x22', b'\x03', b'\x17', b'\x04', b'\x41\x00\x32', b'\x2c', b'\x36'])
        self.assertEqual(len(V2_LUT), 159)
        self.assertLessEqual(max(map(len, self.spi.writes)), 64)
        self.assertGreaterEqual(self.now, 112)

    def test_invalid_frame_never_writes(self):
        for profile in ('V1_IL3820', 'V2_SSD1680'):
            p = self.panel(profile)
            with self.assertRaises(ValueError): next(p.display(b'bad', lambda: self.now))
            self.assertEqual(self.spi.writes, [])

    def test_both_profiles_rotate_corners_and_require_busy_cycle(self):
        for profile, suffix in (('V1_IL3820', [b'\x22', b'\xc4', b'\x20', b'\xff']),
                                ('V2_SSD1680', [b'\x22', b'\xc7', b'\x20'])):
            p = self.panel(profile); p.ready = True
            frame = bytearray(b'\xff'*4736)
            frame[0] = 0x7f  # landscape top-left -> portrait row 295 bit 7
            frame[-1] = 0xfe  # landscape bottom-right -> portrait row 0 bit 0
            job = p.display(frame, lambda: self.now)
            for _ in range(200):
                next(job)
                if self.spi.writes[-len(suffix):] == suffix: break
            self.assertEqual(self.spi.writes[-len(suffix):], suffix)
            i = self.spi.writes.index(b'\x24')
            ram = b''.join(self.spi.writes[i+1:-len(suffix)])
            self.assertEqual(len(ram), 4736)
            self.assertEqual(ram[295*16], 0x7f)
            self.assertEqual(ram[15], 0xfe)
            self.assertEqual(sum(8-b.bit_count() for b in ram), 2)
            self.assertIsNone(next(job))  # stuck low has not completed
            self.busy.value(1); self.assertIsNone(next(job))
            self.busy.value(0); self.assertIs(next(job), True)

    def test_both_profiles_reject_stuck_busy(self):
        for profile in ('V1_IL3820', 'V2_SSD1680'):
            for level in (0, 1):
                p = self.panel(profile); self.busy.value(level)
                job = p.wait_idle(lambda: self.now, require_assert=True)
                next(job); self.now = 100
                with self.assertRaises(OSError): next(job)


class FirmwareGateTests(unittest.TestCase):
    def test_boot_uses_only_current_local_qualification(self):
        class Pin(FakePin):
            OUT, IN, PULL_UP, IRQ_RISING, IRQ_FALLING = 1, 2, 3, 4, 8
            def __init__(self, number, *args, value=0): super().__init__(value)
            def irq(self, **kwargs): pass
        machine = SimpleNamespace(Pin=Pin, PWM=lambda *a, **k: SimpleNamespace(duty_u16=lambda _: None, deinit=lambda: None), UART=lambda *a, **k: None, SPI=lambda *a, **k: None,
                                  WDT=lambda **k: None, disable_irq=lambda: 0, enable_irq=lambda _: None)
        class ReachedLoop(Exception): pass
        class Core:
            failed = False
            def __init__(self, *a, **k): pass
            def panel_failed(self): Core.failed = True
            def sample(self, *a): raise ReachedLoop()
        namespace = {'__name__': 'SIM_firmware_test'}
        with redirect_stdout(io.StringIO()), patch.dict(sys.modules, {'machine': machine, 'micropython': SimpleNamespace(alloc_emergency_exception_buf=lambda _: None)}), patch.object(config, 'MAINTENANCE_AT_BOOT', True, create=True):
            source = Path(__file__).resolve().parents[1]/'firmware'/'main.py'
            exec(compile(source.read_text(), str(source), 'exec'), namespace)
        namespace.update(Supervisor=Core, Clock=lambda: SimpleNamespace(now=lambda: 0), load_font=lambda: None)
        selected = []
        namespace['make_panel'] = lambda profile, *a, **k: selected.append(profile)
        with tempfile.TemporaryDirectory() as directory, patch.object(config, 'PANEL_RECORD', str(Path(directory)/'panel.json')):
            for state in ('missing', 'stale', 'V1_IL3820', 'V2_SSD1680'):
                selected.clear(); Core.failed = False
                if state != 'missing':
                    save(config.PANEL_RECORD, 'V1_IL3820' if state == 'stale' else state,
                         'SIM test identity', '0'*64 if state == 'stale' else fingerprint())
                with redirect_stdout(io.StringIO()), self.assertRaises(ReachedLoop): namespace['run']()
                self.assertEqual(Core.failed, state in ('missing', 'stale'))
                self.assertEqual(selected, [] if Core.failed else [state])
                self.assertEqual(namespace['cut'].value(), 0)
