# Breaker supervisor UART protocol 1

In development; not hardware qualification. MicroPython firmware and the CPython simulator import the same implementation in `breaker/`. Linux can use the same codec and canonicalizer. Protocol 1 has no SE051 dependency, signatures or simulated secure-element keys.

## Trust and assumptions

The Pico owns handle sensing, confirmation, face, ring, and GP18. Linux may request HOLD, DENY, CUT or TRIP. It cannot restore, set a GPIO, render arbitrary text, synthesize a button, clear a trip or issue RELEASE. Release is a one-shot Pico event following a fresh physical button hold of at least 1,000 ms, while the exact held face has finished refreshing. A button already down before a hold/refresh does not count.

Level A limitation: Linux ultimately forwards HTTP bytes. A compromised CM5 can ignore a release token or falsify its own logs. This protocol cannot prove downstream transmission, authenticate the CM5 to itself, or prevent malicious Linux from sending other traffic while its port is enabled. The Linux adapter MUST buffer immutable request bytes, compare their SHA-256 again immediately before dispatch, consume each release once, discard pending requests on disconnect/reboot/reset, and never send on an ERROR, DENIED or STATE event. CRC is noise detection, not authentication. The physical VBUS cut is independent of Linux; delivered-hardware phantom power and timing remain bench tests.

On boot GP18 is LOW and all ring channels are off. Restore requires a newly observed stable physical OFF then ON, including after reboot. Policy trips latch governed requests; fault, CUT, invalid structured request or invalid handle contacts latch GP18 off until this physical cycle. A single malformed wire frame cancels holds and latches requests while preserving an existing GP18 enable; repeated wire errors cut as specified below. OFF alone is not enough. Handle (GP16, GP17): ON=(0,1), OFF=(1,0), TRANSIT=(1,1), INVALID=(0,0). These are the **two throws of pole B**, not independent electrical sensing of pole A. The supplied electrical design has no pole-A sense wire; pole A mechanically interrupts VBUS. Do not describe this as two independently sensed poles. Both non-position combinations cut immediately. Stable contacts and button debounce: 30 ms. A configured fault assertion cuts immediately. The current HiLetgo H-trigger relay and two-PN2222 buffer have no fault or contact-feedback output; GP14 is NC. That path is available for the earlier fault-equipped design and is exercised in SIM, not misreported as a present sensor. No passkey restore in this demo. A stable physical OFF witness survives Linux TRIP/CUT, host timeout, UART/protocol errors, disconnect and host TX backpressure while OFF. These events never create an OFF witness or enable the port. Local safety faults (including cut/case/contact/input/display/clock/loop and local hold-integrity faults) erase it; a fresh OFF→ON is then required. Normal OFF→TRANSIT→ON preserves it.

Only one pending hold. Hold lifetime 60,000 ms from acceptance, including panel refresh. Physical OFF, transit, fault, UART protocol error, mismatch, host loss, timeout or a DENY cancels it. No release retry after lost UART output: submit a new HOLD and require a new physical confirmation. Heartbeat PING at least once per second; loss for 3,000 ms after the session first becomes live trips. Before a live session the device cannot arm. Session restoration after loss requires OFF then ON; HELLO/PING never restore. Timeouts use monotonic elapsed milliseconds, not wall clock.

## Wire framing

115200 baud, 8 data bits, no parity, 1 stop bit, 3.3 V UART0 (GP0 TX, GP1 RX with internal pull-up from boot, retained in UART setup). Every frame is ASCII:

```
BRK1|<verb>|<fields...>|<crc32>\n
```

