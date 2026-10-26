"""DRAFT v0.1, electrical handoff checked locally 2026-10-25.

GPIO numbers, NOT physical pin numbers. Only pin map in this firmware.
See PROTOCOL.md and docs/FLASH.md in this repository.
"""
PINS = {
    "uart_tx": 0, "uart_rx": 1,
    "se_sda": 4, "se_scl": 5, "se_enable": 6,
    "epd_dc": 8, "epd_cs": 9, "epd_sck": 10, "epd_mosi": 11,
    "epd_reset": 12, "epd_busy": 13,
    "cut_fault_n": 14,  # Reserved only for a separately qualified fault-equipped cut.
    "handle_on_n": 16, "handle_off_n": 17,
    "cut_enable": 18, "confirm_n": 19,
    "led_r": 20, "led_g": 21, "tamper_closed_n": 22, "led_b": 26,
}
UART_ID = 0
UART_BAUD = 115200
SPI_ID = 1
SPI_HZ = 2000000  # DESIGN conservative starting speed; qualify on the bench.
BENCH_MODE = True  # Printed on every face; no pretend case tamper protection.
CUT_DRIVER = "HILETGO_H_2XNPN"  # HiLetgo B00LW15A4W, jumper H, Q6/Q7 non-inverting buffer.
CUT_FAULT_PRESENT = False  # GP14 is NC; relay has NO /FAULT or contact feedback.
RELAY_SETTLE_MS = 100  # DESIGN link-observation settle, never delay GP18/safety sampling.
PANEL_RECORD = "panel-verified.json"  # Local USB arrival test only; missing/stale = cut.
EPD_TIMEOUT_MS = 10000
WATCHDOG_MS = 2000
LOOP_SLEEP_MS = 5
# DESIGN visual levels/timing, not measured brightness; qualify amber on real ring.
RING_PWM_HZ = 1000
RING_ARMED = 8192
RING_AMBER_MIN, RING_AMBER_MAX = 10000, 45000
RING_RED, RING_FLASH = 45000, 65535
RING_PULSE_MS, RING_FLASH_MS = 1200, 400
