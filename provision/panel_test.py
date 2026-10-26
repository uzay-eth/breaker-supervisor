#!/usr/bin/env python3
"""Select V1/V2 in one command and record the physical one-minute arrival test.

This uses the already flashed driver and never changes application code. Module
markings select the profile; output compatibility alone does not identify silicon.
"""
import argparse
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SUPERVISOR = HERE.parent
sys.path.insert(0, str(SUPERVISOR))
from breaker.panel_record import PROFILES, valid


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", required=True, help="explicit locally attached Pico USB serial device")
    ap.add_argument("--panel", choices=("V1", "V2"), required=True)
    ap.add_argument("--identity", required=True, help="exact revision sticker/PCB/FPC markings and photo reference")
    ap.add_argument("--output", type=Path, help="new host evidence file; default build/HIL-panel-UTC.json")
    args = ap.parse_args()
    if not 1 <= len(args.identity) <= 160 or any(not 32 <= ord(c) <= 126 for c in args.identity):
        ap.error("identity must be 1..160 printable ASCII characters, taken from the hardware")
    if not args.port.startswith("/dev/"):
        ap.error("select the physical Pico USB serial device under /dev; no remote/SIM endpoint")
    profile = PROFILES[0] if args.panel == "V1" else PROFILES[1]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or HERE/"build"/("HIL-panel-"+stamp+".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve exclusively; a failed run leaves an explicit failure, not an old PASS.
    with output.open("x") as target:
        target.write(json.dumps({"hardware": True, "status": "incomplete", "profile": profile})+"\n")
    device_record = output.with_name(output.stem+"-device.json")
    if device_record.exists():
        raise FileExistsError(device_record)
    print("Physical preparation: load/carrier/UART disconnected; handle OFF.", flush=True)
    print("Hold CONFIRM through Pico power-on and USB connection. Release when the device asks.", flush=True)
    print("V1/V2 must match actual revision evidence, not the Amazon title or an absent sticker.", flush=True)
    expression = "from breaker.panel_arrival import run; run(%r, %r)" % (profile, args.identity)
    command = ["uv", "run", "--offline", "--project", str(SUPERVISOR), "--group", "provision",
               "mpremote", "connect", args.port, "exec", expression, "+", "fs", "cp",
               ":panel-verified.json", str(device_record)]
    try:
        # The physical test itself has a 60 s cutoff. The extra 15 s bounds USB
        # connection/copy overhead, not a panel refresh specification.
        subprocess.run(command, check=True, timeout=75)
        record = json.loads(device_record.read_text())
        if not valid(record, record.get("fingerprint")) or record["profile"] != profile or record["identity"] != args.identity:
            raise ValueError("invalid returned panel evidence")
        result = {"hardware": True, "status": "operator_verified", "host_utc": stamp,
                  "port": args.port, "device_record": record,
                  "device_record_sha256": hashlib.sha256(device_record.read_bytes()).hexdigest(),
                  "identity_method": "operator module markings; not SPI autodetection",
                  "cut_enable": "held LOW throughout test"}
        output.write_text(json.dumps(result, indent=2)+"\n")
    except BaseException as exc:
        output.write_text(json.dumps({"hardware": True, "status": "failed_or_incomplete", "host_utc": stamp,
                                      "profile": profile, "reason": str(exc)}, indent=2)+"\n")
        raise
    print("Recorded:", output)
    print("Release CONFIRM and power-cycle. Arming still requires the normal live UART + physical OFF->ON.")


if __name__ == "__main__":
    main()
