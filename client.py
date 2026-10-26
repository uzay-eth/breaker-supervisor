"""Host-only reference UART client for HIL and examples (not a forwarding proxy)."""
import os
import select
import socket
import termios
import time
import tty
from breaker.wire import Decoder, encode


class Client:
    def __init__(self, endpoint):
        self.socket = None
        self.serial = None
        if endpoint.startswith("tcp:"):
            host, port = endpoint[4:].rsplit(":", 1)
            if host != "127.0.0.1":
                raise ValueError("SIM UART TCP must be loopback")
            self.socket = socket.create_connection((host, int(port)), timeout=2)
            self.socket.setblocking(False)
            self.fd = self.socket.fileno()
        else:
            self.fd = os.open(endpoint, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            self.serial = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
            attrs = termios.tcgetattr(self.fd)
            attrs[4] = attrs[5] = termios.B115200
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        self.decoder = Decoder()
        self.started = time.monotonic()
        self.boot = None
        self.seq = 0
        self.last_event = 0
        self.state = None
        self.events = []
        self.last_ping = 0
        self.write(encode("HELLO"))

    def now(self):
        return int((time.monotonic()-self.started)*1000)

    def write(self, data):
        deadline = time.monotonic()+1
        while data:
            if time.monotonic() > deadline: raise TimeoutError("UART write")
            _, ready, _ = select.select([], [self.fd], [], 0.05)
            if ready:
                count = os.write(self.fd, data)
                data = data[count:]

    def command(self, verb, *fields):
        if self.boot is None: raise RuntimeError("wait for READY")
        self.seq += 1
        self.write(encode(verb, self.boot, self.seq, *fields))
        return self.seq

    def pump(self, timeout=0.01, heartbeat=True):
        if heartbeat and self.boot and self.now()-self.last_ping >= 500:
            self.command("PING")
            self.last_ping = self.now()
        readable, _, _ = select.select([self.fd], [], [], timeout)
        results = self.decoder.expire(self.now())
        if readable:
            data = os.read(self.fd, 4096)
            if not data: raise EOFError("UART disconnected")
            results += self.decoder.feed(data, self.now())
        for fields, error in results:
            if error: raise ValueError("UART response " + error)
            if len(fields) < 3: raise ValueError("UART response shape")
            if self.boot is None:
                if fields[0] != "READY": continue
                self.boot = fields[1]
            if fields[1] != self.boot: raise RuntimeError("supervisor reboot; discard pending")
            event = int(fields[2])
            if event <= self.last_event: continue
            self.last_event = event
            self.events.append(fields)
            if fields[0] == "STATE":
                self.state = fields
                self.seq = max(self.seq, int(fields[-2]))

    def wait(self, predicate, timeout=5):
        deadline = time.monotonic()+timeout
        while not predicate():
            if time.monotonic() > deadline: raise TimeoutError("UART state predicate")
            self.pump()

    def close(self):
        if self.socket is not None: self.socket.close()
        elif self.serial is not None:
            termios.tcsetattr(self.fd, termios.TCSANOW, self.serial)
            os.close(self.fd)
