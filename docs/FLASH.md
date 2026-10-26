# Pico 2 flash and local qualification

This is an unqualified development circuit. Source pins are in
`breaker/config.py`. GP18 is active-high through a HiLetgo B00LW15A4W H-trigger
relay buffer with two PN2222 stages; never connect the Pico directly to a 5 V
relay input. GP14 is unused: there is no fault/contact feedback. Handle pole A
and relay COM–NO are in series in USB VBUS. The two throws of pole B sense
ON/OFF; they do not independently measure pole A. Preserve data lines and ground.
Do not attempt wiring without a separately checked circuit and actual module
identity. A welded contact, phantom power and link latency require bench tests.

## Application upload

1. Isolate carrier/relay/load power and both policy UART leads. Use the Pico's
   USB data cable alone, handle OFF and load disconnected. Programming USB,
   RUN and SWD must not be connected to the Linux policy host during operation.
2. Obtain an official **RPI_PICO2** MicroPython UF2 independently. Verify its
   publisher provenance and digest; record version and SHA-256. Do not use a
   Pico/RP2040 or Pico 2 W image. No UF2 is bundled or downloaded by these tools.
3. Hold BOOTSEL while connecting USB. Confirm the mounted volume's
   INFO_UF2.TXT identifies RP2350. Copy the verified UF2 to that volume using
   the file manager, or use `provision/flash.py --uf2 ... --uf2-sha256 ...
   --volume ...` with the actual selected paths and checked 64-hex digest.
   This writes the MicroPython runtime, not the application filesystem.
4. For an existing application, hold CONFIRM with the handle OFF at power-on,
   and keep both held through upload. This physical maintenance boot keeps
   relay enable, ring and SE051 disabled and does not start the policy loop.
   It permits USB transfer without watchdog interruptions. No UART command
   enters maintenance. A first flash uses the stock runtime's USB REPL.
5. With mpremote already available in the locked provisioning group, identify
   the explicitly connected Pico USB serial port and upload:

   ```sh
   uv run --offline --group provision mpremote connect list
   python3 provision/flash.py --stage scratch/flash --upload --port YOUR_PICO_USB_PORT
   ```

   The script verifies every UF2 block if copying a UF2, stages 14 files, holds
   configured outputs LOW, copies application files in one raw-REPL session and
   resets. Retain `scratch/flash/manifest.json` in a local hardware record, not
   in the source repository. Do not burn OTP or signed-boot fuses for this test.

## Panel selection and qualification

No qualification record ships. The application refuses to arm without one.
Both 296×128 B/W profiles are supported: V1 IL3820 and V2 SSD1680. Identify the
physical revision from actual sticker/PCB/FPC evidence, not an online listing.
An absent V2 sticker does not prove V1. Unknown or conflicting identity must
remain unverified. See [PANEL-SOURCES.md](PANEL-SOURCES.md) for the command and
timing references, checked dates and BUSY assumptions.

Keep the load, carrier and policy UART isolated; use local USB power, handle
OFF, CONFIRM held at power-on. At 20–30 °C bench ambient, select the identified
profile without editing code:

```sh
python3 provision/panel_test.py --port YOUR_PICO_USB_PORT --panel V2 --identity "ACTUAL module revision and local photo reference"
```

Release CONFIRM when prompted. Within the 60 s physical test, inspect white,
pattern, inverse and pattern images for complete coverage, contrast and correct
orientation. After the last image, release and hold CONFIRM for one second only
if every image is correct. No response or any failure leaves the panel
unqualified. GP18 and the ring remain LOW throughout. The driver requires BUSY
assertion and deassertion; BUSY alone cannot prove optical correctness.

The successful local record binds profile, identity, checks and installed source
fingerprint. The host copies and checksums the exact device record. Retain that
record locally. Changing panel, font, renderer, driver or configuration requires
retesting. UART cannot select or verify a panel. Power-cycle with the button
released; arming still needs live PING plus a fresh physical OFF→ON cycle.

## Hardware acceptance

Only after independently qualifying the wiring, remove programming USB and
connect the 3.3 V policy UART with common ground. The recorder must be its sole
writer; stop any Linux proxy and persistent UART broker first:

```sh
python3 provision/hil.py --uart YOUR_PHYSICAL_UART --output HIL-arrival.jsonl
```

The recorder separates UART observations from human-entered measurements. It
never fakes a button or handle, chooses a port automatically, or fills in a
hardware PASS. Use only the local mock request. Do not publish hardware records
containing machine identifiers. Record:

- GP18 LOW/ring dark from power-on and reset, including high-impedance states.
  Already-ON at boot cannot arm. LOW, floating or absent Pico must leave the
  coil off and COM–NO open; test with the relay supply still powered.
- AGENT_5V dead with handle OFF, with GP18 LOW, and with Pico removed; live only
  with handle ON plus locally enabled GP18. Record actual rail/control voltages
  and independent traffic evidence; a click or LED does not prove contact state.
- Safe relay buffer polarity and all independent vetoes, no back-power, USB/link
  behavior, contact bounce and cut endpoints using scope/packet captures. The
  100 ms observation settling interval is a DESIGN wait, not a measured bound.
  Acquisition starts at the actual edge and includes that interval.
- Dim green ARMED, amber pulse HELD, green flash APPROVED, red TRIPPED and dark
  OFF. Verify actual color/brightness and OPEN BENCH on the physical face.
  Confirm full redraw, 3 s APPROVED dwell and 5 s NOT SENT dwell after 60 s expiry.
- Fresh one-second local confirmation only after the matching HELD face; short,
  preheld or cancelled presses never release. Check ticket/full hash and no
  second release. Changed bytes, forbidden UART restore, host loss and invalid
  contacts must follow PROTOCOL.md. Physical OFF→ON is the only rearm path.
- RP2350 render/panel-step, GC and sample-gap maxima under UART load. Preserve
  the 100 ms hard stall veto; target sample gaps at most 20 ms for margin. Do not
  raise limits to make a test pass. Measure cut/link timing independently.

The final absent-Pico test first cuts and closes UART, then requires all power
isolated before removal. Keep the relay/buffer common ground and supply intact
when testing the absent state. Refit only while unpowered. No acceptance result
is prefilled, and no end-to-end timing or isolation guarantee follows from the
bare relay specification, software timers or simulated tests.
