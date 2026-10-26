import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import pty
import tty
from breaker.canonical import payout, digest, to_hex
from breaker.wire import encode, Decoder
from client import Client
from simctl import control

ROOT = Path(__file__).resolve().parents[1]


class SimulatorTests(unittest.TestCase):
    def test_pty_uart_and_unix_physical_controls(self):
        # Short private path is required by macOS's Unix socket name limit.
        with tempfile.TemporaryDirectory(prefix="SIM-pty-", dir="/tmp") as directory:
            master, slave = pty.openpty()
            tty.setraw(master); tty.setraw(slave)
            path = str(Path(directory)/"SIM-control.sock")
            process = subprocess.Popen([sys.executable, str(ROOT/"simulator.py"), "--uart-device", os.ttyname(slave),
                                        "--control-socket", path, "--output", directory],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"})
            try:
                self.assertTrue(select.select([process.stdout], [], [], 5)[0])
                details = json.loads(process.stdout.readline())
                self.assertEqual(details["control"], path)
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
                os.write(master, encode("HELLO"))
                decoder, events = Decoder(), []
                deadline = time.monotonic()+2
                while not any(e[0] == "READY" for e in events) and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.05)[0]:
                        events.extend(fields for fields, error in decoder.feed(os.read(master, 4096), 0) if not error)
                self.assertTrue(any(e[0] == "READY" for e in events))
                result = control(path, {"handle": "OFF"})
                self.assertTrue(result["sim"])
                self.assertEqual(result["inputs"][:2], [1, 0])
                self.assertEqual(result["state"][2], 0)
                control(path, {"stop": True}); process.wait(timeout=3)
                self.assertEqual(process.returncode, 0, process.stderr.read())
                self.assertFalse(Path(path).exists())
            finally:
                if process.poll() is None: process.terminate(); process.wait(timeout=3)
                process.stdout.close(); process.stderr.close()
                os.close(master); os.close(slave)

    def test_real_socket_release_mismatch_trip_and_off(self):
        with tempfile.TemporaryDirectory(prefix="SIM-supervisor-") as directory:
            process = subprocess.Popen([sys.executable, str(ROOT/"tests/socket_fixture.py"), "--uart-port", "0",
                                        "--control-port", "0", "--output", directory],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"})
            client = None
            try:
                if not select.select([process.stdout], [], [], 30)[0]:
                    self.fail("simulator startup timeout")
                details = json.loads(process.stdout.readline())
                port = details["control"][1]
                client = Client("tcp:127.0.0.1:%d" % details["uart"][1])
                def wait(predicate):
                    # Wall time bounds transport progress only, not device timing.
                    try:
                        client.wait(predicate, timeout=30)
                    except TimeoutError as exc:
                        raise AssertionError("SIM wait failed: " + repr(control(port, {"status": True}))) from exc
                wait(lambda: client.boot is not None and client.state is not None)
                client.command("PING")
                control(port, {"handle": "OFF"})
                wait(lambda: client.state[3] == "OFF" and client.state[10] == "1")
                # Regression: the old wall-clock fixture loses its OFF witness to
                # loop.stall here, then times out waiting for ARMED. Always resume.
                os.kill(process.pid, signal.SIGSTOP)
                try:
                    time.sleep(0.25)
                finally:
                    os.kill(process.pid, signal.SIGCONT)
                # Stable OFF sampling and face completion both observed before ON.
                control(port, {"handle": "ON"})
                wait(lambda: client.state[3] == "ARMED" and client.state[10] == "1")
                raw = payout(184000)
                ticket = client.command("HOLD", digest(raw), to_hex(raw))
                wait(lambda: client.state[3] == "HELD" and client.state[10] == "1")
                released_at = control(port, {"button": False})["fixture_ms"]
                until = time.monotonic()+30
                while control(port, {"status": True})["fixture_ms"]-released_at < 40:
                    self.assertLess(time.monotonic(), until, "SIM release debounce did not progress")
                    client.pump()
                pressed_at = control(port, {"button": True})["fixture_ms"]
                wait(lambda: any(e[0] == "RELEASE" for e in client.events))
                trace = [json.loads(line) for line in (Path(directory)/"SIM-events.jsonl").read_text().splitlines()]
                released = next(row for row in trace if row["kind"] == "SIM UART TX" and row["fields"][0] == "RELEASE")
                self.assertGreaterEqual(released["ms"]-pressed_at, 1000)
                releases = [e for e in client.events if e[0] == "RELEASE"]
                self.assertEqual([(e[3], e[4]) for e in releases], [(str(ticket), digest(raw))])
                control(port, {"button": False})
                wait(lambda: client.state[3] == "ARMED" and client.state[10] == "1")
                ticket = client.command("HOLD", digest(raw), to_hex(raw))
                wait(lambda: client.state[3] == "HELD")
                mutated = payout(184001)
                client.command("CHECK", ticket, digest(mutated), to_hex(mutated))
                wait(lambda: client.state[3] == "TRIPPED" and client.state[5] == "0")
                self.assertEqual(len([e for e in client.events if e[0] == "RELEASE"]), 1)
                control(port, {"handle": "OFF"})
                wait(lambda: client.state[3] == "OFF" and client.state[10] == "1")
                control(port, {"handle": "ON"})
                wait(lambda: client.state[3] == "ARMED" and client.state[10] == "1")
                client.command("TRIP", "mail.mass_delete")
                wait(lambda: client.state[3] == "TRIPPED")
                self.assertEqual(client.state[5], "1")
                control(port, {"handle": "OFF"})
                wait(lambda: client.state[3] == "OFF" and client.state[10] == "1")
                self.assertEqual(client.state[5], "0")
                self.assertGreater(len(list(Path(directory).glob("SIM-*-face.png"))), 4)
                control(port, {"stop": True})
                process.wait(timeout=3)
                self.assertEqual(process.returncode, 0, process.stderr.read())
            finally:
                if client: client.close()
                if process.poll() is None: process.terminate(); process.wait(timeout=3)
                process.stdout.close(); process.stderr.close()
