"""First executable boot action: veto AGENT power, all lamps and SE051 off."""
from machine import Pin
from breaker import config
from breaker.config import PINS

Pin(PINS["cut_enable"], Pin.OUT, value=0)
for name in ("led_r", "led_g", "led_b", "se_enable"):
    Pin(PINS[name], Pin.OUT, value=0)
Pin(PINS["uart_rx"], Pin.IN, Pin.PULL_UP)  # Idle HIGH before CM5 pinmux/UART setup.
# Q6/Q7 non-inverting buffer defaults off before Python; R27 stays at K1 IN/DC-.
# GP18 HIGH energizes the H-jumper relay. No relay delay may precede this LOW.

# Local physical maintenance entry permits mpremote without an active watchdog.
# It cannot enable power and has no UART command. Remove programming USB for demo.
import time
on = Pin(PINS["handle_on_n"], Pin.IN, Pin.PULL_UP)
off = Pin(PINS["handle_off_n"], Pin.IN, Pin.PULL_UP)
button = Pin(PINS["confirm_n"], Pin.IN, Pin.PULL_UP)
maintenance = on.value() == 1 and off.value() == 0 and button.value() == 0
time.sleep_ms(30)
config.MAINTENANCE_AT_BOOT = maintenance and on.value() == 1 and off.value() == 0 and button.value() == 0
