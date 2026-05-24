# nox-nif-decoder

Reverse-engineered Python reader for **Nox Medical `.NIF`** recordings (the raw,
undocumented container written by Nox PSG recorders such as the **Nox A1 / A1S**).

Noxturnal normally "explodes" a `.NIF` into per-signal `.ndf` files. This project
lets you read the raw `.NIF` **directly** — no proprietary software required — so
the next person (or AI) does not have to redo the byte-level reverse-engineering.

> ⚠️ **Not affiliated with Nox Medical. Reverse-engineered, not official.**
> Validated on a Nox A1S recording (firmware `3.1.2.2868`): channel rates match
> the device montage exactly and decoded SpO2/pulse are physiological. Verify
> against your own device before relying on it. **Not a medical device.**

## Why this exists
The `.NIF` format is proprietary and undocumented. Public tooling targets the
extracted `.ndf` files, not the raw container. Reverse-engineering it from
scratch takes many hours of binary spelunking. This repo captures that work:
a documented format spec (`FORMAT.md`) + a small, dependency-light parser.

## What it decodes
- **Framing & timestamps** (microseconds since Unix epoch, local time)
- **All channels by id**: EEG, EOG, EMG (chin + legs), ECG, respiration
  (nasal pressure, thermistor, RIP belts), **SpO2 / pulse** (Nonin scalar),
  PPG, audio (8 kHz), accelerometer/position, per-electrode impedances
- **int16 / int32 / uint8** sample formats (per-channel dtype map)
- Detection of **unplugged / dead channels** (sentinel values)

## Install
```bash
pip install numpy
# then drop the noxnif/ package next to your code, or pip install -e .
```

## Quick start
```python
from noxnif import NoxNIF, epoch_us_to_datetime

nif = NoxNIF("01_XXXX.NIF")
print("Start:", nif.start_time)
print("Channels present:", nif.channel_summary())   # {cid: (count, bytes)}

# Oxygen saturation & pulse (1 Hz)
t_us, spo2, pulse = nif.decode_oximetry()
print("SpO2 median:", round(float(__import__('numpy').nanmedian(spo2)), 1), "%")

# Airflow (nasal pressure, int32 @200 Hz) — full signal + per-record timestamps
samples, rec_ts = nif.extract(0x38)

# Memory-friendly downsample of a respiratory channel (per-0.1s mean ~10 Hz)
airflow_10hz, ts10 = nif.record_means(0x38)
```

See [`examples/extract_signals.py`](examples/extract_signals.py) for a fuller demo
(SpO2 stats, airflow, position) and [`FORMAT.md`](FORMAT.md) for the byte-level
specification.

## Channel id cheat-sheet (A1S)
`0x06-0x0d` EEG (F4,F3,C4,C3,O1,O2,M1,M2) · `0x04/05` EOG · `0x10-0x12` chin EMG ·
`0x13/14` leg EMG · `0x0e` ECG · `0x0f` thermistor · `0x38` nasal pressure ·
`0x39/0x3a` RIP thorax/abdomen · `0x35` **SpO2/pulse scalar** · `0x37` PPG ·
`0x25` audio · `0x01-03` accelerometer · `0x15-0x24` impedances. Full map in
`noxnif/parser.py` (`A1S_CHANNELS`).

## Limitations / known gaps
- Header **scale/offset** fields not fully decoded → for calibrated physical
  units, read `SETUP.INI` / `RECTEMP.GZ` (recording template) shipped with the
  recording. Raw integers are returned as-is.
- Per-channel **dtype** is a known map for A1S; pass `dtype=` to override.
- The Nonin **oximetry waveform** (`0x36`) and PPG framing are only partially
  characterised; the **1 Hz SpO2/pulse scalar (`0x35`)** is fully usable.
- Tested on one device model/firmware. Other Nox models may differ.

## Related work
- [**jussivirkkala/Noxturnal-NDF**](https://github.com/jussivirkkala/Noxturnal-NDF)
  — MATLAB utilities for reading Noxturnal `.ndf` files (the per-signal files
  Noxturnal *extracts* from a recording). Complementary to this project: that one
  targets the exploded `.ndf` signals, whereas **noxnif** reads the raw `.NIF`
  **container** directly (before Noxturnal explodes it). Useful cross-reference
  for field names and signal conventions.

## Contributing
PRs welcome, especially: scale/offset decoding, more device models, the Nonin
DF19/DF22 waveform layout, and `.ndf`/`.ndb` (SQLite) support. Please include a
small redacted sample or the `DEVICE.INI`/`SETUP.INI` of the model you tested.

## Legal & reverse engineering
*Not legal advice.* This project is published in good faith on the following basis:

- **Independent, black-box work.** The decoder was written from scratch by
  observing the **byte patterns of recordings the author owns**. No Noxturnal
  binary was decompiled or disassembled; no Nox source code or assets were
  copied.
- **File formats are not copyrightable.** Copyright protects creative code
  expression, not data structures, interfaces or file formats (cf. EU case
  *SAS Institute v World Programming*, C-406/10). This repo documents a format
  and provides original code to read it.
- **Interoperability.** In the EU, reverse engineering for interoperability is a
  protected exception (Software Directive 2009/24/EC, Art. 6; FR: CPI
  L122-6-1), and contractual clauses that forbid it are unenforceable.
- **No personal / health data.** This repository contains **no recordings, no
  patient data, and no device identifiers** (serial, GUID, MAC). The
  `.gitignore` blocks `*.NIF`, `PATINF*`, `*.edf`, etc. Never commit real
  recordings — they are special-category health data under the GDPR.
- **Trademarks.** "Nox", "Noxturnal" and "Nox Medical" are trademarks of their
  owner and are used here **nominatively** only, to describe compatibility. This
  project is **not affiliated with, authorized, or endorsed by Nox Medical**.
- **Not a medical device.** For research/engineering use only; not for
  diagnosis or treatment. Provided "as is", without warranty (see LICENSE).

If you intend to use this commercially, or have any doubt, consult an IP lawyer.

## License
MIT — see [LICENSE](LICENSE). Authored anonymously by the *noxnif contributors*.
