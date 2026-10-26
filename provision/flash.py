#!/usr/bin/env python3
"""Stage a reproducible Pico file tree; optionally copy a verified UF2 and upload.

Never downloads firmware, chooses a serial device automatically, or burns OTP.
Default stages files only. Use --upload only with the isolated, locally attached
Pico selected explicitly. Physical flash/wiring steps are in README.md.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
SUPERVISOR = HERE.parent
MODULES = ("__init__.py", "canonical.py", "wire.py", "core.py", "face.py", "font.bin",
           "FONT-LICENSE.txt", "config.py", "epaper.py", "panel_record.py", "panel_arrival.py", "EPD-LICENSE.txt")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage(target):
    target.mkdir(parents=True, exist_ok=True)
    package = target / "breaker"
    package.mkdir(exist_ok=True)
    # Upload copies this package recursively: reject stale host-only files rather
    # than accidentally flashing them from a reused staging directory.
    if package.is_symlink() or any(p.name not in MODULES or not p.is_file() or p.is_symlink()
                                   for p in package.iterdir()):
        raise ValueError("unexpected file in flash package; use a clean stage directory")
    for name in MODULES:
        shutil.copy2(SUPERVISOR / "breaker" / name, target / "breaker" / name)
    for name in ("boot.py", "main.py"):
        shutil.copy2(SUPERVISOR / "firmware" / name, target / name)
    files = [target / n for n in ("boot.py", "main.py")] + [target / "breaker" / n for n in MODULES]
    manifest = {str(p.relative_to(target)): sha(p) for p in files}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    return manifest


def check_uf2(path, expected):
    if len(expected) != 64 or sha(path) != expected.lower():
        raise ValueError("UF2 SHA-256 mismatch")
    data = path.read_bytes()
    if not data or len(data) % 512:
        raise ValueError("not a UF2 block stream")
    for offset in range(0, len(data), 512):
        block = data[offset:offset+512]
        if struct.unpack("<II", block[:8]) != (0x0a324655, 0x9e5d5157) or struct.unpack("<I", block[-4:])[0] != 0x0ab16f30:
            raise ValueError("invalid UF2 block")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", type=Path, default=HERE / "build")
    ap.add_argument("--uf2", type=Path, help="Official MicroPython RPI_PICO2 UF2, already downloaded")
    ap.add_argument("--uf2-sha256", help="SHA-256 checked against the selected official download")
    ap.add_argument("--volume", type=Path, help="Mounted RP2350 BOOTSEL volume")
    ap.add_argument("--upload", action="store_true", help="Upload staged files through mpremote")
    ap.add_argument("--port", help="Explicit Pico USB serial path, e.g. /dev/cu.usbmodem... (not UART0)")
    a = ap.parse_args()
    if a.uf2 and (not a.uf2_sha256 or not a.volume):
        ap.error("--uf2 requires --uf2-sha256 and --volume")
    if a.upload and not a.port:
        ap.error("--upload requires an explicit --port")
    manifest = stage(a.stage)
    print("Staged", len(manifest), "files:", a.stage / "manifest.json")
    if a.uf2:
        check_uf2(a.uf2, a.uf2_sha256)
        info = (a.volume / "INFO_UF2.TXT").read_text()
        if "RP2350" not in info.upper():
            raise ValueError("selected BOOTSEL volume does not identify RP2350")
        shutil.copyfile(a.uf2, a.volume / "breaker-micropython.uf2")
        print("UF2 copied; reconnect/enumerate USB before uploading application files.")
        if a.upload:
            deadline = time.monotonic()+15
            while not Path(a.port).exists():
                if time.monotonic() > deadline: raise TimeoutError("Pico USB port did not appear")
                time.sleep(0.1)
    if a.upload:
        mp = ["uv", "run", "--offline", "--project", str(SUPERVISOR), "--group", "provision",
              "mpremote", "connect", a.port]
        # One session: keep the VM in raw REPL while copying, reboot only at end.
        # Direct veto before touching existing application files.
        # Pull actual pin values from the staged config; no second pin map.
        namespace = {}
        exec((a.stage / "breaker" / "config.py").read_text(), namespace)
        pins = namespace["PINS"]
        veto = "from machine import Pin; " + "; ".join(
            "Pin(%d,Pin.OUT,value=0)" % pins[n] for n in ("cut_enable", "led_r", "led_g", "led_b", "se_enable"))
        cmd = mp + ["exec", veto, "+", "fs", "cp", str(a.stage / "boot.py"), ":boot.py", "+",
                    "fs", "cp", "-r", str(a.stage / "breaker"), ":.", "+",
                    "fs", "cp", str(a.stage / "main.py"), ":main.py", "+", "reset"]
        subprocess.run(cmd, check=True)
        print("Uploaded and reset. Record firmware/runtime IDs and perform HIL; upload is not a hardware PASS.")
    else:
        print("No application files uploaded. The staged config is unchanged; the repository default cannot arm.")


if __name__ == "__main__": main()
