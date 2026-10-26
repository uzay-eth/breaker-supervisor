"""Physical one-minute panel qualification, run only over local USB maintenance.

No port-enable path and no policy UART. The caller selects a positively identified
module revision; a successful refresh is NOT a silicon-ID autodetection method.
"""
from . import config
from .face import Canvas
from .panel_record import PROFILES, fingerprint, invalidate, save

# Local test budgets, not panel specifications. Sources/design rationale are in
# docs/PANEL-SOURCES.md in this repository (checked 2026-10-25).
TEST_MS = 60000
VIEW_MS = 600
RELEASE_MS = 30
CONFIRM_MS = 1000
POLL_MS = 5


def pattern(profile):
    if profile not in PROFILES:
        raise ValueError("unknown panel")
    c = Canvas()
    c.text(6, 16, "PANEL TEST / CUT OFF", 1)
    c.text(6, 40, "V1 / IL3820" if profile == PROFILES[0] else "V2 / SSD1680", 2)
    c.text(6, 63, "TOP LEFT", 1)
    c.text(6, 86, "Check WHITE / pattern / inverse", 0)
    c.text(6, 106, "If all correct: hold CONFIRM 1 s", 0)
    c.right(290, 124, "BOTTOM RIGHT", 1)
    for y in range(24, 72, 8):
        for x in range(240, 288, 8):
            if (x+y)//8 % 2 == 0:
                c.rect(x, y, 8, 8)
    return bytes(c.buf)


def qualify(panel, service, button_released, profile):
    """Cooperatively exercise actual driver/BUSY; all paths run service's veto.

    service() checks physical OFF/deadline and returns increasing milliseconds.
    Inputs are local callables, never exposed through UART.
    """
    released_at = None
    while True:
        now = service()
        if button_released():
            if released_at is None:
                released_at = now
            if now-released_at >= RELEASE_MS:
                break
        else:
            released_at = None
        yield None
    face = pattern(profile)
    for buffer in (b"\xff"*4736, face, bytes(255-b for b in face), face):
        complete = False
        for value in panel.display(buffer, service):
            service()
            if value is True:
                complete = True
            yield None
        if not complete:
            raise OSError("panel did not complete refresh")
        started = service()
        while service()-started < VIEW_MS:
            yield None
    # Do not accept the button held at boot or during a failed/previous image.
    released_at = None
    pressed_at = None
    primed = False
    while True:
        now = service()
        if button_released():
            pressed_at = None
            if released_at is None:
                released_at = now
            if now-released_at >= RELEASE_MS:
                primed = True
        else:
            released_at = None
            if primed:
                if pressed_at is None:
                    pressed_at = now
                if now-pressed_at >= CONFIRM_MS:
                    return
        yield None


def run(profile, identity):
    from machine import Pin, SPI
    import time
    import json
    from .epaper import make_panel

    pins = config.PINS
    cut = Pin(pins["cut_enable"], Pin.OUT, value=0)
    for name in ("led_r", "led_g", "led_b", "se_enable"):
        Pin(pins[name], Pin.OUT, value=0)
    if not getattr(config, "MAINTENANCE_AT_BOOT", False):
        raise ValueError("boot with physical OFF + CONFIRM; local maintenance required")
    # A failed/aborted retest must never leave a previous PASS usable.
    invalidate(config.PANEL_RECORD)
    if profile not in PROFILES or type(identity) is not str or not 1 <= len(identity) <= 160 or any(not 32 <= ord(c) <= 126 for c in identity):
        raise ValueError("known revision and printed module/FPC identity required")
    tested = fingerprint()
    on = Pin(pins["handle_on_n"], Pin.IN, Pin.PULL_UP)
    off = Pin(pins["handle_off_n"], Pin.IN, Pin.PULL_UP)
    button = Pin(pins["confirm_n"], Pin.IN, Pin.PULL_UP)
    start = time.ticks_ms()

    def service():
        cut.value(0)
        if on.value() != 1 or off.value() != 0:
            raise ValueError("physical handle must remain OFF")
        elapsed = time.ticks_diff(time.ticks_ms(), start)
        if elapsed >= TEST_MS or elapsed < 0:
            raise OSError("panel test exceeded one minute; unverified")
        return elapsed

    try:
        service()
        panel = make_panel(profile,
                           SPI(config.SPI_ID, baudrate=config.SPI_HZ, polarity=0, phase=0,
                               sck=Pin(pins["epd_sck"]), mosi=Pin(pins["epd_mosi"])),
                           Pin(pins["epd_cs"], Pin.OUT, value=1), Pin(pins["epd_dc"], Pin.OUT, value=0),
                           Pin(pins["epd_reset"], Pin.OUT, value=1), Pin(pins["epd_busy"], Pin.IN),
                           timeout=config.EPD_TIMEOUT_MS)
        print("HARDWARE PANEL TEST", profile, "GP18 LOW. Release CONFIRM now.")
        print("Watch WHITE, PATTERN, INVERSE, PATTERN. Check all pixels and corner labels.")
        print("ONLY if all correct: release then hold physical CONFIRM 1 s. Otherwise do nothing.")
        for _ in qualify(panel, service, button.value, profile):
            time.sleep_ms(POLL_MS)
        service()
        if tested != fingerprint():
            raise ValueError("panel code changed during qualification")
        record = save(config.PANEL_RECORD, profile, identity, tested)
        print("HARDWARE PANEL PASS", json.dumps(record))
        print("GP18 stays LOW. Release CONFIRM, power-cycle, then normal OFF->ON is still required.")
    finally:
        cut.value(0)
