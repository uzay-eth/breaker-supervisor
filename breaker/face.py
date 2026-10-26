"""v0.2 faces: fixed copy and locally parsed payment fields only."""
from .canonical import DEFAULT_ALLOWLIST, MAX_AMOUNT, is_hex

WIDTH, HEIGHT, STRIDE = 296, 128, 37
GLYPHS = ''.join(chr(c) for c in range(32, 127)) + "•·…"
FONT_STYLES = 8
TIERS = {"S": (5, 16, 30), "L": (6, 24, 21), "XL": (7, 40, 12)}
_font = None
REASONS = {
    "boot.locked": "RESET TO ARM", "hash.mismatch": "MISMATCH",
    "request.invalid": "INVALID REQUEST", "hold.busy": "REQUEST CONFLICT",
    "mail.mass_delete": "Mail: mass delete", "secret.exfiltration": "Secret blocked",
    "spend.cap": "Spend limit", "linux.cut": "Host requested cut",
    "policy.state_invalid": "POLICY STATE",
    "cut.fault": "CUT FAULT", "display.fault": "DISPLAY FAULT",
    "host.timeout": "HOST LOST", "handle.invalid": "CONTACT FAULT",
    "case.open": "CASE OPEN", "loop.stall": "LOOP STALLED",
    "input.edge": "CONTACT CHANGED", "input.invalid": "INPUT FAULT",
    "clock.invalid": "CLOCK FAULT", "hash.invalid": "HASH INVALID",
    "handle.transit": "Handle moving",
    "hold.integrity": "HOLD INTEGRITY", "ticket.invalid": "HOLD TICKET ERROR",
    "command.invalid": "HOST COMMAND", "rule.invalid": "HOST RULE ERROR",
    "session.invalid": "HOST SESSION", "sequence.invalid": "HOST SEQUENCE",
    "sequence.replay": "HOST REPLAY", "rx.burst": "UART RX OVERFLOW",
    "io.backpressure": "UART TX OVERFLOW", "uart.disconnect": "HOST LOST",
    "frame_crc": "UART ERROR", "frame_ascii": "UART ERROR",
    "frame_shape": "UART ERROR", "frame_size": "UART ERROR",
    "frame_timeout": "UART TIMEOUT", "sim.stopped": "SIM STOPPED",
}


def load_font():
    global _font
    if _font is None:
        path = __file__.rsplit("/", 1)[0] + "/font.bin"
        with open(path, "rb") as file:
            _font = file.read()
        if _font[:7] != b"BRKF2" + bytes((FONT_STYLES, len(GLYPHS))):
            raise ValueError("font")
    return _font


class Canvas:
    def __init__(self):
        self.buf = bytearray(b"\xff" * (STRIDE * HEIGHT))

    def pixel(self, x, y, ink=True):
        if not 0 <= x < WIDTH or not 0 <= y < HEIGHT:
            raise ValueError("face_overflow")
        offset, mask = y * STRIDE + x // 8, 128 >> (x % 8)
        if ink:
            self.buf[offset] &= 255 ^ mask
        else:
            self.buf[offset] |= mask

    def rect(self, x, y, w, h, ink=True):
        for _ in self.rect_steps(x, y, w, h, ink):
            pass

    def rect_steps(self, x, y, w, h, ink=True):
        if w <= 0 or h <= 0 or not (0 <= x < x+w <= WIDTH and 0 <= y < y+h <= HEIGHT):
            raise ValueError("face_overflow")
        # Byte masks avoid thousands of Python pixel calls in a filled band.
        first, last = x//8, (x+w-1)//8
        for yy in range(y, y+h):
            for byte in range(first, last+1):
                left = max(x, byte*8)-byte*8
                right = min(x+w, byte*8+8)-byte*8
                mask = (255 >> left) & (255 << (8-right) & 255)
                pos = yy*STRIDE+byte
                if ink: self.buf[pos] &= 255 ^ mask
                else: self.buf[pos] |= mask
            if (yy-y) % 4 == 3:
                yield None
        yield None

    def glyph(self, ch, style):
        data = load_font()
        c = GLYPHS.find(ch)
        if len(ch) != 1 or c < 0 or not 0 <= style < FONT_STYLES:
            raise ValueError("face_character")
        pos = 7 + (style*len(GLYPHS)+c)*6
        w, h, top, advance = data[pos:pos+4]
        offset = (data[pos+4] << 8) | data[pos+5]
        return w, h, top-64, advance, 7+FONT_STYLES*len(GLYPHS)*6+offset

    def width(self, text, style=1):
        return sum(self.glyph(ch, style)[3] for ch in text)

    def text(self, x, baseline, text, style=1, ink=True):
        for _ in self.text_steps(x, baseline, text, style, ink):
            pass

    def text_steps(self, x, baseline, text, style=1, ink=True):
        """One glyph per chunk; copy row bytes instead of calling pixel per bit."""
        data = load_font()
        for ch in text:
            w, h, top, advance, offset = self.glyph(ch, style)
            if not (0 <= x and x+w <= WIDTH and 0 <= baseline+top and baseline+top+h <= HEIGHT):
                raise ValueError("face_overflow")
            stride, shift = (w+7)//8, x%8
            for yy in range(h):
                pos = (baseline+top+yy)*STRIDE + x//8
                for byte in range(stride):
                    mask = data[offset+yy*stride+byte]
                    if byte == stride-1:
                        mask &= 255 << (stride*8-w)  # Ignore unused font padding, even at row/buffer edge.
                    first = mask >> shift
                    if first:
                        if ink: self.buf[pos+byte] &= 255 ^ first
                        else: self.buf[pos+byte] |= first
                    spill = (mask << (8-shift)) & 255
                    if spill:
                        if ink: self.buf[pos+byte+1] &= 255 ^ spill
                        else: self.buf[pos+byte+1] |= spill
            x += advance
            yield None

    def right(self, x, baseline, text, style=1, ink=True):
        for _ in self.right_steps(x, baseline, text, style, ink):
            pass

    def right_steps(self, x, baseline, text, style=1, ink=True):
        yield from self.text_steps(x-self.width(text, style), baseline, text, style, ink)

    def line_steps(self, x, baseline, text, tier, ink=True, right=False):
        style, _, budget = TIERS[tier]
        if type(text) is not str or len(text) > budget:
            raise ValueError("face_char_budget")
        width = self.width(text, style)
        left = x-width if right else x
        if left < 8 or left+width > WIDTH-8:
            raise ValueError("face_line_width")
        yield from self.text_steps(left, baseline, text, style, ink)


