"""
noxnif.parser — Reverse-engineered reader for Nox Medical ``.NIF`` recordings.

The Nox ``.NIF`` ("Nox Internal Format") is the proprietary, undocumented raw
recording container written by Nox Medical PSG recorders (e.g. Nox A1 / A1S).
Noxturnal normally "explodes" it into per-signal ``.ndf`` files. This module
decodes the raw ``.NIF`` directly, so an AI/engineer does not have to redo the
reverse-engineering. See ``FORMAT.md`` for the byte-level specification.

Validated on a Nox A1S recording (firmware 3.1.2.2868): channel rates match the
device montage exactly, and decoded SpO2/pulse are physiological.

Status: reverse-engineered, NOT official. Solid: framing, timestamps, channel
ids, int16/int32 sample extraction, Nonin SpO2/pulse scalar. Heuristic: the
binary metadata fields inside header channel descriptors (scale/offset) — names
and types are read from strings; sample dtype uses a known A1S map + override.

No dependencies beyond numpy.
"""
from __future__ import annotations
import struct
import datetime as _dt
from dataclasses import dataclass
import numpy as np

# Microseconds-since-Unix-epoch sanity window (local time). Adjust if needed.
_TS_MIN = 1_500_000_000_000_000   # ~2017
_TS_MAX = 2_000_000_000_000_000   # ~2033

# Known A1S channel-id -> (short name, default numpy dtype, nominal Hz).
# dtype matters: most EEG/EOG/EMG are int16; a subset are int32 (see FORMAT.md).
A1S_CHANNELS = {
    0x01: ("Accel_X", "<i2", 1),   0x02: ("Accel_Y", "<i2", 1),   0x03: ("Accel_Z", "<i2", 20),
    0x04: ("EOG_E2", "<i2", 200),  0x05: ("EOG_E1", "<i2", 200),
    0x06: ("EEG_F4", "<i2", 200),  0x07: ("EEG_F3", "<i2", 200),
    0x08: ("EEG_C4", "<i2", 200),  0x09: ("EEG_C3", "<i2", 200),
    0x0a: ("EEG_O1", "<i2", 200),  0x0b: ("EEG_O2", "<i2", 200),
    0x0c: ("EEG_M1", "<i2", 200),  0x0d: ("EEG_M2", "<i4", 200),
    0x0e: ("ECG",    "<i4", 200),  # NOTE: may be all-sentinel if electrode unplugged
    0x0f: ("Thermistor", "<i2", 200),
    0x10: ("EMG_chinF", "<i2", 200), 0x11: ("EMG_chin2", "<i2", 200), 0x12: ("EMG_chin1", "<i4", 200),
    0x13: ("EMG_legL", "<i4", 200),  0x14: ("EMG_legR", "<i2", 1),
    0x15: ("IMP_E2", "<i2", 1), 0x16: ("IMP_E1", "<i2", 1), 0x17: ("IMP_F4", "<i2", 1),
    0x18: ("IMP_F3", "<i2", 1), 0x19: ("IMP_C4", "<i2", 1), 0x1a: ("IMP_C3", "<i2", 1),
    0x1b: ("IMP_O1", "<i2", 1), 0x1c: ("IMP_O2", "<i2", 1), 0x1d: ("IMP_M1", "<i2", 1),
    0x1e: ("IMP_M2", "<i2", 1), 0x1f: ("IMP_ECG", "<i2", 1), 0x20: ("IMP_chinF", "<i2", 1),
    0x21: ("IMP_chin2", "<i2", 1), 0x22: ("IMP_chin1", "<i2", 1),
    0x23: ("IMP_legL", "<i2", 1), 0x24: ("IMP_legR", "<i2", 1),
    0x25: ("Audio", "u1", 8000),         # raw microphone, 8-bit, often AGC-compressed
    0x27: ("SnoreEnvelope", "<i2", 1),
    0x28: ("Light", "<i2", 10), 0x29: ("DeviceCurrent", "<i2", 10),
    0x2a: ("CoreVoltage", "<i2", 10), 0x33: ("BatteryVoltage", "<i2", 10),
    0x35: ("OximetryScalar", "u1", 1),   # SpO2/pulse scalar — use decode_oximetry()
    0x36: ("OximetryWave", "<i2", 1), 0x37: ("PPG", "<i4", 192),
    0x38: ("NasalPressure", "<i4", 200), 0x39: ("RIP_Thorax", "<i4", 200),
    0x3a: ("RIP_Abdomen", "<i4", 200),
}