CRC32 is standard CRC-32/ISO-HDLC (`binascii.crc32`), 8 lowercase hex digits, over all bytes preceding the final `|crc32` (no newline). No CR, NUL, escapes, spaces or non-ASCII bytes anywhere. Maximum frame including LF: 1,024 bytes. Canonical bytes are lowercase hex, maximum 384 decoded bytes. Receivers process arbitrary fragments/coalesced frames. Oversize/invalid bytes or >1,000 ms from the first byte to LF cause a protocol error, discard through the next LF, cancel any hold and latch requests. Physical cutting follows the rolling error budget below. Never interpret an oversize suffix as a new command. Empty lines are invalid. Unknown verbs, wrong arity and out-of-range fields fail closed.

**Wire noise budget:** `frame_crc`, `frame_ascii`, `frame_shape`, `frame_size` and `frame_timeout` each count once per rejected frame/discard episode. The first and second error cancel the pending hold, emit DENIED (if held) plus ERROR, reset button confirmation, and latch TRIPPED while preserving the current enable bit. The **third error in a rolling 1,000 ms window** physically cuts. The interval is `(now-1000, now]`; an error exactly 1,000 ms old is excluded. History is bounded to three timestamps. Valid commands, HELLO/PING, and handle reset do not erase the window. Expiry never restores or clears a latch. A single bad frame therefore cannot release a hold, permit a replacement HOLD, or enable an already-cut port. New work requires the normal physical reset and a fresh hold/confirmation.

This noise allowance applies only to decoder/framing errors. Valid-CRC unknown commands, bad arity/session/sequence/ticket/rule, invalid canonical requests, bad claimed hashes, CHECK divergence, adapter `rx.burst`, host timeout, TX backpressure and local safety faults retain immediate physical cutting. CRC is not authorization; a plausible frame cannot invoke a physical control. The Linux adapter must cancel/discard its pending request on every ERROR regardless of STATE cut. To preserve the intended noisy-line behavior, do not automatically send CUT solely for an isolated decoder error reported by Pico; keep heartbeat alive while waiting for a human reset. The Linux adapter's own transport/integrity failures remain fail-closed.

Boot session ID: 16 random bytes, 32 lowercase hex digits, generated once per boot by the Pico. All commands except HELLO carry this ID and a strictly increasing decimal sequence 1..2147483647, no leading zeroes. A duplicate/stale sequence is rejected and trips; do not blindly retry mutating commands. HELLO reveals the existing ID, never resets session, sequence or safety state. Output event numbers are strictly increasing per boot. Serial framing preserves direction; a Pico output verb sent to the Pico is invalid.

### Linux → Pico

| Payload after `BRK1|` | Meaning |
|---|---|
| `HELLO` | Return READY and STATE. Not a restore or heartbeat. |
| `PING|boot|seq` | Liveness only; returns STATE. |
| `STATUS|boot|seq` | Return STATE; not a heartbeat. |
| `HOLD|boot|seq|sha256|canonical_hex` | Parse the entire request locally, verify full digest, hold it. `seq` becomes the ticket. |
| `CHECK|boot|seq|ticket|sha256|canonical_hex` | Strictly parse the candidate and verify its supplied hash, then compare to the matching pending ticket. Cannot release; genuine byte/hash divergence denies and cuts (`hash.mismatch`). |
| `DENY|boot|seq|ticket` | Cancel this hold, never release. |
| `CUT|boot|seq|rule` | Latch a trip and cut. |
| `TRIP|boot|seq|rule` | Latch governed requests; preserve an already enabled GP18 for HTTP 423/model traffic. Cannot enable it. |

`rule` is one of `linux.cut`, `mail.mass_delete`, `secret.exfiltration`, `spend.cap`, `policy.state_invalid`. These are reported by Linux, not independently counted by the Pico. The face uses fixed labels, with no fabricated counts or link measurements. HOLD while another hold exists fails closed and denies the first; never silently replace the screen under a pressed button.

`hash.mismatch` is reserved for a valid CHECK naming the existing ticket with a strictly parsed, correctly hashed candidate that differs from the held bytes/hash. A wrong supplied digest is `hash.invalid`; an absent/wrong CHECK ticket is `ticket.invalid`; corruption detected by the final local reparse is `hold.integrity`. All deny/cut, none release. A later independently submitted altered payout is a new HELD request, not MISMATCH. `CHECK` is diagnostic; a normal buffered HTTP adapter need not use it.

