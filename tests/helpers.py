from breaker.core import Supervisor
from breaker.canonical import payout, digest, to_hex
from breaker.wire import encode

BOOT = "a" * 32


class Rig:
    def __init__(self, bench=True):
        self.s = Supervisor(BOOT, bench=bench)
        self.now = 0
        self.seq = 0
        self.inputs = [1, 0, 1, 1, 0]
        self.events = []
        self.collect()

    def collect(self):
        self.events += self.s.drain()

    def command(self, verb, *fields, seq=None, boot=BOOT):
        if verb == "HELLO":
            frame = encode(verb)
        else:
            self.seq = self.seq + 1 if seq is None else seq
            frame = encode(verb, boot, self.seq, *fields)
        self.raw(frame)
        return self.seq

    def raw(self, frame):
        for i in range(0, len(frame), 64):
            self.s.receive(frame[i:i + 64], self.now)
            self.collect()

    def step(self, ms=10, heartbeat=False):
        for _ in range(ms // 10):
            self.now += 10
            self.s.sample(self.now, *self.inputs)
            self.collect()
            if heartbeat and self.now % 1000 == 0:
                self.command("PING")

    def panel(self):
        self.s.panel_done(self.s.face_generation, self.now)
        self.collect()

    def arm(self):
        self.command("PING")
        self.inputs[:2] = [1, 0]
        self.step(50)
        self.panel()
        self.inputs[:2] = [0, 1]
        self.step(50)
        self.panel()

    def hold(self, amount=184000, raw=None, claimed=None):
        raw = payout(amount) if raw is None else raw
        ticket = self.command("HOLD", digest(raw) if claimed is None else claimed, to_hex(raw))
        return ticket, raw

    def press(self, ms=1040):
        self.inputs[2] = 0
        self.step(ms, heartbeat=True)

    def release(self):
        self.inputs[2] = 1
        self.step(40)

    def confirms(self):
        return [e for e in self.events if e[0] == "RELEASE"]