@dataclass
class Channel:
    cid: int
    name: str
    label: str
    type: str


def epoch_us_to_datetime(us: int) -> _dt.datetime:
    """Convert a NIF microsecond timestamp (local time) to a datetime."""
    return _dt.datetime(1970, 1, 1) + _dt.timedelta(microseconds=int(us))


class NoxNIF:
    """Reader for a Nox ``.NIF`` file.

    Example
    -------
    >>> nif = NoxNIF("01_XXXX.NIF")
    >>> nif.start_time
    >>> samples, rec_ts = nif.extract(0x38)          # nasal pressure (airflow)
    >>> t, spo2, pulse = nif.decode_oximetry()       # SpO2 %, pulse bpm @1Hz
    """

    def __init__(self, path: str):
        self.path = path
        self._a = np.memmap(path, dtype=np.uint8, mode="r")
        self._mv = memoryview(self._a)
        self._n = len(self._a)
        self.channels: dict[int, Channel] = {}
        self.start_time = None
        self._data_start = self._parse_header()

    # ------------------------------------------------------------------ header
    def _cstr(self, p, maxlen=64):
        """Read a NUL-terminated string at p, searching at most ``maxlen`` bytes."""
        end = min(self._n, p + maxlen)
        chunk = bytes(self._a[p:end])
        e = chunk.find(b"\x00")
        if e < 0:
            return None, p
        return chunk[:e], p + e + 1

    def _parse_header(self):
        """Best-effort parse of the channel-descriptor table (names/labels/types).

        Returns the byte offset where the data stream begins. Channel ids used
        for extraction come from the data stream itself, so this is informational.
        """
        a = self._a
        known = set(n for n, _, _ in A1S_CHANNELS.values())
        # The descriptor table lives right after a small fixed header. We scan
        # for triplets of C-strings (name,label,type) where type looks like a
        # Nox signal type (contains '-' or '.', e.g. 'EEG-C4', 'Resp.Pressure').
        p = 180
        last = p
        while p < min(self._n, 200_000):
            s, np_ = self._cstr(p)
            if s and 1 <= len(s) <= 24 and all(32 <= c < 127 for c in s):
                lbl, np2 = self._cstr(np_)
                typ, np3 = self._cstr(np2)
                if typ and (b"-" in typ or b"." in typ) and all(32 <= c < 127 for c in typ):
                    # channel id = byte at np3+6 in the trailing binary (observed)
                    cid = a[np3 + 6] if np3 + 6 < self._n else 0
                    try:
                        name = s.decode("latin1"); label = lbl.decode("latin1"); t = typ.decode("latin1")
                    except Exception:
                        p += 1; continue
                    self.channels[cid] = Channel(cid, name, label, t)
                    last = np3
                    p = np3
                    continue
            p += 1
        # data stream starts at the first 0xff/0xfb record after the table
        p = last
        while p < self._n - 8 and not (a[p] == 0xff or (a[p] == 0xfb and a[p + 1] == 0x0a)):
            p += 1
        # capture first timestamp as start_time
        for q in range(p, min(self._n - 11, p + 5000)):
            if a[q] == 0xfb and a[q + 1] == 0x0a and a[q + 2] == 0x00:
                v = struct.unpack_from("<Q", self._mv, q + 3)[0]
                if _TS_MIN < v < _TS_MAX:
                    self.start_time = epoch_us_to_datetime(v)
                    break
        return p

    # ------------------------------------------------------------------ walker
    def _next_ff(self, p):
        a = self._a
        while p < self._n - 8:
            if a[p] == 0xff:
                ln = int(a[p + 1]) | (int(a[p + 2]) << 8)
                if 1 <= ln <= 4096:
                    return p
            p += 1
        return p

    def iter_records(self, cids=None):
        """Yield ``(timestamp_us, cid, payload_bytes)`` for every data record.

        ``payload_bytes`` are the raw sample bytes (CRC stripped). Pass ``cids``
        (a set) to only yield those channels (faster). ``timestamp_us`` is the
        most recent frame-marker timestamp (0.1 s resolution).
        """
        a, mv, n = self._a, self._mv, self._n
        p = self._data_start
        cur = 0
        want = None if cids is None else set(cids)
        while p < n - 12:
            t = a[p]
            if t == 0xff:
                ln = int(a[p + 1]) | (int(a[p + 2]) << 8)
                if not (1 <= ln <= 4096):
                    p = self._next_ff(p + 1); continue
                cid = int(a[p + 7])
                if cid == 0 or cid > 0x3a:
                    p = self._next_ff(p + 1); continue
                if want is None or cid in want:
                    # samples region = a[p+8 : p+3+ln]  (length ln-5)
                    yield cur, cid, bytes(a[p + 8:p + 3 + ln])
                p += ln + 6
            elif t == 0xfb and a[p + 1] == 0x0a and a[p + 2] == 0x00:
                cur = struct.unpack_from("<Q", mv, p + 3)[0]
                p += 11
            else:
                p = self._next_ff(p + 1)

    # --------------------------------------------------------------- extraction
    def extract(self, cid: int, dtype: str | None = None):
        """Extract one channel.

        Returns ``(samples, record_ts)`` where ``samples`` is a concatenated 1-D
        numpy array of all samples and ``record_ts`` is the microsecond timestamp
        of each *record* (one per ~0.1 s chunk). ``dtype`` defaults to the known
        A1S dtype for that channel ('<i2', '<i4', or 'u1').
        """
        if dtype is None:
            dtype = A1S_CHANNELS.get(cid, ("?", "<i2", 0))[1]
        itemsize = np.dtype(dtype).itemsize
        chunks, ts = [], []
        for cur, _c, pl in self.iter_records(cids={cid}):
            m = len(pl) // itemsize
            if m:
                chunks.append(np.frombuffer(pl[:m * itemsize], dtype=dtype))
                ts.append(cur)
        if not chunks:
            return np.array([], dtype=dtype), np.array([], dtype=np.int64)
        return np.concatenate(chunks), np.array(ts, dtype=np.int64)

    def channel_summary(self):
        """Return {cid: (count, total_payload_bytes)} — useful to see what's present."""
        import collections
        c = collections.Counter(); b = collections.Counter()
        for _ts, cid, pl in self.iter_records():
            c[cid] += 1; b[cid] += len(pl)
        return {cid: (c[cid], b[cid]) for cid in sorted(c)}

    # ---------------------------------------------------------------- oximetry
    def decode_oximetry(self, cid: int = 0x35):
        """Decode the 1 Hz Nonin oximetry scalar channel.

        Returns ``(time_us, spo2, pulse)`` numpy arrays. SpO2 is in %, pulse in
        bpm. Values >100 (SpO2) or >=250 (pulse) are sentinels for "no signal"
        and are returned as NaN. Byte layout of the 10-byte record observed:
        ``[0]=0x0a len, [6]=per-second counter, [7]=SpO2%, [9]=pulse bpm``.
        """
        ts, spo2, pulse = [], [], []
        for cur, _c, pl in self.iter_records(cids={cid}):
            if len(pl) >= 10:
                s = pl[7]; p = pl[9]
                ts.append(cur)
                spo2.append(np.nan if s > 100 else float(s))
                pulse.append(np.nan if p >= 250 else float(p))
        return (np.array(ts, dtype=np.int64),
                np.array(spo2, dtype=float), np.array(pulse, dtype=float))

    def record_means(self, cid: int, dtype: str | None = None):
        """Per-record mean of a channel (downsamples to ~record rate, e.g. 10 Hz).

        Handy for low-frequency respiratory/airflow waveforms without holding the
        full 200 Hz signal in memory. Returns ``(means, record_ts)``.
        """
        if dtype is None:
            dtype = A1S_CHANNELS.get(cid, ("?", "<i4", 0))[1]
        itemsize = np.dtype(dtype).itemsize
        means, ts = [], []
        for cur, _c, pl in self.iter_records(cids={cid}):
            m = len(pl) // itemsize
            if m:
                means.append(float(np.frombuffer(pl[:m * itemsize], dtype=dtype).mean()))
                ts.append(cur)
        return np.array(means, dtype=float), np.array(ts, dtype=np.int64)
