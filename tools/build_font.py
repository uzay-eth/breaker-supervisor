"""Offline bitmap export from the bundled Inter source font (SIL OFL 1.1).

Development only: python3 tools/build_font.py (requires a local Pillow installation).
Firmware and simulation do not depend on Pillow or a platform font renderer.
"""
from pathlib import Path
import hashlib
import struct
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "Inter.woff2"
# First five styles retain panel-qualification typography. Product tiers follow.
SPECS = ((10, "SemiBold"), (11, "Bold"), (13, "Bold"), (24, "ExtraBold"), (28, "ExtraBold"),
         (16, "SemiBold"), (24, "SemiBold"), (40, "ExtraBold"))
GLYPHS = ''.join(chr(c) for c in range(32, 127)) + "•·…"
# Optical pixel corrections for tiny 1-bit text. Inter's monochrome hinting
# raises r above the x-height and collapses s. Keep a shared baseline/x-height,
# a curved r shoulder and a visible s bowl.
SMALL_GLYPHS = {
    (10, "s"): (".####.", "##....", ".####.", "....##", "##..##", ".####."),
    (10, "r"): (".#.##", ".##..", ".#...", ".#...", ".#...", ".#..."),
    (11, "s"): ("..####.", ".##....", "..####.", ".....##", ".##..##", "..####."),
    (11, "r"): (".#.##", ".###.", ".##..", ".##..", ".##..", ".##.."),
    (13, "s"): ("..####..", ".##..##.", ".##.....", "..####..", ".....##.", ".##..##.", "..####.."),
    (13, "r"): (".##.##", ".####.", ".###..", ".##...", ".##...", ".##...", ".##..."),
}
# Inter's 1-bit r is translated up one row in these styles: both the shoulder
# and stem are one row above n. Correct position only, preserving shape/advance.
BASELINE_SHIFT = {(16, "SemiBold", "r"): 1, (24, "SemiBold", "r"): 1,
                  (24, "ExtraBold", "r"): 1, (28, "ExtraBold", "r"): 1}


def main():
    # Fixed records: width, height, baseline-relative top+64, advance, offset u16.
    index, pixels = bytearray(), bytearray()
    for size, weight in SPECS:
        font = ImageFont.truetype(str(SOURCE), size)
        font.set_variation_by_name(weight)
        for ch in GLYPHS:
            left, top, right, bottom = font.getbbox(ch, anchor="ls")
            # Keep x bearings in the glyph bitmap; all selected ASCII fonts fit.
            width, height = max(1, right, round(font.getlength(ch))), max(1, bottom - top)
            img = Image.new("1", (width, height), 0)
            draw = ImageDraw.Draw(img); draw.fontmode = "1"
            draw.text((0, -top), ch, font=font, fill=1, anchor="ls")
            advance = round(font.getlength(ch))
            if (size, ch) in SMALL_GLYPHS:
                rows = SMALL_GLYPHS[size, ch]
                width, height, top, advance = len(rows[0]), len(rows), -len(rows), len(rows[0])
                img = Image.new("1", (width, height), 0)
                for y, row in enumerate(rows):
                    for x, value in enumerate(row): img.putpixel((x, y), int(value == "#"))
            top += BASELINE_SHIFT.get((size, weight, ch), 0)
            index.extend(struct.pack(">BBBBH", width, height, top+64, advance, len(pixels)))
            pixels.extend(img.tobytes())
    target = ROOT / "breaker" / "font.bin"
    target.write_bytes(b"BRKF2" + bytes((len(SPECS), len(GLYPHS))) + index + pixels)
    (ROOT / "fixtures" / "font-source.txt").write_text(
        "Breaker face bitmap font, derived from Inter (SIL OFL 1.1).\n"
        + "Source: " + str(SOURCE.relative_to(ROOT)) + "\n"
        + "SHA256: " + hashlib.sha256(SOURCE.read_bytes()).hexdigest() + "\n"
        + "Specs: " + repr(SPECS) + "\n"
        + "Optical corrections: s/r at sizes 10/11/13, shared lowercase baseline/x-height; see SMALL_GLYPHS.\n"
        + "Baseline corrections: r at 16 SemiBold, 24 SemiBold/ExtraBold, 28 ExtraBold shifted down one pixel; see BASELINE_SHIFT.\n"
        + "Generated locally, no downloads. See breaker/FONT-LICENSE.txt.\n")
    print(target, len(index)+len(pixels)+7, "bytes")


if __name__ == "__main__":
    main()
