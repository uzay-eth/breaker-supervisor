"""Host-only branding for SIM/demo exports. Never included in the flash manifest."""
from breaker.face import Canvas, WIDTH, inverted_header, render_steps as hardware_steps


def render_steps(model):
    c = Canvas()
    for value in hardware_steps(model):
        if value is None:
            yield None
        else:
            c.buf[:] = value
    inverted = inverted_header(model)
    yield from c.rect_steps(160, 0, WIDTH-160, 24, inverted)
    yield from c.line_steps(288, 18, "DEMO", "S", not inverted, right=True)
    yield bytes(c.buf)


def render(model):
    result = None
    for result in render_steps(model):
        pass
    return result
