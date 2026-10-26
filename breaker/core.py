"""The sole supervisor state machine, imported unchanged on Pico and host.

All times are increasing milliseconds supplied by the trusted local adapter.
Only sample() reads human inputs. UART dispatch cannot call it or panel_done().
"""
from .canonical import parse, from_hex, is_hex, uint, InvalidRequest
from .wire import Decoder, encode
from . import config

DEBOUNCE_MS = 30
CONFIRM_MS = 1000
HOLD_MS = 60000
APPROVED_MS = 3000
NOT_SENT_MS = 5000
HEARTBEAT_MS = 3000
MAX_SAMPLE_GAP_MS = 100
FRAME_ERROR_LIMIT = 3
FRAME_ERROR_WINDOW_MS = 1000
RULES = ("linux.cut", "mail.mass_delete", "secret.exfiltration", "spend.cap", "policy.state_invalid")


class Supervisor:
    def __init__(self, boot_id, allowlist=None, bench=True):
        if not is_hex(boot_id, 32):
            raise ValueError("boot_id")
        self.boot_id = boot_id
        self.allowlist = allowlist
        self.bench = bench
        self.decoder = Decoder()
        self.frame_errors = []
        self.now = 0
        self.last_sample = None
        self.last_ping = None
        self.last_seq = 0
        self.event = 0
        self.reset_epoch = 0
        self.outbox = []
        self.handle = "UNKNOWN"
        self.stable_handle = "UNKNOWN"
        self.handle_since = 0
        self.off_seen = False
        self.enabled = False
        self.latched = True
        self.reason = "boot.locked"
        self.pending = None
        self.notice = None  # Parsed display summary only; never a reusable authorization.
        self.notice_until = None
        self.button = 1
        self.button_since = 0
        self.confirm_since = None
        self.button_primed = False
        self.fault = 1
        self.tamper = 1
        self.panel_healthy = False
        self.face_ready = False
        self.face_generation = 0
        self._face_key = None
        self._state_key = None
        self._sync()

    @property
    def live(self):
        return self.last_ping is not None and self.now - self.last_ping < HEARTBEAT_MS

    @property
    def mode(self):
        if self.handle in ("UNKNOWN", "OFF", "TRANSIT"):
            return "OFF"
        if self.latched:
            return "TRIPPED"
        return "HELD" if self.pending else "ARMED"

    @property
    def ring(self):
        return {"OFF": "off", "TRIPPED": "red", "HELD": "amber", "ARMED": "green"}[self.mode]

    def ring_levels(self):
        """Nonblocking RGB PWM duties; wire ring remains its semantic color."""
        if self.ring == "off": return (0, 0, 0)
        if self.ring == "red": return (config.RING_RED, 0, 0)
        if self.ring == "amber":
            phase = (self.now-self.pending["started"]) % config.RING_PULSE_MS
            half = config.RING_PULSE_MS//2
            ramp = phase if phase < half else config.RING_PULSE_MS-phase
            level = config.RING_AMBER_MIN + (config.RING_AMBER_MAX-config.RING_AMBER_MIN)*ramp//half
            return (level, level//2, 0)
        if self.notice and self.notice["mode"] == "APPROVED":
            on = (self.now-self.notice["at"]) % config.RING_FLASH_MS < config.RING_FLASH_MS//2
            return (0, config.RING_FLASH if on else 0, 0)
        return (0, config.RING_ARMED, 0)

    def _clear_notice(self):
        self.notice = None
        self.notice_until = None

    def _show_notice(self, mode, parsed):
        self.notice = {"mode": mode, "amount": parsed["amount"],
                       "destination_label": parsed["destination_label"],
                       "sha256": parsed["sha256"], "at": self.now}
        self.notice_until = None  # Visible dwell begins at matching panel completion.

    def _emit(self, verb, *fields):
        # No allocation growth under a hostile host which stops reading TX.
        if len(self.outbox) >= 32:
            self.outbox = []
            self.enabled = False
            self.latched = True
            # Host TX backpressure does not invalidate a physical OFF witness.
            self.pending = None
            self._clear_notice()
            self.reason = "io.backpressure"
            self.button_primed = False
            self.confirm_since = None
            verb, fields = "ERROR", ("io.backpressure",)
        self.event += 1
        self.outbox.append((verb, self.boot_id, str(self.event)) + tuple(str(x) for x in fields))

    def drain(self):
        result = self.outbox
        self.outbox = []
        return result

    def drain_wire(self):
        return b"".join(encode(*x) for x in self.drain())

    def _deny(self, reason):
        if self.pending:
            p = self.pending
            self.pending = None  # Consume before output; no second release path.
            self._emit("DENIED", p["ticket"], p["sha256"], reason)
        self.button_primed = False
        self.confirm_since = None

    def trip(self, reason, physical=True, preserve_off=False):
        self._clear_notice()
        self._deny(reason)
        self.latched = True
        if not preserve_off:
            self.off_seen = False
        self.reason = reason
        if physical:
            self.enabled = False

    def fail(self, reason):
        """Local adapter faults use device-owned codes, never exception strings."""
        self.trip(reason, preserve_off=reason in ("uart.disconnect", "io.backpressure"))
        self._sync()

    def _protocol_error(self, reason, physical=True):
        # A host/UART event cannot make an already-observed physical OFF untrue.
        # This never creates an OFF witness or raises enabled.
        self.trip(reason, physical=physical, preserve_off=True)
        self._emit("ERROR", reason)

    def _frame_error(self, reason):
        # Rolling interval (now-1000, now], bounded even under continuous noise.
        self.frame_errors = [t for t in self.frame_errors if self.now-t < FRAME_ERROR_WINDOW_MS]
        self.frame_errors = self.frame_errors[-(FRAME_ERROR_LIMIT-1):] + [self.now]
        self._protocol_error(reason, physical=len(self.frame_errors) >= FRAME_ERROR_LIMIT)

    def _advance(self, now):
        if now < self.now:
            self.trip("clock.invalid")
            return False
        self.now = now
        if self.notice_until is not None and now >= self.notice_until:
            self._clear_notice()
        if self.last_ping is not None and not self.live:
            # Do not keep invalidating an OFF observed after the loss.
            if self.reason != "host.timeout":
                self.trip("host.timeout", preserve_off=True)
        if self.pending and now - self.pending["started"] >= HOLD_MS:
            expired = self.pending
            self._deny("hold.timeout")
            self.reason = "hold.timeout"
            self._show_notice("NOT SENT", expired)
        for _, error in self.decoder.expire(now):
            self._frame_error(error)
        return True

    def tick(self, now):
        self._advance(now)
        self._sync()

    def sample(self, now, on_n, off_n, button_n, fault_n=1, tamper_n=0):
        """Trusted physical input sample; unsafe levels take effect without debounce."""
        if not self._advance(now):
            self._sync()
            return
        if any(type(x) is not int or x not in (0, 1) for x in (on_n, off_n, button_n, fault_n, tamper_n)):
            self.fail("input.invalid")
            return
        if self.last_sample is not None and now - self.last_sample > MAX_SAMPLE_GAP_MS:
            self.trip("loop.stall")
        self.last_sample = now
        self.fault, self.tamper = fault_n, tamper_n
        handle = {(0, 1): "ON", (1, 0): "OFF", (1, 1): "TRANSIT", (0, 0): "INVALID"}[(on_n, off_n)]
        if handle != self.handle:
            self.handle = handle
            self.handle_since = now
            self.confirm_since = None
            self.button_primed = False
        if handle != "ON":
            self._clear_notice()
            was_enabled = self.enabled
            self.enabled = False
            self._deny("handle." + handle.lower())
            self.latched = True
            if was_enabled:
                self.off_seen = False
                self.reason = "handle.off"
            if handle == "INVALID":
                self.trip("handle.invalid")
        if not fault_n:
            self.trip("cut.fault")
        if not self.bench and tamper_n:
            self.trip("case.open")
        if now - self.handle_since >= DEBOUNCE_MS and handle != self.stable_handle:
            self.stable_handle = handle
            if handle == "OFF" and fault_n and (self.bench or not tamper_n):
                self.off_seen = True
            elif handle == "ON":
                if self.off_seen and fault_n and self.live and self.panel_healthy and (self.bench or not tamper_n):
                    self.enabled = True
                    self.latched = False
                    self.reason = "armed"
                    self.reset_epoch += 1
                self.off_seen = False  # Failed rearm needs a fresh physical cycle.
        # Require fresh released debounce AFTER the held face is complete.
        if button_n != self.button:
            self.button = button_n
            self.button_since = now
        if button_n:
            self.confirm_since = None  # Release bounce cancels immediately.
            if self.pending and self.face_ready and now - self.button_since >= DEBOUNCE_MS:
                self.button_primed = True
        elif (self.pending and self.face_ready and self.button_primed and self.enabled
              and not self.latched and self.stable_handle == "ON" and handle == "ON"
              and fault_n and self.live and now - self.button_since >= DEBOUNCE_MS):
            if self.confirm_since is None:
                self.confirm_since = now
            if now - self.confirm_since >= CONFIRM_MS:
                p = self.pending
                # Last decision point independently re-hashes the immutable record.
                try:
                    verified = parse(p["canonical"], self.allowlist)
                    good = verified["sha256"] == p["sha256"]
                except (ValueError, TypeError):
                    good = False
                if not good:
                    self.trip("hold.integrity")
                    self._emit("ERROR", "hold.integrity")
                else:
                    self.pending = None
                    self.confirm_since = None
                    self.button_primed = False
                    self.reason = "released"  # Authorization, NOT a delivery claim.
                    self._show_notice("APPROVED", verified)
                    self._emit("RELEASE", p["ticket"], p["sha256"])
        else:
            self.confirm_since = None
        self._sync()

    def panel_done(self, generation, now):
        if not self._advance(now):
            self._sync()
            return
        if generation == self.face_generation:
            self.face_ready = True
            self.panel_healthy = True
            self.button_primed = False
            self.confirm_since = None
            self.button_since = now
            if self.notice and self.notice_until is None:
                self.notice_until = now + (APPROVED_MS if self.notice["mode"] == "APPROVED" else NOT_SENT_MS)
        self._sync()

    def panel_failed(self):
        self.panel_healthy = False
        self.face_ready = False
        self.fail("display.fault")

    def receive(self, data, now):
        if not self._advance(now):
            self._sync()
            return
        if len(data) > 256:
            # Adapter reads are bounded; violating that contract never authorizes.
            self._protocol_error("rx.burst")
            self.decoder = Decoder()
            self.decoder.dropping = True
        else:
            for fields, error in self.decoder.feed(data, now):
                if error:
                    self._frame_error(error)
                else:
                    self._command(fields)
        self._sync()

    def _command(self, fields):
        if fields == ["HELLO"]:
            self._emit("READY", 1)
            self._state_key = None
            return
        verb = fields[0] if fields else ""
        arity = {"PING": 3, "STATUS": 3, "HOLD": 5, "CHECK": 6, "DENY": 4, "CUT": 4, "TRIP": 4}
        if verb not in arity or len(fields) != arity[verb]:
            self._protocol_error("command.invalid")
            return
        if fields[1] != self.boot_id:
            self._protocol_error("session.invalid")
            return
        try:
            seq = uint(fields[2])
        except ValueError:
            self._protocol_error("sequence.invalid")
            return
        if seq <= self.last_seq:
            self._protocol_error("sequence.replay")
            return
        self.last_seq = seq
        if verb == "PING":
            self.last_ping = self.now
            self._state_key = None
        elif verb == "STATUS":
            self._state_key = None
        elif verb in ("TRIP", "CUT"):
            if fields[3] not in RULES:
                self._protocol_error("rule.invalid")
            else:
                self.trip(fields[3], physical=(verb == "CUT"), preserve_off=True)
        elif verb == "DENY":
            if not self.pending or fields[3] != str(self.pending["ticket"]):
                self._protocol_error("ticket.invalid")
            else:
                self._deny("host.denied")
                self.reason = "host.denied"
        elif verb in ("HOLD", "CHECK"):
            claimed, hexdata = fields[-2:]
            try:
                if not is_hex(claimed, 64):
                    raise InvalidRequest("hash")
                parsed = parse(from_hex(hexdata), self.allowlist)
            except (ValueError, TypeError):
                self._protocol_error("request.invalid")
                return
            if parsed["sha256"] != claimed:
                self._protocol_error("hash.invalid")
            elif verb == "CHECK":
                if not self.pending or fields[3] != str(self.pending["ticket"]):
                    self._protocol_error("ticket.invalid")
                elif claimed != self.pending["sha256"] or parsed["canonical"] != self.pending["canonical"]:
                    self._protocol_error("hash.mismatch")
                # Valid CHECK is read-only; it cannot extend TTL or restart confirm.
            elif self.pending:
                self._protocol_error("hold.busy")
            elif self.mode != "ARMED" or not self.enabled or not self.live or not self.panel_healthy:
                self._emit("DENIED", seq, claimed, "not.armed")
            else:
                self._clear_notice()
                parsed["ticket"] = seq
                parsed["started"] = self.now
                self.pending = parsed
                self.reason = "held"
                self.button_primed = False
                self.confirm_since = None
                self._emit("HELD", seq, claimed, HOLD_MS)

    def face_model(self):
        p = self.pending
        mode = self.mode
        reason = self.reason
        if self.handle == "OFF": mode = "OFF"
        elif self.last_ping is None and reason == "boot.locked": mode = "STARTING"
        elif self.handle in ("UNKNOWN", "TRANSIT"):
            mode = "TRIPPED"
            if self.handle == "TRANSIT": reason = "handle.transit"
        elif mode == "ARMED" and self.notice:
            mode, p = self.notice["mode"], self.notice
        return {"mode": mode, "handle": self.handle, "reason": reason,
                "cut": int(self.enabled), "bench": self.bench,
                "amount": p["amount"] if p else None,
                "destination": p["destination_label"] if p else None,
                "sha256": p["sha256"] if p else None}

    def _state(self):
        p = self.pending
        return (self.mode, self.handle, int(self.enabled), self.ring,
                p["ticket"] if p else 0, p["sha256"] if p else "-",
                self.face_generation, int(self.face_ready), self.last_seq, self.reset_epoch)

    def _sync(self):
        m = self.face_model()
        key = tuple(m[k] for k in ("mode", "handle", "reason", "cut", "bench", "amount", "destination", "sha256"))
        if key != self._face_key:
            self._face_key = key
            self.face_generation += 1
            self.face_ready = False
            self.button_primed = False
            self.confirm_since = None
        state = self._state()
        if state != self._state_key:
            self._state_key = state
            self._emit("STATE", *state)