### Pico → Linux

Every output starts `verb|boot|event`. Fields after that:

| Verb | Remaining fields |
|---|---|
| READY | `1` (protocol version) |
| STATE | `mode|handle|cut|ring|ticket|sha256|face_generation|face_ready|last_seq|reset_epoch` |
| HELD | `ticket|sha256|60000` |
| RELEASE | `ticket|sha256` |
| DENIED | `ticket|sha256|reason` |
| ERROR | `reason` |

Modes: OFF, ARMED, HELD, TRIPPED. Handle: UNKNOWN, ON, OFF, TRANSIT, INVALID. Cut: 0/1 is **commanded GP18**, not measured VBUS/link. Ring: off/green/amber/red. Absent ticket is `0`, absent digest is `-`. Face generation is a monotonic local redraw identifier; `face_ready=1` means the matching display refresh completed according to the selected controller's BUSY protocol, not optical verification. STATE is emitted unsolicited on every state transition as well as after each valid command. `reset_epoch` increases only on a successful physical OFF→ON rearm, never on UART. The Linux adapter persists latch provenance `(boot, reset_epoch)` and clears only on proven physical rearm as specified in the host reconnect contract below; an unarmed new boot remains fail-closed. No generic ACK implies permission. Only RELEASE grants one ticket, in the current boot, for one exact hash. The Linux adapter ignores events from other boots and duplicate event numbers.

## Canonical request: strict money mode

Demo supports one operation only: a local mock USD payout. No arbitrary URL, query, headers, agent labels, memos, descriptions, Unicode, floats, booleans, null, arrays, unknown fields or duplicate JSON keys. No real payment provider. The local mock host is an explicit reserved `.test` name. The host and test harness map it to the WAN-side mock; never resolve it to a real provider.

Canonical bytes are ASCII, these nine lines in this exact order, each ending LF (including the last):

```
BREAKER-PAYOUT-1
POST
https
payout.demo.breaker.test
443
/v1/payouts
application/json
0123456789abcdef0123456789abcdef
{"amount":184000,"currency":"usd","destination":"demo_acct_4471"}
```

Line 8 is the actual HTTP Idempotency-Key, exactly 32 lowercase hex characters. Line 9 is the exact HTTP body, excluding its canonical-record trailing LF. Amount is integer USD cents, 1..99999999, canonical decimal without leading zeroes. Currency is exactly `usd`. Destination must exist in the Pico's provisioned device allowlist; initial demo entry `demo_acct_4471` has the device-owned label `acct ••4471`. It is a synthetic local mock fixture, not a real bank/account; the host render header says DEMO and hardware always says OPEN BENCH. JSON body has the exact field order, punctuation and spelling above; no insignificant whitespace is accepted. No reparsing/re-encoding of a mutated original to recover a match. The parser rebuilds this strict representation and requires byte equality. The human sees amount formatted from integer cents, device-owned destination label, fixed payment label and an optional `Log #` plus four-character hash fingerprint. The canonical service/method/path remain strict but are not additional screen lines. Release always uses all 256 bits, never the displayed abbreviation.

`sha256` = SHA-256 of these exact canonical bytes, 64 lowercase hex digits. A changed body byte, whitespace, key order, idempotency key, routing field, trailing newline or destination is either a different hash or an invalid request; it cannot reuse a hold. For an HTTP client, the Linux adapter must construct exactly the shown body and Idempotency-Key and fix method, authority, port, path and content type to the parsed values; reject query strings, redirects, alternate headers affecting semantics, duplicate headers, extra body bytes and content-encoding. Transport-only framing (HTTP/2 vs HTTP/1.1, content-length, TLS record boundaries) is not part of the canonical request. This is semantic request identity with exact body bytes, not a hash of the complete raw TCP/TLS stream.

