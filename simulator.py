"""SIM UART / SIM e-ink / SIM cut. No GPIO or real network-cut claim.

UART: TCP loopback byte stream (or --uart-device PTY). Human controls are on a
separate loopback TCP socket; they do not exist in the firmware UART protocol.
"""
import argparse
import json
import os
from pathlib import Path
import selectors
import socket
import time
import tty

from breaker.core import Supervisor
from face_host import render_steps
from breaker.wire import encode, Decoder
from png import save_face


def listen(port):
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(4)
    sock.setblocking(False)
    return sock


class Simulation:
    def __init__(self, output, panel_ms=50):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.logfile = (self.output / "SIM-events.jsonl").open("w")
        self.s = Supervisor(os.urandom(16).hex(), bench=True)
        self.inputs = [1, 1, 1, 1, 0]  # TRANSIT at start; no automatic human reset.
        self.panel_ms = panel_ms
        self.panel_stuck = False
        self.panel_job = None
        self.panel_buffer = None
        self.panel_generation = 0
        self.panel_started = 0
        self.panel_done_at = None
        self.rendered = 0
        self.tx = bytearray()
        self.running = True
        self.start = time.monotonic()
        self.last_outputs = None
        self.log("SIM start", boot=self.s.boot_id)

    def now(self):
        return int((time.monotonic()-self.start)*1000)

    def log(self, kind, **values):
        entry = {"ms": self.now(), "kind": kind, **values}
        self.logfile.write(json.dumps(entry, sort_keys=True)+"\n")
        self.logfile.flush()

    def flush_events(self):
        for event in self.s.drain():
            self.log("SIM UART TX", fields=event)
            self.tx.extend(encode(*event))
        if len(self.tx) > 8192:
            self.tx.clear()
            self.s.fail("io.backpressure")

    def poll(self):
        now = self.now()
        self.s.sample(now, *self.inputs)
        output = (self.s.enabled, self.s.ring)
        if output != self.last_outputs:
            self.log("SIM cut / SIM ring", cut_enable=int(output[0]), ring=output[1])
            self.last_outputs = output
        if self.panel_job is None and self.panel_done_at is None and self.rendered != self.s.face_generation:
            self.panel_generation = self.s.face_generation
            self.panel_started = now
            self.panel_job = render_steps(self.s.face_model())
        if self.panel_job is not None:
            try:
                buffer = next(self.panel_job)
                if buffer is not None:
                    self.panel_buffer = buffer
                    self.panel_job = None
                    self.panel_done_at = now + self.panel_ms
            except Exception:
                self.s.panel_failed()
                raise
        if self.panel_done_at is not None:
            if now - self.panel_started > 10000:
                self.panel_done_at = None
                self.rendered = self.s.face_generation
                self.s.panel_failed()
                self.log("SIM e-ink fault", generation=self.panel_generation)
            elif not self.panel_stuck and now >= self.panel_done_at:
                # Buffer is what the driver would show, even if superseded mid-refresh.
                name = "SIM-%06d-face.png" % self.panel_generation
                save_face(self.panel_buffer, self.output / name)
                save_face(self.panel_buffer, self.output / "SIM-latest.png")
                self.rendered = self.panel_generation
                self.panel_done_at = None
                self.s.panel_done(self.panel_generation, now)
                self.log("SIM e-ink complete", generation=self.panel_generation, file=name)
        self.flush_events()

    def receive(self, data):
        self.log("SIM UART RX", hex=data.hex())
        self.s.receive(data, self.now())
        self.flush_events()

    def control(self, data):
        if not isinstance(data, dict) or len(data) != 1:
            raise ValueError("one control field required")
        key, value = next(iter(data.items()))
        if key == "handle" and value in ("ON", "OFF", "TRANSIT", "INVALID"):
            self.inputs[:2] = {"ON": [0, 1], "OFF": [1, 0], "TRANSIT": [1, 1], "INVALID": [0, 0]}[value]
        elif key in ("button", "fault", "tamper_open") and type(value) is bool:
            index = {"button": 2, "fault": 3, "tamper_open": 4}[key]
            self.inputs[index] = int(value) if key == "tamper_open" else int(not value)
        elif key == "panel_stuck" and type(value) is bool:
            self.panel_stuck = value
        elif key == "stop" and value is True:
            self.running = False
        elif key != "status" or value is not True:
            raise ValueError("invalid SIM control")
        self.log("SIM physical control", control=data)
        # Apply controls now, not when a UART packet happens to arrive.
        self.poll()
        return {"sim": True, "boot": self.s.boot_id, "state": self.s._state(),
                "reason": self.s.reason, "inputs": self.inputs,
                "face": str(self.output / "SIM-latest.png")}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--uart-port", type=int, default=19400)
    ap.add_argument("--uart-device", help="PTY/serial path instead of TCP")
    ap.add_argument("--control-port", type=int, default=19401)
    ap.add_argument("--control-socket", help="Unix SIM control socket instead of loopback TCP; path must not exist")
    ap.add_argument("--output", default=str(Path(__file__).parent / "runs" / "SIM-live"))
    ap.add_argument("--panel-ms", type=int, default=50, help="SIM refresh delay; not hardware timing")
    args = ap.parse_args()
    if not 0 <= args.panel_ms <= 10000:
        ap.error("panel-ms must be 0..10000")
    sim = Simulation(args.output, args.panel_ms)
    sel = selectors.DefaultSelector()
    uart_server = None
    uart = None
    if args.uart_device:
        uart = os.open(args.uart_device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        tty.setraw(uart)
        sel.register(uart, selectors.EVENT_READ, "uart")
    else:
        uart_server = listen(args.uart_port)
        sel.register(uart_server, selectors.EVENT_READ, "uart_accept")
    if args.control_socket:
        controls = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        controls.bind(args.control_socket)  # Never unlink/replace an existing endpoint.
        os.chmod(args.control_socket, 0o600)
        controls.listen(4)
        controls.setblocking(False)
        socket_inode = os.stat(args.control_socket).st_ino
    else:
        controls = listen(args.control_port)
    sel.register(controls, selectors.EVENT_READ, "control_accept")
    buffers = {}
    print(json.dumps({"sim": True, "uart": args.uart_device or ["127.0.0.1", uart_server.getsockname()[1]],
                      "control": args.control_socket or ["127.0.0.1", controls.getsockname()[1]], "output": str(sim.output)}), flush=True)
    try:
        while sim.running:
            sim.poll()
            for key, _ in sel.select(0.005):
                obj, kind = key.fileobj, key.data
                if kind == "uart_accept":
                    new, _ = obj.accept(); new.setblocking(False)
                    if uart is not None:
                        new.close()
                    else:
                        uart = new
                        sim.tx.clear()
                        sim.s.decoder = Decoder()
                        sel.register(uart, selectors.EVENT_READ, "uart")
                        sim.log("SIM UART attached")
                elif kind == "control_accept":
                    conn, _ = obj.accept(); conn.setblocking(False)
                    if len(buffers) >= 8:
                        conn.close()
                    else:
                        buffers[conn] = (bytearray(), time.monotonic())
                        sel.register(conn, selectors.EVENT_READ, "control")
                elif kind == "control":
                    data = obj.recv(512)
                    buffer, _ = buffers[obj]
                    buffer.extend(data)
                    if not data or len(buffer) > 512 or b"\n" in buffer:
                        try:
                            if len(buffer) > 512: raise ValueError("control_size")
                            reply = sim.control(json.loads(bytes(buffer)))
                        except (ValueError, TypeError, KeyError):
                            reply = {"sim": True, "error": "invalid control"}
                        try: obj.send((json.dumps(reply)+"\n").encode())
                        except OSError: pass
                        sel.unregister(obj); buffers.pop(obj); obj.close()
                elif kind == "uart":
                    try:
                        data = os.read(obj, 256) if isinstance(obj, int) else obj.recv(256)
                    except OSError:
                        data = b""
                    if data:
                        sim.receive(data)
                    else:
                        sel.unregister(obj)
                        if isinstance(obj, int): os.close(obj)
                        else: obj.close()
                        uart = None
                        sim.tx.clear()
                        sim.s.decoder = Decoder()
                        sim.s.fail("uart.disconnect")
                        sim.log("SIM UART disconnected; cut")
            for conn, (_, started) in list(buffers.items()):
                if time.monotonic()-started > 1:
                    sel.unregister(conn); buffers.pop(conn); conn.close()
            if uart is not None and sim.tx:
                try:
                    n = os.write(uart, sim.tx[:256]) if isinstance(uart, int) else uart.send(sim.tx[:256])
                    del sim.tx[:n]
                except BlockingIOError:
                    pass
                except OSError:
                    sim.s.fail("uart.disconnect")
                    sim.tx.clear()
            elif uart is None:
                sim.tx.clear()  # Logs retain events; a reconnect cannot replay a release.
    finally:
        sim.s.fail("sim.stopped")
        sim.log("SIM stopped", cut_enable=0)
        sim.logfile.close()
        for key in list(sel.get_map().values()):
            obj = key.fileobj
            if isinstance(obj, int): os.close(obj)
            else: obj.close()
        sel.close()
        if args.control_socket:
            try:
                if os.lstat(args.control_socket).st_ino == socket_inode:
                    os.unlink(args.control_socket)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
