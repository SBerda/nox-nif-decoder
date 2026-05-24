# Nox `.NIF` format — reverse-engineered specification

> **Status:** reverse-engineered, **not** an official Nox Medical spec. Derived
> from a Nox **A1S** recording (firmware `3.1.2.2868`). Validated: channel rates
> match the device montage exactly, decoded SpO2/pulse are physiological, and a
> generic record walker stays aligned over the whole 886 MB file (~1% resync,
> mostly inside audio blocks). Use at your own risk; verify against your device.

The `.NIF` ("Nox Internal Format") is the raw on-device recording. Noxturnal
normally extracts it into per-signal `.ndf` files. This document describes the
container so you can read it directly.

All multi-byte integers are **little-endian**.

## 1. File header
```
offset 0x00: 85 a6 04 5a            magic
             ... "NOX\0" "A1S\0"    device family
             ... <serial>\0 <licensee>\0 <recording GUID>\0
```
Then a **channel descriptor table**, then the **data stream**.

### Channel descriptor (one per channel)
Each descriptor contains three NUL-terminated strings in a row:
```
<binary meta> <name>\0 <label>\0 <type>\0 <binary meta incl. 16-bit channel id>
```
- `name`  e.g. `EXG8`, `BP2`, `Audio`, `OximetryMeasurement`
- `label` localized, e.g. `C3`, `ECG`, `Jambe gauche`
- `type`  e.g. `EEG-C3`, `Resp.Pressure-Raw.Nasal`, `EMG.Tibialis-Leg.Left`
- The **channel id** (`cid`, used in the data stream) is a small integer that
  increments across the table; it appears in the binary right after the type
  string (observed at type-string-end + 6). The exact layout of scale/offset
  fields was **not** fully decoded — read scale/rate from `SETUP.INI` /
  `RECTEMP.GZ` (the recording template) if you need calibrated units.

## 2. Data stream
A flat sequence of two record kinds. Parse by reading the leading tag byte.

### Frame marker (timestamp)
```
0xfb  0x0a  0x00  <ts: u64>      (11 bytes total)
```
`ts` = **microseconds since the Unix epoch, in LOCAL time**. Emitted ~10×/s, so
frames are ~0.1 s. Every data record inherits the most recent frame timestamp.

### Data record (one channel chunk)
```
0xff  <len: u16>  <frameseq: u32>  <cid: u8>  <samples...>  <crc: 3 bytes>
```
- **Total record size = `len + 6` bytes.**
- Header = 8 bytes (`0xff` + `len` + `frameseq` + `cid`).
- **Sample bytes = `len − 5`** (i.e. `bytes[off+8 : off+3+len]`); the trailing
  3 bytes are a CRC/checksum.
- `frameseq` is constant for all records within the same frame.
- Samples are raw little-endian integers (see dtype below). A 200 Hz channel
  emits 20 samples per 0.1 s frame.

### Resync
`0xff` occurs naturally inside sample data (esp. 8-bit audio). A robust walker:
when the next byte is not a valid record/marker, scan forward to the next `0xff`
whose `len` (u16) is plausible (1..4096). ~1% of bytes need resync; concentrated
in audio. This does not affect low-rate channels (SpO2, position, impedance).

## 3. Sample dtypes (A1S, observed)
| Record `len` | Sample bytes | Interpretation |
|---|---|---|
| 45 | 40 | 20 × `int16` @200 Hz (most EEG/EOG/EMG) |
| 85 | 80 | 20 × `int32` @200 Hz (M2, chin1, ECG, legL, thorax, nasal pressure) |
| 7  | 2  | 1 × `int16` @1 Hz (impedances; also "dead" channels) |
| 15 | 10 | oximetry scalar (see below) |
| 245| 240| 240 × `uint8` @8 kHz audio (silence ≈ 0x7d/0x80; AGC-compressed) |

Why some channels are `int32`: a subset is stored at higher precision; the
signal varies in the low 16 bits (high word ≈ constant). `extract()` in
`parser.py` uses a per-cid dtype map; override with `dtype=` if your device
differs.

## 4. Channel id map (A1S — this recording)
See `A1S_CHANNELS` in `noxnif/parser.py`. Highlights:
- `0x01-0x03` accelerometer (position)
- `0x04/0x05` EOG E2/E1 · `0x06-0x0d` EEG F4,F3,C4,C3,O1,O2,M1,M2
- `0x0e` ECG (may be all-sentinel `fe ff ff 7f` if unplugged)
- `0x0f` thermistor airflow · `0x10-0x12` chin EMG · `0x13/0x14` leg EMG L/R
- `0x15-0x24` per-electrode impedances (≈ Ohm)
- `0x25` raw 8 kHz audio · `0x27` snore envelope
- `0x35` **oximetry scalar** · `0x36` oximetry waveform · `0x37` PPG
- `0x38` nasal pressure (airflow) · `0x39` RIP thorax · `0x3a` RIP abdomen

## 5. Oximetry scalar (`cid 0x35`, Nonin, 1 Hz)
10-byte sample record. Observed layout:
```
byte[0] = 0x0a (length)   byte[6] = per-second counter (wraps 0..255)
byte[7] = SpO2 %          byte[9] = pulse rate (bpm)
```
SpO2 > 100 and pulse ≥ 250 are "no-signal" sentinels → treat as NaN.
(`cid 0x36/0x37` carry the oximetry/PPG **waveforms**; the easy scalar is `0x35`.)

## 6. Sentinels / "dead" channels
- `int32` no-data sentinel: `fe ff ff 7f` (= INT32_MAX-ish) → channel unplugged.
- `uint8`/battery: `0x80` padding.
- A channel emitting only 1 value/s where a waveform is expected = electrode not
  connected (seen here for ECG `0x0e` and right leg `0x14`).