For the exact example above the full SHA-256 is `6147d0eac889c35b220e0514defd7700d8e8f8746c33b959a57297abc4b08657`. Displayed abbreviations never replace the full release hash.

Helper API (run from this repository root):

```python
from breaker.canonical import payout, parse, digest, to_hex, from_hex
from breaker.wire import encode, decode, Decoder
raw = payout(184000, "demo_acct_4471", "0123456789abcdef0123456789abcdef")
assert digest(raw) == "6147d0eac889c35b220e0514defd7700d8e8f8746c33b959a57297abc4b08657"
frame = encode("HOLD", boot_id, seq, digest(raw), to_hex(raw))
# decode(one_complete_frame) -> list[str], without BRK1/checksum.
# Decoder().feed(arbitrary_fragment, monotonic_ms) -> [(fields, None) | (None, error)].
# Decoder.expire(monotonic_ms) must run even when UART is silent.
```

Firmware/simulator adapters pass at most 256 bytes per `Supervisor.receive()` call and sample physical inputs at least every 100 ms (target 5 ms). A larger gap invalidates confirmation and physically trips; elapsed time through a stalled loop cannot count as a held button. Button debounce adds at least 30 ms to the 1,000 ms confirmation interval. A valid CHECK neither restarts confirmation nor extends the 60-second expiry.

## Display and hardware gate

A new HELD generation invalidates confirmation until that generation finishes a full refresh. Display operations must be nonblocking with regard to input sampling and cut control; text rendering yields per glyph using row-byte masks and filled rectangles yield each four rows; stuck BUSY trips. Denial and parse errors display fixed device copy; MISMATCH only means the CHECK divergence above. Wire/host failures have explicit UART ERROR / UART TIMEOUT / HOST REPLAY / HOST SESSION / HOST LOST labels, not INVALID REQUEST; unknown internal reason codes render SUPERVISOR ERROR. No received reason text is rendered verbatim; UART text is never an instruction. Ring is off when handle OFF or in transit, dim steady green ARMED, pulsing amber HELD, flashing green during APPROVED and red TRIPPED at ON/invalid contacts. NOT SENT uses the underlying dim green ARMED ring. These are nonblocking PWM duties on the existing RGB transistor pins; GP18 is never PWM. No “link dead,” “funds sent,” “vault sealed,” counters or signatures are inferred from an output bit.

### Display contract

This is a display-only extension of protocol 1: canonical bytes, full hash,
release conditions, UART verbs, STATE fields and mode/ring enums are unchanged.
Only the locally owned allowlist **display label** changes to `acct ••4471`.
Hardware and host use the same fixed renderer and font. The host replaces only
reserved top-right header pixels with DEMO; there is no branding parameter in
the firmware renderer. SETUP is a fixed illustration (`Code 4817`), never a UART
or supervisor runtime mode. Pairing, updates, phone approval and new payees remain
out of scope; Linux acquires no new authority.

| Display mode | Fixed copy / locally parsed fields |
|---|---|
| ARMED | S BREAKER; XL ARMED; S Asks first. Handle down cuts. |
| HELD | Inverted S HELD · payment; XL amount; L to acct ••4471; optional S Log #hash; S Hold the button 1 s to send. |
| APPROVED | XL APPROVED; L amount; S to acct ••4471 · released once |
| NOT SENT | XL NOT SENT; L amount; S to acct ••4471 · not sent |
| TRIPPED | Inverted XL TRIPPED; L fixed rule/error label; S Blocked until you reset.; S Reset: handle OFF, then ON. |
| OFF | XL OFF; L Agent port off; S Handle up to re-arm. |
| STARTING | Inverted XL TRIPPED; S Breaker restarted. Starting…; S Wait, then handle OFF and ON. |
| SETUP (design only) | XL SETUP; L Code 4817; S Open setup on your laptop.; S Then hold the button. |

