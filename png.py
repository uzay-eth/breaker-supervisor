"""PNG export of the actual device framebuffer, using only the Python stdlib."""
import struct
import zlib
from pathlib import Path
from breaker.face import WIDTH, HEIGHT, STRIDE


def write_png(buffer, path, scale=1):
    if len(buffer) != STRIDE * HEIGHT:
        raise ValueError("buffer_size")
    if scale not in (1, 8):
        raise ValueError("scale")
    # 1-bit grayscale, 1=white, exact nearest-neighbor expansion.
    raw = bytearray()
    for y in range(HEIGHT):
        row = buffer[y*STRIDE:(y+1)*STRIDE]
        if scale == 8:
            row = bytes(255 if b & (128 >> bit) else 0 for b in row for bit in range(8))
        for _ in range(scale):
            raw.extend(b"\x00" + row)
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind+data) & 0xffffffff)
    data = b"\x89PNG\r\n\x1a\n"
    data += chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH*scale, HEIGHT*scale, 1, 0, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def save_face(buffer, path):
    path = Path(path)
    write_png(buffer, path)
    write_png(buffer, path.with_name(path.stem + "@8x.png"), 8)
