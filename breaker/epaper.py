"""Cooperative Waveshare 2.9 B/W V1/V2 full-refresh drivers.

Command/parameter/timing provenance (checked 2026-10-25):
docs/PANEL-SOURCES.md in this repository. Vendor waveform license: EPD-LICENSE.txt.
No controller autodetection; physical arrival qualification selects the profile.
"""
from .face import WIDTH, HEIGHT, STRIDE

V1_LUT = (
    b"\x50\xaa\x55\xaa\x11\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\xff\xff\x1f\x00\x00\x00\x00\x00\x00\x00"
)
V2_LUT = (
    b"\x80\x66\x00\x00\x00\x00\x00\x00\x40\x00\x00\x00\x10\x66\x00\x00"
    b"\x00\x00\x00\x00\x20\x00\x00\x00\x80\x66\x00\x00\x00\x00\x00\x00"
    b"\x40\x00\x00\x00\x10\x66\x00\x00\x00\x00\x00\x00\x20\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x14\x08\x00\x00"
    b"\x00\x00\x02\x0a\x0a\x00\x0a\x0a\x00\x01\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x14\x08\x00\x01\x00\x00\x01\x00\x00\x00\x00\x00"
    b"\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x44\x44\x44\x44\x44\x44\x00\x00\x00\x22\x17\x41\x00\x32\x36"
)


class SSD1680:
    UPDATE = b"\xc7"
    TERMINATE = False

    def __init__(self, spi, cs, dc, reset, busy, busy_active=1, timeout=10000):
        self.spi, self.cs, self.dc, self.reset, self.busy = spi, cs, dc, reset, busy
        self.busy_active, self.timeout = busy_active, timeout
        self.ready = False
        self.cs.value(1)

    def command(self, command, data=b""):
        self.dc.value(0); self.cs.value(0)
        self.spi.write(bytes([command])); self.cs.value(1)
        if data:
            self.data(data)

    def data(self, data):
        self.dc.value(1); self.cs.value(0)
        self.spi.write(data); self.cs.value(1)

    def delay(self, now, ms):
        started = now()
        while now()-started < ms:
            yield None

    def wait_idle(self, now, require_assert=False):
        started = now()
        seen_busy = False
        while True:
            busy = self.busy.value() == self.busy_active
            seen_busy = seen_busy or busy
            if not busy and (seen_busy or not require_assert):
                return
            if now()-started >= self.timeout:
                raise OSError("epd_busy_timeout")
            yield None

    def initialize(self, now):
        # V2 vendor reset(): 50 / 2 / 50 ms; BUSY active HIGH.
        self.reset.value(1); yield from self.delay(now, 50)
        self.reset.value(0); yield from self.delay(now, 2)
        self.reset.value(1); yield from self.delay(now, 50)
        yield from self.wait_idle(now)
        self.command(0x12)
        # Extra 10 ms scheduling guard, not a replacement for BUSY.
        yield from self.delay(now, 10)
        yield from self.wait_idle(now)
        for cmd, data in ((0x01, b"\x27\x01\x00"), (0x11, b"\x03"),
                          (0x44, b"\x00\x0f"), (0x45, b"\x00\x00\x27\x01"),
                          (0x21, b"\x00\x80"), (0x4e, b"\x00"), (0x4f, b"\x00\x00")):
            self.command(cmd, data)
            yield None
        yield from self.wait_idle(now)
        self.command(0x32)
        for pos in range(0, 153, 64):
            self.data(V2_LUT[pos:min(pos+64, 153)])
            yield None
        yield from self.wait_idle(now)
        for cmd, data in ((0x3f, V2_LUT[153:154]), (0x03, V2_LUT[154:155]),
                          (0x04, V2_LUT[155:158]), (0x2c, V2_LUT[158:159])):
            self.command(cmd, data)
            yield None
        self.ready = True

    def display(self, landscape, now):
        if len(landscape) != STRIDE*HEIGHT:
            raise ValueError("epd_buffer")
        if not self.ready:
            yield from self.initialize(now)
        # Controller RAM is portrait: x=0..127, y=0..295. Rotate landscape 90deg.
        portrait = bytearray(b"\xff" * len(landscape))
        for py in range(WIDTH):
            for px in range(HEIGHT):
                sx, sy = WIDTH-1-py, px
                if not landscape[sy*STRIDE+sx//8] & (128 >> (sx%8)):
                    portrait[py*16+px//8] &= 255 ^ (128 >> (px%8))
            if py % 4 == 3:
                yield None
        self.command(0x4e, b"\x00")
        self.command(0x4f, b"\x00\x00")
        self.command(0x24)
        view = memoryview(portrait)
        for pos in range(0, len(portrait), 64):
            self.data(view[pos:pos+64])
            yield None
        self.command(0x22, self.UPDATE)
        self.command(0x20)
        if self.TERMINATE:
            self.command(0xff)
        # A disconnected/stuck-low BUSY must not falsely authorize confirmation.
        yield from self.wait_idle(now, require_assert=True)
        yield True


class IL3820(SSD1680):
    UPDATE = b"\xc4"
    TERMINATE = True

    def initialize(self, now):
        # V1 vendor reset(): 200 / 5 / 200 ms; no V2 commands or waveform.
        self.reset.value(1); yield from self.delay(now, 200)
        self.reset.value(0); yield from self.delay(now, 5)
        self.reset.value(1); yield from self.delay(now, 200)
        yield from self.wait_idle(now)
        for cmd, data in ((0x01, b"\x27\x01\x00"), (0x0c, b"\xd7\xd6\x9d"),
                          (0x2c, b"\xa8"), (0x3a, b"\x1a"), (0x3b, b"\x08"),
                          (0x11, b"\x03"), (0x32, V1_LUT),
                          (0x44, b"\x00\x0f"), (0x45, b"\x00\x00\x27\x01")):
            self.command(cmd, data)
            yield None
        self.ready = True


def make_panel(profile, *args, **kwargs):
    if profile == "V1_IL3820":
        return IL3820(*args, **kwargs)
    if profile == "V2_SSD1680":
        return SSD1680(*args, **kwargs)
    raise ValueError("unknown panel profile")