XL is 40 px / maximum 12 characters, L 24 px / 21, S 16 px / 30. Each
line is validated against its character budget and actual glyph width with 8 px
horizontal margins. Overflow raises an error; never wrap or shrink. One tier per
line except the short S hash tag at the right of the L payee. A separate S header
row keeps OPEN BENCH legible beside long XL titles. Full redraw only, no fades;
the design's nominal redraw duration is not a measured hardware claim.

APPROVED is created only after the existing final canonical reparse/full-hash
check and local one-second confirmation consumes the pending hold. It stores a
copy of parsed display fields, never another authorization. NOT SENT is created
only by the Pico's **60,000 ms hold expiry**. After the matching face generation
completes, APPROVED remains for 3,000 ms and NOT SENT for 5,000 ms, then ARMED.
Repeated/stale panel completions cannot extend/start the dwell. Full redraw time
is additional. New holds immediately replace notices and require fresh local
confirmation; handle movement, trips, UART/host errors and local faults preempt
them. Wire STATE remains ARMED with ticket=0 and sha256=- during either notice.
`face_ready` still means completion of the reported generation, not a release.

An explicit Linux DENY is immediate cancellation, **not** a fabricated 60 s
expiry, and returns to ARMED. A demo path that sends DENY at an earlier host
deadline will not show NOT SENT; the scene must keep its request/heartbeat alive
until the Pico emits `DENIED ... hold.timeout`. Do not extend release dispatch
past the original hold deadline. APPROVED means released once, not paid/delivered.

TRIPPED shows a device-fixed label (mail.mass_delete → `Mail: mass delete`), never
Linux-reported counts, text or EN/link claims. `MISMATCH` remains reserved for a
genuine CHECK divergence. STARTING represents boot without a live Linux session;
physical OFF takes precedence. Loss of an established session uses HOST LOST.
The ring is the live indication; the unpowered e-paper can retain any old image.

Intended circuit (not bench-qualified): HiLetgo 5 V relay module B00LW15A4W, jumper H, two PN2222 stages Q6/Q7 (CUT_DRIVER=HILETGO_H_2XNPN), GP18 active-high, GP14 NC. Handle pole A and K1 COM–NO are in series in the UE300 USB VBUS path. GPIO LOW/floating/Pico absent must de-energize the coil and open COM–NO through the external buffer; R27 stays directly across module IN–DC−. Coil/buffer power is upstream RAW_5V with common ground retained, separate from the switched USB contact supply. No /FAULT, contact feedback, current limit or inrush control is present; welded contacts and USB data-pin back-power require physical checks. Pins and the local qualification-record path are in `breaker/config.py`; V1/V2 selection comes only from the physical USB arrival test record. Delivered e-paper controller and BUSY polarity must be identified; the firmware must refuse to arm when the display is unconfigured/unready. Bench tamper bypass, if enabled, is visible as OPEN BENCH. SE051 remains disabled. Hardware tests must prove cold/reset GP18 level, contact polarity, button timing, RGB colors, actual face orientation and completion, fault latch only when a real fault line exists, VBUS fall, link loss and no phantom-power restore.


Relay circuit assumptions and physical checks are in [docs/FLASH.md](docs/FLASH.md). `RELAY_SETTLE_MS=100` is a **DESIGN observation settling interval** after either GPIO transition, never a cut delay or a measured link bound. GP18 drops immediately on a cut; safety sampling, heartbeat and confirmation do not sleep for relay settling. The Pico has no link sensor and STATE.cut still means commanded GP18 only. HIL waits at least this interval before interpreting a new steady link observation, while scope/packet capture starts at the actual edge to measure latency. No extra minimum-off timer is added: physical OFF then 30 ms stable ON is already required, unsafe edges latch the output off, and software neither PWMs nor automatically retries the relay. Real contact bounce remains a measurement, not a software guarantee.