def amount_text(cents):
    # No floats or locale dependency in money display.
    if type(cents) is not int or not 1 <= cents <= MAX_AMOUNT:
        raise ValueError("face_amount")
    dollars, cents = divmod(cents, 100)
    s = str(dollars)
    groups = []
    while s:
        groups.insert(0, s[-3:]); s = s[:-3]
    return "$" + ",".join(groups) + ".%02d" % cents


def inverted_header(model):
    return model["mode"] in ("HELD", "TRIPPED", "STARTING")


def payment_fields(model):
    amount = amount_text(model["amount"])
    destination = model["destination"]
    if destination not in DEFAULT_ALLOWLIST.values() or not is_hex(model["sha256"], 64):
        raise ValueError("face_payment")
    return amount, "to " + destination


def render_steps(model):
    """Hardware renderer: fixed OPEN BENCH header; no caller-selectable branding."""
    c = Canvas()
    mode = model["mode"]
    if mode not in ("ARMED", "HELD", "APPROVED", "NOT SENT", "TRIPPED", "OFF", "STARTING", "SETUP"):
        raise ValueError("face_mode")
    inverted = inverted_header(model)
    if inverted:
        yield from c.rect_steps(0, 0, WIDTH, 26 if mode == "HELD" else 60)
    # The separate S header row accommodates the hardware marker without shrinking XL.
    if mode in ("ARMED", "HELD"):
        yield from c.line_steps(8, 18, "BREAKER" if mode == "ARMED" else "HELD · payment", "S", not inverted)
    yield from c.line_steps(288, 18, "OPEN BENCH", "S", not inverted, right=True)
    if mode == "ARMED":
        yield from c.line_steps(8, 65, "ARMED", "XL")
        yield from c.line_steps(8, 103, "Asks first. Handle down cuts.", "S")
    elif mode == "HELD":
        amount, payee = payment_fields(model)
        yield from c.line_steps(8, 69, amount, "XL")
        tag = "Log #" + model["sha256"][:4]
        if 8+c.width(payee, TIERS["L"][0])+8 > 288-c.width(tag, TIERS["S"][0]):
            raise ValueError("face_line_width")
        yield from c.line_steps(8, 98, payee, "L")
        yield from c.line_steps(288, 98, tag, "S", right=True)
        yield from c.rect_steps(8, 104, 280, 1)
        yield from c.line_steps(8, 123, "Hold the button 1 s to send.", "S")
    elif mode in ("APPROVED", "NOT SENT"):
        amount, payee = payment_fields(model)
        yield from c.line_steps(8, 58, mode, "XL")
        yield from c.line_steps(8, 89, amount, "L")
        yield from c.line_steps(8, 119, payee + (" · released once" if mode == "APPROVED" else " · not sent"), "S")
    elif mode == "TRIPPED":
        yield from c.line_steps(8, 55, "TRIPPED", "XL", False)
        yield from c.line_steps(8, 85, REASONS.get(model["reason"], "SUPERVISOR ERROR"), "L")
        yield from c.line_steps(8, 104, "Blocked until you reset.", "S")
        yield from c.line_steps(8, 122, "Reset: handle OFF, then ON.", "S")
    elif mode == "OFF":
        yield from c.line_steps(8, 58, "OFF", "XL")
        yield from c.line_steps(8, 89, "Agent port off", "L")
        yield from c.line_steps(8, 119, "Handle up to re-arm.", "S")
    elif mode == "STARTING":
        yield from c.line_steps(8, 55, "TRIPPED", "XL", False)
        yield from c.line_steps(8, 87, "Breaker restarted. Starting…", "S")
        yield from c.line_steps(8, 117, "Wait, then handle OFF and ON.", "S")
    else:  # Design-only illustration; core and UART never enter SETUP.
        yield from c.line_steps(8, 58, "SETUP", "XL")
        yield from c.line_steps(8, 83, "Code 4817", "L")
        yield from c.line_steps(8, 106, "Open setup on your laptop.", "S")
        yield from c.line_steps(8, 125, "Then hold the button.", "S")
    yield bytes(c.buf)


def render(model):
    result = None
    for result in render_steps(model):
        pass
    return result
