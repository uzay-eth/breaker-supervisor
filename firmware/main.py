"""MicroPython RP2350 adapter. Shared core/face contain every policy decision."""
from machine import Pin, PWM, UART, SPI, WDT, disable_irq, enable_irq
from breaker.config import PINS

# Repeat boot veto before importing policy/font code which might fail.
cut = Pin(PINS["cut_enable"], Pin.OUT, value=0)
leds = [Pin(PINS[n], Pin.OUT, value=0) for n in ("led_r", "led_g", "led_b")]
led_pwm = []
Pin(PINS["se_enable"], Pin.OUT, value=0)

import os
import time
import binascii
import micropython
from breaker import config
from breaker.core import Supervisor
from breaker.face import render_steps, load_font
from breaker.epaper import make_panel
from breaker.panel_record import load as load_panel_record

micropython.alloc_emergency_exception_buf(128)


class Clock:
    def __init__(self):
        self.last = time.ticks_ms()
        self.elapsed = 0

    def now(self):
        current = time.ticks_ms()
        self.elapsed += time.ticks_diff(current, self.last)
        self.last = current
        return self.elapsed


def run():
    watchdog = WDT(timeout=config.WATCHDOG_MS)
    led_pwm.extend(PWM(pin, freq=config.RING_PWM_HZ, duty_u16=0) for pin in leds)
    clock = Clock()
    s = Supervisor(binascii.hexlify(os.urandom(16)).decode(), bench=config.BENCH_MODE)
    on = Pin(PINS["handle_on_n"], Pin.IN, Pin.PULL_UP)
    off = Pin(PINS["handle_off_n"], Pin.IN, Pin.PULL_UP)
    button = Pin(PINS["confirm_n"], Pin.IN, Pin.PULL_UP)
    fault = Pin(PINS["cut_fault_n"], Pin.IN, Pin.PULL_UP) if config.CUT_FAULT_PRESENT else None
    tamper = Pin(PINS["tamper_closed_n"], Pin.IN, Pin.PULL_UP)
    edge = bytearray(1)

    def veto_irq(_):
        fault_active = fault is not None and not fault.value()
        if on.value() or not off.value() or fault_active or (not config.BENCH_MODE and tamper.value()):
            was_enabled = cut.value()
            cut.value(0)
            # A normal OFF->TRANSIT->ON movement must retain the stable OFF
            # witness. Catch missed unsafe pulses only when they vetoed power;
            # faults/tamper latch even while power was already disabled.
            if was_enabled or fault_active or (not config.BENCH_MODE and tamper.value()):
                edge[0] = 1

    for pin in (on, off, tamper) + ((fault,) if fault is not None else ()):
        pin.irq(handler=veto_irq, trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, hard=True)

    uart = UART(config.UART_ID, baudrate=config.UART_BAUD, tx=Pin(PINS["uart_tx"]),
                rx=Pin(PINS["uart_rx"], Pin.IN, Pin.PULL_UP), bits=8, parity=None, stop=1,
                timeout=0, timeout_char=1, rxbuf=2048, txbuf=1024)
    panel = None
    panel_record = load_panel_record(config.PANEL_RECORD)
    if panel_record is not None:
        panel = make_panel(panel_record["profile"], SPI(config.SPI_ID, baudrate=config.SPI_HZ, polarity=0, phase=0,
                             sck=Pin(PINS["epd_sck"]), mosi=Pin(PINS["epd_mosi"])),
                        Pin(PINS["epd_cs"], Pin.OUT, value=1), Pin(PINS["epd_dc"], Pin.OUT, value=0),
                        Pin(PINS["epd_reset"], Pin.OUT, value=1), Pin(PINS["epd_busy"], Pin.IN),
                        timeout=config.EPD_TIMEOUT_MS)
    else:
        s.panel_failed()
        print("DISPLAY UNVERIFIED: GP18 remains LOW; run local panel arrival test")
    print("CUT DRIVER:", config.CUT_DRIVER, "FAULT INPUT PRESENT:", config.CUT_FAULT_PRESENT,
          "OPEN BENCH:", config.BENCH_MODE)
    load_font()
    job, phase, generation, completed = None, None, 0, 0
    tx = bytearray()

    def outputs():
        # IRQ cannot be overwritten by a high output between sample and write.
        # Only this brief GPIO decision is atomic; display/UART never mask IRQs.
        irq_state = disable_irq()
        safe = (not edge[0] and not on.value() and off.value() and (fault is None or fault.value())
                and (config.BENCH_MODE or not tamper.value()))
        cut.value(1 if s.enabled and safe else 0)
        enable_irq(irq_state)
        for pwm, value in zip(led_pwm, s.ring_levels()): pwm.duty_u16(value)

    while True:
        if edge[0]:
            edge[0] = 0
            s.fail("input.edge")
        s.sample(clock.now(), on.value(), off.value(), button.value(), fault.value() if fault else 1, tamper.value())
        outputs()
        if uart.any():
            data = uart.read(min(uart.any(), 256))
            if data: s.receive(data, clock.now())
        outputs()
        if panel and job is None and completed != s.face_generation:
            generation = s.face_generation
            job = render_steps(s.face_model())
            phase = "render"
        if job is not None:
            try:
                value = next(job)
                if phase == "render" and value is not None:
                    job, phase = panel.display(value, clock.now), "display"
                elif phase == "display" and value is True:
                    completed = generation
                    job = None
                    s.panel_done(generation, clock.now())
            except Exception:
                cut.value(0)
                s.panel_failed()
                panel, job = None, None  # No silent display recovery; reboot/qualify.
        outputs()
        tx.extend(s.drain_wire())
        if len(tx) > 8192:
            tx = bytearray()
            s.fail("io.backpressure")
            outputs()
        if tx:
            count = uart.write(tx[:128])
            if count: del tx[:count]
        watchdog.feed()
        time.sleep_ms(config.LOOP_SLEEP_MS)


if getattr(config, "MAINTENANCE_AT_BOOT", False):
    print("LOCAL MAINTENANCE: GP18 LOW, ring OFF, no UART policy loop. Power-cycle to exit.")
else:
    try:
        run()
    except BaseException:
        cut.value(0)
        for pwm in led_pwm:
            pwm.duty_u16(0)
            pwm.deinit()
        for led in leds: led.value(0)
        # Watchdog resets, external pulls keep cut off; boot needs OFF→ON again.
        raise
