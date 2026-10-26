import struct
import tempfile
import unittest
from pathlib import Path
from breaker.face import render, amount_text
from breaker.epaper import SSD1680
from png import write_png
from face_host import render as render_host
from helpers import Rig


class FaceTests(unittest.TestCase):
    def test_all_faces_and_maximum_amount_fit(self):
        r = Rig(); models = [r.s.face_model()]
        r.arm(); models.append(r.s.face_model())
        for amount in (1, 184000, 99999999):
            ticket, _ = r.hold(amount); models.append(r.s.face_model()); r.command("DENY", ticket)
        r.command("TRIP", "mail.mass_delete"); models.append(r.s.face_model())
        for model in models:
            for sim in (False, True):
                with self.subTest(mode=model["mode"], sim=sim):
                    self.assertEqual(len((render_host(model) if sim else render(model))), 4736)

    def test_money_is_integer_formatted(self):
        self.assertEqual(amount_text(184000), "$1,840.00")
        self.assertEqual(amount_text(1), "$0.01")
        self.assertEqual(amount_text(99999999), "$999,999.99")

    def test_png_native_and_8x_are_1bit(self):
        r = Rig(); r.arm(); buf = render_host(r.s.face_model())
        with tempfile.TemporaryDirectory() as directory:
            for scale in (1, 8):
                p = Path(directory)/("SIM-test%d.png" % scale)
                write_png(buf, p, scale)
                header = p.read_bytes()
                self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(struct.unpack(">II", header[16:24]), (296*scale, 128*scale))
                self.assertEqual(header[24], 1)


class FakePin:
    def __init__(self, value=0): self.v = value
    def value(self, value=None):
        if value is not None: self.v = value
        return self.v


class FakeSPI:
    def __init__(self): self.writes = []
    def write(self, data): self.writes.append(bytes(data))


class PanelTests(unittest.TestCase):
    def setup_panel(self):
        self.now = 0
        self.busy = FakePin(0)
        self.spi = FakeSPI()
        self.panel = SSD1680(self.spi, FakePin(), FakePin(), FakePin(), self.busy, timeout=100)

    def test_stuck_low_busy_never_reports_complete(self):
        self.setup_panel()
        job = self.panel.wait_idle(lambda: self.now, require_assert=True)
        for _ in range(10): next(job); self.now += 10
        with self.assertRaises(OSError): next(job)

    def test_busy_must_assert_and_deassert(self):
        self.setup_panel()
        job = self.panel.wait_idle(lambda: self.now, require_assert=True)
        next(job); self.now += 10
        self.busy.value(1); next(job); self.now += 10
        self.busy.value(0)
        with self.assertRaises(StopIteration): next(job)

    def test_stuck_high_busy_times_out(self):
        self.setup_panel(); self.busy.value(1)
        job = self.panel.wait_idle(lambda: self.now)
        next(job); self.now += 100
        with self.assertRaises(OSError): next(job)

    def test_spi_ram_chunks_bounded(self):
        self.setup_panel(); self.panel.ready = True
        job = self.panel.display(bytes([255])*4736, lambda: self.now)
        # Drive rotation / RAM upload until update command is issued.
        for _ in range(500):
            next(job)
            if self.spi.writes[-1:] == [b"\x20"]: break
        self.assertEqual(self.spi.writes[-1], b"\x20")
        self.assertLessEqual(max(map(len, self.spi.writes)), 64)
        self.busy.value(1); next(job); self.busy.value(0)
        self.assertIs(next(job), True)
