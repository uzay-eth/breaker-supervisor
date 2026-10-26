"""Bounded newline UART codec. RX yields (fields, None) or (None, reason)."""
import binascii
from .canonical import is_hex

MAX_FRAME = 1024
FRAME_MS = 1000


def crc32_fallback(raw):
    """Portable fallback for MicroPython builds without binascii.crc32."""
    value = 0xffffffff
    for byte in raw:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xedb88320 if value & 1 else 0)
    return value ^ 0xffffffff


def crc(raw):
    return "%08x" % (getattr(binascii, "crc32", crc32_fallback)(raw) & 0xffffffff)


def encode(*fields):
    text = [str(f) for f in fields]
    if not text or any(not f or any(ord(c) < 33 or ord(c) > 126 or c == "|" for c in f) for f in text):
        raise ValueError("wire_field")
    raw = ("BRK1|" + "|".join(text)).encode("ascii")
    frame = raw + b"|" + crc(raw).encode("ascii") + b"\n"
    if len(frame) > MAX_FRAME:
        raise ValueError("frame_size")
    return frame


def decode(frame):
    if not frame.endswith(b"\n") or len(frame) > MAX_FRAME:
        raise ValueError("frame_size")
    body = frame[:-1]
    if any(c < 33 or c > 126 for c in body):
        raise ValueError("frame_ascii")
    parts = body.split(b"|")
    if len(parts) < 3 or parts[0] != b"BRK1" or any(not p for p in parts):
        raise ValueError("frame_shape")
    checksum = parts[-1].decode("ascii")
    if not is_hex(checksum, 8) or checksum != crc(body[:-9]):
        raise ValueError("frame_crc")
    return [p.decode("ascii") for p in parts[1:-1]]


class Decoder:
    def __init__(self):
        self.buffer = bytearray()
        self.started = None
        self.dropping = False

    def expire(self, now):
        if self.started is not None and now - self.started >= FRAME_MS:
            self.buffer = bytearray()
            self.started = None
            self.dropping = True
            return [(None, "frame_timeout")]
        return []

    def feed(self, data, now):
        results = self.expire(now)
        for c in data:
            if self.dropping:
                if c == 10:
                    self.dropping = False
                continue
            if self.started is None:
                self.started = now
            if c != 10 and (c < 33 or c > 126):
                self.buffer = bytearray()
                self.started = None
                self.dropping = True
                results.append((None, "frame_ascii"))
            elif c == 10:
                frame = bytes(self.buffer) + b"\n"
                self.buffer = bytearray()
                self.started = None
                try:
                    results.append((decode(frame), None))
                except ValueError as exc:
                    results.append((None, str(exc)))
            elif len(self.buffer) >= MAX_FRAME - 1:
                self.buffer = bytearray()
                self.started = None
                self.dropping = True
                results.append((None, "frame_size"))
            else:
                self.buffer.append(c)
        return results
