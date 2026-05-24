#!/usr/bin/env python3
"""Demo: read a Nox .NIF and print a quick clinical-ish summary.

Usage:
    python examples/extract_signals.py /path/to/01_XXXX.NIF
"""
import sys
import numpy as np
from noxnif import NoxNIF, epoch_us_to_datetime


def main(path):
    nif = NoxNIF(path)
    print(f"File:        {path}")
    print(f"Start time:  {nif.start_time}")

    # What channels are present, and how busy each is.
    summary = nif.channel_summary()
    print("\nChannels (cid: records, payload bytes):")
    for cid, (cnt, nb) in summary.items():
        name = nif.channels.get(cid)
        from noxnif import A1S_CHANNELS
        label = A1S_CHANNELS.get(cid, ("?",))[0]
        print(f"  0x{cid:02x} {label:16} records={cnt:8d}")

    # --- Oximetry (SpO2 / pulse), 1 Hz ---
    t_us, spo2, pulse = nif.decode_oximetry()
    if len(spo2):
        sp = spo2[~np.isnan(spo2)]
        pu = pulse[~np.isnan(pulse)]
        dur_h = (t_us[-1] - t_us[0]) / 1e6 / 3600
        print(f"\nOximetry over {dur_h:.1f} h "
              f"({100 * (~np.isnan(spo2)).mean():.0f}% valid):")
        print(f"  SpO2  mean {sp.mean():.1f}%  median {np.median(sp):.0f}%  "
              f"nadir {sp.min():.0f}%")
        print(f"  Pulse mean {pu.mean():.0f} bpm  range {pu.min():.0f}-{pu.max():.0f}")

    # --- Airflow (nasal pressure) downsampled to ~10 Hz ---
    airflow, ts10 = nif.record_means(0x38)
    if len(airflow):
        print(f"\nNasal-pressure airflow: {len(airflow)} samples @~10 Hz "
              f"(use for apnea/hypopnea detection)")

    # --- Detect unplugged channels ---
    print("\nSanity:")
    for cid, label in [(0x0e, "ECG"), (0x14, "right leg EMG")]:
        cnt = summary.get(cid, (0,))[0]
        rate = cnt / max((t_us[-1] - t_us[0]) / 1e6, 1) if len(t_us) else 0
        flag = " (looks UNPLUGGED — ~1 rec/s)" if 0 < rate < 2 else ""
        print(f"  {label}: {cnt} records{flag}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__); sys.exit(1)
    main(sys.argv[1])
