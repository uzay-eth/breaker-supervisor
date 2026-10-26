# breaker-supervisor

**Status: in development, not production hardware.**

The Breaker supervisor is MicroPython firmware for a Raspberry Pi Pico 2 that
owns handle sensing, a confirmation button, an RGB ring, a 296×128 e-paper face
and an active-high relay enable. It parses a restricted payout request and binds
a physical one-second confirmation to that request's full SHA-256. The host
simulator imports the same state machine and renderer. Project:
[orkhon.ai/breaker](https://orkhon.ai/breaker).

## Boundary and limits

The software starts with GP18 LOW. Under the documented circuit and input
assumptions, only a newly observed physical OFF→ON cycle can enable it. Linux
can request a cut or a policy trip; no UART command restores power, synthesizes
a button press or releases a hold. A release consumes one pending ticket and
full hash after the matching display completes and a fresh local confirmation.
The face uses fixed copy and locally parsed fields, with a locally owned payee
label. See [PROTOCOL.md](PROTOCOL.md) for exact framing, canonical bytes, timing,
noise handling, reset and host obligations.

These are software guarantees under the stated assumptions, not hardware
qualification. Level A limits apply: Linux forwards the HTTP bytes, and a
compromised Linux host can ignore this policy while its port is enabled. A
release is authorization, not evidence of payment or delivery. Protocol 1 has
no authentication or signatures; CRC detects noise. There is no secure-element
dependency, signed boot, phone approval, pairing or network re-arm. Physical
programming access can replace the firmware.

The intended VBUS cut uses a HiLetgo H-trigger relay and two non-inverting NPN
buffer stages, in series with handle pole A. GP18 LOW/floating/Pico absent must
leave the coil off and COM–NO open. There is no contact-feedback or current-fault
sensor. **Phantom power, contact behavior, cut/link timing and the complete
circuit are not yet bench-qualified.** E-paper can retain an old image without
power; it is not a live power indicator. Each panel must pass local qualification
before arming. Simulated tests cannot establish any of these physical properties.

## Tests and compiler check

Python 3.12 is used for host tests. They use the standard library and local
loopback TCP/PTY sockets, not external services:

```sh
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

The locked optional `verify` group supplies mpy-cross; the `provision` group
supplies mpremote. With those packages already in the local uv cache:

```sh
uv sync --offline --frozen --group verify --group provision
uv run --offline --group verify python tools/check_micropython.py
```

The compiler check does not run RP2350 peripherals. A new contributor without a
cache must obtain the locked dependencies separately before using offline mode.
Runtime firmware and simulation have no third-party Python dependency.

## Simulator and examples

```sh
python3 simulator.py --uart-port 19400 --control-port 19401 --output runs/SIM-local
```

In another terminal, connect a UART protocol client which sends HELLO and regular
PINGs. The optional example client can keep the session live while controls are
operated:

```sh
python3 -c 'from client import Client; c=Client("tcp:127.0.0.1:19400"); exec("while True: c.pump(0.05)")'
python3 simctl.py --port 19401 handle OFF
python3 simctl.py --port 19401 handle ON
python3 simctl.py --port 19401 status
```

Wait for the OFF face to complete before ON; arming still requires live UART
heartbeat. Keep the client in its own terminal. The UART endpoint accepts only
protocol frames; the separate local control socket supplies SIM physical inputs.
Neither endpoint is a production management interface. Stop with
`python3 simctl.py --port 19401 stop`. All logs and runtime image names say SIM.

```sh
python3 tools/render_faces_v02.py --snapshot --output examples
python3 tools/reference.py vector
```

[Example images](examples/README.md) use a render-only **DEMO** header.
Snapshot rendering writes `examples/RENDER-PROVENANCE.md` and preserves the example README.
`face_host.py` is not in the flash payload; hardware always shows **OPEN BENCH**.
SETUP is a fixed illustration only. APPROVED and NOT SENT are display summaries,
not new UART modes or reusable releases. Earlier host DENY cancels immediately;
NOT SENT requires the Pico's actual 60 s expiry. Full redraw only; hardware
refresh duration and visible dwell need measurement.

## Flash and hardware checks

Read [docs/FLASH.md](docs/FLASH.md) first. Use a verified official **RPI_PICO2**
MicroPython UF2, an explicitly selected local USB port and isolated bench power.
Stage only, with no device access:

```sh
python3 provision/flash.py --stage scratch/flash
```

The stage manifest hashes all 14 application/font/license files; host render code
is excluded. Unknown files in a reused package directory are rejected. Upload,
panel selection and the physical acceptance recorder are described in the guide.
Never infer hardware acceptance from compilation or simulation.

## Licenses

Copyright 2026 Orkhon. Original project code and documentation are licensed under
[Apache-2.0](LICENSE), except for the following retained third-party material:

- `assets/Inter.woff2` and its derived `breaker/font.bin` remain under the
  [SIL Open Font License 1.1](breaker/FONT-LICENSE.txt), not Apache-2.0. The bitmap
  has documented optical/baseline modifications in `tools/build_font.py` and
  `fixtures/font-source.txt`. Rebuilding requires a local Pillow installation;
  running the firmware, tests and PNG generator does not.
- Vendor waveforms and reference-driver material adapted in `breaker/epaper.py`
  retain the [Waveshare permission notice](breaker/EPD-LICENSE.txt). The local
  cooperative driver adaptations are described in `breaker/epaper.py` comments. Command/timing
  sources and checked dates are in [docs/PANEL-SOURCES.md](docs/PANEL-SOURCES.md).

Third-party attribution notices are preserved; these components are not
relicensed by the top-level license.
