#!/usr/bin/env python3
"""Interactive hardware acceptance recorder. It cannot measure GPIO or link itself.

Run on CM5 UART0 with proxy AND UART broker stopped, or from a Mac with an
existing 3.3V USB-UART adapter. It sends requests only. All restore/button actions
must be physical. Meter observations and UART evidence are recorded separately.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from breaker.canonical import payout, digest, to_hex
from breaker.config import CUT_DRIVER, RELAY_SETTLE_MS
from client import Client


class Bench:
    def __init__(self, endpoint, output):
        self.file = Path(output).open("x")  # Never overwrite a measurement record.
        self.client = Client(endpoint)
        self.lock = threading.Lock()
        self.stop = False
        self.error = None
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()

    def record(self, kind, **values):
        self.file.write(json.dumps({"time_unix": time.time(), "kind": kind, "hardware": True, **values})+"\n")
        self.file.flush()

    def worker(self):
        consumed = 0
        while not self.stop:
            try:
                with self.lock:
                    self.client.pump(0.02)
                    for fields in self.client.events[consumed:]: self.record("UART observation", fields=fields)
                    consumed = len(self.client.events)
            except Exception as exc:
                self.error = repr(exc)
                return
            time.sleep(0.005)

    def command(self, *fields):
        with self.lock: return self.client.command(*fields)

    def wait(self, predicate, seconds=20):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            if self.error: raise RuntimeError(self.error)
            with self.lock:
                if predicate(self.client): return
            time.sleep(0.02)
        raise TimeoutError("Hardware did not reach expected state")

    def observe(self, step, instruction):
        print("\n" + step + ": " + instruction)
        value = input("Record observed result (start with PASS or FAIL; never guess): ").strip()
        with self.lock: self.record("human observation", step=step, observation=value)
        if not value.startswith("PASS"):
            raise RuntimeError("HIL stopped: failed/unverified observation " + step)

    def voltage(self, point, expectation, maximum=None):
        text = input("Measure %s to common GND (%s), enter actual DC volts: " % (point, expectation))
        value = float(text)
        if not -0.2 <= value <= 6: raise ValueError("invalid/unexpected measured voltage")
        with self.lock: self.record("human meter reading", point=point, volts=value, expectation=expectation)
        if maximum is not None and value >= maximum:
            raise RuntimeError("Measured voltage failed the stated DESIGN screen")

    def rearm(self):
        input("Move handle physically OFF; keep it there, then press Enter: ")
        self.wait(lambda c: c.state and c.state[4] == "OFF" and c.state[10] == "1")
        input("Move handle physically ON, then press Enter: ")
        self.wait(lambda c: c.state and c.state[3] == "ARMED" and c.state[10] == "1")
        self.settle()

    def settle(self):
        # The UART worker keeps PING alive. This is not a measured contact delay.
        time.sleep(RELAY_SETTLE_MS / 1000)
        with self.lock:
            self.record("observation settle", design_ms=RELAY_SETTLE_MS,
                        meaning="wait only; not a measured GPIO/contact/link time")

    def timing(self, step, start, end, allow_negative=False):
        print("Capture from the physical edge, including settling; UART log arrival is not that edge.")
        value = float(input("Measured %s (%s -> %s), actual milliseconds: " % (step, start, end)))
        if not math.isfinite(value) or (value < 0 and not allow_negative):
            raise ValueError("finite measured interval required; negative only for signed packet timing")
        capture = input("Scope/packet capture path and trace/marker IDs: ").strip()
        if not capture:
            raise ValueError("timing requires capture evidence")
        with self.lock:
            self.record("human timing measurement", step=step, start=start, end=end,
                        milliseconds=value, capture=capture)

    def disconnect(self):
        """End UART before deliberately removing the Pico; keep the evidence file."""
        self.stop = True
        self.thread.join(timeout=2)
        if self.thread.is_alive(): raise RuntimeError("UART worker did not stop")
        self.client.close()
        self.client = None

    def close(self):
        try:
            if self.client is not None:
                try:
                    if self.client.boot and not self.error:
                        self.command("CUT", "linux.cut")
                        self.settle()
                finally:
                    self.disconnect()
        finally:
            self.file.close()


def relay_absent_checks(b):
    """Physical offline phase: relay/buffer stay powered while the Pico is absent."""
    seq = b.command("CUT", "linux.cut")
    b.wait(lambda c: c.state[5] == "0" and int(c.state[11]) >= seq)
    b.settle()
    b.disconnect()
    b.observe("remove Pico unpowered", "Isolate ALL rig/programming power before changing connections. Remove Pico and its leads. Retain K1/buffer common GND, RAW_5V supply and R27 at K1. Set handle ON, then power CM5/K1/buffer and Ethernet peer. Record setup/photo; never remove the common ground.")
    b.settle()
    b.voltage("K1 DC+/RAW_5V (Pico absent)", "record actual powered coil supply")
    b.voltage("HOST_VBUS (Pico absent)", "record actual powered contact source")
    b.voltage("RELAY_IN (Pico absent)", "LOW from Q7; record actual voltage")
    b.voltage("AGENT_5V (Pico absent, handle ON)", "after discharge, host/peer powered; <0.4 V DESIGN", 0.4)
    b.observe("Pico absent veto", "K1 remains de-energized, COM-NO open and traffic absent despite powered source/coil supply and handle ON. Cite voltage and scope/traffic evidence; LED/click alone is not proof. Never use an ohmmeter on a powered circuit.")
    b.observe("finish isolated", "Move handle OFF and isolate ALL power. Refit Pico only while unpowered using the electrical checklist. Next power-on still requires physical OFF->ON; no hardware pass is inferred from reconnecting.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--uart", required=True, help="physical UART path, normally /dev/serial0 on CM5")
    ap.add_argument("--output", required=True, help="new HIL-*.jsonl measurement file")
    ap.add_argument("--fault-equipped", action="store_true", help="ONLY with separately qualified /FAULT hardware")
    a = ap.parse_args()
    if a.uart.startswith("tcp:"): ap.error("HIL refuses SIM UART; use simulator tests")
    print("HARDWARE TEST. Stop BOTH CM5 proxy and UART broker; this script is the sole UART writer.")
    print("Default cut:", CUT_DRIVER, "HiLetgo B00LW15A4W jumper H; GP14 NC, no /FAULT/contact feedback.")
    print("Keep a safe local mock only; no payment provider. Human observation is not automated proof.")
    b = Bench(a.uart, a.output)
    try:
        b.wait(lambda c: c.boot is not None and c.state is not None)
        b.observe("identity", "Record Pico revision, MicroPython UF2 version/SHA, app commit/manifest, panel/controller/BUSY polarity, panel arrival JSON path/device_record_sha256/source fingerprint, ambient temperature, meter ID, HiLetgo/Songle markings, H jumper and Q6/Q7/R27 wiring revision. Coil supply is RAW_5V, never switched USB VBUS; COM-NO is in series with handle pole A.")
        input("Move handle ON now, then Enter to command CUT for the independent GP18-low check: ")
        seq = b.command("CUT", "linux.cut")
        b.wait(lambda c: c.state[5] == "0" and int(c.state[11]) >= seq)
        b.settle()
        b.observe("cold boot", "Using a prior power-on capture, verify GP18 LOW and all LEDs OFF before handle read; already-ON at boot cannot arm. DMM alone cannot capture a startup pulse. Record capture path.")
        b.voltage("GP18", "LOW; DESIGN screen <0.3 V", 0.3)
        b.voltage("RELAY_IN", "LOW through Q7; record actual voltage")
        b.voltage("AGENT_5V", "after discharge; DESIGN screen <0.4 V", 0.4)
        b.observe("GP18-low veto", "With handle ON, HOST_VBUS and K1 RAW_5V powered, GP18 LOW must leave coil off/COM-NO open and link/traffic absent. Record source voltages and captures. Click/LED alone does not prove open contacts.")
        b.rearm()
        b.voltage("GP18", "HIGH, near Pico 3V3; record actual value")
        b.voltage("RELAY_IN", "HIGH through Q6/Q7 buffer; record actual voltage")
        b.voltage("AGENT_5V", "powered; record actual loaded rail")
        b.observe("armed", "Relay clicks on enable without chatter; live AGENT_5V only with handle ON plus GP18 HIGH. Dim steady green ring, ARMED face, OPEN BENCH visible. Verify actual Ethernet/USB enumeration separately; no inference from GP18 or click.")
        raw = payout(184000)
        ticket = b.command("HOLD", digest(raw), to_hex(raw))
        b.wait(lambda c: c.state[3] == "HELD" and c.state[10] == "1")
        b.observe("held face", "Pulsing amber ring; HELD · payment, $1,840.00, to acct ••4471, Log #6147, Hold the button 1 s to send. OPEN BENCH at top right; never DEMO on hardware.")
        before = len([e for e in b.client.events if e[0] == "RELEASE"])
        b.observe("short press", "Release button first, then press for less than 1 second and release. Confirm face stays HELD. Record timed/video evidence.")
        if len([e for e in b.client.events if e[0] == "RELEASE"]) != before:
            raise RuntimeError("Short press released")
        print("Release the button, then physically hold CONFIRM at least 1.1 seconds.")
        b.wait(lambda c: any(e[0] == "RELEASE" and e[3] == str(ticket) and e[4] == digest(raw) for e in c.events), 60)
        b.observe("release timing", "Record button falling edge to RELEASE using a logic analyzer. Must be >=1 s; one release for the exact ticket/hash. Record the green flash and APPROVED / $1,840.00 / to acct ••4471 · released once, visible for 3 s after refresh, then ARMED. This script only observes UART, not physical timing.")
        b.wait(lambda c: c.state[3] == "ARMED" and c.state[10] == "1")
        ticket = b.command("HOLD", digest(raw), to_hex(raw))
        b.wait(lambda c: c.state[3] == "HELD")
        new = payout(184001)
        b.command("CHECK", ticket, digest(new), to_hex(new))
        b.wait(lambda c: c.state[3] == "TRIPPED" and c.state[5] == "0" and c.state[10] == "1")
        b.observe("CHECK diagnostic (not demo path)", "Red ring; TRIPPED / MISMATCH / Blocked until you reset. from the explicit diagnostic CHECK above. Hold button: no release. This is not the ordinary buffered HTTP demo state.")
        b.rearm()
        ticket = b.command("HOLD", digest(raw), to_hex(raw))
        b.wait(lambda c: c.state[3] == "HELD" and c.state[10] == "1")
        bad_frame = b"BRK1|HELLO|00000000\n"
        with b.lock: b.client.write(bad_frame)
        b.wait(lambda c: c.state[3] == "TRIPPED" and c.state[5] == "1" and c.state[10] == "1"
               and any(e[0] == "DENIED" and e[3] == str(ticket) for e in c.events))
        b.observe("single UART noise", "TRIPPED / UART ERROR / Blocked until you reset., red ring. Held ticket denied; physical confirm cannot release. Measure GP18 still HIGH. Record capture and actual voltage.")
        # Human observation may take longer than the rolling window; send three
        # new rejected frames together so the threshold check is deterministic.
        with b.lock: b.client.write(bad_frame * 3)
        b.wait(lambda c: c.state[3] == "TRIPPED" and c.state[5] == "0")
        b.voltage("GP18", "three wire errors within 1 s; LOW <0.3 V DESIGN", 0.3)
        b.rearm(); b.command("TRIP", "mail.mass_delete")
        b.wait(lambda c: c.state[3] == "TRIPPED" and c.state[10] == "1")
        if b.client.state[5] != "1": raise RuntimeError("Policy TRIP unexpectedly cut")
        b.observe("policy trip", "Red ring, TRIPPED / Mail: mass delete / Blocked until you reset. / Reset: handle OFF, then ON. No count or EN status is displayed. Linux returns HTTP 423 while ungoverned model path remains reachable. No UART reset clears it.")
        b.rearm()
        expired = payout(1840000)
        ticket = b.command("HOLD", digest(expired), to_hex(expired))
        b.wait(lambda c: c.state[3] == "HELD" and c.state[10] == "1")
        print("Do not press the button. Capture HELD $18,400.00 through its 60 s expiry and 5 s NOT SENT face.")
        b.wait(lambda c: any(e[0] == "DENIED" and e[3] == str(ticket) and e[-1] == "hold.timeout" for e in c.events), 65)
        b.observe("expiry face", "From continuous video: HELD expires at 60 s without RELEASE; NOT SENT / $18,400.00 / to acct ••4471 · not sent is visible for 5 s after refresh, then ARMED. OPEN BENCH stays present. Record measured dwell separately from full-refresh time.")
        b.command("TRIP", "mail.mass_delete")
        b.command("RESTORE")
        b.wait(lambda c: c.state[3] == "TRIPPED" and c.state[5] == "0")
        b.observe("forbidden UART command", "RESTORE was rejected and cut remained LOW. Confirm no release occurred; only the next physical OFF->ON may rearm.")
        b.rearm()
        input("Move handle OFF now, then Enter: ")
        b.wait(lambda c: c.state[3] == "OFF" and c.state[5] == "0" and c.state[10] == "1")
        b.settle()
        b.observe("off", "Ring dark; OFF / Agent port off / Handle up to re-arm.; OPEN BENCH. Continuity-qualified pole A removes VBUS independently; pole B sense matches OFF.")
        b.voltage("AGENT_5V", "after discharge with host/peer active; <0.4 V DESIGN", 0.4)
        b.observe("invalid contacts", "With power isolated before wiring changes, use a separately qualified isolated sense-input fixture to exercise TRANSIT and INVALID B inputs. Both cut; returning directly to ON cannot restore. Restore original wiring. Do not bridge any power contact.")
        if a.fault_equipped:
            b.observe("fault input", "Only on qualified fault-equipped hardware with CUT_FAULT_PRESENT=True: assert open-drain /FAULT low, observe immediate GP18 low and latched fault; release does not rearm. Never short the USB rail.")
        else:
            with b.lock: b.record("not applicable", step="current-fault input", reason="HiLetgo relay has no /FAULT/contact feedback; GP14 NC")
        b.observe("cut qualification", "Attach scope/packet captures for mechanical and supervisor cut separately, 20 cycles each; record actual module operate/release, contact bounce and end-to-end endpoints. No 5/10/100 ms value is a measured result. Verify no phantom power during host USB rescan; no chatter or welded contact. DMM cannot prove latency. Identify the worst measured cycle for the following entries.")
        b.timing("supervisor rail cut", "GP18 falling edge", "AGENT_5V below 0.4 V")
        # Last packet can precede the edge; preserve its signed measured offset.
        b.timing("handle traffic cut", "handle pole A opening", "last forwarded packet", allow_negative=True)
        b.observe("power and recovery", "Record inrush/rail sag, USB3 signal integrity, full display rotation/BUSY timing, RX idle HIGH/pull-up, per-render/display-step and GC maxima, sample gaps under UART load, and watchdog/cold-reset behavior. Every power-on needs a new OFF->ON. Programming USB removed for demo.")
        relay_absent_checks(b)
        with b.lock: b.record("HIL completed", result="UART assertions passed; physical checks are operator attestations with cited evidence")
        print("HIL record written. Final physical step leaves handle OFF and rig power isolated.")
    except BaseException as exc:
        with b.lock: b.record("HIL incomplete or failed", reason=str(exc))
        raise
    finally:
        b.close()


if __name__ == "__main__": main()
