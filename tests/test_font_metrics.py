"""Raster baseline regressions: inspect ink, not just font bounding boxes."""
import unittest
from breaker import face
from face_host import render as host_render
from tools.render_faces_v02 import models


def ink_bounds(ch, style):
    c, data = face.Canvas(), face.load_font()
    w, h, top, _, offset = c.glyph(ch, style)
    stride = (w+7)//8
    rows = [top+y for y in range(h)
            if any(data[offset+y*stride:offset+(y+1)*stride])]
    return (min(rows), max(rows)) if rows else None


class FontMetricsTests(unittest.TestCase):
    def test_r_matches_x_height_and_baseline_in_every_size(self):
        for style in range(face.FONT_STYLES):
            with self.subTest(style=style):
                self.assertEqual(ink_bounds('r', style), ink_bounds('n', style))

    def test_printable_ascii_alignment(self):
        # Punctuation, Q's tail, dots/ascenders and descenders have intentional
        # different extents. Audit every printable glyph, then assert shared
        # cap/x-height and baseline for the classes which actually share them.
        caps = 'ABCDEFGHIJKLMNOPRSTUVWXYZ0123456789!#%&?bdhkl'
        x_height = 'acemnorsuvwxz'
        for style in range(face.FONT_STYLES):
            bounds = {chr(code): ink_bounds(chr(code), style) for code in range(32, 127)}
            self.assertIsNone(bounds[' '])
            self.assertTrue(all(b is not None for c, b in bounds.items() if c != ' '))
            for ch in caps:
                self.assertEqual(bounds[ch], bounds['H'], (style, ch))
            for ch in x_height:
                self.assertEqual(bounds[ch], bounds['n'], (style, ch))
            for ch in 'fit':
                self.assertEqual(bounds[ch][1], -1, (style, ch))
            for ch in 'gpqy':
                self.assertEqual(bounds[ch][0], bounds['n'][0], (style, ch))
                self.assertGreaterEqual(bounds[ch][1], 0, (style, ch))
            for ch in 'Qj':
                self.assertGreaterEqual(bounds[ch][1], 0, (style, ch))

    def test_every_face_has_three_clear_bottom_rows(self):
        examples = models()
        cases = list(examples.values()) + [dict(examples['tripped'], reason=r) for r in face.REASONS]
        for model in cases:
            for render in (face.render, host_render):
                with self.subTest(mode=model['mode'], reason=model['reason'], render=render.__module__):
                    self.assertEqual(render(model)[-3*face.STRIDE:], b'\xff'*(3*face.STRIDE))
