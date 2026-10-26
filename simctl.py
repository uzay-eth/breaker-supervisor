"""Explicit SIM-only controls; never linked into Pico firmware."""
import argparse
import json
import socket


def control(port, command):
    if isinstance(port, str):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(2)
        connection.connect(port)
    else:
        connection = socket.create_connection(("127.0.0.1", port), timeout=2)
    with connection as sock:
        sock.sendall((json.dumps(command)+"\n").encode())
        response = bytearray()
        while b"\n" not in response and len(response) < 4096:
            data = sock.recv(4096)
            if not data: break
            response.extend(data)
        return json.loads(response)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=19401)
    p.add_argument("--socket", help="Unix SIM control socket instead of TCP")
    p.add_argument("command", choices=("status", "handle", "button", "fault", "panel_stuck", "tamper_open", "stop"))
    p.add_argument("value", nargs="?")
    a = p.parse_args()
    value = a.value
    if a.command in ("status", "stop"): value = True
    elif a.command != "handle":
        if value not in ("on", "off"): p.error("value is on or off")
        value = value == "on"
    print(json.dumps(control(a.socket or a.port, {a.command: value}), indent=2))


if __name__ == "__main__":
    main()
