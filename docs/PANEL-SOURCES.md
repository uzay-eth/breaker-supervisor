# Panel command and timing evidence

Checked **2026-10-25**, public read-only documents/source. Profiles are for Waveshare
2.9-inch **B/W** modules, not B/C/D or other resolutions. Full refresh only.

Sources (page numbers are printed PDF pages):

- **D1:** [Waveshare V1 datasheet](https://files.waveshare.com/upload/e/e6/2.9inch_e-Paper_Datasheet.pdf), issue 2017-03-02, §7 pp12–16 and §10 p19.
- **D2:** [Waveshare V2 specification](https://files.waveshare.com/upload/7/79/2.9inch-e-paper-v2-specification.pdf), Rev2.1, cover 2020-02-10 / revision history 2020-04-15, §5 pp20–24.
- **IC2:** [Solomon Systech SSD1680 datasheet](https://files.seeedstudio.com/wiki/Other_Display/213-epaper/IC%20Driver%20SSD1680%20Datasheet.pdf), Rev0.14 June 2019, command table §9 and SPI §12.
- **R1:** [Waveshare V1 reference](https://raw.githubusercontent.com/waveshareteam/e-Paper/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epd2in9.py), header 2019-06-20; `reset`, `init`, `lut_full_update`, `SetWindow`, `SetCursor`, `display`, `TurnOnDisplay`.
- **R2:** [Waveshare V2 reference](https://raw.githubusercontent.com/waveshareteam/e-Paper/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epd2in9_V2.py), header 2022-08-09; `reset`, `init`, `WS_20_30`, `lut`, `SetLut`, `SetWindow`, `SetCursor`, `display`, `TurnOnDisplay`.
- **RC2:** [Waveshare V2 C reference](https://raw.githubusercontent.com/waveshareteam/e-Paper/master/RaspberryPi_JetsonNano/c/lib/e-Paper/EPD_2in9_V2.c), corroborates explicit waveform loading and C7 activation. Implementation follows R2's waveform/reset, not RC2's different timings.
- **M:** [Waveshare revision guide](https://docs.waveshare.net/2.9inch_e-Paper_Module/), revision section: SKU12956, V2 back label uses V2 program. Also lists V3; this implementation does not automatically qualify V3.

Every command issued by `breaker/epaper.py` is listed below. All rows use the
checked date above. Values are hex. Datasheets define registers; vendor reference
programs supply the module-specific values and waveforms.

| Profile | Command / data | Meaning / evidence |
|---|---|---|
| Both | 01 / 27 01 00; 11 / 03 | 296 gates; increment X/Y. D1 p12, D2 p20, R1/R2 init. |
| Both | 44 / 00 0F; 45 / 00 00 27 01 | RAM window. D1 p16, IC2 pp31–32, R1/R2 SetWindow. |
| Both | 4E / 00; 4F / 00 00 | RAM cursor. D1 p16, IC2 p32, R1/R2 SetCursor. |
| Both | 24 / 4736 framebuffer bytes | Image RAM. D1 p14, D2 p22, R1/R2 display. |
| Both | 20 | Activate update; D1 p13, D2 p21, R1/R2 TurnOnDisplay. |
| V1 | 0C / D7 D6 9D; 2C / A8 | Booster; VCOM. D1 pp12,14; exact values R1 init (not D1's example booster values). |
| V1 | 3A / 1A; 3B / 08 | Dummy/gate periods; D1 p15, R1 init. |
| V1 | 32 / 30-byte V1_LUT | Waveform. D1 p15, R1 lut_full_update. |
| V1 | 22 / C4; FF | Update selection; terminate/NOP. D1 pp14,16, R1 TurnOnDisplay. |
| V2 | 12 | Software reset. D2 p21, R2 init. |
| V2 | 21 / 00 80 | Display RAM option. D2 p21, R2 init. |
| V2 | 32 / first 153 bytes of V2_LUT | Waveform. D2 p23, R2 WS_20_30 and lut. |
| V2 | 3F / 22 | LUT end option. IC2 p31, R2 SetLut. |
| V2 | 03 / 17; 04 / 41 00 32; 2C / 36 | Gate/source/VCOM settings. D2 pp20,22, R2 final six WS_20_30 bytes and SetLut. |
| V2 | 22 / C7 | Full update with uploaded waveform. D2 p21, R2 TurnOnDisplay. |

V2 no longer uses the previous candidate's F7/18/3C sequence. V1 and V2 waveform
bytes are copied unchanged from R1/R2; `breaker/EPD-LICENSE.txt` preserves their
permission notice. R2 names its waveform `WS_20_30`: qualify at **20–30 °C bench
ambient**, record temperature with HIL; no broader temperature performance claim.

| Timing or signal | Source and choice |
|---|---|
| V1 reset HIGH 200 / LOW 5 / HIGH 200 ms | R1 reset; D1 §6 reset active-low. |
| V2 reset HIGH 50 / LOW 2 / HIGH 50 ms | R2 reset; IC2 §6.14. D2 p19's generic 10/10 ms flow differs; use the current module driver. |
| Software reset guard 10 ms | Breaker DESIGN extra scheduling guard before BUSY check; IC2 §6.14 reset and D2 p21 BUSY behavior. Not a claimed datasheet minimum. |
| BUSY HIGH during work, LOW at completion | R1/R2 ReadBusy; D2 p21. D1 p8 note 6-4 says LOW while busy, contradicting R1: use module reference HIGH polarity and require observed HIGH→LOW plus optical qualification. No automatic polarity guessing. |
| SPI mode 0, MSB first, 2 MHz | D1 §6.2/§10: rising-edge writes, 250 ns minimum cycle. IC2 §6.1.2/§12 supports this write speed. Breaker conservative 500 ns cycle; GPIO setup/hold and signal edges remain HIL checks. |
| BUSY deadline 10,000 ms | Breaker DESIGN fail-closed budget, not specified refresh time. Every frame must observe assertion and deassertion. |
| 64-byte transfers / yield each four rotated rows | Breaker DESIGN cooperative work limit; no panel timing claim. CS may delimit byte groups; RAM advances until another command (D1 p14, D2 p22). |
| 5 ms main/test polling; 600 ms viewing per test image | Breaker DESIGN scheduling / human viewing intervals, not refresh duration. Reset delays are minimums, rounded upward by scheduler. |
| 60 s test; 75 s host subprocess limit | Breaker DESIGN arrival budget and USB overhead. Missing confirmation/deadline aborts without a new PASS. |
| 30 ms release debounce; 1,000 ms confirm | Breaker product rule; independent of controller timing. |

No SPI silicon-ID read is implemented. The connected bus supplies no MISO and
V1 specifies write-only serial operation. Markings identify the candidate;
BUSY transitions and a human checking white/pattern/inverse qualify its operation.
An absent V2 sticker is not positive V1 identification. Neither successful SPI
writes nor a plausible BUSY waveform establishes optical correctness.

Downloaded reference fingerprints (SHA-256; mutable master URLs checked above):

```text
d137e8b11d5b4dda89aa1a893a7a5d834266fbee3b1730348d6639fd20858c16  V1.pdf
82286e778dbe46e791f4958f696ccf12f0e1da75572cf9798942027fbb724b7f  V2.pdf
776f436b037b15e042e29dc3ee24cd4bdd2c08caf95928471a61e07ce70c7406  epd2in9.py
981f5218c2bac6c251c4ad143de2f88774440f88455defaaa2bf7af34a37e399  epd2in9_V2.py
db4be9a479d6692278ff214144460eb2d1e6bd8680cfea94fcb41a575b8e2755  EPD_2in9_V2.c
```