The [Songle SRD sheet](https://msako.jp/res/SRD-05VDC-SL-C.pdf), source checked 2026-10-25, lists bare-relay operate ≤10 ms and release ≤5 ms at its initial conditions, conditional on delivered SRD-05VDC-SL-C identity. The module's suppression and contact/load behavior can differ. Neither these values nor the 100 ms settling interval establishes VBUS decay, last-packet or dead-link time. Record measured endpoints and captures on arrival; USB/Ethernet restoration can take seconds. [HiLetgo listing](https://hiletgo.com/ProductDetail/1958599.html), source checked on the same date; module identity, H jumper and buffer operation still require bench qualification.


## Local maintenance, implementation and qualification

Physical handle OFF plus CONFIRM held at power-on enters a labelled local maintenance boot: GP18, RGB and SE051 stay LOW; policy UART and watchdog do not start. This permits USB mpremote updates without watchdog interruptions. Exit requires a power-cycle with CONFIRM released. There is no UART maintenance command. Programming USB/RUN/SWD must not be connected to Linux in the demo setup. This is not locked-down production firmware or signed boot; an attacker with physical USB access can reflash it.

The shared core uses monotonically increasing integer milliseconds; the RP2350 adapter extends `ticks_ms` with `ticks_diff` to handle wraparound. It samples at a target 5 ms loop interval; gaps >100 ms trip and cannot count toward confirmation. IRQs veto GP18 on unsafe edges, including pulses between samples while enabled. The physical OFF witness survives a normal OFF→TRANSIT→ON movement. Policy TRIP keeps an existing enable high, while hardware, structured-request integrity, heartbeat and display failures veto it immediately; isolated wire noise follows the three-error budget above. The 2-second watchdog restarts on a stalled interpreter, always into the boot interlock; it does not establish the <100 ms cut target.

The checked-in firmware uses `CUT_FAULT_PRESENT=False` and ships no panel qualification record. It refuses to arm until the local USB arrival test verifies the explicitly identified V1 IL3820 or V2 SSD1680 B/W panel. Missing, unknown, malformed, unverified or stale records fail closed; no policy UART command can select/verify a panel. See `docs/PANEL-SOURCES.md` for command/timing evidence and qualification assumptions. No SE051 I2C operation, release signature, receipt signature or SIM private key is present. Releases are unsigned authorization events, not transaction receipts.

Reference entry point: `python3 tools/reference.py vector|encode|decode`. Executable integration instructions: `README.md`. Flash and physical acceptance: `docs/FLASH.md`. Host results are simulations only, including panel BUSY and fault injection. PNGs are produced from the exact shared framebuffer with a host-only DEMO header. Hardware always draws OPEN BENCH, including inverted headers; no UART or model field can select DEMO. `face_host.py` is outside the firmware package and flash manifest; reused staging packages containing extra files are rejected. Simulation logs remain labelled SIM.

A token is valid only for the in-memory pending immutable request with the matching boot/ticket/full hash. The Linux adapter must impose a bounded dispatch deadline (no later than the original 60-second hold deadline), never persist/reuse a release after disconnect or restart, and keep the idempotency key unchanged. UART liveness is not caller authentication. Fault/hold/policy state and event counters are volatile; a reboot starts a new random boot ID and requires new physical OFF→ON. No durable receipt, monotonic secure counter, secure time, automatic retry or downstream idempotency guarantee is claimed here. Idempotency across process/device restarts is the host and test harness's responsibility.

## Compatibility notes

Wire layout, READY version 1, canonical bytes, SHA-256, tickets, STATE shape and release conditions are unchanged. Changes: physical OFF survives remote events; wire errors use the 3/1,000 ms budget; new ERROR/DENIED reasons `hash.invalid` and `hold.integrity` distinguish non-CHECK failures; wrong CHECK ticket uses `ticket.invalid`. All ERROR events still cancel authorization. Explicit RX pull-up is a Pico hardware adapter change. Do not infer optical completion, delivery or link health from these events.

## Host broker continuity and persisted policy recovery

The existing decoder ERROR budget and 3,000 ms Pico heartbeat timeout are unchanged.
The Linux adapter uses a **separate local UART broker service** as the sole physical UART writer,
boot/session/sequence owner and heartbeat sender. An HTTP proxy restart alone must
not close the broker's Pico link, stop PING, emit CUT or replay commands. An actual
broker/transport failure still uses the existing heartbeat/physical cut interlock.
The Pico still requires live heartbeat, qualified display and physical OFF→ON to
raise GP18; no new verb, grace lease, automatic rearm or Linux restore exists.

Loss of the proxy's broker-socket ownership (disconnect/restart) makes its holds
unusable: the broker cancels its local pending authorization and discards any
released-but-undispatched token. If the broker still tracks a pending hold, use
existing `TRIP|boot|seq|linux.cut` as a cancellation barrier before cancelling the
approval task and suppress its stale DENY cleanup:
it preserves the enable bit and latches for human reset whether the hold is pending,
already released or expired. Do not send a speculative DENY from a cached ticket;
physical RELEASE/expiry may have consumed it and a stale DENY correctly cuts with
`ticket.invalid`. An idle proxy restart needs no TRIP. No token survives into a new
proxy connection. This ownership-loss rule does not change explicit in-session
DENY for HTTP cancellation, busy holds or the host's earlier hold deadline.
HELLO/status/heartbeat, reconnect and reading a reset epoch grant no
forwarding permission. Kernel redirection/default-deny keep governed traffic
blocked while the proxy is down. The persistent broker can observe a physical
OFF→ON during that interval; the Pico emits its higher reset_epoch as usual.

For Pico `ERROR` with reason **frame_crc, frame_ascii, frame_shape, frame_size or
frame_timeout**, the Linux adapter must cancel/discard its hold, mark governed requests unavailable
until physical reset, and continue the broker heartbeat. Do not translate that
ERROR into CUT, proxy shutdown or broker restart. The Pico remains responsible for
the three-error cut threshold. Every ERROR is non-authorizing, including before a
STATE arrives. The Linux adapter's own transport failures and semantic/integrity ERROR reasons
retain the existing fail-closed handling; no error clears a latch.

Persist the Pico **boot ID and reset_epoch observed at the trip** with each policy
latch. On reconnect consume a fresh broker/Pico snapshot (never an old connection's
cached event) and clear only on either of these physical-rearm proofs:

- **Same boot:** the fresh epoch is strictly greater than the persisted trip epoch,
  and STATE is ARMED, handle ON, cut=1, ticket=0, sha256=-, face_ready=1.
- **Different known boot:** fresh STATE meets those same ARMED/ON/cut/empty/ready
  conditions and reset_epoch>0. This proves a physical OFF→ON in the new Pico boot;
  a bare READY, epoch 0 or a new unarmed boot is insufficient.

Otherwise preserve the latch. Never numerically compare epochs across boot IDs.
Missing/invalid provenance cannot prove a reset: establish a fresh baseline while
keeping governed traffic latched and require a subsequent physical reset. A reset
observation clears only the persisted policy latch; it never restores GPIO or
reuses a hold/release. Fresh matching RELEASE is still required for each payout.

`TRIP|boot|seq|policy.state_invalid` is added for corrupt/missing required persisted
policy provenance. It cancels holds, latches requests and preserves an already
powered port. The face uses fixed **TRIPPED / POLICY STATE / Blocked until you reset. /
Reset: handle OFF, then ON.** copy. EN status is available on UART, not the face. No filesystem path,
exception or host-supplied free text reaches the face. The Linux adapter quarantines bad state,
logs it clearly, sends this TRIP, anchors its recovery latch to the fresh current
boot/epoch, and waits for a **later** proven physical rearm. The initial snapshot
used as that baseline must not itself clear this new recovery latch. Filesystem
repair/deletion, proxy restart, HELLO or PING alone cannot clear it.
